"""Performance tracking engine — baselines, milestones, live ROI updates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from sme.performance.math import is_valid_baseline, live_display, price_multiple, roi_pct
from sme.performance.horizons import Horizon, HORIZONS, is_eligible


DEFAULT_MILESTONE_MULTIPLES = (1.25, 1.5, 2.0, 3.0, 5.0, 10.0)


class PerformanceEngine:
    """
    Tracks immutable baselines and milestone hits for signals.
    Does not reset baseline when a later wallet buys the same token.
    """

    def __init__(self, milestone_multiples: Sequence[float] = DEFAULT_MILESTONE_MULTIPLES):
        self.milestone_multiples = sorted(milestone_multiples)

    def create_baseline(
        self,
        *,
        signal_uid: str,
        chain: str,
        token_address: str,
        token_symbol: Optional[str],
        signal_category: str,
        baseline_price: float,
        baseline_ts: datetime,
        price_source: str = "event",
        methodology: str = "event_time",
        confidence: str = "UNKNOWN",
        data_quality: str = "UNKNOWN",
        wallet_attribution: Optional[str] = None,
    ) -> dict:
        if not is_valid_baseline(baseline_price):
            raise ValueError(f"Invalid baseline price for signal {signal_uid}: {baseline_price}")
        if baseline_ts.tzinfo is None:
            baseline_ts = baseline_ts.replace(tzinfo=timezone.utc)
        return {
            "signal_uid": signal_uid,
            "chain": chain,
            "token_address": token_address,
            "token_symbol": token_symbol,
            "signal_category": signal_category,
            "baseline_price": float(baseline_price),
            "baseline_ts": baseline_ts,
            "price_source": price_source,
            "methodology": methodology,
            "confidence": confidence,
            "data_quality": data_quality,
            "wallet_attribution": wallet_attribution,
        }

    def check_milestones(
        self,
        *,
        signal_uid: str,
        baseline_price: float,
        current_price: float,
        observed_at: datetime,
        already_hit: set[float],
        source: str = "price_feed",
        data_quality: str = "UNKNOWN",
    ) -> list[dict]:
        """
        Return list of newly reached milestones (not already in already_hit).
        Each milestone fires at most once per signal.
        """
        if not is_valid_baseline(baseline_price):
            return []
        mult = price_multiple(current_price, baseline_price)
        if mult is None:
            return []
        new_hits: list[dict] = []
        for threshold in self.milestone_multiples:
            if threshold in already_hit:
                continue
            if mult >= threshold:
                new_hits.append(
                    {
                        "signal_uid": signal_uid,
                        "threshold_multiple": threshold,
                        "observed_price": current_price,
                        "observed_at": observed_at,
                        "source": source,
                        "data_quality": data_quality,
                        "notified": False,
                    }
                )
        return new_hits

    def live_status(
        self,
        *,
        baseline_price: float,
        current_price: Optional[float],
        max_price: Optional[float] = None,
    ) -> dict:
        """Snapshot for performance reply card."""
        cur_mult = price_multiple(current_price, baseline_price)
        cur_roi = roi_pct(current_price, baseline_price)
        peak_mult = price_multiple(max_price, baseline_price) if max_price is not None else cur_mult
        peak_roi = roi_pct(max_price, baseline_price) if max_price is not None else cur_roi
        return {
            "current_multiple": cur_mult,
            "current_roi_pct": cur_roi,
            "peak_multiple": peak_mult,
            "peak_roi_pct": peak_roi,
            "display": live_display(current_price, baseline_price),
            "peak_display": live_display(max_price if max_price is not None else current_price, baseline_price),
            "valid": is_valid_baseline(baseline_price) and current_price is not None,
        }
