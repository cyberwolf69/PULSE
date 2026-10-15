"""SQLAlchemy ORM models — persistent state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class IngestedEvent(Base):
    """Persistent event identity for deduplication."""

    __tablename__ = "ingested_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    tx_signature: Mapped[str] = mapped_column(String(128), nullable=False)
    event_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_ingested_chain_tx", "chain", "tx_signature"),
    )


class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    address: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    roles: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of roles
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    realized_pnl_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    win_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    smart_money_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("chain", "address", name="uq_wallet_chain_addr"),
        Index("ix_wallet_chain_addr", "chain", "address"),
    )


class TradeRecord(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    tx_signature: Mapped[str] = mapped_column(String(128), nullable=False)
    event_index: Mapped[int] = mapped_column(Integer, default=0)
    wallet_address: Mapped[str] = mapped_column(String(128), nullable=False)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    token_symbol: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    token_quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quote_quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    usd_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    block_height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confirmation: Mapped[str] = mapped_column(String(32), default="PENDING")
    provider_source: Mapped[str] = mapped_column(String(64), default="unknown")
    swap_evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_trade_wallet_token", "chain", "wallet_address", "token_address"),
        Index("ix_trade_token", "chain", "token_address"),
    )


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    wallet_address: Mapped[str] = mapped_column(String(128), nullable=False)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="NO_POSITION")
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    cost_basis_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_entry_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    realized_pnl_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_buy_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sell_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("chain", "wallet_address", "token_address", name="uq_position"),
        Index("ix_position_lookup", "chain", "wallet_address", "token_address"),
    )


class SignalRecord(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_uid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    token_symbol: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    score: Mapped[int] = mapped_column(Integer, default=0)
    lifecycle: Mapped[str] = mapped_column(String(32), default="DETECTED")
    entry_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry_market_cap: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    original_payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Delivery IDs (one original + one performance reply)
    telegram_message_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    telegram_perf_message_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    discord_message_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    discord_perf_message_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_signal_token", "chain", "token_address"),
        Index("ix_signal_lifecycle", "lifecycle"),
    )


class PerformanceSnapshot(Base):
    __tablename__ = "performance_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    milestone_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    milestone_label: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    roi_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    peak_roi_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    drawdown_from_peak_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    available: Mapped[bool] = mapped_column(Boolean, default=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("signal_uid", "milestone_seconds", name="uq_perf_milestone"),
        Index("ix_perf_signal", "signal_uid"),
    )


class EvmPollCheckpoint(Base):
    """Dedicated ingestion cursor; provider health updates cannot overwrite it."""

    __tablename__ = "evm_poll_checkpoints"
    chain: Mapped[str] = mapped_column(String(32), primary_key=True)
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False)
    block_number: Mapped[int] = mapped_column(Integer, nullable=False)
    block_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    blocked_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pending_block_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pending_block_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)


class ProviderCheckpoint(Base):
    __tablename__ = "provider_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    health: Mapped[str] = mapped_column(String(32), default="UNCONFIGURED")
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_block_or_slot: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TokenMetadata(Base):
    __tablename__ = "token_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    address: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    decimals: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    liquidity_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    market_cap_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    risk_flags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explorer_url: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    chart_url: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("chain", "address", name="uq_token"),
        Index("ix_token_lookup", "chain", "address"),
    )


# ── R4ISE performance / leaderboard tables ──────────────────────────────────


class PriceObservation(Base):
    """Persistent price tick for a chain-qualified token."""

    __tablename__ = "price_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), default="unknown")
    confidence: Mapped[str] = mapped_column(String(32), default="UNKNOWN")  # HIGH|MEDIUM|LOW|UNKNOWN
    liquidity_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume_24h_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pool_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_price_token_time", "chain", "token_address", "observed_at"),
        UniqueConstraint(
            "chain", "token_address", "observed_at",
            name="uq_price_obs_chain_token_ts",
        ),
    )


class SignalBaseline(Base):
    """Immutable baseline for a signal. Never reset by later buys."""

    __tablename__ = "signal_baselines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_uid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    token_symbol: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    signal_category: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_price: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price_source: Mapped[str] = mapped_column(String(64), default="event")
    methodology: Mapped[str] = mapped_column(String(64), default="event_time")
    confidence: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    wallet_attribution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    data_quality: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_baseline_token", "chain", "token_address"),
    )


class MilestoneHit(Base):
    """First time a signal reaches a price-multiple threshold. Deduplicated."""

    __tablename__ = "milestone_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    horizon: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # 1d|3d|7d|30d|None=live
    threshold_multiple: Mapped[float] = mapped_column(Float, nullable=False)  # 1.5, 2.0, 3.0 ...
    observed_price: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="unknown")
    data_quality: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("signal_uid", "threshold_multiple", name="uq_milestone_signal_threshold"),
        Index("ix_milestone_signal", "signal_uid"),
    )


class HorizonEvaluation(Base):
    """Final (or pending) evaluation of a signal for a specific horizon."""

    __tablename__ = "horizon_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    horizon: Mapped[str] = mapped_column(String(8), nullable=False)  # 1d|3d|7d|30d
    status: Mapped[str] = mapped_column(String(32), default="PENDING")  # PENDING|COMPLETE|INCOMPLETE|UNAVAILABLE
    # Max observed multiple within the horizon window
    max_multiple: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Endpoint (nearest observation at horizon end)
    endpoint_multiple: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    endpoint_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    endpoint_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    endpoint_delta_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Hit flags for configured targets
    hit_1_5x: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_2x: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_3x: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_5x: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_10x: Mapped[bool] = mapped_column(Boolean, default=False)
    baseline_price: Mapped[float] = mapped_column(Float, nullable=False)
    evaluated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("signal_uid", "horizon", name="uq_horizon_eval"),
        Index("ix_horizon_status", "horizon", "status"),
    )


class LeaderboardSnapshot(Base):
    """Persisted leaderboard report for deduplication and historical audit."""

    __tablename__ = "leaderboard_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    horizon: Mapped[str] = mapped_column(String(8), nullable=False)
    ranking_metric: Mapped[str] = mapped_column(String(32), default="max_multiple")
    hit_target: Mapped[float] = mapped_column(Float, default=2.0)
    top_n: Mapped[int] = mapped_column(Integer, default=10)
    report_uid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)  # full report
    calls: Mapped[int] = mapped_column(Integer, default=0)
    hit_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    median_multiple: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    top_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    delivered_telegram: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered_discord: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("ix_lb_horizon_time", "horizon", "generated_at"),
    )


class AlertDelivery(Base):
    """Transactional outbox for alert delivery with destination-specific state."""

    __tablename__ = "alert_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    destination: Mapped[str] = mapped_column(String(32), nullable=False)  # telegram|discord
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # initial|performance|leaderboard|milestone
    status: Mapped[str] = mapped_column(String(32), default="PENDING")  # PENDING|SENDING|SENT|RETRYABLE_FAILURE|PERMANENT_FAILURE
    remote_message_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("alert_uid", "destination", "kind", name="uq_delivery"),
        Index("ix_delivery_status", "status"),
    )


class WalletCandidate(Base):
    """Autonomous discovery candidates — never auto-labeled SMART_MONEY/KOL."""

    __tablename__ = "wallet_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    address: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="CANDIDATE")  # CANDIDATE|REJECTED|PROMOTED|INSUFFICIENT_DATA
    source: Mapped[str] = mapped_column(String(64), default="solana_swap_coactivity")
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_tx_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list of sigs
    last_evidence_tx: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    reject_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    eval_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    promoted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("chain", "address", name="uq_wallet_candidate"),
        Index("ix_candidate_status", "status"),
        Index("ix_candidate_chain", "chain"),
    )
