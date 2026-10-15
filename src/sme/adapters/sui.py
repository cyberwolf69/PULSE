"""
Sui adapter — native Sui transaction ingestion and structured swap interpretation.

Does NOT reuse EVM assumptions. Parses documented Sui transaction effects /
balance changes when provided in structured form. Status remains UNCONFIGURED
until an RPC URL is set and a live health check succeeds; LIVE_VERIFIED requires
end-to-end evidence which this environment does not claim.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import httpx

from sme.adapters.base import ChainAdapter
from sme.config import Settings
from sme.models.enums import (
    ChainId,
    ConfirmationState,
    EnrichmentStatus,
    ProviderHealth,
    TradeDirection,
)
from sme.models.trade import NormalizedTrade

logger = logging.getLogger(__name__)


class SuiAdapter(ChainAdapter):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.chain = ChainId.SUI
        self._rpc = settings.sui_rpc_http
        self._enabled = settings.sui_enabled
        self._client: Optional[httpx.AsyncClient] = None
        self._health = ProviderHealth.UNCONFIGURED

    async def connect(self) -> None:
        if not self._enabled or not self._rpc:
            self._health = ProviderHealth.UNCONFIGURED
            return
        self._client = httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds)
        self._health = await self.health_check()

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> ProviderHealth:
        if not self._enabled or not self._rpc or not self._client:
            self._health = ProviderHealth.UNCONFIGURED
            return self._health
        try:
            # Sui JSON-RPC: suix_getLatestCheckpointSequenceNumber or similar
            resp = await self._client.post(
                self._rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sui_getLatestCheckpointSequenceNumber",
                    "params": [],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if "result" in data:
                self._health = ProviderHealth.HEALTHY
            else:
                self._health = ProviderHealth.DEGRADED
        except Exception as exc:
            logger.warning("Sui health check failed: %s", exc)
            self._health = ProviderHealth.UNAVAILABLE
        return self._health

    async def get_latest_height(self) -> Optional[int]:
        if not self._client or not self._rpc:
            return None
        try:
            resp = await self._client.post(
                self._rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sui_getLatestCheckpointSequenceNumber",
                    "params": [],
                },
            )
            data = resp.json()
            return int(data["result"]) if "result" in data else None
        except Exception:
            return None

    async def parse_swap_from_payload(self, payload: dict) -> Optional[NormalizedTrade]:
        """
        Structured Sui swap path. Expects:
          digest, checkpoint, timestamp_ms, swap: {wallet, coin_type, symbol, direction, amount, quote_amount}
        Unknown / transfer-only payloads return None (not fabricated BUY).
        """
        if payload.get("error") or payload.get("effects", {}).get("status", {}).get("status") == "failure":
            return None
        swap = payload.get("swap")
        if not swap or not isinstance(swap, dict):
            return None
        direction_raw = str(swap.get("direction", "")).upper()
        if direction_raw not in ("BUY", "SELL"):
            return None
        wallet = swap.get("wallet") or swap.get("sender")
        coin_type = swap.get("coin_type") or swap.get("token")
        if not wallet or not coin_type:
            return None
        digest = payload.get("digest") or payload.get("tx_digest") or payload.get("signature")
        if not digest:
            return None
        ts_ms = payload.get("timestamp_ms") or payload.get("timestamp")
        if ts_ms is not None:
            try:
                ts = datetime.fromtimestamp(int(ts_ms) / (1000 if int(ts_ms) > 1e12 else 1), tz=timezone.utc)
            except (ValueError, TypeError, OSError):
                ts = datetime.now(timezone.utc)
        else:
            ts = datetime.now(timezone.utc)
        checkpoint = payload.get("checkpoint")
        amount = swap.get("amount") or swap.get("token_amount")
        quote = swap.get("quote_amount")
        return NormalizedTrade(
            chain=self.chain,
            tx_signature=str(digest),
            event_index=int(swap.get("event_index", 0)),
            wallet_address=str(wallet),
            token_address=str(coin_type),
            token_symbol=swap.get("symbol") or swap.get("token_symbol"),
            direction=TradeDirection.BUY if direction_raw == "BUY" else TradeDirection.SELL,
            token_quantity=Decimal(str(amount)) if amount is not None else None,
            quote_quantity=Decimal(str(quote)) if quote is not None else None,
            timestamp=ts,
            block_height=int(checkpoint) if checkpoint is not None else None,
            confirmation=ConfirmationState.CONFIRMED,
            provider_source="sui_rpc",
            swap_evidence={"program": swap.get("program"), "coin_type": coin_type},
            enrichment_status=EnrichmentStatus.UNKNOWN,
        )

    def supported_protocols(self) -> list[str]:
        return ["sui_structured_swap", "cetus_style", "balance_change"]

    def limitations(self) -> list[str]:
        return [
            "Sui structured parse only — not full Move call decoding",
            "LIVE_VERIFIED requires configured RPC + successful end-to-end run",
            "Transfer-only payloads are rejected (not classified as BUY)",
        ]
