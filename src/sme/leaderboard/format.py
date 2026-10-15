"""R4ISE leaderboard text formatting — original visual identity."""

from __future__ import annotations

from sme.leaderboard.engine import LeaderboardReport
from sme.performance.math import format_multiple


MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def format_leaderboard_text(report: LeaderboardReport, *, brand: str = "R4ISE") -> str:
    """Compact readable ranking + statistics section."""
    h = report.horizon.value.upper()
    lines: list[str] = [
        f"{brand} | {h} | #SMARTMONEY",
        report.header_line(),
        "",
    ]

    if not report.rows:
        lines.append("Insufficient data for ranking.")
    else:
        for row in report.rows:
            medal = MEDALS.get(row.rank)
            mult_str = format_multiple(row.max_multiple)
            sym = row.token_symbol if row.token_symbol.startswith("$") else f"${row.token_symbol}"
            if medal:
                lines.append(f"{medal} {sym} [{mult_str}]")
            else:
                lines.append(f"{row.rank}. {sym} [{mult_str}]")

    s = report.stats
    lines.append("")
    lines.append("Statistics")
    lines.append(f"Calls: {s.calls}")
    if s.hit_rate is not None:
        lines.append(
            f"Hit Rate: {s.hit_rate:.2f}% ({s.hit_count}/{s.calls} eligible signals)"
        )
    else:
        lines.append("Hit Rate: N/A")
    lines.append(f"Median: {format_multiple(s.median_multiple)}")
    lines.append(f"Top Return: {format_multiple(s.top_return)}")
    lines.append(f"Average Return: {format_multiple(s.average_return)}")

    if s.pending or s.incomplete:
        lines.append(f"(Pending: {s.pending} | Incomplete: {s.incomplete})")
    if s.sample_warning:
        lines.append("⚠ Small sample — interpret with caution.")
    if s.calls == 0:
        lines.append("No eligible signals with sufficient data.")

    return "\n".join(lines)
