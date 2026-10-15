"""Durable delivery outbox — retry, message-ID persistence, restart recovery.

States: PENDING → SENDING → SENT | RETRYABLE_FAILURE | PERMANENT_FAILURE | UNCERTAIN_OUTCOME

Telegram and Discord are independent destinations. Message IDs are stored so
performance edits and leaderboard dedup survive process restarts.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sme.db.models import AlertDelivery, SignalRecord
from sme.delivery.discord import DiscordDelivery
from sme.delivery.telegram import TelegramDelivery

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
RETRYABLE_STATUSES = frozenset({"PENDING", "RETRYABLE_FAILURE"})
# SENDING rows older than this are treated as abandoned after a crash/hang.
DEFAULT_SENDING_LEASE_SECONDS = 120


class DeliveryOutbox:
    """Process pending delivery rows with bounded retries."""

    def __init__(
        self,
        telegram: TelegramDelivery,
        discord: DiscordDelivery,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        sending_lease_seconds: int = DEFAULT_SENDING_LEASE_SECONDS,
        commit_each_success: bool = True,
    ):
        self.telegram = telegram
        self.discord = discord
        self.max_attempts = max_attempts
        self.sending_lease_seconds = max(5, int(sending_lease_seconds))
        # Commit after each successful send to shrink the send-then-crash window.
        # Does not provide exactly-once delivery; see process_pending docstring.
        self.commit_each_success = commit_each_success

    async def enqueue(
        self,
        session: AsyncSession,
        *,
        alert_uid: str,
        destination: str,
        kind: str,
        payload: dict[str, Any],
    ) -> AlertDelivery:
        """Insert or return existing outbox row (dedup by unique constraint).

        For kind=performance, refresh payload and re-queue SENT rows so the
        existing performance message can be edited (one-card invariant).
        """
        result = await session.execute(
            select(AlertDelivery).where(
                AlertDelivery.alert_uid == alert_uid,
                AlertDelivery.destination == destination,
                AlertDelivery.kind == kind,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            if kind == "performance":
                existing.payload_json = json.dumps(payload)
                if existing.status in ("SENT", "RETRYABLE_FAILURE"):
                    existing.status = "PENDING"
                    # Do not reset attempts fully — allow bounded re-edits
                    if (existing.attempts or 0) >= self.max_attempts:
                        existing.attempts = max(0, self.max_attempts - 2)
                existing.updated_at = datetime.now(timezone.utc)
                await session.flush()
            return existing

        row = AlertDelivery(
            alert_uid=alert_uid,
            destination=destination,
            kind=kind,
            status="PENDING",
            payload_json=json.dumps(payload),
            attempts=0,
        )
        session.add(row)
        await session.flush()
        return row

    async def process_pending(self, session: AsyncSession, limit: int = 50) -> dict[str, int]:
        """Drain pending/retryable rows with atomic claim and lease-based recovery.

        Guarantees and limits
        ---------------------
        * One outbox row per (alert_uid, destination, kind) — DB unique constraint.
        * Concurrent workers: claim uses UPDATE ... WHERE status IN retryable so only
          one worker transitions a given row to SENDING (works on SQLite and Postgres;
          multi-process still depends on DB isolation).
        * Stuck SENDING: only reclaimed if updated_at is older than sending_lease_seconds
          (avoids stealing an in-flight send from another healthy worker).
        * If remote_message_id is already stored, external send is skipped (mitigation
          when the id was committed but status was not).
        * commit_each_success=True commits after each successful send so a later crash
          does not roll back an already-delivered remote_message_id.

        Still unresolved / intentional limits
        -------------------------------------
        Exactly-once initial delivery is not possible without provider idempotency.
        Stale SENDING without remote_message_id for kind=initial becomes
        UNCERTAIN_OUTCOME (not auto-retried) to avoid duplicate initial cards.
        That can leave a gap if the crash was *before* the HTTP send — operator
        can re-queue UNCERTAIN rows deliberately. Performance rows prefer
        edit-in-place when existing_perf_message_id is present in the payload.
        """
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        counts = {
            "sent": 0,
            "retryable": 0,
            "permanent": 0,
            "skipped": 0,
            "recovered_id": 0,
            "claim_lost": 0,
            "reclaimed_stale": 0,
            "uncertain": 0,
            "ownership_lost": 0,
        }

        # Reclaim only stale SENDING rows (lease expired).
        # Without remote_message_id, initial delivery is AMBIGUOUS: the provider may
        # already have created the message. Do not auto-resend initial cards.
        lease_cutoff = now - timedelta(seconds=self.sending_lease_seconds)
        stuck = await session.execute(
            select(AlertDelivery).where(AlertDelivery.status == "SENDING")
        )
        for srow in stuck.scalars().all():
            updated = srow.updated_at
            if updated is not None and updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if updated is None or updated <= lease_cutoff:
                prev = (srow.last_error or "")[:350]
                if srow.remote_message_id:
                    # ID was written but status stuck — safe to finalize via recovered_id path
                    srow.status = "RETRYABLE_FAILURE"
                    srow.last_error = (prev + "|reclaimed_stale_sending").strip("|")
                    counts["reclaimed_stale"] += 1
                elif srow.kind == "initial":
                    # Ambiguous: may already exist remotely — do not blind-retry
                    srow.status = "UNCERTAIN_OUTCOME"
                    srow.last_error = (
                        prev + "|uncertain_after_stale_sending_no_remote_id"
                    ).strip("|")
                    counts["uncertain"] = counts.get("uncertain", 0) + 1
                    counts["reclaimed_stale"] += 1
                elif srow.kind == "performance":
                    # Prefer retry when payload carries existing_perf_message_id (edit path)
                    try:
                        pl = json.loads(srow.payload_json or "{}")
                    except Exception:
                        pl = {}
                    if pl.get("existing_perf_message_id"):
                        srow.status = "RETRYABLE_FAILURE"
                        srow.last_error = (prev + "|reclaimed_stale_perf_edit").strip("|")
                        counts["reclaimed_stale"] += 1
                    else:
                        srow.status = "UNCERTAIN_OUTCOME"
                        srow.last_error = (
                            prev + "|uncertain_perf_no_existing_id"
                        ).strip("|")
                        counts["uncertain"] = counts.get("uncertain", 0) + 1
                        counts["reclaimed_stale"] += 1
                else:
                    # leaderboard / other — retry is lower risk
                    srow.status = "RETRYABLE_FAILURE"
                    srow.last_error = (prev + "|reclaimed_stale_sending").strip("|")
                    counts["reclaimed_stale"] += 1
                srow.updated_at = now
        await session.flush()

        result = await session.execute(
            select(AlertDelivery)
            .where(AlertDelivery.status.in_(list(RETRYABLE_STATUSES)))
            .order_by(AlertDelivery.id)
            .limit(limit)
        )
        rows = list(result.scalars().all())

        for row in rows:
            if (row.attempts or 0) >= self.max_attempts:
                row.status = "PERMANENT_FAILURE"
                row.last_error = f"exceeded max_attempts={self.max_attempts}"
                row.updated_at = datetime.now(timezone.utc)
                counts["permanent"] += 1
                continue

            # Partial success already recorded — finalize without re-send
            if row.remote_message_id and row.kind in ("initial", "performance", "leaderboard"):
                claimed = await self._claim_row(session, row.id, row.attempts or 0)
                if not claimed:
                    counts["claim_lost"] += 1
                    continue
                # Re-load after claim
                row = await session.get(AlertDelivery, row.id)
                if row is None:
                    continue
                if row.remote_message_id:
                    row.status = "SENT"
                    row.last_error = None
                    row.updated_at = datetime.now(timezone.utc)
                    await self._persist_signal_message_id(session, row, row.remote_message_id)
                    counts["recovered_id"] += 1
                    counts["sent"] += 1
                    if self.commit_each_success:
                        await session.commit()
                    continue

            # Atomic claim PENDING/RETRYABLE -> SENDING
            claimed = await self._claim_row(session, row.id, row.attempts or 0)
            if not claimed:
                counts["claim_lost"] += 1
                continue
            row = await session.get(AlertDelivery, row.id)
            if row is None:
                continue

            try:
                mid = await self._dispatch(row)
                if mid is not None:
                    # Re-check ownership: another worker must not be overwritten
                    current = await session.get(AlertDelivery, row.id)
                    if (
                        current is None
                        or current.status != "SENDING"
                        or (current.attempts or 0) != (row.attempts or 0)
                    ):
                        counts["ownership_lost"] += 1
                        logger.warning(
                            "Outbox ownership lost after send alert=%s dest=%s; "
                            "remote_id=%s not written by this worker",
                            getattr(row, "alert_uid", None),
                            getattr(row, "destination", None),
                            mid,
                        )
                        # Best-effort: if no remote id yet, store it so future recovery skips resend
                        if current is not None and not current.remote_message_id:
                            current.remote_message_id = mid
                            await session.flush()
                            if self.commit_each_success:
                                await session.commit()
                        continue
                    # Persist id before final status so a partial commit still skips resend
                    current.remote_message_id = mid
                    await session.flush()
                    current.status = "SENT"
                    current.last_error = None
                    counts["sent"] += 1
                    await self._persist_signal_message_id(session, current, mid)
                    if self.commit_each_success:
                        await session.commit()
                else:
                    dest_enabled = (
                        self.telegram.enabled
                        if row.destination == "telegram"
                        else self.discord.enabled
                    )
                    if not dest_enabled:
                        row.status = "SENT"
                        row.last_error = "destination_disabled"
                        counts["skipped"] += 1
                    else:
                        row.status = "RETRYABLE_FAILURE"
                        row.last_error = "no_message_id_returned"
                        counts["retryable"] += 1
            except Exception as exc:
                logger.warning(
                    "Outbox dispatch failed alert=%s dest=%s: %s",
                    row.alert_uid,
                    row.destination,
                    exc,
                )
                row.status = "RETRYABLE_FAILURE"
                row.last_error = str(exc)[:500]
                counts["retryable"] += 1
            row.updated_at = datetime.now(timezone.utc)
            await session.flush()

        return counts

    async def _claim_row(self, session: AsyncSession, row_id: int, current_attempts: int) -> bool:
        """Atomically claim a retryable row for sending. Returns True if this worker won."""
        now = datetime.now(timezone.utc)
        stmt = (
            update(AlertDelivery)
            .where(
                AlertDelivery.id == row_id,
                AlertDelivery.status.in_(list(RETRYABLE_STATUSES)),
            )
            .values(
                status="SENDING",
                attempts=current_attempts + 1,
                updated_at=now,
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return (result.rowcount or 0) == 1

    async def _dispatch(self, row: AlertDelivery) -> Optional[str]:
        payload = json.loads(row.payload_json or "{}")
        if row.destination == "telegram":
            if row.kind == "initial":
                return await self.telegram.send_signal(payload)
            if row.kind == "performance":
                return await self.telegram.send_or_edit_performance(
                    existing_perf_message_id=payload.get("existing_perf_message_id"),
                    current_roi=payload.get("current_roi"),
                    peak_roi=payload.get("peak_roi"),
                    current_price=payload.get("current_price"),
                    entry_price=payload.get("entry_price"),
                    wallets_holding=payload.get("wallets_holding"),
                    reply_to_message_id=payload.get("reply_to_message_id"),
                )
            if row.kind == "leaderboard":
                return await self.telegram.send_text(payload.get("text", ""))
            return None
        if row.destination == "discord":
            if row.kind == "initial":
                return await self.discord.send_signal(payload)
            if row.kind == "performance":
                return await self.discord.send_or_edit_performance(
                    existing_perf_message_id=payload.get("existing_perf_message_id"),
                    current_roi=payload.get("current_roi"),
                    peak_roi=payload.get("peak_roi"),
                    current_price=payload.get("current_price"),
                    entry_price=payload.get("entry_price"),
                    wallets_holding=payload.get("wallets_holding"),
                )
            if row.kind == "leaderboard":
                return await self.discord.send_text(payload.get("text", ""))
            return None
        return None

    async def _persist_signal_message_id(
        self, session: AsyncSession, row: AlertDelivery, message_id: str
    ) -> None:
        """Write remote message id back onto SignalRecord for restart recovery."""
        if row.kind not in ("initial", "performance"):
            return
        result = await session.execute(
            select(SignalRecord).where(SignalRecord.signal_uid == row.alert_uid)
        )
        sig = result.scalar_one_or_none()
        if sig is None:
            return
        if row.destination == "telegram":
            if row.kind == "initial":
                sig.telegram_message_id = message_id
            else:
                sig.telegram_perf_message_id = message_id
        elif row.destination == "discord":
            if row.kind == "initial":
                sig.discord_message_id = message_id
            else:
                sig.discord_perf_message_id = message_id
        sig.updated_at = datetime.now(timezone.utc)

    async def recover_message_ids(self, session: AsyncSession, signal_uid: str) -> dict[str, Optional[str]]:
        """Load persisted Telegram/Discord message IDs for a signal after restart."""
        result = await session.execute(
            select(SignalRecord).where(SignalRecord.signal_uid == signal_uid)
        )
        sig = result.scalar_one_or_none()
        if sig is None:
            # Fall back to outbox remote ids
            result = await session.execute(
                select(AlertDelivery).where(
                    AlertDelivery.alert_uid == signal_uid,
                    AlertDelivery.status == "SENT",
                )
            )
            ids: dict[str, Optional[str]] = {
                "telegram_message_id": None,
                "telegram_perf_message_id": None,
                "discord_message_id": None,
                "discord_perf_message_id": None,
            }
            for row in result.scalars().all():
                key = f"{row.destination}_{'perf_' if row.kind == 'performance' else ''}message_id"
                if key in ids:
                    ids[key] = row.remote_message_id
            return ids
        return {
            "telegram_message_id": sig.telegram_message_id,
            "telegram_perf_message_id": sig.telegram_perf_message_id,
            "discord_message_id": sig.discord_message_id,
            "discord_perf_message_id": sig.discord_perf_message_id,
        }
