"""Extract discovery candidates from an already-fetched Solana transaction.

Only wallets with a *classified* BUY/SELL for themselves (via decode_solana_swap)
are candidates. Fee payers, programs, transfer-only, failed txs, and ambiguous
balance changes are excluded.
"""

from __future__ import annotations

from typing import Any, Optional

from sme.adapters.solana_decode import decode_solana_swap, QUOTE_MINTS
from sme.intelligence.fomo import is_placeholder_wallet

# System / program-like accounts to skip
_SKIP_PREFIXES = (
    "11111111",
    "ComputeBudget",
    "Tokenkeg",
    "AToken",
    "MemoSq",
)


def _owners_from_tx(tx: dict) -> set[str]:
    owners: set[str] = set()
    meta = tx.get("meta") or {}
    for bal in (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or []):
        o = bal.get("owner")
        if o and isinstance(o, str):
            owners.add(o)
    return owners


def extract_solana_swap_candidates(
    tx: dict,
    *,
    signature: str,
    watched_wallet: str,
) -> list[dict[str, Any]]:
    """
    Return candidate dicts: {address, direction, token_mint, reason, signature}.

    Requires a successful classified swap for the *candidate* wallet itself.
    The watched wallet is excluded (already tracked).
    """
    if not tx or not signature or not watched_wallet:
        return []
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return []

    watched = watched_wallet.strip()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for owner in _owners_from_tx(tx):
        if not owner or owner == watched:
            continue
        if owner in seen:
            continue
        if is_placeholder_wallet(owner):
            continue
        if any(owner.startswith(p) for p in _SKIP_PREFIXES):
            continue
        # Must independently classify as BUY/SELL for this owner
        result = decode_solana_swap(tx, wallet=owner, signature=signature)
        if not result.ok:
            continue
        if result.direction not in ("BUY", "SELL"):
            continue
        if not result.token_mint or result.token_mint in QUOTE_MINTS:
            continue
        seen.add(owner)
        out.append(
            {
                "address": owner,
                "direction": result.direction,
                "token_mint": result.token_mint,
                "signature": signature,
                "source": "solana_swap_coactivity",
                "program_hint": result.program_hint,
            }
        )
    return out
