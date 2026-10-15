"""
Original signal card design.

Distinct visual identity — deep charcoal tone language, emerald for buys,
muted red for exits, cool blue for wallet intel. Not a copy of any reference.
"""

from __future__ import annotations

from typing import Any, Optional


def _fmt_usd(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.1f}K"
    return f"${v:.2f}"


def _fmt_price(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if v < 0.0001:
        return f"${v:.8f}"
    if v < 1:
        return f"${v:.6f}"
    return f"${v:.4f}"


def _short_addr(addr: str, n: int = 4) -> str:
    if len(addr) <= 10:
        return addr
    return f"{addr[:n+2]}…{addr[-n:]}" if addr.startswith("0x") else f"{addr[:n]}…{addr[-n:]}"


def format_roi_label(roi_pct: Optional[float]) -> str:
    """Primary ROI label: always percentage form. +100% → 100% (never 1X)."""
    if roi_pct is None:
        return "—"
    if abs(roi_pct - round(roi_pct)) < 0.05:
        r = int(round(roi_pct))
        if r == 100:
            return "100%"
        if r > 0:
            return f"+{r}%"
        return f"{r}%"
    if roi_pct > 0:
        return f"+{roi_pct:.1f}%"
    return f"{roi_pct:.1f}%"


def format_multiple_label(roi_pct: Optional[float]) -> Optional[str]:
    """Secondary multiple field: only at/above +200% ROI → 2X, 3X, … Never 1X for +100%."""
    if roi_pct is None or roi_pct < 200:
        return None
    x_val = roi_pct / 100.0
    if abs(x_val - round(x_val)) < 0.05:
        return f"{int(round(x_val))}X"
    return f"{x_val:.1f}X"


def build_telegram_card(signal: dict[str, Any], *, explorer_base: str = "") -> str:
    """
    Compact mobile-first Telegram HTML/Markdown-style card.
    Uses Telegram-supported HTML subset.
    """
    chain = signal.get("chain", "?").upper()
    symbol = signal.get("token_symbol") or _short_addr(signal.get("token_address", ""))
    sig_type = signal.get("signal_type", "SIGNAL").replace("_", " ")
    priority = signal.get("priority", "MEDIUM")
    score = signal.get("score", 0)
    evidence = signal.get("evidence") or {}
    orig = signal.get("original_payload") or {}

    direction = orig.get("direction", "BUY")
    dir_icon = "▲" if direction == "BUY" else "▼"
    accent = "🟢" if direction == "BUY" else "🔴"

    lines = [
        f"<b>{accent} {sig_type}</b>",
        f"<code>{symbol}</code> · <b>{chain}</b> · score <b>{score}</b> · {priority}",
        "────────────",
        f"<b>Market</b>",
        f"Entry price: {_fmt_price(signal.get('entry_price'))}",
        f"Entry mcap:  {_fmt_usd(signal.get('entry_market_cap'))}",
        f"Trade USD:   {_fmt_usd(orig.get('usd_value'))}",
        "────────────",
        f"<b>Wallet intel</b>",
        f"Unique wallets: {evidence.get('unique_wallets', 1)}",
        f"Roles: {', '.join(evidence.get('roles') or ['—'])}",
        f"Actor: <code>{_short_addr(orig.get('wallet', ''))}</code>",
        "────────────",
        f"<b>Token</b>",
        f"<code>{signal.get('token_address', '')}</code>",
    ]

    if explorer_base and signal.get("token_address"):
        lines.append(f'<a href="{explorer_base}{signal["token_address"]}">Explorer</a>')

    lines.append("")
    lines.append(f"<i>Data confidence: evidence-based · missing fields shown as —</i>")
    return "\n".join(lines)


def build_discord_embed(signal: dict[str, Any]) -> dict[str, Any]:
    """Original Discord embed structure."""
    chain = signal.get("chain", "?").upper()
    symbol = signal.get("token_symbol") or "UNKNOWN"
    sig_type = signal.get("signal_type", "SIGNAL").replace("_", " ")
    score = signal.get("score", 0)
    evidence = signal.get("evidence") or {}
    orig = signal.get("original_payload") or {}
    direction = orig.get("direction", "BUY")
    color = 0x10B981 if direction == "BUY" else 0xEF4444  # emerald / muted red

    return {
        "title": f"{sig_type} · {symbol}",
        "description": f"**{chain}** · score **{score}** · {signal.get('priority', 'MEDIUM')}",
        "color": color,
        "fields": [
            {
                "name": "Market snapshot",
                "value": (
                    f"Entry: {_fmt_price(signal.get('entry_price'))}\n"
                    f"Mcap: {_fmt_usd(signal.get('entry_market_cap'))}\n"
                    f"Trade: {_fmt_usd(orig.get('usd_value'))}"
                ),
                "inline": True,
            },
            {
                "name": "Wallet intel",
                "value": (
                    f"Unique: {evidence.get('unique_wallets', 1)}\n"
                    f"Roles: {', '.join(evidence.get('roles') or ['—'])}\n"
                    f"Actor: `{_short_addr(orig.get('wallet', ''))}`"
                ),
                "inline": True,
            },
            {
                "name": "Token",
                "value": f"`{signal.get('token_address', '')}`",
                "inline": False,
            },
        ],
        "footer": {"text": "SME · evidence-based · unknown values shown as —"},
    }


def build_performance_text(
    *,
    current_roi: Optional[float],
    peak_roi: Optional[float],
    current_price: Optional[float],
    entry_price: Optional[float],
    wallets_holding: Optional[int] = None,
) -> str:
    """Editable performance reply body."""
    lines = ["📈 <b>Performance</b>"]
    if current_roi is None:
        lines.append("Current: unavailable")
    else:
        cur = format_roi_label(current_roi)
        mult = format_multiple_label(current_roi)
        if mult:
            lines.append(f"Current: <b>{cur}</b> ({mult})")
        else:
            lines.append(f"Current: <b>{cur}</b>")
    if peak_roi is not None:
        pk = format_roi_label(peak_roi)
        pmult = format_multiple_label(peak_roi)
        lines.append(f"Peak: {pk}" + (f" ({pmult})" if pmult else ""))
        if current_roi is not None and peak_roi > 0:
            dd = ((1 + current_roi / 100) / (1 + peak_roi / 100) - 1) * 100
            lines.append(f"From peak: {dd:.1f}%")
    if entry_price is not None:
        lines.append(f"Entry: {_fmt_price(entry_price)}")
    if current_price is not None:
        lines.append(f"Now: {_fmt_price(current_price)}")
    if wallets_holding is not None:
        lines.append(f"Wallets still holding: {wallets_holding}")
    return "\n".join(lines)
