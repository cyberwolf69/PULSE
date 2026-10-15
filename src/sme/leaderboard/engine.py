"""Historical leaderboard engine.

Ranking default: maximum observed price multiple within the horizon.
Hit-rate: signals that reached the configured target / eligible evaluated signals
with sufficient data. Pending and incomplete are excluded from the denominator.
Losing signals remain in the dataset.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

from sme.performance.horizons import Horizon, HORIZON_SECONDS, is_eligible
from sme.performance.math import format_multiple, price_multiple, roi_pct


DEFAULT_HIT_TARGET = 2.0  # 2X multiple == +100% ROI
DEFAULT_TOP_N = 10
DEFAULT_MILESTONES = (1.5, 2.0, 3.0, 5.0, 10.0)


@dataclass
class LeaderboardRow:
    rank: int
    signal_uid: str
    token_symbol: str
    chain: str
    max_multiple: float
    endpoint_multiple: Optional[float]
    signal_category: str
    baseline_ts: datetime
    data_quality: str
    chart_url: Optional[str] = None
    explorer_url: Optional[str] = None


@dataclass
class LeaderboardStats:
    calls: int  # eligible evaluated signals with sufficient data
    pending: int
    incomplete: int
    hit_count: int
    hit_rate: Optional[float]  # percentage
    median_multiple: Optional[float]
    top_return: Optional[float]  # max of the ranking metric among ranked
    average_return: Optional[float]
    hit_target: float
    ranking_metric: str
    sample_warning: bool = False


@dataclass
class LeaderboardReport:
    horizon: Horizon
    ranking_metric: str
    hit_target: float
    top_n: int
    rows: list[LeaderboardRow]
    stats: LeaderboardStats
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    report_uid: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    def header_line(self) -> str:
        return (
            f"Ranking: {self.ranking_metric.replace('_', ' ').title()} | "
            f"Hit Target: {self.hit_target:g}X | Horizon: {self.horizon.value.upper()}"
        )


@dataclass
class SignalEvalInput:
    """Minimal input required to evaluate one signal for leaderboard purposes."""

    signal_uid: str
    chain: str
    token_address: str
    token_symbol: str
    signal_category: str
    baseline_price: float
    baseline_ts: datetime
    # Observations within / around the horizon (price, timestamp)
    observations: Sequence[tuple[float, datetime]]  # (price_usd, observed_at)
    data_quality: str = "UNKNOWN"
    chart_url: Optional[str] = None
    explorer_url: Optional[str] = None


def _max_multiple_in_window(
    baseline_price: float,
    baseline_ts: datetime,
    observations: Sequence[tuple[float, datetime]],
    horizon_end: datetime,
) -> tuple[Optional[float], Optional[float], Optional[datetime]]:
    """Return (max_multiple, max_price, max_ts) for observations in [baseline, horizon_end]."""
    best_mult: Optional[float] = None
    best_price: Optional[float] = None
    best_ts: Optional[datetime] = None
    def _aware(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            from datetime import timezone
            return dt.replace(tzinfo=timezone.utc)
        return dt

    baseline_ts = _aware(baseline_ts)
    horizon_end = _aware(horizon_end)
    for price, ts in observations:
        ts = _aware(ts)
        if ts < baseline_ts or ts > horizon_end:
            continue
        m = price_multiple(price, baseline_price)
        if m is None:
            continue
        if best_mult is None or m > best_mult:
            best_mult = m
            best_price = price
            best_ts = ts
    return best_mult, best_price, best_ts


def _endpoint_multiple(
    baseline_price: float,
    horizon_end: datetime,
    observations: Sequence[tuple[float, datetime]],
    tolerance_seconds: int,
) -> tuple[Optional[float], Optional[float], Optional[datetime], Optional[int]]:
    """Nearest observation within tolerance of horizon_end."""
    best: Optional[tuple[float, datetime, int]] = None  # price, ts, abs_delta
    def _aware(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            from datetime import timezone
            return dt.replace(tzinfo=timezone.utc)
        return dt
    horizon_end = _aware(horizon_end)
    for price, ts in observations:
        ts = _aware(ts)
        delta = abs(int((ts - horizon_end).total_seconds()))
        if delta > tolerance_seconds:
            continue
        if best is None or delta < best[2]:
            best = (price, ts, delta)
    if best is None:
        return None, None, None, None
    price, ts, delta = best
    mult = price_multiple(price, baseline_price)
    return mult, price, ts, delta


class LeaderboardEngine:
    """Pure computation engine — no I/O. Persist results via repository."""

    def __init__(
        self,
        *,
        hit_target: float = DEFAULT_HIT_TARGET,
        top_n: int = DEFAULT_TOP_N,
        ranking_metric: str = "max_multiple",
        milestones: Sequence[float] = DEFAULT_MILESTONES,
        small_sample_threshold: int = 10,
    ):
        self.hit_target = hit_target
        self.top_n = top_n
        self.ranking_metric = ranking_metric
        self.milestones = list(milestones)
        self.small_sample_threshold = small_sample_threshold

    def evaluate_signal(
        self,
        inp: SignalEvalInput,
        horizon: Horizon,
        as_of: Optional[datetime] = None,
        tolerance_seconds: Optional[int] = None,
    ) -> dict:
        """
        Evaluate one signal for one horizon.

        Returns a dict with keys:
          status, max_multiple, endpoint_multiple, hit_target, eligible, ...
        """
        from sme.performance.horizons import (
            HORIZON_SECONDS,
            horizon_end,
            nearest_observation_tolerance_seconds,
        )

        now = as_of or datetime.now(timezone.utc)
        eligible = is_eligible(inp.baseline_ts, horizon, now)
        h_end = horizon_end(inp.baseline_ts, horizon)
        tol = tolerance_seconds if tolerance_seconds is not None else nearest_observation_tolerance_seconds(horizon)

        max_m, max_p, max_ts = _max_multiple_in_window(
            inp.baseline_price, inp.baseline_ts, inp.observations, h_end
        )
        ep_m, ep_p, ep_ts, ep_delta = _endpoint_multiple(
            inp.baseline_price, h_end, inp.observations, tol
        )

        if not eligible:
            status = "PENDING"
        elif max_m is None and ep_m is None:
            status = "INCOMPLETE"
        else:
            status = "COMPLETE"

        hit = bool(max_m is not None and max_m >= self.hit_target)

        return {
            "signal_uid": inp.signal_uid,
            "horizon": horizon.value,
            "status": status,
            "eligible": eligible,
            "max_multiple": max_m,
            "max_price": max_p,
            "max_observed_at": max_ts,
            "endpoint_multiple": ep_m,
            "endpoint_price": ep_p,
            "endpoint_observed_at": ep_ts,
            "endpoint_delta_seconds": ep_delta,
            "hit_target": hit,
            "baseline_price": inp.baseline_price,
            "token_symbol": inp.token_symbol,
            "chain": inp.chain,
            "signal_category": inp.signal_category,
            "baseline_ts": inp.baseline_ts,
            "data_quality": inp.data_quality,
            "chart_url": inp.chart_url,
            "explorer_url": inp.explorer_url,
        }

    def build_report(
        self,
        evaluations: Sequence[dict],
        horizon: Horizon,
    ) -> LeaderboardReport:
        """
        Build a leaderboard from pre-computed evaluations for a single horizon.

        Only COMPLETE evaluations with a usable ranking metric enter ranking + stats.
        PENDING and INCOMPLETE are counted separately and excluded from hit-rate denominator.
        """
        complete = [e for e in evaluations if e["status"] == "COMPLETE" and e.get("max_multiple") is not None]
        pending = sum(1 for e in evaluations if e["status"] == "PENDING")
        incomplete = sum(1 for e in evaluations if e["status"] == "INCOMPLETE")

        # Sort by ranking metric (default max_multiple descending)
        key = self.ranking_metric
        if key == "endpoint_multiple":
            complete_sorted = sorted(
                [e for e in complete if e.get("endpoint_multiple") is not None],
                key=lambda e: e["endpoint_multiple"],
                reverse=True,
            )
        else:
            complete_sorted = sorted(complete, key=lambda e: e["max_multiple"], reverse=True)

        rows: list[LeaderboardRow] = []
        for i, e in enumerate(complete_sorted[: self.top_n], start=1):
            rows.append(
                LeaderboardRow(
                    rank=i,
                    signal_uid=e["signal_uid"],
                    token_symbol=e.get("token_symbol") or "???",
                    chain=e.get("chain") or "?",
                    max_multiple=e["max_multiple"],
                    endpoint_multiple=e.get("endpoint_multiple"),
                    signal_category=e.get("signal_category") or "UNKNOWN",
                    baseline_ts=e["baseline_ts"],
                    data_quality=e.get("data_quality") or "UNKNOWN",
                    chart_url=e.get("chart_url"),
                    explorer_url=e.get("explorer_url"),
                )
            )

        multiples = [e["max_multiple"] for e in complete]
        hit_count = sum(1 for e in complete if e.get("hit_target"))
        calls = len(complete)
        hit_rate = (hit_count / calls * 100.0) if calls > 0 else None
        median = statistics.median(multiples) if multiples else None
        avg = statistics.mean(multiples) if multiples else None
        top_ret = multiples[0] if multiples else None  # after sort descending
        if complete_sorted:
            top_ret = complete_sorted[0]["max_multiple"] if key != "endpoint_multiple" else complete_sorted[0].get("endpoint_multiple")

        stats = LeaderboardStats(
            calls=calls,
            pending=pending,
            incomplete=incomplete,
            hit_count=hit_count,
            hit_rate=hit_rate,
            median_multiple=median,
            top_return=top_ret,
            average_return=avg,
            hit_target=self.hit_target,
            ranking_metric=self.ranking_metric,
            sample_warning=calls > 0 and calls < self.small_sample_threshold,
        )

        return LeaderboardReport(
            horizon=horizon,
            ranking_metric=self.ranking_metric,
            hit_target=self.hit_target,
            top_n=self.top_n,
            rows=rows,
            stats=stats,
        )
