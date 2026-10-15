"""
Injective adapter — native Injective event/transaction ingestion.

Injective is NOT treated as EVM for parsing purposes. Structured exchange
events are accepted when provided with documented fields. Status remains
UNCONFIGURED until LCD/RPC is configured; LIVE_VERIFIED is not claimed here.
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


class InjectiveAdapter(ChainAdapter):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.chain = ChainId.INJECTIVE
        self._lcd = settings.injective_lcd
        self._enabled = settings.injective_enabled
        self._client: Optional[httpx.AsyncClient] = None
        self._health = ProviderHealth.UNCONFIGURED

    async def connect(self) -> None:
        if not self._enabled or not self._lcd:
            self._health = ProviderHealth.UNCONFIGURED
            return
        self._client = httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds)
        self._health = await self.health_check()

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> ProviderHealth:
        if not self._enabled or not self._lcd or not self._client:
            self._health = ProviderHealth.UNCONFIGURED
            return self._health
        try:
            # Cosmos LCD node_info is a common health endpoint
            url = self._lcd.rstrip("/") + "/cosmos/base/tendermint/v1beta1/node_info"
            resp = await self._client.get(url)
            if resp.status_code == 200:
                self._health = ProviderHealth.HEALTHY
            else:
                self._health = ProviderHealth.DEGRADED
        except Exception as exc:
            logger.warning("Injective health check failed: %s", exc)
            self._health = ProviderHealth.UNAVAILABLE
        return self._health

    async def get_latest_height(self) -> Optional[int]:
        if not self._client or not self._lcd:
            return None
        try:
            url = self._lcd.rstrip("/") + "/cosmos/base/tendermint/v1beta1/blocks/latest"
            resp = await self._client.get(url)
            data = resp.json()
            h = data.get("block", {}).get("header", {}).get("height")
            return int(h) if h is not None else None
        except Exception:
            return None

    async def parse_swap_from_payload(self, payload: dict) -> Optional[NormalizedTrade]:
        """
        Structured Injective exchange event.
        Expects: txhash, height, timestamp, swap: {wallet, denom, symbol, direction, amount, quote_amount}
        """
        if payload.get("code") not in (None, 0):
            return None  # failed tx
        swap = payload.get("swap")
        if not swap or not isinstance(swap, dict):
            return None
        direction_raw = str(swap.get("direction", "")).upper()
        if direction_raw not in ("BUY", "SELL"):
            return None
        wallet = swap.get("wallet") or swap.get("sender")
        denom = swap.get("denom") or swap.get("token")
        if not wallet or not denom:
            return None
        txhash = payload.get("txhash") or payload.get("tx_hash") or payload.get("hash")
        if not txhash:
            return None
        ts_raw = payload.get("timestamp") or payload.get("block_time")
        if ts_raw is not None:
            try:
                if isinstance(ts_raw, (int, float)):
                    ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
                else:
                    ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except (ValueError, TypeError, OSError):
                ts = datetime.now(timezone.utc)
        else:
            ts = datetime.now(timezone.utc)
        height = payload.get("height") or payload.get("block_height")
        amount = swap.get("amount") or swap.get("token_amount")
        quote = swap.get("quote_amount")
        return NormalizedTrade(
            chain=self.chain,
            tx_signature=str(txhash),
            event_index=int(swap.get("event_index", 0)),
            wallet_address=str(wallet),
            token_address=str(denom),
            token_symbol=swap.get("symbol") or swap.get("token_symbol"),
            direction=TradeDirection.BUY if direction_raw == "BUY" else TradeDirection.SELL,
            token_quantity=Decimal(str(amount)) if amount is not None else None,
            quote_quantity=Decimal(str(quote)) if quote is not None else None,
            timestamp=ts,
            block_height=int(height) if height is not None else None,
            confirmation=ConfirmationState.CONFIRMED,
            provider_source="injective_lcd",
            swap_evidence={"market_id": swap.get("market_id"), "denom": denom},
            enrichment_status=EnrichmentStatus.UNKNOWN,
        )

    def supported_protocols(self) -> list[str]:
        return ["injective_exchange_structured", "spot_market"]

    def limitations(self) -> list[str]:
        return [
            "Injective structured event parse only — not full proto decoding",
            "Not treated as EVM",
            "LIVE_VERIFIED requires configured LCD + successful end-to-end run",
        ]
