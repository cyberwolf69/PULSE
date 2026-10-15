"""Normalized trade contract — chain-agnostic boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from sme.models.enums import (
    ChainId,
    ConfirmationState,
    EnrichmentStatus,
    TradeDirection,
)


class NormalizedTrade(BaseModel):
    """
    Canonical trade representation emitted by every chain adapter.

    Unavailable fields remain None / UNKNOWN. Never fabricate values.
    """

    # Identity
    id: str = Field(default_factory=lambda: str(uuid4()))
    chain: ChainId
    tx_signature: str
    event_index: int = 0
    # Unique event key: chain + tx_signature + event_index
    event_id: str = ""

    # Parties
    wallet_address: str
    token_address: str
    token_symbol: Optional[str] = None
    token_name: Optional[str] = None

    # Trade
    direction: TradeDirection = TradeDirection.UNKNOWN
    token_quantity: Optional[Decimal] = None
    quote_quantity: Optional[Decimal] = None
    quote_token_address: Optional[str] = None
    usd_value: Optional[Decimal] = None
    price: Optional[Decimal] = None

    # Context
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    block_height: Optional[int] = None
    confirmation: ConfirmationState = ConfirmationState.PENDING
    provider_source: str = "unknown"
    swap_evidence: dict[str, Any] = Field(default_factory=dict)
    enrichment_status: EnrichmentStatus = EnrichmentStatus.UNKNOWN

    # Optional enrichment snapshots (filled later, never required)
    liquidity_usd: Optional[Decimal] = None
    market_cap_usd: Optional[Decimal] = None
    risk_flags: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    def model_post_init(self, __context: Any) -> None:
        if not self.event_id:
            self.event_id = f"{self.chain.value}:{self.tx_signature}:{self.event_index}"

    @field_validator("token_quantity", "quote_quantity", "usd_value", "price", "liquidity_usd", "market_cap_usd", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        if isinstance(v, Decimal):
            return v
        try:
            return Decimal(str(v))
        except Exception:
            return None

    def is_actionable(self) -> bool:
        """Trade has enough evidence to enter the signal pipeline."""
        return (
            self.direction in (TradeDirection.BUY, TradeDirection.SELL)
            and self.wallet_address
            and self.token_address
            and self.confirmation != ConfirmationState.REORGED
        )
