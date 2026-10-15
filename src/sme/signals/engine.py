"""Evidence-based signal scoring and confluence detection."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from sme.config import Settings
from sme.db import repository as repo
from sme.models.enums import (
    SignalLifecycle,
    SignalPriority,
    SignalType,
    TradeDirection,
    WalletRole,
)
from sme.models.trade import NormalizedTrade

logger = logging.getLogger(__name__)


def score_trade(
    trade: NormalizedTrade,
    *,
    wallet_roles: list[str],
    wallet_trade_count: int = 0,
    unique_wallets_in_window: int = 1,
    settings: Settings,
) -> tuple[int, SignalPriority, SignalType, dict[str, Any]]:
    """
    Pure scoring function. Missing data is never treated as positive evidence.
    Score range 0–100.
    """
    score = 0
    evidence: dict[str, Any] = {
        "direction": trade.direction.value,
        "wallet": trade.wallet_address,
        "token": trade.token_address,
        "roles": wallet_roles,
        "unique_wallets": unique_wallets_in_window,
    }

    if trade.direction != TradeDirection.BUY:
        # SELL / distribution path
        if WalletRole.SMART_MONEY.value in wallet_roles:
            score += 25
            sig_type = SignalType.SMART_MONEY_DISTRIBUTION
        else:
            score += 10
            sig_type = SignalType.PARTIAL_EXIT if trade.direction == TradeDirection.SELL else SignalType.FULL_EXIT
        priority = SignalPriority.MEDIUM if score >= 25 else SignalPriority.LOW
        evidence["reason"] = "exit_or_distribution"
        return min(score, 100), priority, sig_type, evidence

    # BUY path
    if WalletRole.SMART_MONEY.value in wallet_roles:
        score += 35
        evidence["smart_money"] = True
    if WalletRole.KOL.value in wallet_roles:
        score += 25
        evidence["kol"] = True
    if WalletRole.FOMO_TOP_TRADER.value in wallet_roles:
        score += 15
        evidence["fomo"] = True
    if WalletRole.WHALE.value in wallet_roles:
        score += 15
        evidence["whale"] = True

    if trade.usd_value is not None:
        usd = float(trade.usd_value)
        if usd >= settings.whale_usd_threshold:
            score += 10
            evidence["large_usd"] = usd
        elif usd >= 5_000:
            score += 5

    if wallet_trade_count >= 10:
        score += 5
        evidence["experienced_wallet"] = wallet_trade_count

    # Confluence
    if unique_wallets_in_window >= settings.confluence_min_wallets:
        score += 20
        evidence["confluence"] = unique_wallets_in_window
        sig_type = SignalType.MULTIWALLET_CONFLUENCE
    elif WalletRole.SMART_MONEY.value in wallet_roles:
        sig_type = SignalType.SMART_MONEY_BUY
    elif WalletRole.KOL.value in wallet_roles:
        sig_type = SignalType.KOL_ACTIVITY
    elif WalletRole.FOMO_TOP_TRADER.value in wallet_roles:
        sig_type = SignalType.FOMO_TRADER_ACTIVITY
    elif WalletRole.WHALE.value in wallet_roles:
        sig_type = SignalType.WHALE_ACTIVITY
    else:
        # Unknown wallet — do not label as Smart Money
        sig_type = SignalType.WHALE_ACTIVITY if WalletRole.WHALE.value in wallet_roles else SignalType.FOMO_TRADER_ACTIVITY
        if not wallet_roles:
            sig_type = SignalType.FOMO_TRADER_ACTIVITY  # generic unattributable activity

    if score >= 80:
        priority = SignalPriority.CRITICAL
    elif score >= 60:
        priority = SignalPriority.HIGH
    elif score >= 40:
        priority = SignalPriority.MEDIUM
    else:
        priority = SignalPriority.LOW

    return min(score, 100), priority, sig_type, evidence


async def process_trade_for_signals(
    session: AsyncSession,
    trade: NormalizedTrade,
    settings: Settings,
    *,
    wallet_roles: Optional[list[str]] = None,
) -> Optional[dict[str, Any]]:
    """
    Full path: dedup → persist trade → update position → score → maybe emit signal.
    Returns signal dict if a new signal should be delivered, else None.
    """
    if not trade.is_actionable():
        return None

    # Persistent deduplication
    inserted = await repo.record_event(session, trade)
    if not inserted:
        logger.debug("Duplicate event suppressed: %s", trade.event_id)
        return None

    await repo.persist_trade(session, trade)
    await repo.upsert_wallet(
        session,
        trade.chain.value,
        trade.wallet_address,
        roles=wallet_roles,
    )
    await repo.increment_wallet_trade_count(session, trade.chain.value, trade.wallet_address)
    await repo.apply_trade_to_position(session, trade)

    roles = wallet_roles or []
    since = trade.timestamp - timedelta(seconds=settings.confluence_window_seconds)
    recent = await repo.recent_buys_for_token(
        session, trade.chain.value, trade.token_address, since
    )
    unique_wallets = len({r.wallet_address for r in recent} | {trade.wallet_address})

    # Wallet trade count for scoring
    from sqlalchemy import select
    from sme.db.models import Wallet

    result = await session.execute(
        select(Wallet).where(
            Wallet.chain == trade.chain.value,
            Wallet.address == trade.wallet_address,
        )
    )
    w = result.scalar_one_or_none()
    trade_count = w.trade_count if w else 0

    score, priority, sig_type, evidence = score_trade(
        trade,
        wallet_roles=roles,
        wallet_trade_count=trade_count,
        unique_wallets_in_window=unique_wallets,
        settings=settings,
    )

    if score < settings.min_signal_score:
        logger.debug("Score %s below threshold for %s", score, trade.event_id)
        return None

    signal_uid = str(uuid.uuid4())
    payload = {
        "signal_uid": signal_uid,
        "chain": trade.chain.value,
        "token_address": trade.token_address,
        "token_symbol": trade.token_symbol,
        "signal_type": sig_type.value,
        "priority": priority.value,
        "score": score,
        "lifecycle": SignalLifecycle.DETECTED.value,
        "entry_price": float(trade.price) if trade.price is not None else None,
        "entry_market_cap": float(trade.market_cap_usd) if trade.market_cap_usd is not None else None,
        "evidence": evidence,
        "original_payload": {
            "event_id": trade.event_id,
            "tx": trade.tx_signature,
            "wallet": trade.wallet_address,
            "direction": trade.direction.value,
            "usd_value": float(trade.usd_value) if trade.usd_value is not None else None,
            "timestamp": trade.timestamp.isoformat(),
        },
    }
    await repo.save_signal(session, payload)
    return payload
