"""
Solana raw transaction → swap classification.

Uses pre/post token balances and native SOL deltas for a specific wallet.
Does NOT invent trades from insufficient evidence.

Classification rules (documented):
  BUY  — non-quote mint increases for wallet AND (quote mint decreases OR
         native SOL decreases beyond a small fee allowance).
  SELL — non-quote mint decreases for wallet AND (quote mint increases OR
         native SOL increases).
  UNCLASSIFIED — failed tx, transfer-only, liquidity-like multi-leg ambiguity,
                 missing metadata, or no wallet match.

Returns DecodeResult with trade fields or reason string; never fabricates direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

# Native wrapped SOL + common stables treated as quote legs
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTE_MINTS = frozenset({WSOL, USDC, USDT})

# Ignore SOL decreases smaller than this (fees / rent) when used as quote evidence
SOL_FEE_ALLOWANCE = Decimal("0.01")

KNOWN_SWAP_PROGRAMS = frozenset(
    {
        "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",  # Jupiter v6
        "JUP4Fb2cqiRUcaTHdrLXEH5vevj51kYWB7M1QK5p1n",  # Jupiter v4
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",  # Orca
        "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # Raydium CLMM
        "routeUGWgWgjKFsrAihphm1g1YgqqiLVh8Dh1tfLbqr",  # Jupiter aggregator route
    }
)


@dataclass
class DecodeResult:
    ok: bool
    reason: str = ""
    direction: Optional[str] = None  # BUY | SELL
    wallet: Optional[str] = None
    token_mint: Optional[str] = None
    token_amount: Optional[Decimal] = None
    quote_amount: Optional[Decimal] = None
    quote_mint: Optional[str] = None
    signature: Optional[str] = None
    slot: Optional[int] = None
    block_time: Optional[int] = None
    program_hint: Optional[str] = None
    evidence: dict[str, Any] = field(default_factory=dict)


def _account_keys(tx: dict) -> list[str]:
    keys: list[str] = []
    msg = (tx.get("transaction") or {}).get("message") or {}
    for k in msg.get("accountKeys") or []:
        if isinstance(k, dict):
            keys.append(str(k.get("pubkey") or ""))
        else:
            keys.append(str(k))
    return keys


def _ui_amount(bal: dict) -> Decimal:
    ui = (bal.get("uiTokenAmount") or {}).get("uiAmount")
    if ui is not None:
        try:
            return Decimal(str(ui))
        except Exception:
            pass
    amt = (bal.get("uiTokenAmount") or {}).get("amount") or "0"
    dec = int((bal.get("uiTokenAmount") or {}).get("decimals") or 0)
    try:
        return Decimal(amt) / (Decimal(10) ** dec)
    except Exception:
        return Decimal(0)


def _wallet_balances(balances: list, wallet: str) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for b in balances:
        if b.get("owner") != wallet:
            continue
        mint = b.get("mint")
        if not mint:
            continue
        out[mint] = _ui_amount(b)
    return out


def _sol_delta(tx: dict, meta: dict, wallet: str) -> Decimal:
    keys = _account_keys(tx)
    if wallet not in keys:
        return Decimal(0)
    idx = keys.index(wallet)
    pre_b = meta.get("preBalances") or []
    post_b = meta.get("postBalances") or []
    if idx >= len(pre_b) or idx >= len(post_b):
        return Decimal(0)
    return (Decimal(post_b[idx]) - Decimal(pre_b[idx])) / Decimal(1_000_000_000)


def _program_hint(tx: dict) -> Optional[str]:
    """Best-effort: scan account keys for known DEX program ids."""
    for k in _account_keys(tx):
        if k in KNOWN_SWAP_PROGRAMS:
            return k
    # log messages sometimes mention program invoke
    meta = tx.get("meta") or {}
    for line in meta.get("logMessages") or []:
        for pid in KNOWN_SWAP_PROGRAMS:
            if pid in line:
                return pid
    return None


def decode_solana_swap(
    tx: Optional[dict],
    *,
    wallet: str,
    signature: Optional[str] = None,
) -> DecodeResult:
    """
    Classify a jsonParsed getTransaction result for `wallet`.

    `tx` may be the full RPC result object, or a payload that embeds the same
    fields at top level (signature, slot, blockTime, meta, transaction).
    """
    if not wallet:
        return DecodeResult(ok=False, reason="missing_wallet")
    if not tx:
        return DecodeResult(ok=False, reason="missing_transaction")

    meta = tx.get("meta")
    if meta is None:
        return DecodeResult(ok=False, reason="missing_meta")
    if meta.get("err") is not None:
        return DecodeResult(
            ok=False,
            reason="failed_transaction",
            signature=signature or tx.get("transaction", {}).get("signatures", [None])[0]
            if isinstance(tx.get("transaction"), dict)
            else signature,
            evidence={"err": meta.get("err")},
        )

    sig = signature
    if not sig:
        txn = tx.get("transaction") or {}
        sigs = txn.get("signatures") if isinstance(txn, dict) else None
        if sigs:
            sig = sigs[0]
        sig = sig or tx.get("signature")

    pre = meta.get("preTokenBalances")
    post = meta.get("postTokenBalances")
    if pre is None or post is None:
        return DecodeResult(ok=False, reason="missing_token_balances", signature=sig)

    pre_m = _wallet_balances(pre, wallet)
    post_m = _wallet_balances(post, wallet)
    # Include mints that appear only in post (new ATA) or only in pre
    all_mints = set(pre_m) | set(post_m)
    deltas: dict[str, Decimal] = {}
    for m in all_mints:
        d = post_m.get(m, Decimal(0)) - pre_m.get(m, Decimal(0))
        if d != 0:
            deltas[m] = d

    sol_delta = _sol_delta(tx, meta, wallet)
    program = _program_hint(tx)

    if not deltas and sol_delta == 0:
        return DecodeResult(
            ok=False,
            reason="no_balance_change",
            signature=sig,
            wallet=wallet,
            evidence={"sol_delta": str(sol_delta)},
        )

    token_mints = [m for m in deltas if m not in QUOTE_MINTS]
    quote_mints_changed = [m for m in deltas if m in QUOTE_MINTS]

    # Quote evidence: token quote delta or meaningful SOL move
    quote_abs = sum((abs(deltas[m]) for m in quote_mints_changed), Decimal(0))
    sol_as_quote = abs(sol_delta) if abs(sol_delta) > SOL_FEE_ALLOWANCE else Decimal(0)

    if not token_mints:
        return DecodeResult(
            ok=False,
            reason="quote_only_or_no_non_quote_token",
            signature=sig,
            wallet=wallet,
            evidence={
                "deltas": {k: str(v) for k, v in deltas.items()},
                "sol_delta": str(sol_delta),
            },
        )

    # Primary non-quote token = largest absolute delta
    primary = max(token_mints, key=lambda m: abs(deltas[m]))
    token_delta = deltas[primary]
    other_tokens = [m for m in token_mints if m != primary]

    # Ambiguous multi-token without clear primary dominance
    if other_tokens:
        second = max(other_tokens, key=lambda m: abs(deltas[m]))
        if abs(deltas[second]) > abs(token_delta) * Decimal("0.5"):
            # Two large non-quote legs — likely LP / multi-hop residual / unknown
            if quote_abs == 0 and sol_as_quote == 0:
                return DecodeResult(
                    ok=False,
                    reason="ambiguous_multi_token",
                    signature=sig,
                    wallet=wallet,
                    evidence={
                        "primary": primary,
                        "primary_delta": str(token_delta),
                        "second": second,
                        "second_delta": str(deltas[second]),
                    },
                )

    has_counterparty_leg = quote_abs > 0 or sol_as_quote > 0

    if token_delta > 0:
        if not has_counterparty_leg:
            return DecodeResult(
                ok=False,
                reason="transfer_or_airdrop_no_quote_leg",
                signature=sig,
                wallet=wallet,
                token_mint=primary,
                token_amount=abs(token_delta),
                evidence={"token_delta": str(token_delta), "sol_delta": str(sol_delta)},
            )
        # BUY: received token, spent quote/SOL
        direction = "BUY"
    elif token_delta < 0:
        if not has_counterparty_leg:
            return DecodeResult(
                ok=False,
                reason="transfer_out_no_quote_leg",
                signature=sig,
                wallet=wallet,
                token_mint=primary,
                token_amount=abs(token_delta),
                evidence={"token_delta": str(token_delta), "sol_delta": str(sol_delta)},
            )
        direction = "SELL"
    else:
        return DecodeResult(ok=False, reason="zero_token_delta", signature=sig)

    # Quote amount / mint
    quote_mint = None
    if quote_mints_changed:
        quote_mint = max(quote_mints_changed, key=lambda m: abs(deltas[m]))
        quote_amount = abs(deltas[quote_mint])
    elif sol_as_quote > 0:
        quote_mint = WSOL  # represent native SOL as WSOL quote unit
        quote_amount = sol_as_quote
    else:
        quote_amount = None

    slot = tx.get("slot")
    block_time = tx.get("blockTime")

    return DecodeResult(
        ok=True,
        reason="classified",
        direction=direction,
        wallet=wallet,
        token_mint=primary,
        token_amount=abs(token_delta),
        quote_amount=quote_amount,
        quote_mint=quote_mint,
        signature=sig,
        slot=int(slot) if slot is not None else None,
        block_time=int(block_time) if block_time is not None else None,
        program_hint=program,
        evidence={
            "deltas": {k: str(v) for k, v in deltas.items()},
            "sol_delta": str(sol_delta),
            "has_counterparty_leg": has_counterparty_leg,
            "classification": "balance_delta",
        },
    )


def decode_result_to_swap_payload(result: DecodeResult) -> Optional[dict]:
    """Convert a successful DecodeResult into the structured swap payload shape."""
    if not result.ok or not result.direction or not result.wallet or not result.token_mint:
        return None
    return {
        "signature": result.signature,
        "slot": result.slot,
        "blockTime": result.block_time,
        "meta": {"err": None},
        "swap": {
            "wallet": result.wallet,
            "token_mint": result.token_mint,
            "direction": result.direction,
            "token_amount": str(result.token_amount) if result.token_amount is not None else None,
            "quote_amount": str(result.quote_amount) if result.quote_amount is not None else None,
            "quote_mint": result.quote_mint,
            "program": result.program_hint or "balance_delta",
            "event_index": 0,
        },
        "decode_reason": result.reason,
        "decode_evidence": result.evidence,
    }
