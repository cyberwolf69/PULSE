"""Candidate wallet performance evaluation from persisted trades + prices.

Uses existing ROI/multiple math and horizon definitions.
Does not fabricate prices, trades, or ROI. Incomplete/pending samples are
not counted as losses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sme.performance.horizons import (
    HORIZONS,
    Horizon,
    horizon_end,
    is_eligible,
    nearest_observation_tolerance_seconds,
)
from sme.performance.math import is_valid_price, price_multiple, roi_pct


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _observations_in_window(
    observations: Sequence[tuple[float, datetime]],
    start: datetime,
    end: datetime,
    *,
    as_of: Optional[datetime] = None,
) -> list[tuple[float, datetime]]:
    """Filter to [start, end], drop invalid/before-entry/after-as_of.

    Duplicate timestamps: keep the last valid price for that timestamp for
    deterministic peak/endpoint selection.
    """
    start, end = _aware(start), _aware(end)
    as_of = _aware(as_of)
    by_ts: dict[datetime, float] = {}
    for price, ts in observations:
        ts = _aware(ts)
        if ts is None or start is None or end is None:
            continue
        if ts < start:
            continue
        if ts > end:
            continue
        # No future leakage relative to evaluation time
        if as_of is not None and ts > as_of:
            continue
        if not is_valid_price(price):
            continue
        by_ts[ts] = float(price)  # last write wins for identical timestamps
    return sorted(((price, ts) for ts, price in by_ts.items()), key=lambda x: x[1])


def evaluate_buy_trade(
    *,
    entry_price: Optional[float],
    entry_ts: datetime,
    observations: Sequence[tuple[float, datetime]],
    as_of: Optional[datetime] = None,
    hit_target_multiple: float = 2.0,
) -> dict[str, Any]:
    """
    Per-trade horizon breakdown.
    Status per horizon: PENDING | INCOMPLETE | COMPLETE | INVALID_ENTRY
    """
    as_of = _aware(as_of) or datetime.now(timezone.utc)
    entry_ts = _aware(entry_ts) or as_of
    if not is_valid_price(entry_price):
        return {
            "valid": False,
            "reason": "invalid_entry_price",
            "horizons": {h.value: {"status": "INVALID_ENTRY"} for h in HORIZONS},
        }

    entry = float(entry_price)
    horizons: dict[str, Any] = {}
    for h in HORIZONS:
        if not is_eligible(entry_ts, h, as_of=as_of):
            horizons[h.value] = {"status": "PENDING"}
            continue
        end = horizon_end(entry_ts, h)
        window = _observations_in_window(observations, entry_ts, end, as_of=as_of)
        if not window:
            horizons[h.value] = {"status": "INCOMPLETE", "reason": "no_valid_observation"}
            continue
        # Peak multiple in window
        best_m = None
        best_p = None
        for price, _ts in window:
            m = price_multiple(price, entry)
            if m is None:
                continue
            if best_m is None or m > best_m:
                best_m = m
                best_p = price
        if best_m is None:
            horizons[h.value] = {"status": "INCOMPLETE", "reason": "no_valid_multiple"}
            continue
        # Endpoint: observation nearest horizon end within tolerance
        tol = nearest_observation_tolerance_seconds(h)
        endpoint_price = None
        endpoint_m = None
        best_dist = None
        for price, ts in window:
            dist = abs((ts - end).total_seconds())
            if dist <= tol and (best_dist is None or dist < best_dist):
                best_dist = dist
                endpoint_price = price
                endpoint_m = price_multiple(price, entry)
        hit = best_m >= hit_target_multiple
        horizons[h.value] = {
            "status": "COMPLETE",
            "max_multiple": best_m,
            "max_roi_pct": roi_pct(best_p, entry),
            "endpoint_multiple": endpoint_m,
            "hit_target": hit,
        }
    return {"valid": True, "entry_price": entry, "horizons": horizons}


def aggregate_wallet_performance(
    trade_results: Sequence[dict[str, Any]],
    *,
    min_samples: int = 3,
    primary_horizon: str = "1d",
) -> dict[str, Any]:
    """
    Aggregate COMPLETE samples only. PENDING/INCOMPLETE excluded from hit-rate
    denominator (never counted as failures).
    """
    complete = []
    pending = 0
    incomplete = 0
    invalid = 0
    for tr in trade_results:
        if not tr.get("valid"):
            invalid += 1
            continue
        h = (tr.get("horizons") or {}).get(primary_horizon) or {}
        st = h.get("status")
        if st == "COMPLETE":
            complete.append(h)
        elif st == "PENDING":
            pending += 1
        elif st == "INCOMPLETE":
            incomplete += 1
        else:
            invalid += 1

    summary: dict[str, Any] = {
        "primary_horizon": primary_horizon,
        "complete_samples": len(complete),
        "pending_samples": pending,
        "incomplete_samples": incomplete,
        "invalid_samples": invalid,
        "min_samples": min_samples,
        "sufficient_samples": len(complete) >= min_samples,
        "hit_rate": None,
        "median_max_multiple": None,
        "avg_max_multiple": None,
        "quality": "INSUFFICIENT_DATA",
    }
    if not complete:
        return summary

    n = len(complete)
    if n <= 0:  # defensive; never divide by zero
        return summary
    hits = sum(1 for c in complete if c.get("hit_target"))
    multiples = [c["max_multiple"] for c in complete if c.get("max_multiple") is not None]
    summary["hit_rate"] = (hits / n) * 100.0
    if multiples:
        ordered = sorted(multiples)
        mid = len(ordered) // 2
        summary["median_max_multiple"] = (
            ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
        )
        summary["avg_max_multiple"] = sum(multiples) / len(multiples)
    if summary["sufficient_samples"]:
        summary["quality"] = "EVALUATED"
    return summary
