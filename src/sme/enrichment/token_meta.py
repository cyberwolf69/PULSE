"""
EVM token metadata (decimals) resolution with cache.

Rules:
  - Never assume 18 decimals for arbitrary tokens.
  - Normalized quantity is only produced when decimals are known.
  - Raw integer amounts are preserved for auditability.
  - RPC failures / rate limits → decimals unknown (no silent conversion).
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sme.models.trade import NormalizedTrade

import httpx

from sme.config import Settings

logger = logging.getLogger(__name__)

# ERC-20 decimals() selector = keccak256("decimals()")[:4]
DECIMALS_SELECTOR = "0x313ce567"

# Process memory cache: (chain, address_lower) -> (decimals, expires_monotonic)
_mem_cache: dict[tuple[str, str], tuple[int, float]] = {}
_DEFAULT_TTL = 3600.0

# Seed only well-known addresses (not a default for unknown tokens)
_KNOWN: dict[tuple[str, str], int] = {
    # Base
    ("base", "0x4200000000000000000000000000000000000006"): 18,  # WETH
    ("base", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"): 6,  # USDC
    ("base", "0x50c5725949a6f0c72e6c4a641f24049a917db0cb"): 18,  # DAI
    # BSC
    ("bsc", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"): 18,  # WBNB
    ("bsc", "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"): 18,  # USDC (BSC often 18)
    ("bsc", "0x55d398326f99059ff775485246999027b3197955"): 18,  # USDT BSC
    # Ethereum (for shared adapter readiness)
    ("ethereum", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"): 18,  # WETH
    ("ethereum", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"): 6,  # USDC
    ("ethereum", "0xdac17f958d2ee523a2206206994597c13d831ec7"): 6,  # USDT
}


def clear_decimals_cache() -> None:
    _mem_cache.clear()


def known_decimals(chain: str, address: str) -> Optional[int]:
    key = (chain.lower(), address.lower())
    return _KNOWN.get(key)


def normalize_raw_amount(raw: int | str | Decimal, decimals: int) -> Decimal:
    """Convert raw integer units → human units. Raises if decimals invalid."""
    if decimals < 0 or decimals > 77:
        raise ValueError(f"invalid decimals: {decimals}")
    raw_d = Decimal(str(raw))
    if raw_d != raw_d.to_integral_value() and isinstance(raw, (int, str)):
        # already fractional — do not treat as raw
        raise ValueError("raw amount must be integer units")
    with localcontext() as ctx:
        ctx.prec = max(100, len(raw_d.as_tuple().digits) + decimals)
        return raw_d / (Decimal(10) ** decimals)


def try_normalize(
    raw_amount: Optional[int | str | Decimal],
    decimals: Optional[int],
) -> tuple[Optional[Decimal], str]:
    """
    Returns (normalized_or_none, status).

    status:
      - normalized
      - decimals_unknown
      - missing_raw
      - invalid
    """
    if raw_amount is None:
        return None, "missing_raw"
    if decimals is None:
        return None, "decimals_unknown"
    try:
        return normalize_raw_amount(raw_amount, int(decimals)), "normalized"
    except (InvalidOperation, ValueError, TypeError):
        return None, "invalid"


async def fetch_erc20_decimals(
    *,
    rpc_http: str,
    token_address: str,
    chain: str,
    settings: Settings,
    client: Optional[httpx.AsyncClient] = None,
    ttl_seconds: float = _DEFAULT_TTL,
) -> Optional[int]:
    """
    Resolve decimals via eth_call. Uses memory cache and known seeds.
    Returns None on failure — never invents a value.
    """
    if not rpc_http or not token_address:
        return None
    chain_l = chain.lower()
    addr = token_address.lower()
    key = (chain_l, addr)

    # known seed
    if key in _KNOWN:
        return _KNOWN[key]

    now = time.monotonic()
    cached = _mem_cache.get(key)
    if cached and cached[1] > now:
        return cached[0]

    from sme.adapters.evm_rpc import evm_rpc, quantity
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=settings.provider_timeout_seconds)
    try:
        result = await evm_rpc(client, rpc_http, "eth_call", [
            {"to": addr, "data": DECIMALS_SELECTOR}, "latest",
        ], settings)
        decimals = quantity(result)
        if decimals > 77:
            return None
        _mem_cache[key] = (decimals, now + ttl_seconds)
        return decimals
    except Exception as exc:
        logger.debug("decimals fetch failed (%s)", type(exc).__name__)
        return None
    finally:
        if owns and client:
            await client.aclose()


async def resolve_decimals(
    *,
    chain: str,
    token_address: str,
    settings: Settings,
    rpc_http: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
    db_lookup=None,
) -> Optional[int]:
    """
    Resolution order:
      1. Known seed table
      2. Process memory cache
      3. Optional db_lookup(chain, address) -> Optional[int]
      4. eth_call via rpc_http
    """
    chain_l = chain.lower()
    addr = token_address.lower()
    known = known_decimals(chain_l, addr)
    if known is not None:
        return known

    now = time.monotonic()
    cached = _mem_cache.get((chain_l, addr))
    if cached and cached[1] > now:
        return cached[0]

    if db_lookup is not None:
        try:
            db_val = await db_lookup(chain_l, addr)
            if db_val is not None:
                _mem_cache[(chain_l, addr)] = (int(db_val), now + _DEFAULT_TTL)
                return int(db_val)
        except Exception as exc:
            logger.debug("db decimals lookup failed: %s", exc)

    if not rpc_http:
        return None
    return await fetch_erc20_decimals(
        rpc_http=rpc_http,
        token_address=addr,
        chain=chain_l,
        settings=settings,
        client=client,
    )


async def finalize_evm_trade_amounts(
    trade: "NormalizedTrade",
    settings: Settings,
    *,
    rpc_http: Optional[str] = None,
    session=None,
) -> "NormalizedTrade":
    """
    Second-pass amount normalization using DB cache + RPC.

    Safe to call for any trade; non-EVM or already-normalized trades are no-ops.
    Mutates and returns the same trade instance (NormalizedTrade is mutable via
    model_copy if frozen — use assignment of fields).
    """
    from sme.models.enums import ChainId

    if trade.chain not in (
        ChainId.BASE,
        ChainId.BSC,
        ChainId.ETHEREUM,
        ChainId.ARBITRUM,
        ChainId.AVALANCHE,
        ChainId.ROBINHOOD,
    ):
        return trade

    evidence = dict(trade.swap_evidence or {})
    status = evidence.get("amount_status") or ""
    if status in ("normalized", "normalized_structured"):
        return trade

    amount_raw = evidence.get("token_amount_raw")
    quote_raw = evidence.get("quote_amount_raw")
    quote_token = trade.quote_token_address or evidence.get("quote_token")

    async def _db_lookup(chain: str, address: str):
        if session is None:
            return None
        from sme.db import repository as repo
        return await repo.get_token_decimals(session, chain, address)

    async def _db_store(chain: str, address: str, decimals: int):
        if session is None:
            return
        from sme.db import repository as repo
        await repo.upsert_token_decimals(session, chain, address, decimals)

    # Base token
    if amount_raw is not None and (trade.token_quantity is None or status in ("raw_pending_decimals", "decimals_unknown")):
        decimals = await resolve_decimals(
            chain=trade.chain.value,
            token_address=trade.token_address,
            settings=settings,
            rpc_http=rpc_http,
            db_lookup=_db_lookup if session is not None else None,
        )
        qty, st = try_normalize(amount_raw, decimals)
        evidence["amount_status"] = st if decimals is None else ("normalized" if st == "normalized" else st)
        if decimals is not None:
            evidence["token_decimals"] = decimals
            try:
                await _db_store(trade.chain.value, trade.token_address, decimals)
            except Exception:
                pass
        if qty is not None:
            trade.token_quantity = qty
            evidence["amount_status"] = "normalized"
        else:
            trade.token_quantity = None
            if evidence.get("amount_status") not in ("invalid", "missing_raw"):
                evidence["amount_status"] = "decimals_unknown"
            evidence["quantity_note"] = (
                "token_quantity unset; raw preserved — unavailable for position/PnL qty"
            )

    # Quote leg (independent)
    if quote_raw is not None and quote_token:
        q_dec = await resolve_decimals(
            chain=trade.chain.value,
            token_address=str(quote_token),
            settings=settings,
            rpc_http=rpc_http,
            db_lookup=_db_lookup if session is not None else None,
        )
        q_qty, q_st = try_normalize(quote_raw, q_dec)
        evidence["quote_amount_status"] = q_st
        if q_dec is not None:
            evidence["quote_decimals"] = q_dec
            try:
                await _db_store(trade.chain.value, str(quote_token), q_dec)
            except Exception:
                pass
        if q_qty is not None:
            trade.quote_quantity = q_qty
            evidence["quote_amount_status"] = "normalized"
        else:
            trade.quote_quantity = None

    trade.swap_evidence = evidence
    return trade
