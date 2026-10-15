"""
Price / liquidity enrichment with rate limiting, backoff, and cache.

Primary: DexScreener public API.
Optional fallback: Jupiter price API (Solana mints) when jupiter_price_enabled.

Never fabricates prices. HTTP 429 and transient errors are retried with
exponential backoff + jitter, then return UNAVAILABLE.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from decimal import Decimal
from typing import Any, Optional

import httpx

from sme.config import Settings
from sme.models.enums import ChainId, EnrichmentStatus
from sme.models.trade import NormalizedTrade

logger = logging.getLogger(__name__)

_CHAIN_PATH = {
    ChainId.SOLANA: "solana",
    ChainId.BASE: "base",
    ChainId.BSC: "bsc",
    ChainId.ETHEREUM: "ethereum",
    ChainId.ARBITRUM: "arbitrum",
    ChainId.AVALANCHE: "avalanche",
}

_lock = asyncio.Lock()
_last_request_at: float = 0.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_DEFAULT_MIN_INTERVAL = 0.35


def _cache_key(provider: str, chain: str, token: str) -> str:
    return f"{provider}:{chain.lower()}:{_token_identity(token)}"


def _token_identity(token: str) -> str:
    # EVM hex addresses are case insensitive; Solana base58 mints are not.
    return token.lower() if token.startswith("0x") else token


def _is_finite_positive(v: Any) -> bool:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return f > 0.0 and math.isfinite(f)


async def _rate_limit(min_interval: float) -> None:
    global _last_request_at
    async with _lock:
        now = time.monotonic()
        wait = min_interval - (now - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


def _cache_get(key: str) -> Optional[dict[str, Any]]:
    entry = _cache.get(key)
    if not entry:
        return None
    exp, data = entry
    if time.monotonic() > exp:
        _cache.pop(key, None)
        return None
    return data


def _cache_set(key: str, data: dict[str, Any], ttl: float) -> None:
    _cache[key] = (time.monotonic() + max(1.0, ttl), data)


def clear_price_cache() -> None:
    _cache.clear()


async def _get_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_retries: int,
    backoff_base: float,
    min_interval: float,
) -> Optional[httpx.Response]:
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            await _rate_limit(min_interval)
            resp = await client.get(url)
            if resp.status_code == 429:
                ra = resp.headers.get("Retry-After")
                try:
                    delay = float(ra) if ra else backoff_base * (2 ** attempt)
                except ValueError:
                    delay = backoff_base * (2 ** attempt)
                delay = min(delay, 30.0) + random.uniform(0, 0.3)
                logger.warning("Price provider 429; backoff %.2fs (attempt %s)", delay, attempt + 1)
                await asyncio.sleep(delay)
                continue
            if resp.status_code >= 500:
                delay = min(backoff_base * (2 ** attempt) + random.uniform(0, 0.3), 15.0)
                await asyncio.sleep(delay)
                continue
            return resp
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            delay = min(backoff_base * (2 ** attempt) + random.uniform(0, 0.3), 15.0)
            logger.debug("Price request error %s; retry in %.2fs", exc, delay)
            await asyncio.sleep(delay)
    if last_exc:
        logger.debug("Price request exhausted retries: %s", last_exc)
    return None


def _normalize_chain_path(chain: str) -> str:
    for cid, path in _CHAIN_PATH.items():
        if chain == cid.value or chain == path:
            return path
    return chain


async def fetch_dexscreener_price(
    token_address: str,
    chain: str,
    settings: Settings,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[dict[str, Any]]:
    if not settings.dexscreener_enabled:
        return None
    ttl = float(getattr(settings, "price_cache_ttl_seconds", 30) or 30)
    chain_path = _normalize_chain_path(chain)
    key = _cache_key("dexscreener", chain_path, token_address)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # Normalize EVM addresses to lowercase for stable cache + HTTP identity
    req_token = token_address.lower() if token_address.startswith("0x") else token_address
    url = f"https://api.dexscreener.com/latest/dex/tokens/{req_token}"
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=settings.provider_timeout_seconds)
    try:
        max_retries = max(0, min(3, settings.provider_max_retries))
        backoff = float(getattr(settings, "provider_backoff_base", 0.5) or 0.5)
        min_interval = float(
            getattr(settings, "price_min_interval_seconds", _DEFAULT_MIN_INTERVAL) or _DEFAULT_MIN_INTERVAL
        )
        resp = await _get_with_retries(
            client, url, max_retries=max_retries, backoff_base=backoff, min_interval=min_interval
        )
        if resp is None or resp.status_code != 200:
            return None
        try:
            body = resp.json() or {}
        except Exception:
            return None
        pairs = body.get("pairs") or []
        # priceUsd is the pair's base-token price. A quote-token match is not
        # sufficient, even on the right chain. Missing identity fails closed.
        pair = next((p for p in pairs if isinstance(p, dict)
                     and p.get("chainId") == chain_path
                     and isinstance(p.get("baseToken"), dict)
                     and isinstance(p["baseToken"].get("address"), str)
                     and _token_identity(p["baseToken"]["address"]) == _token_identity(req_token)
                     and _is_finite_positive(p.get("priceUsd"))), None)
        if not pair:
            return None
        price = pair.get("priceUsd")
        if not _is_finite_positive(price):
            return None
        price_f = float(price)
        liq = (pair.get("liquidity") or {}).get("usd")
        mcap = pair.get("marketCap") or pair.get("fdv")
        base = pair.get("baseToken") or {}
        out = {
            "price_usd": price_f,
            "liquidity_usd": (float(liq) if liq is not None and str(liq) not in ("nan", "NaN") and float(liq) == float(liq) and float(liq) >= 0 else None),
            "market_cap_usd": float(mcap) if mcap is not None and _is_finite_positive(mcap) else None,
            "symbol": base.get("symbol"),
            "name": base.get("name"),
            "provider": "dexscreener",
            "confidence": "HIGH" if liq and _is_finite_positive(liq) and float(liq) > 1000 else "MEDIUM",
            "chain": chain_path,
            "token_address": req_token,
            "fetched_at_mono": time.monotonic(),
        }
        _cache_set(key, out, ttl)
        return out
    except Exception as exc:
        logger.debug("DexScreener fetch failed: %s", exc)
        return None
    finally:
        if owns and client:
            await client.aclose()


async def fetch_jupiter_price(
    token_address: str,
    settings: Settings,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[dict[str, Any]]:
    if not getattr(settings, "jupiter_price_enabled", False):
        return None
    ttl = float(getattr(settings, "price_cache_ttl_seconds", 30) or 30)
    key = _cache_key("jupiter", "solana", token_address)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    base = getattr(settings, "jupiter_price_url", None) or "https://api.jup.ag/price/v2"
    url = f"{base}?ids={token_address}"
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=settings.provider_timeout_seconds)
    try:
        max_retries = max(0, min(3, settings.provider_max_retries))
        backoff = float(getattr(settings, "provider_backoff_base", 0.5) or 0.5)
        min_interval = float(
            getattr(settings, "price_min_interval_seconds", _DEFAULT_MIN_INTERVAL) or _DEFAULT_MIN_INTERVAL
        )
        resp = await _get_with_retries(
            client, url, max_retries=max_retries, backoff_base=backoff, min_interval=min_interval
        )
        if resp is None or resp.status_code != 200:
            return None
        entry = ((resp.json() or {}).get("data") or {}).get(token_address)
        if not entry or entry.get("price") is None:
            return None
        price_f = float(entry["price"])
        if not _is_finite_positive(price_f):
            return None
        out = {
            "price_usd": price_f,
            "liquidity_usd": None,
            "market_cap_usd": None,
            "symbol": None,
            "name": None,
            "provider": "jupiter",
            "confidence": "MEDIUM",
            "chain": "solana",
            "token_address": token_address,
            "fetched_at_mono": time.monotonic(),
        }
        _cache_set(key, out, ttl)
        return out
    except Exception as exc:
        logger.debug("Jupiter price fetch failed: %s", exc)
        return None
    finally:
        if owns and client:
            await client.aclose()


async def fetch_token_price(
    token_address: str,
    chain: str,
    settings: Settings,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[dict[str, Any]]:
    result = await fetch_dexscreener_price(token_address, chain, settings, client=client)
    if result is not None:
        return result
    if chain in ("solana",):
        return await fetch_jupiter_price(token_address, settings, client=client)
    return None


async def enrich_trade(trade: NormalizedTrade, settings: Settings) -> NormalizedTrade:
    if not settings.dexscreener_enabled and not getattr(settings, "jupiter_price_enabled", False):
        trade.enrichment_status = EnrichmentStatus.UNAVAILABLE
        return trade
    data = await fetch_token_price(trade.token_address, trade.chain.value, settings)
    if not data:
        trade.enrichment_status = EnrichmentStatus.UNAVAILABLE
        return trade
    trade.price = Decimal(str(data["price_usd"]))
    if data.get("liquidity_usd") is not None:
        trade.liquidity_usd = Decimal(str(data["liquidity_usd"]))
    if data.get("market_cap_usd") is not None:
        trade.market_cap_usd = Decimal(str(data["market_cap_usd"]))
    if not trade.token_symbol and data.get("symbol"):
        trade.token_symbol = data["symbol"]
        trade.token_name = data.get("name")
    trade.enrichment_status = EnrichmentStatus.PARTIAL
    return trade
