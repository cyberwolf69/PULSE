"""
EVM adapter (Base primary; reusable for BSC / Ethereum / Arbitrum / Avalanche).

Supports documented Uniswap V2-style Swap and V3-style Swap events.
Plain ERC-20 Transfer is NOT treated as a trade.
Failed transactions are rejected.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import httpx

from sme.adapters.base import ChainAdapter
from sme.adapters.evm_rpc import evm_rpc, quantity, EvmRpcError
from sme.config import Settings
from sme.models.enums import (
    ChainId,
    ConfirmationState,
    EnrichmentStatus,
    ProviderHealth,
    TradeDirection,
)
from sme.models.trade import NormalizedTrade
from sme.enrichment.token_meta import resolve_decimals, try_normalize
from sme.adapters.evm_decode import (
    decode_evm_swap_for_wallet,
    decode_result_to_payload,
    UNISWAP_V2_SWAP,
    UNISWAP_V3_SWAP,
)

logger = logging.getLogger(__name__)

class EvmAdapter(ChainAdapter):
    """Shared EVM adapter; instantiate per network with distinct config."""

    def __init__(
        self,
        settings: Settings,
        chain: ChainId,
        *,
        rpc_http: Optional[str],
        enabled: bool,
        chain_id_num: int,
        name: str = "evm",
    ):
        self.settings = settings
        self.chain = chain
        self._rpc_http = rpc_http
        self._enabled = enabled
        self._chain_id_num = chain_id_num
        self._name = name
        self._client: Optional[httpx.AsyncClient] = None
        self._health = ProviderHealth.UNCONFIGURED

    async def connect(self) -> None:
        if not self._enabled or not self._rpc_http:
            self._health = ProviderHealth.UNCONFIGURED
            logger.info("%s adapter UNCONFIGURED", self._name)
            return
        self._client = httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds)
        self._health = await self.health_check()

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _rpc(self, method: str, params: list[Any] | None = None) -> Any:
        if not self._client or not self._rpc_http:
            raise RuntimeError(f"{self._name} RPC not connected")
        return await evm_rpc(self._client, self._rpc_http, method, params or [], self.settings)

    async def health_check(self) -> ProviderHealth:
        if not self._enabled or not self._rpc_http:
            self._health = ProviderHealth.UNCONFIGURED
            return self._health
        try:
            if quantity(await self._rpc("eth_chainId")) != self._chain_id_num:
                raise EvmRpcError("RPC chain identity mismatch")
            result = await self._rpc("eth_blockNumber")
            quantity(result)
            self._health = ProviderHealth.HEALTHY
        except Exception as exc:
            logger.warning("%s health_check failed: %s", self._name, exc)
            self._health = ProviderHealth.UNAVAILABLE
        return self._health

    async def get_latest_height(self) -> Optional[int]:
        try:
            hex_num = await self._rpc("eth_blockNumber")
            return int(hex_num, 16) if hex_num else None
        except Exception:
            return None

    def supported_protocols(self) -> list[str]:
        return [
            "Uniswap V2 Swap event",
            "Uniswap V3 Swap event",
            "Compatible forks (Aerodrome, PancakeSwap-style) when topic matches",
        ]

    def limitations(self) -> list[str]:
        return [
            "Live log subscription not enabled in this vertical slice",
            "Auto-decode uses Transfer correlation + Swap topic; plain Transfer rejected",
            "Plain ERC-20 Transfer is rejected",
            "USD value requires external price enrichment",
            f"Confirmation depth configured at {self.settings.evm_confirmation_depth}",
        ]

    async def parse_swap_from_payload(self, payload: dict) -> Optional[NormalizedTrade]:
        """
        Parse an EVM transaction/receipt-shaped payload.

        Structured format (tests + integrators):
        {
          "hash": "0x...",
          "blockNumber": "0x123" or 291,
          "status": "0x1" or 1,
          "timestamp": 1710000000,
          "swap": {
            "wallet": "0x...",
            "token": "0x...",
            "token_symbol": "DEGEN",
            "direction": "BUY"|"SELL",
            "token_amount": "1000.5",
            "quote_amount": "0.2",
            "protocol": "UniswapV3",
            "log_index": 0,
            "topic0": "0xc42079f9..."
          }
        }
        """
        if not payload:
            return None
        if payload.get("chain") not in (None, self.chain.value):
            return None
        if payload.get("chain_id") not in (None, self._chain_id_num):
            return None
        if payload.get("_source") == "live_poller":
            if (payload.get("chain_id") != self._chain_id_num
                    or payload.get("confirmations", -1) < self.settings.evm_confirmation_depth
                    or not payload.get("blockHash")):
                return None

        status = payload.get("status")
        if status is not None:
            # Accept 1, "0x1", True
            ok = status in (1, "0x1", "1", True)
            if not ok:
                logger.debug("Rejecting failed EVM tx %s", payload.get("hash"))
                return None

        swap = payload.get("swap")
        if not swap:
            # Automatic decode from receipt logs + watched wallet
            wallet = (
                payload.get("watch_wallet")
                or payload.get("wallet")
                or payload.get("from")
            )
            if not wallet or not (payload.get("logs") or []):
                return None
            result = decode_evm_swap_for_wallet(payload, wallet=wallet, chain=self.chain.value)
            self.last_decode_reason = result.reason
            if not result.ok:
                logger.debug(
                    "EVM decode unclassified tx=%s reason=%s",
                    (result.tx_hash or "")[:12],
                    result.reason,
                )
                return None
            built = decode_result_to_payload(
                result, timestamp=payload.get("timestamp")
            )
            if not built:
                return None
            payload = {**payload, **built}
            swap = payload["swap"]

        direction_raw = (swap.get("direction") or "").upper()
        if direction_raw not in ("BUY", "SELL"):
            return None

        wallet = swap.get("wallet") or swap.get("sender") or swap.get("to")
        token = swap.get("token") or swap.get("token_address")
        if not wallet or not token:
            return None

        tx_hash = payload.get("hash") or payload.get("tx_hash") or payload.get("transactionHash")
        if not tx_hash:
            return None

        event_index = int(swap.get("log_index") or swap.get("event_index") or 0)
        symbol = swap.get("token_symbol")
        protocol = swap.get("protocol") or "Uniswap"
        topic0 = swap.get("topic0")

        block_raw = payload.get("blockNumber") or payload.get("block_number")
        block_height = None
        if block_raw is not None:
            try:
                block_height = int(block_raw, 16) if isinstance(block_raw, str) and block_raw.startswith("0x") else int(block_raw)
            except (ValueError, TypeError):
                block_height = None

        ts_raw = payload.get("timestamp") or payload.get("block_timestamp")
        ts = (
            datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
            if ts_raw
            else datetime.now(timezone.utc)
        )

        # ── Amount units ──────────────────────────────────────────
        # Structured fixtures may supply human units (amount_status=normalized
        # or pre-set token_amount without raw). Auto-decode supplies
        # token_amount_raw only until decimals are resolved.
        amount_status = swap.get("amount_status") or payload.get("amount_status")
        token_qty: Optional[Decimal] = None
        quote_qty: Optional[Decimal] = None
        token_decimals: Optional[int] = None
        quote_decimals: Optional[int] = None
        amount_raw = swap.get("token_amount_raw")
        quote_raw = swap.get("quote_amount_raw")

        if amount_status == "raw_pending_decimals" or (
            amount_raw is not None and swap.get("token_amount") is None
        ):
            # Resolve decimals — never guess
            token_decimals = await resolve_decimals(
                chain=self.chain.value,
                token_address=token,
                settings=self.settings,
                rpc_http=self._rpc_http if self._enabled else None,
                client=self._client,
            )
            token_qty, amount_status = try_normalize(amount_raw, token_decimals)
            if swap.get("quote_token") and quote_raw is not None:
                quote_decimals = await resolve_decimals(
                    chain=self.chain.value,
                    token_address=swap["quote_token"],
                    settings=self.settings,
                    rpc_http=self._rpc_http if self._enabled else None,
                    client=self._client,
                )
                quote_qty, _qstatus = try_normalize(quote_raw, quote_decimals)
        else:
            # Structured / pre-normalized human units
            if swap.get("token_amount") is not None or swap.get("amount") is not None:
                token_qty = Decimal(str(swap.get("token_amount") or swap.get("amount")))
                amount_status = "normalized_structured"
            if swap.get("quote_amount") is not None:
                quote_qty = Decimal(str(swap.get("quote_amount")))
            if swap.get("token_decimals") is not None:
                try:
                    token_decimals = int(swap["token_decimals"])
                except (TypeError, ValueError):
                    token_decimals = None

        evidence = {
            "protocol": protocol,
            "topic0": topic0,
            "chain_id": self._chain_id_num,
            "amount_status": amount_status,
            "block_hash": payload.get("blockHash"),
            "confirmations": payload.get("confirmations"),
        }
        if amount_raw is not None:
            evidence["token_amount_raw"] = str(amount_raw)
        if token_decimals is not None:
            evidence["token_decimals"] = token_decimals
        if quote_raw is not None:
            evidence["quote_amount_raw"] = str(quote_raw)
        if quote_decimals is not None:
            evidence["quote_decimals"] = quote_decimals
        if amount_status == "decimals_unknown":
            evidence["quantity_note"] = (
                "token_quantity left unset; raw units preserved — "
                "do not mix with USD price as if human units"
            )

        return NormalizedTrade(
            chain=self.chain,
            # Wallet-centric decoding may produce two distinct views of one swap.
            event_id=f"{self.chain.value}:{tx_hash.lower()}:{event_index}:{wallet.lower()}",
            tx_signature=tx_hash,
            event_index=event_index,
            wallet_address=wallet.lower() if wallet.startswith("0x") else wallet,
            token_address=token.lower() if token.startswith("0x") else token,
            token_symbol=symbol,
            direction=TradeDirection.BUY if direction_raw == "BUY" else TradeDirection.SELL,
            token_quantity=token_qty,
            quote_quantity=quote_qty,
            quote_token_address=(swap.get("quote_token") or None),
            timestamp=ts,
            block_height=block_height,
            confirmation=ConfirmationState.CONFIRMED,
            provider_source=f"{self._name}_rpc",
            swap_evidence=evidence,
            enrichment_status=EnrichmentStatus.UNKNOWN,
        )


def build_base_adapter(settings: Settings) -> EvmAdapter:
    return EvmAdapter(
        settings,
        ChainId.BASE,
        rpc_http=settings.base_rpc_http,
        enabled=settings.base_enabled,
        chain_id_num=settings.base_chain_id,
        name="base",
    )


def build_bsc_adapter(settings: Settings) -> EvmAdapter:
    return EvmAdapter(
        settings,
        ChainId.BSC,
        rpc_http=settings.bsc_rpc_http,
        enabled=settings.bsc_enabled,
        chain_id_num=settings.bsc_chain_id,
        name="bsc",
    )


def build_ethereum_adapter(settings: Settings) -> EvmAdapter:
    return EvmAdapter(
        settings,
        ChainId.ETHEREUM,
        rpc_http=settings.ethereum_rpc_http,
        enabled=settings.ethereum_enabled,
        chain_id_num=1,
        name="ethereum",
    )


def build_arbitrum_adapter(settings: Settings) -> EvmAdapter:
    return EvmAdapter(
        settings,
        ChainId.ARBITRUM,
        rpc_http=settings.arbitrum_rpc_http,
        enabled=settings.arbitrum_enabled,
        chain_id_num=42161,
        name="arbitrum",
    )


def build_avalanche_adapter(settings: Settings) -> EvmAdapter:
    return EvmAdapter(
        settings,
        ChainId.AVALANCHE,
        rpc_http=settings.avalanche_rpc_http,
        enabled=settings.avalanche_enabled,
        chain_id_num=43114,
        name="avalanche",
    )


def build_robinhood_adapter(settings: Settings) -> EvmAdapter:
    """Robinhood Chain (chain ID 4663) — EVM-compatible; uses same Uniswap-style parse path."""
    return EvmAdapter(
        settings,
        ChainId.ROBINHOOD,
        rpc_http=settings.robinhood_rpc_http,
        enabled=settings.robinhood_enabled,
        chain_id_num=settings.robinhood_chain_id,
        name="robinhood",
    )
