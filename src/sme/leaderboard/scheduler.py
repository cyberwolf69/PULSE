"""Persistent scheduled leaderboard reporting for 1D / 3D / 7D / 30D.

Evaluates signals against persisted price observations, writes HorizonEvaluation
rows, builds LeaderboardReport, persists LeaderboardSnapshot, and enqueues
delivery via the outbox. Duplicate report_uids prevent re-delivery after restart.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sme.db.models import (
    HorizonEvaluation,
    LeaderboardSnapshot,
    PriceObservation,
    SignalBaseline,
)
from sme.leaderboard.engine import LeaderboardEngine, SignalEvalInput
from sme.leaderboard.format import format_leaderboard_text
from sme.performance.horizons import HORIZONS, Horizon, horizon_end, is_eligible

logger = logging.getLogger(__name__)


class LeaderboardScheduler:
    def __init__(
        self,
        *,
        hit_target: float = 2.0,
        top_n: int = 10,
        ranking_metric: str = "max_multiple",
        interval_seconds: float = 3600.0,
    ):
        self.engine = LeaderboardEngine(
            hit_target=hit_target,
            top_n=top_n,
            ranking_metric=ranking_metric,
        )
        self.interval_seconds = interval_seconds
        self.hit_target = hit_target
        self.top_n = top_n

    async def run_once(
        self,
        session: AsyncSession,
        *,
        horizons: Sequence[Horizon] = HORIZONS,
        as_of: Optional[datetime] = None,
        enqueue_delivery=None,
    ) -> dict[str, dict]:
        """
        Evaluate all baselines for each horizon, persist evaluations + snapshots.
        Returns {horizon: {report_uid, calls, hit_rate, delivered}}.
        """
        now = as_of or datetime.now(timezone.utc)
        results: dict[str, dict] = {}

        baselines = await self._load_baselines(session)
        if not baselines:
            logger.info("Leaderboard: no signal baselines — skip")
            for h in horizons:
                results[h.value] = {"calls": 0, "status": "empty"}
            return results

        for h in horizons:
            evals = []
            for bl in baselines:
                observations = await self._load_observations(
                    session, bl.chain, bl.token_address, bl.baseline_ts, horizon_end(bl.baseline_ts, h)
                )
                inp = SignalEvalInput(
                    signal_uid=bl.signal_uid,
                    chain=bl.chain,
                    token_address=bl.token_address,
                    token_symbol=bl.token_symbol or "???",
                    signal_category=bl.signal_category,
                    baseline_price=bl.baseline_price,
                    baseline_ts=bl.baseline_ts,
                    observations=observations,
                    data_quality=bl.data_quality or "UNKNOWN",
                )
                ev = self.engine.evaluate_signal(inp, h, as_of=now)
                evals.append(ev)
                await self._upsert_horizon_eval(session, ev)

            report = self.engine.build_report(evals, h)
            # Deterministic report uid per horizon+hour window — survives restarts,
            # prevents duplicate delivery for the same schedule slot, and allows
            # catch-up: if the process was down for a full hour, the next run
            # creates a new uid for the current hour (missed hours are not
            # backfilled to avoid surprise bulk alerts).
            window = now.replace(minute=0, second=0, microsecond=0).isoformat()
            report_uid = f"lb-{h.value}-{window}"

            existing = await session.execute(
                select(LeaderboardSnapshot).where(LeaderboardSnapshot.report_uid == report_uid)
            )
            if existing.scalar_one_or_none() is not None:
                results[h.value] = {
                    "report_uid": report_uid,
                    "calls": report.stats.calls,
                    "hit_rate": report.stats.hit_rate,
                    "status": "already_delivered",
                }
                continue

            text = format_leaderboard_text(report)
            snap = LeaderboardSnapshot(
                horizon=h.value,
                ranking_metric=report.ranking_metric,
                hit_target=report.hit_target,
                top_n=report.top_n,
                report_uid=report_uid,
                payload_json=json.dumps({
                    "text": text,
                    "stats": {
                        "calls": report.stats.calls,
                        "hit_count": report.stats.hit_count,
                        "hit_rate": report.stats.hit_rate,
                        "median": report.stats.median_multiple,
                        "top_return": report.stats.top_return,
                        "average_return": report.stats.average_return,
                        "pending": report.stats.pending,
                        "incomplete": report.stats.incomplete,
                    },
                    "rows": [
                        {
                            "rank": r.rank,
                            "symbol": r.token_symbol,
                            "chain": r.chain,
                            "max_multiple": r.max_multiple,
                            "endpoint_multiple": r.endpoint_multiple,
                        }
                        for r in report.rows
                    ],
                }),
                calls=report.stats.calls,
                hit_rate=report.stats.hit_rate,
                median_multiple=report.stats.median_multiple,
                top_return=report.stats.top_return,
                average_return=report.stats.average_return,
                generated_at=now,
            )
            session.add(snap)
            await session.flush()

            if enqueue_delivery is not None and report.stats.calls > 0:
                await enqueue_delivery(session, report_uid, text)

            results[h.value] = {
                "report_uid": report_uid,
                "calls": report.stats.calls,
                "hit_rate": report.stats.hit_rate,
                "status": "generated",
            }
            logger.info(
                "Leaderboard %s: calls=%s hit_rate=%s report=%s",
                h.value,
                report.stats.calls,
                report.stats.hit_rate,
                report_uid,
            )

        return results

    async def _load_baselines(self, session: AsyncSession) -> list[SignalBaseline]:
        result = await session.execute(select(SignalBaseline))
        return list(result.scalars().all())

    async def _load_observations(
        self,
        session: AsyncSession,
        chain: str,
        token_address: str,
        start: datetime,
        end: datetime,
    ) -> list[tuple[float, datetime]]:
        result = await session.execute(
            select(PriceObservation)
            .where(
                PriceObservation.chain == chain,
                PriceObservation.token_address == token_address,
                PriceObservation.observed_at >= start,
                PriceObservation.observed_at <= end,
            )
            .order_by(PriceObservation.observed_at)
        )
        rows = result.scalars().all()
        return [(r.price_usd, r.observed_at) for r in rows]

    async def _upsert_horizon_eval(self, session: AsyncSession, ev: dict) -> None:
        result = await session.execute(
            select(HorizonEvaluation).where(
                HorizonEvaluation.signal_uid == ev["signal_uid"],
                HorizonEvaluation.horizon == ev["horizon"],
            )
        )
        row = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        fields = dict(
            status=ev["status"],
            max_multiple=ev.get("max_multiple"),
            max_price=ev.get("max_price"),
            max_observed_at=ev.get("max_observed_at"),
            endpoint_multiple=ev.get("endpoint_multiple"),
            endpoint_price=ev.get("endpoint_price"),
            endpoint_observed_at=ev.get("endpoint_observed_at"),
            endpoint_delta_seconds=ev.get("endpoint_delta_seconds"),
            hit_1_5x=bool(ev.get("max_multiple") and ev["max_multiple"] >= 1.5),
            hit_2x=bool(ev.get("hit_target")),
            hit_3x=bool(ev.get("max_multiple") and ev["max_multiple"] >= 3.0),
            hit_5x=bool(ev.get("max_multiple") and ev["max_multiple"] >= 5.0),
            hit_10x=bool(ev.get("max_multiple") and ev["max_multiple"] >= 10.0),
            baseline_price=ev["baseline_price"],
            evaluated_at=now if ev["status"] != "PENDING" else None,
        )
        if row is None:
            row = HorizonEvaluation(
                signal_uid=ev["signal_uid"],
                horizon=ev["horizon"],
                **fields,
            )
            session.add(row)
        else:
            for k, v in fields.items():
                setattr(row, k, v)
        await session.flush()
