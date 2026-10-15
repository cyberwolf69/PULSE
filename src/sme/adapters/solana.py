"""
Solana adapter.

LIVE path: real RPC connectivity (getSlot / getHealth / getTransaction).
Swap parsing:
  1. Explicit structured `swap` object (fixtures / integrators)
  2. Automatic balance-delta decode from jsonParsed getTransaction metadata

Does NOT invent BUY/SELL when evidence is insufficient.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import httpx
from sme.adapters.rpc_pacing import rpc_post

from sme.adapters.base import ChainAdapter
from sme.adapters.solana_decode import decode_solana_swap, decode_result_to_swap_payload
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


class SolanaAdapter(ChainAdapter):
    chain = ChainId.SOLANA

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Optional[httpx.AsyncClient] = None
        self._health = ProviderHealth.UNCONFIGURED
        self.last_decode_reason: Optional[str] = None

    async def connect(self) -> None:
        if not self.settings.solana_enabled or not self.settings.solana_rpc_http:
            self._health = ProviderHealth.UNCONFIGURED
            logger.info("Solana adapter UNCONFIGURED (no RPC URL or disabled)")
            return
        self._client = httpx.AsyncClient(
            timeout=self.settings.provider_timeout_seconds,
            headers={"Content-Type": "application/json"},
        )
        self._health = await self.health_check()

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _rpc(self, method: str, params: list[Any] | None = None) -> Any:
        if not self._client or not self.settings.solana_rpc_http:
            raise RuntimeError("Solana RPC not connected")
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        resp = await rpc_post(self._client, self.settings.solana_rpc_http, payload, self.settings)
        if resp.status_code == 429:
            self._health = ProviderHealth.DEGRADED
            raise RuntimeError("Solana RPC rate limited (429)")
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError("Solana RPC error")
        return data.get("result")

    async def health_check(self) -> ProviderHealth:
        if not self.settings.solana_enabled or not self.settings.solana_rpc_http:
            self._health = ProviderHealth.UNCONFIGURED
            return self._health
        try:
            slot = await self._rpc("getSlot", [{"commitment": self.settings.solana_commitment}])
            if isinstance(slot, int) and slot > 0:
                self._health = ProviderHealth.HEALTHY
            else:
                self._health = ProviderHealth.DEGRADED
        except Exception as exc:
            logger.warning("Solana health_check failed: %s", exc)
            self._health = ProviderHealth.UNAVAILABLE
        return self._health

    async def get_latest_height(self) -> Optional[int]:
        try:
            slot = await self._rpc("getSlot", [{"commitment": self.settings.solana_commitment}])
            return int(slot) if slot is not None else None
        except Exception:
            return None

    async def fetch_transaction(self, signature: str) -> Optional[dict]:
        """Fetch jsonParsed transaction; returns None on failure / rate limit."""
        try:
            return await self._rpc(
                "getTransaction",
                [
                    signature,
                    {
                        "encoding": "jsonParsed",
                        "commitment": self.settings.solana_commitment,
                        "maxSupportedTransactionVersion": 1,
                    },
                ],
            )
        except Exception as exc:
            logger.warning("getTransaction %s: %s", signature[:16], exc)
            return None

    def supported_protocols(self) -> list[str]:
        return [
            "Jupiter V6 (balance-delta + program hint)",
            "Raydium / Orca (balance-delta + program hint)",
            "Structured swap payload (fixtures)",
            "Automatic pre/post token balance classification",
        ]

    def limitations(self) -> list[str]:
        return [
            "Full WebSocket subscription not enabled",
            "Transfer-only / airdrop without quote leg → UNCLASSIFIED",
            "Ambiguous multi non-quote token legs without quote → UNCLASSIFIED",
            "USD value requires external price enrichment",
            "Complex LP add/remove may be UNCLASSIFIED",
        ]

    def _trade_from_swap_dict(
        self,
        payload: dict,
        swap: dict,
        *,
        provider_source: str,
        extra_evidence: Optional[dict] = None,
    ) -> Optional[NormalizedTrade]:
        direction_raw = (swap.get("direction") or "").upper()
        if direction_raw not in ("BUY", "SELL"):
            self.last_decode_reason = "invalid_direction"
            return None
        wallet = swap.get("wallet") or swap.get("owner")
        token_mint = swap.get("token_mint") or swap.get("mint")
        if not wallet or not token_mint:
            self.last_decode_reason = "missing_wallet_or_mint"
            return None
        signature = payload.get("signature") or payload.get("tx_signature")
        if not signature:
            self.last_decode_reason = "missing_signature"
            return None
        event_index = int(swap.get("event_index") or 0)
        token_qty = swap.get("token_amount") or swap.get("token_quantity")
        quote_qty = swap.get("quote_amount") or swap.get("quote_quantity")
        symbol = swap.get("token_symbol")
        program = swap.get("program") or "unknown"
        block_time = payload.get("blockTime") or payload.get("block_time")
        ts = (
            datetime.fromtimestamp(int(block_time), tz=timezone.utc)
            if block_time
            else datetime.now(timezone.utc)
        )
        evidence = {
            "program": program,
            "raw_keys": list(swap.keys()),
        }
        if extra_evidence:
            evidence.update(extra_evidence)
        if swap.get("quote_mint"):
            evidence["quote_mint"] = swap["quote_mint"]
        self.last_decode_reason = "classified"
        return NormalizedTrade(
            chain=ChainId.SOLANA,
            tx_signature=signature,
            event_index=event_index,
            wallet_address=wallet,
            token_address=token_mint,
            token_symbol=symbol,
            direction=TradeDirection.BUY if direction_raw == "BUY" else TradeDirection.SELL,
            token_quantity=Decimal(str(token_qty)) if token_qty is not None else None,
            quote_quantity=Decimal(str(quote_qty)) if quote_qty is not None else None,
            quote_token_address=swap.get("quote_mint"),
            timestamp=ts,
            block_height=payload.get("slot"),
            confirmation=ConfirmationState.CONFIRMED,
            provider_source=provider_source,
            swap_evidence=evidence,
            enrichment_status=EnrichmentStatus.UNKNOWN,
        )

    async def parse_swap_from_payload(self, payload: dict) -> Optional[NormalizedTrade]:
        """
        Parse structured swap OR raw jsonParsed transaction into NormalizedTrade.

        For automatic decode, provide either:
          - payload['swap'] structured fields, or
          - payload with meta.preTokenBalances/postTokenBalances and
            payload['watch_wallet'] / payload['wallet'] identifying the trader.
        """
        self.last_decode_reason = None
        if not payload:
            self.last_decode_reason = "empty_payload"
            return None

        meta = payload.get("meta") or {}
        if meta.get("err") is not None:
            self.last_decode_reason = "failed_transaction"
            logger.debug("Rejecting failed Solana tx %s", payload.get("signature"))
            return None

        # 1) Explicit structured swap (fixtures / poller pre-classified)
        swap = payload.get("swap")
        if swap and isinstance(swap, dict) and swap.get("direction"):
            return self._trade_from_swap_dict(
                payload, swap, provider_source=payload.get("_source") or "solana_structured"
            )

        # 2) Automatic balance-delta decode from RPC-shaped payload
        wallet = (
            payload.get("watch_wallet")
            or payload.get("wallet")
            or (swap or {}).get("wallet")
        )
        if not wallet:
            self.last_decode_reason = "no_wallet_for_decode"
            return None

        # Payload may be the full getTransaction result or already flat
        tx_obj = payload
        if "meta" not in payload and payload.get("result"):
            tx_obj = payload["result"]

        result = decode_solana_swap(
            tx_obj,
            wallet=wallet,
            signature=payload.get("signature"),
        )
        self.last_decode_reason = result.reason
        if not result.ok:
            logger.debug(
                "Solana decode unclassified sig=%s reason=%s",
                (result.signature or "")[:12],
                result.reason,
            )
            return None

        built = decode_result_to_swap_payload(result)
        if not built:
            return None
        # Merge slot/blockTime onto outer payload for _trade_from_swap_dict
        merged = {
            **payload,
            "signature": built["signature"] or payload.get("signature"),
            "slot": built.get("slot") or payload.get("slot"),
            "blockTime": built.get("blockTime") or payload.get("blockTime"),
        }
        return self._trade_from_swap_dict(
            merged,
            built["swap"],
            provider_source="solana_balance_delta",
            extra_evidence=result.evidence,
        )
