"""Deterministic ROI and price-multiple calculations.

Rules (from product spec):
  ROI%     = ((current - baseline) / baseline) * 100
  Multiple = current / baseline

Live display convention:
  Below +200% ROI  → percentage  (e.g. +50%, 100%, +150%)
  At/above +200%   → true price multiple as X  (e.g. 3X for +200% ROI)

Never compute a valid ROI from zero, missing, or invalid baseline.
"""

from __future__ import annotations

from typing import Optional


def is_valid_baseline(baseline: Optional[float]) -> bool:
    """Baseline must be a positive finite number."""
    return is_valid_price(baseline)


def is_valid_price(price: Optional[float]) -> bool:
    """Price must be a positive finite number. Zero/negative/NaN/Inf/None invalid."""
    if price is None:
        return False
    try:
        p = float(price)
    except (TypeError, ValueError):
        return False
    # Reject NaN (p != p) and ±Inf; require strictly positive finite
    if p != p:
        return False
    if p == float("inf") or p == float("-inf"):
        return False
    return p > 0.0


def price_multiple(current: Optional[float], baseline: Optional[float]) -> Optional[float]:
    """Return current / baseline, or None if inputs invalid."""
    if not is_valid_baseline(baseline):
        return None
    if current is None:
        return None
    try:
        c = float(current)
    except (TypeError, ValueError):
        return None
    if c < 0 or c != c:  # negative or NaN
        return None
    return c / float(baseline)


def roi_pct(current: Optional[float], baseline: Optional[float]) -> Optional[float]:
    """Return ROI percentage, or None if inputs invalid."""
    mult = price_multiple(current, baseline)
    if mult is None:
        return None
    return (mult - 1.0) * 100.0


def live_display(
    current: Optional[float],
    baseline: Optional[float],
    *,
    decimals: int = 1,
) -> str:
    """
    Format for live performance replies.

    Below +200% ROI → percentage form:
      +50% ROI  → "+50%"
      +100% ROI → "100%"
      +150% ROI → "+150%"

    At/above +200% ROI → true price multiple as X:
      +200% ROI (3X multiple) → "3X"
      +300% ROI (4X multiple) → "4X"
      +400% ROI (5X multiple) → "5X"
      +900% ROI (10X multiple) → "10X"

    Negative  → "-XX%"
    Invalid   → "N/A"
    """
    r = roi_pct(current, baseline)
    if r is None:
        return "N/A"

    # Primary display is always percentage form (never 1X for +100%).
    # Use format_multiple_label / price_multiple for a separate X field when needed.
    if abs(r - round(r)) < 0.05:
        r_int = int(round(r))
        if r_int > 0:
            return f"+{r_int}%" if r_int != 100 else "100%"
        return f"{r_int}%"
    if r > 0:
        return f"+{r:.{decimals}f}%"
    return f"{r:.{decimals}f}%"


def format_multiple(mult: Optional[float], decimals: int = 1) -> str:
    """Format a raw multiple for leaderboard rows."""
    if mult is None:
        return "N/A"
    if mult != mult:  # NaN
        return "N/A"
    if abs(mult - round(mult)) < 0.05:
        return f"{int(round(mult))}x"
    return f"{mult:.{decimals}f}x"
