"""Persistence helpers — deduplication, positions, signals, performance."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sme.db.models import (
    IngestedEvent,
    PerformanceSnapshot,
    Position,
    ProviderCheckpoint,
    SignalRecord,
    TokenMetadata,
    TradeRecord,
    Wallet,
)
from sme.models.enums import PositionState, TradeDirection
from sme.models.trade import NormalizedTrade


def _f(v: Optional[Decimal | float]) -> Optional[float]:
    if v is None:
        return None
    return float(v)


async def is_event_seen(session: AsyncSession, event_id: str) -> bool:
    result = await session.execute(
        select(IngestedEvent.id).where(IngestedEvent.event_id == event_id)
    )
    return result.scalar_one_or_none() is not None


async def record_event(session: AsyncSession, trade: NormalizedTrade) -> bool:
    """
    Insert event identity. Returns True if newly inserted, False if duplicate.
    Uses DB-level uniqueness so concurrent workers are safe.
    """
    stmt = sqlite_insert(IngestedEvent).values(
        event_id=trade.event_id,
        chain=trade.chain.value,
        tx_signature=trade.tx_signature,
        event_index=trade.event_index,
    ).on_conflict_do_nothing(index_elements=["event_id"])
    result = await session.execute(stmt)
    return result.rowcount > 0


async def persist_trade(session: AsyncSession, trade: NormalizedTrade) -> None:
    rec = TradeRecord(
        event_id=trade.event_id,
        chain=trade.chain.value,
        tx_signature=trade.tx_signature,
        event_index=trade.event_index,
        wallet_address=trade.wallet_address,
        token_address=trade.token_address,
        token_symbol=trade.token_symbol,
        direction=trade.direction.value,
        token_quantity=_f(trade.token_quantity),
        quote_quantity=_f(trade.quote_quantity),
        usd_value=_f(trade.usd_value),
        price=_f(trade.price),
        block_height=trade.block_height,
        confirmation=trade.confirmation.value,
        provider_source=trade.provider_source,
        swap_evidence=json.dumps(trade.swap_evidence) if trade.swap_evidence else None,
        timestamp=trade.timestamp,
    )
    session.add(rec)


async def upsert_wallet(
    session: AsyncSession,
    chain: str,
    address: str,
    *,
    label: Optional[str] = None,
    roles: Optional[list[str]] = None,
) -> Wallet:
    result = await session.execute(
        select(Wallet).where(Wallet.chain == chain, Wallet.address == address)
    )
    wallet = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if wallet is None:
        wallet = Wallet(
            chain=chain,
            address=address,
            label=label,
            roles=json.dumps(roles or []),
            trade_count=0,
            first_seen=now,
            last_seen=now,
        )
        session.add(wallet)
    else:
        wallet.last_seen = now
        if label:
            wallet.label = label
        if roles is not None:
            existing = set(json.loads(wallet.roles or "[]"))
            existing.update(roles)
            wallet.roles = json.dumps(sorted(existing))
    return wallet


async def get_or_create_position(
    session: AsyncSession,
    chain: str,
    wallet: str,
    token: str,
) -> Position:
    result = await session.execute(
        select(Position).where(
            Position.chain == chain,
            Position.wallet_address == wallet,
            Position.token_address == token,
        )
    )
    pos = result.scalar_one_or_none()
    if pos is None:
        pos = Position(
            chain=chain,
            wallet_address=wallet,
            token_address=token,
            state=PositionState.NO_POSITION.value,
            quantity=0.0,
        )
        session.add(pos)
        await session.flush()
    return pos


async def apply_trade_to_position(
    session: AsyncSession,
    trade: NormalizedTrade,
) -> Position:
    """
    Update position state machine from a confirmed trade.
    SELL after restart finds existing position via unique key.
    """
    pos = await get_or_create_position(
        session,
        trade.chain.value,
        trade.wallet_address,
        trade.token_address,
    )
    # Refuse to treat missing/unknown-normalized quantity as zero human units.
    amount_status = (trade.swap_evidence or {}).get("amount_status")
    if trade.token_quantity is None or amount_status in (
        "decimals_unknown",
        "raw_pending_decimals",
        "invalid",
        "missing_raw",
    ):
        # Still return the position row; do not mutate quantities with fabricated units.
        return pos

    qty = float(trade.token_quantity)
    usd = float(trade.usd_value) if trade.usd_value is not None else None
    price = float(trade.price) if trade.price is not None else None
    now = trade.timestamp

    if trade.direction == TradeDirection.BUY:
        if pos.state in (PositionState.NO_POSITION.value, PositionState.FULL_EXIT.value):
            pos.state = PositionState.INITIAL_ENTRY.value
            pos.entry_time = now
            pos.quantity = qty
            pos.cost_basis_usd = usd
            pos.avg_entry_price = price
        else:
            # Accumulate
            old_qty = pos.quantity or 0.0
            new_qty = old_qty + qty
            if usd is not None and pos.cost_basis_usd is not None:
                pos.cost_basis_usd = (pos.cost_basis_usd or 0) + usd
            elif usd is not None:
                pos.cost_basis_usd = usd
            if new_qty > 0 and pos.cost_basis_usd is not None:
                pos.avg_entry_price = pos.cost_basis_usd / new_qty
            pos.quantity = new_qty
            pos.state = PositionState.ACCUMULATING.value
        pos.last_buy_time = now

    elif trade.direction == TradeDirection.SELL:
        old_qty = pos.quantity or 0.0
        sell_qty = min(qty, old_qty) if old_qty > 0 else qty
        remaining = max(0.0, old_qty - sell_qty)
        # Simple realized PnL when we have prices
        if price is not None and pos.avg_entry_price is not None and sell_qty > 0:
            realized = (price - pos.avg_entry_price) * sell_qty
            pos.realized_pnl_usd = (pos.realized_pnl_usd or 0.0) + realized
        pos.quantity = remaining
        pos.last_sell_time = now
        if remaining <= 1e-12:
            pos.state = PositionState.FULL_EXIT.value
            pos.quantity = 0.0
        elif pos.state in (
            PositionState.INITIAL_ENTRY.value,
            PositionState.ACCUMULATING.value,
            PositionState.HOLDING.value,
        ):
            pos.state = PositionState.PARTIAL_EXIT.value
        else:
            pos.state = PositionState.REDUCING.value

    pos.updated_at = datetime.now(timezone.utc)
    return pos


async def increment_wallet_trade_count(session: AsyncSession, chain: str, address: str) -> None:
    result = await session.execute(
        select(Wallet).where(Wallet.chain == chain, Wallet.address == address)
    )
    wallet = result.scalar_one_or_none()
    if wallet:
        wallet.trade_count = (wallet.trade_count or 0) + 1
        wallet.last_seen = datetime.now(timezone.utc)


async def save_signal(session: AsyncSession, data: dict[str, Any]) -> SignalRecord:
    rec = SignalRecord(
        signal_uid=data["signal_uid"],
        chain=data["chain"],
        token_address=data["token_address"],
        token_symbol=data.get("token_symbol"),
        signal_type=data["signal_type"],
        priority=data.get("priority", "MEDIUM"),
        score=data.get("score", 0),
        lifecycle=data.get("lifecycle", "DETECTED"),
        entry_price=data.get("entry_price"),
        entry_market_cap=data.get("entry_market_cap"),
        evidence=json.dumps(data.get("evidence")) if data.get("evidence") else None,
        original_payload=json.dumps(data.get("original_payload")) if data.get("original_payload") else None,
    )
    session.add(rec)
    await session.flush()
    return rec


async def get_signal_by_uid(session: AsyncSession, uid: str) -> Optional[SignalRecord]:
    result = await session.execute(
        select(SignalRecord).where(SignalRecord.signal_uid == uid)
    )
    return result.scalar_one_or_none()


async def update_signal_delivery_ids(
    session: AsyncSession,
    signal_uid: str,
    *,
    telegram_message_id: Optional[str] = None,
    telegram_perf_message_id: Optional[str] = None,
    discord_message_id: Optional[str] = None,
    discord_perf_message_id: Optional[str] = None,
) -> None:
    values: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
    if telegram_message_id is not None:
        values["telegram_message_id"] = telegram_message_id
    if telegram_perf_message_id is not None:
        values["telegram_perf_message_id"] = telegram_perf_message_id
    if discord_message_id is not None:
        values["discord_message_id"] = discord_message_id
    if discord_perf_message_id is not None:
        values["discord_perf_message_id"] = discord_perf_message_id
    await session.execute(
        update(SignalRecord).where(SignalRecord.signal_uid == signal_uid).values(**values)
    )


async def upsert_performance(
    session: AsyncSession,
    signal_uid: str,
    milestone_seconds: int,
    milestone_label: str,
    *,
    entry_price: Optional[float],
    current_price: Optional[float],
    roi_pct: Optional[float],
    peak_roi_pct: Optional[float],
    drawdown_from_peak_pct: Optional[float],
    available: bool,
) -> None:
    stmt = sqlite_insert(PerformanceSnapshot).values(
        signal_uid=signal_uid,
        milestone_seconds=milestone_seconds,
        milestone_label=milestone_label,
        entry_price=entry_price,
        current_price=current_price,
        roi_pct=roi_pct,
        peak_roi_pct=peak_roi_pct,
        drawdown_from_peak_pct=drawdown_from_peak_pct,
        available=available,
    ).on_conflict_do_update(
        index_elements=["signal_uid", "milestone_seconds"],
        set_={
            "current_price": current_price,
            "roi_pct": roi_pct,
            "peak_roi_pct": peak_roi_pct,
            "drawdown_from_peak_pct": drawdown_from_peak_pct,
            "available": available,
            "recorded_at": datetime.now(timezone.utc),
        },
    )
    await session.execute(stmt)


async def update_provider_health(
    session: AsyncSession,
    chain: str,
    health: str,
    *,
    last_error: Optional[str] = None,
    last_block: Optional[int] = None,
) -> None:
    result = await session.execute(
        select(ProviderCheckpoint).where(ProviderCheckpoint.chain == chain)
    )
    cp = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if cp is None:
        cp = ProviderCheckpoint(chain=chain, health=health)
        session.add(cp)
    cp.health = health
    cp.updated_at = now
    if health == "HEALTHY":
        cp.last_success_at = now
        cp.last_error = None
    if last_error is not None:
        cp.last_error = last_error
    if last_block is not None:
        cp.last_block_or_slot = last_block


async def recent_buys_for_token(
    session: AsyncSession,
    chain: str,
    token: str,
    since: datetime,
) -> Sequence[TradeRecord]:
    result = await session.execute(
        select(TradeRecord).where(
            TradeRecord.chain == chain,
            TradeRecord.token_address == token,
            TradeRecord.direction == "BUY",
            TradeRecord.timestamp >= since,
        )
    )
    return result.scalars().all()


# ── R4ISE performance / outbox helpers ──────────────────────────────────────


async def save_baseline(session: AsyncSession, data: dict[str, Any]) -> "SignalBaseline":
    from sme.db.models import SignalBaseline
    from sme.performance.math import is_valid_baseline

    result = await session.execute(
        select(SignalBaseline).where(SignalBaseline.signal_uid == data["signal_uid"])
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing  # immutable — never reset
    if not is_valid_baseline(data.get("baseline_price")):
        raise ValueError("refusing to persist baseline with invalid price")
    rec = SignalBaseline(
        signal_uid=data["signal_uid"],
        chain=data["chain"],
        token_address=data["token_address"],
        token_symbol=data.get("token_symbol"),
        signal_category=data["signal_category"],
        baseline_price=float(data["baseline_price"]),
        baseline_ts=data["baseline_ts"],
        price_source=data.get("price_source", "event"),
        methodology=data.get("methodology", "event_time"),
        confidence=data.get("confidence", "UNKNOWN"),
        wallet_attribution=data.get("wallet_attribution"),
        data_quality=data.get("data_quality", "UNKNOWN"),
    )
    session.add(rec)
    await session.flush()
    return rec


async def add_price_observation(
    session: AsyncSession,
    *,
    chain: str,
    token_address: str,
    price_usd: float,
    observed_at: datetime,
    provider: str = "unknown",
    confidence: str = "UNKNOWN",
    liquidity_usd: Optional[float] = None,
) -> "PriceObservation":
    """Persist a price tick. Upserts on (chain, token_address, observed_at).

    Last valid write wins for the same timestamp so evaluation stays deterministic
    and duplicate rows cannot inflate peak multiples.
    """
    from sme.performance.math import is_valid_price
    from sqlalchemy.exc import IntegrityError
    if not is_valid_price(price_usd):
        raise ValueError("refusing to persist price observation with invalid price")
    from sme.db.models import PriceObservation

    result = await session.execute(
        select(PriceObservation).where(
            PriceObservation.chain == chain,
            PriceObservation.token_address == token_address,
            PriceObservation.observed_at == observed_at,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.price_usd = float(price_usd)
        existing.provider = provider or existing.provider
        existing.confidence = confidence or existing.confidence
        if liquidity_usd is not None:
            existing.liquidity_usd = liquidity_usd
        await session.flush()
        return existing

    rec = PriceObservation(
        chain=chain,
        token_address=token_address,
        price_usd=float(price_usd),
        observed_at=observed_at,
        provider=provider,
        confidence=confidence,
        liquidity_usd=liquidity_usd,
    )
    try:
        async with session.begin_nested():
            session.add(rec)
            await session.flush()
        return rec
    except IntegrityError:
        result = await session.execute(
            select(PriceObservation).where(
                PriceObservation.chain == chain,
                PriceObservation.token_address == token_address,
                PriceObservation.observed_at == observed_at,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise
        row.price_usd = float(price_usd)
        await session.flush()
        return row


async def list_baselines(session: AsyncSession) -> Sequence:
    from sme.db.models import SignalBaseline

    result = await session.execute(select(SignalBaseline))
    return result.scalars().all()


async def get_horizon_evaluations(
    session: AsyncSession, horizon: str, status: Optional[str] = None
) -> Sequence:
    from sme.db.models import HorizonEvaluation

    q = select(HorizonEvaluation).where(HorizonEvaluation.horizon == horizon)
    if status:
        q = q.where(HorizonEvaluation.status == status)
    result = await session.execute(q)
    return result.scalars().all()


async def get_token_decimals(session: AsyncSession, chain: str, address: str) -> Optional[int]:
    """Return cached decimals from token_metadata or None."""
    result = await session.execute(
        select(TokenMetadata).where(
            TokenMetadata.chain == chain.lower(),
            TokenMetadata.address == address.lower(),
        )
    )
    row = result.scalar_one_or_none()
    if row is None or row.decimals is None:
        return None
    return int(row.decimals)


async def upsert_token_decimals(
    session: AsyncSession,
    chain: str,
    address: str,
    decimals: int,
    *,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
) -> TokenMetadata:
    """Persist decimals for reuse. Does not invent values."""
    result = await session.execute(
        select(TokenMetadata).where(
            TokenMetadata.chain == chain.lower(),
            TokenMetadata.address == address.lower(),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = TokenMetadata(
            chain=chain.lower(),
            address=address.lower(),
            decimals=decimals,
            symbol=symbol,
            name=name,
        )
        session.add(row)
    else:
        row.decimals = decimals
        if symbol and not row.symbol:
            row.symbol = symbol
        if name and not row.name:
            row.name = name
    await session.flush()
    return row


# ── Wallet discovery candidates ─────────────────────────────────────────────


async def get_candidate(session: AsyncSession, chain: str, address: str):
    from sme.db.models import WalletCandidate

    result = await session.execute(
        select(WalletCandidate).where(
            WalletCandidate.chain == chain.lower(),
            WalletCandidate.address == address,
        )
    )
    return result.scalar_one_or_none()


async def create_candidate(
    session: AsyncSession,
    *,
    chain: str,
    address: str,
    source: str = "solana_swap_coactivity",
    evidence_tx: str = "",
    expires_at=None,
):
    """Insert candidate. Returns existing row if unique conflict (concurrent worker)."""
    import json
    from datetime import datetime, timezone
    from sqlalchemy.exc import IntegrityError
    from sme.db.models import WalletCandidate

    existing = await get_candidate(session, chain, address)
    if existing is not None:
        return existing

    now = datetime.now(timezone.utc)
    txs = [evidence_tx] if evidence_tx else []
    row = WalletCandidate(
        chain=chain.lower(),
        address=address,
        status="CANDIDATE",
        source=source,
        evidence_count=len(txs),
        evidence_tx_json=json.dumps(txs),
        last_evidence_tx=evidence_tx or None,
        first_seen=now,
        last_seen=now,
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
        return row
    except IntegrityError:
        existing = await get_candidate(session, chain, address)
        return existing


async def count_candidates(session: AsyncSession, status: Optional[str] = None) -> int:
    from sme.db.models import WalletCandidate
    from sqlalchemy import func

    q = select(func.count()).select_from(WalletCandidate)
    if status:
        q = q.where(WalletCandidate.status == status)
    result = await session.execute(q)
    return int(result.scalar_one() or 0)


async def count_candidates_created_since(session: AsyncSession, since) -> int:
    from sme.db.models import WalletCandidate
    from sqlalchemy import func

    result = await session.execute(
        select(func.count())
        .select_from(WalletCandidate)
        .where(WalletCandidate.created_at >= since)
    )
    return int(result.scalar_one() or 0)


async def list_candidates(
    session: AsyncSession,
    *,
    status: Optional[str] = None,
    limit: int = 100,
):
    from sme.db.models import WalletCandidate

    q = select(WalletCandidate).order_by(WalletCandidate.id)
    if status:
        q = q.where(WalletCandidate.status == status)
    q = q.limit(limit)
    result = await session.execute(q)
    return list(result.scalars().all())


async def list_promoted_wallets(session: AsyncSession, chain: Optional[str] = None) -> list[tuple[str, str]]:
    """Return [(chain, address), ...] for non-expired PROMOTED candidates."""
    from datetime import datetime, timezone
    from sme.db.models import WalletCandidate

    q = select(WalletCandidate).where(WalletCandidate.status == "PROMOTED")
    if chain:
        q = q.where(WalletCandidate.chain == chain.lower())
    result = await session.execute(q)
    now = datetime.now(timezone.utc)
    out: list[tuple[str, str]] = []
    for r in result.scalars().all():
        if r.expires_at is not None:
            exp = r.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < now:
                continue
        out.append((r.chain, r.address))
    return out


async def list_trades_for_wallet(
    session: AsyncSession,
    chain: str,
    wallet_address: str,
    *,
    limit: int = 100,
) -> Sequence:
    from sme.db.models import TradeRecord

    result = await session.execute(
        select(TradeRecord)
        .where(
            TradeRecord.chain == chain,
            TradeRecord.wallet_address == wallet_address,
        )
        .order_by(TradeRecord.timestamp.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_price_observations_for_token(
    session: AsyncSession,
    chain: str,
    token_address: str,
    *,
    since=None,
    until=None,
    limit: int = 500,
) -> list[tuple[float, object]]:
    """Return [(price_usd, observed_at), ...] with valid prices only."""
    from sme.db.models import PriceObservation
    from sme.performance.math import is_valid_price

    q = select(PriceObservation).where(
        PriceObservation.chain == chain,
        PriceObservation.token_address == token_address,
    )
    if since is not None:
        q = q.where(PriceObservation.observed_at >= since)
    if until is not None:
        q = q.where(PriceObservation.observed_at <= until)
    q = q.order_by(PriceObservation.observed_at.asc()).limit(limit)
    result = await session.execute(q)
    out: list[tuple[float, object]] = []
    for row in result.scalars().all():
        if is_valid_price(row.price_usd):
            out.append((float(row.price_usd), row.observed_at))
    return out



async def list_candidate_trade_tokens(
    session: AsyncSession,
    *,
    limit: int = 50,
) -> list[tuple[str, str]]:
    """Distinct (chain, token_address) from trades of non-rejected candidates."""
    from sme.db.models import WalletCandidate, TradeRecord

    cand = await session.execute(
        select(WalletCandidate.chain, WalletCandidate.address).where(
            WalletCandidate.status.in_(("CANDIDATE", "INSUFFICIENT_DATA", "PROMOTED"))
        )
    )
    pairs = list(cand.all())
    if not pairs:
        return []
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for chain, addr in pairs:
        trades = await session.execute(
            select(TradeRecord.chain, TradeRecord.token_address)
            .where(
                TradeRecord.chain == chain,
                TradeRecord.wallet_address == addr,
                TradeRecord.direction == "BUY",
            )
            .limit(20)
        )
        for c, tok in trades.all():
            key = (c, tok)
            if key not in seen:
                seen.add(key)
                out.append(key)
            if len(out) >= limit:
                return out
    return out


async def is_legacy_evm_event_seen(session: AsyncSession, trade: NormalizedTrade) -> bool:
    """Retain pre-Phase-1 dedup for the wallet stored under the old event ID."""
    legacy = f"{trade.chain.value}:{trade.tx_signature}:{trade.event_index}"
    result = await session.execute(select(TradeRecord.wallet_address).where(TradeRecord.event_id == legacy))
    wallet = result.scalar_one_or_none()
    return wallet is not None and wallet.lower() == trade.wallet_address.lower()
