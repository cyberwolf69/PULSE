"""Historical horizon definitions — elapsed UTC durations, not calendar days."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional


class Horizon(str, Enum):
    H1D = "1d"
    H3D = "3d"
    H7D = "7d"
    H30D = "30d"


# Elapsed seconds after baseline timestamp
HORIZON_SECONDS: dict[Horizon, int] = {
    Horizon.H1D: 24 * 3600,       # 24h
    Horizon.H3D: 72 * 3600,       # 72h
    Horizon.H7D: 168 * 3600,      # 168h
    Horizon.H30D: 720 * 3600,     # 720h
}

HORIZONS = (Horizon.H1D, Horizon.H3D, Horizon.H7D, Horizon.H30D)


def elapsed_seconds(baseline_ts: datetime, as_of: Optional[datetime] = None) -> float:
    """Seconds elapsed since baseline (UTC)."""
    if baseline_ts.tzinfo is None:
        baseline_ts = baseline_ts.replace(tzinfo=timezone.utc)
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - baseline_ts).total_seconds()


def is_eligible(
    baseline_ts: datetime,
    horizon: Horizon,
    as_of: Optional[datetime] = None,
) -> bool:
    """
    A signal is eligible for a horizon's *final* statistics only after
    the full duration has elapsed. Pending signals are never counted as failures.
    """
    return elapsed_seconds(baseline_ts, as_of) >= HORIZON_SECONDS[horizon]


def horizon_end(
    baseline_ts: datetime,
    horizon: Horizon,
) -> datetime:
    """Absolute UTC timestamp when the horizon ends."""
    if baseline_ts.tzinfo is None:
        baseline_ts = baseline_ts.replace(tzinfo=timezone.utc)
    return baseline_ts + timedelta(seconds=HORIZON_SECONDS[horizon])


def nearest_observation_tolerance_seconds(horizon: Horizon) -> int:
    """Configurable tolerance for matching an observation to the horizon endpoint."""
    # Wider tolerance for longer horizons
    return {
        Horizon.H1D: 900,      # 15 min
        Horizon.H3D: 1800,     # 30 min
        Horizon.H7D: 3600,     # 1 h
        Horizon.H30D: 7200,    # 2 h
    }[horizon]
