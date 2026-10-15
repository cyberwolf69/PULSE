"""
EVM Uniswap V2/V3-compatible swap log decoding.

Classification strategy (documented):
1. Reject failed transactions (status != 1).
2. Reject logs with removed=true (reorg).
3. Plain ERC-20 Transfer alone is NEVER a swap.
4. Require at least one Uniswap V2 or V3 Swap topic in the receipt logs.
5. Attribute economic direction using Transfer logs involving the watched wallet
   in the same transaction (more reliable than assuming tx.from is the trader
   when aggregators/routers are used).
6. Prefer non-stable / non-WETH as the primary "token" leg when both move.

Without sufficient Transfer evidence for the watched wallet, returns UNCLASSIFIED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
import re

# keccak256 topic signatures (public)
UNISWAP_V2_SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
UNISWAP_V3_SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
ERC20_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Addresses are network-scoped; an identical address elsewhere is not a quote.
QUOTE_TOKENS_BY_CHAIN = {
    # https://docs.robinhood.com/chain/contracts/ (2026-10-15)
    "robinhood": frozenset({
        "0x0bd7d308f8e1639fab988df18a8011f41eacad73",  # WETH
        "0x5fc5360d0400a0fd4f2af552add042d716f1d168",  # USDG
    }),
    "base": frozenset({
        "0x4200000000000000000000000000000000000006",
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",
    }),
    "ethereum": frozenset({
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "0xdac17f958d2ee523a2206206994597c13d831ec7",
    }),
    "bsc": frozenset({
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
        "0x55d398326f99059ff775485246999027b3197955",
    }),
    "arbitrum": frozenset({"0x82af49447d8a07e3bd95bd0d56f35241523fbab1"}),
    "avalanche": frozenset({"0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7"}),
}
# Public compatibility export; runtime decoding always selects a single chain.
QUOTE_TOKENS = frozenset().union(*QUOTE_TOKENS_BY_CHAIN.values())


@dataclass
class EvmDecodeResult:
    ok: bool
    reason: str = ""
    direction: Optional[str] = None
    wallet: Optional[str] = None
    token: Optional[str] = None
    token_amount: Optional[Decimal] = None
    quote_token: Optional[str] = None
    quote_amount: Optional[Decimal] = None
    tx_hash: Optional[str] = None
    log_index: Optional[int] = None
    block_number: Optional[int] = None
    protocol: Optional[str] = None
    topic0: Optional[str] = None
    evidence: dict[str, Any] = field(default_factory=dict)


def _norm_addr(a: Optional[str]) -> str:
    if not a:
        return ""
    return a.lower() if a.startswith("0x") else a


def _topic_addr(topic: str) -> str:
    """Extract address from 32-byte topic."""
    if not topic:
        return ""
    t = topic.lower()
    if t.startswith("0x"):
        t = t[2:]
    return "0x" + t[-40:]


def _int_data_words(data: str) -> list[int]:
    if not data or data == "0x":
        return []
    raw = data[2:] if data.startswith("0x") else data
    words = []
    for i in range(0, len(raw), 64):
        chunk = raw[i : i + 64]
        if len(chunk) < 64:
            break
        words.append(int(chunk, 16))
    return words


def _parse_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        if isinstance(v, str) and v.startswith("0x"):
            return int(v, 16)
        return int(v)
    except (TypeError, ValueError):
        return None


def _status_ok(status: Any) -> bool:
    return status in (1, "0x1", "1", True)


def decode_evm_swap_for_wallet(
    receipt_or_payload: dict,
    *,
    wallet: str,
    timestamp: Optional[int] = None,
    chain: str = "base",
) -> EvmDecodeResult:
    """
    Decode a transaction receipt (or payload with logs/status/hash) for `wallet`.

    Expects fields commonly returned by eth_getTransactionReceipt:
      hash/transactionHash, status, blockNumber, logs[{address, topics, data, logIndex, removed}]
    """
    wallet = _norm_addr(wallet)
    if not wallet:
        return EvmDecodeResult(ok=False, reason="missing_wallet")

    payload = receipt_or_payload or {}
    status = payload.get("status")
    if status is not None and not _status_ok(status):
        return EvmDecodeResult(
            ok=False,
            reason="failed_transaction",
            tx_hash=payload.get("transactionHash") or payload.get("hash"),
        )

    tx_hash = payload.get("transactionHash") or payload.get("hash") or payload.get("tx_hash")
    block_number = _parse_int(payload.get("blockNumber") or payload.get("block_number"))
    logs = payload.get("logs") or []

    swap_logs = []
    transfers: list[dict] = []  # {token, from, to, amount, log_index, topic0}

    for log in logs:
        if not isinstance(log, dict):
            return EvmDecodeResult(ok=False, reason="malformed_log", tx_hash=tx_hash)
        if log.get("removed") is True:
            continue
        topics = log.get("topics") or []
        if not topics:
            continue
        if not isinstance(topics, list) or not isinstance(topics[0], str):
            return EvmDecodeResult(ok=False, reason="malformed_topics", tx_hash=tx_hash)
        topic0 = topics[0].lower()
        log_index = _parse_int(log.get("logIndex"))
        addr = _norm_addr(log.get("address"))

        if topic0 in (UNISWAP_V2_SWAP, UNISWAP_V3_SWAP):
            swap_logs.append(
                {
                    "pool": addr,
                    "topic0": topic0,
                    "log_index": log_index,
                    "protocol": "UniswapV3" if topic0 == UNISWAP_V3_SWAP else "UniswapV2",
                }
            )
        elif topic0 == ERC20_TRANSFER:
            # ERC-721 uses the same signature but has an indexed token ID.
            if len(topics) != 3:
                continue
            if (not all(isinstance(t, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", t) for t in topics[1:])
                    or not re.fullmatch(r"0x[0-9a-fA-F]{64}", log.get("data") or "")):
                return EvmDecodeResult(ok=False, reason="malformed_transfer", tx_hash=tx_hash)
            frm = _topic_addr(topics[1])
            to = _topic_addr(topics[2])
            words = _int_data_words(log.get("data") or "0x0")
            amount = words[0] if words else 0
            transfers.append(
                {
                    "token": addr,
                    "from": frm,
                    "to": to,
                    "amount": amount,
                    "log_index": log_index,
                }
            )

    if not swap_logs:
        return EvmDecodeResult(
            ok=False,
            reason="no_swap_event",
            tx_hash=tx_hash,
            evidence={"transfer_count": len(transfers)},
        )

    quote_tokens = QUOTE_TOKENS_BY_CHAIN.get(chain, frozenset())

    # Wallet-centric transfer netting
    # positive delta = received token, negative = sent
    deltas: dict[str, int] = {}
    for tr in transfers:
        if tr["to"] == wallet:
            deltas[tr["token"]] = deltas.get(tr["token"], 0) + tr["amount"]
        if tr["from"] == wallet:
            deltas[tr["token"]] = deltas.get(tr["token"], 0) - tr["amount"]

    # Drop dust zeros
    deltas = {k: v for k, v in deltas.items() if v != 0}
    if not deltas:
        return EvmDecodeResult(
            ok=False,
            reason="no_wallet_token_transfer_with_swap",
            tx_hash=tx_hash,
            evidence={"swap_logs": len(swap_logs)},
        )

    received = {k: v for k, v in deltas.items() if v > 0}
    sent = {k: abs(v) for k, v in deltas.items() if v < 0}

    if not received and not sent:
        return EvmDecodeResult(ok=False, reason="zero_net_transfer", tx_hash=tx_hash)

    # Primary non-quote token leg
    non_quote_recv = {k: v for k, v in received.items() if k not in quote_tokens}
    non_quote_sent = {k: v for k, v in sent.items() if k not in quote_tokens}
    quote_recv = {k: v for k, v in received.items() if k in quote_tokens}
    quote_sent = {k: v for k, v in sent.items() if k in quote_tokens}

    if (len(non_quote_recv) + len(non_quote_sent) != 1
            or len(quote_recv) + len(quote_sent) > 1):
        return EvmDecodeResult(ok=False, reason="ambiguous_wallet_legs", tx_hash=tx_hash)

    protocol = swap_logs[0]["protocol"]
    topic0 = swap_logs[0]["topic0"]
    log_index = swap_logs[0]["log_index"]

    if non_quote_recv and (quote_sent or non_quote_sent):
        # Prefer BUY when receiving non-quote and sending quote
        if quote_sent or (non_quote_sent and quote_recv):
            # If both directions of non-quote, take largest receive as primary BUY
            token = max(non_quote_recv, key=lambda t: non_quote_recv[t])
            direction = "BUY"
            token_amount = non_quote_recv[token]
            if quote_sent:
                qt = max(quote_sent, key=lambda t: quote_sent[t])
                quote_token, quote_amount = qt, quote_sent[qt]
            else:
                quote_token, quote_amount = None, None
        else:
            # Received non-quote but no clear payment — ambiguous (airdrop+swap?)
            return EvmDecodeResult(
                ok=False,
                reason="ambiguous_receive_without_quote_payment",
                tx_hash=tx_hash,
                evidence={"received": {k: str(v) for k, v in received.items()}},
            )
    elif non_quote_sent and (quote_recv or non_quote_recv):
        token = max(non_quote_sent, key=lambda t: non_quote_sent[t])
        direction = "SELL"
        token_amount = non_quote_sent[token]
        if quote_recv:
            qt = max(quote_recv, key=lambda t: quote_recv[t])
            quote_token, quote_amount = qt, quote_recv[qt]
        else:
            quote_token, quote_amount = None, None
    elif non_quote_recv and not sent:
        return EvmDecodeResult(
            ok=False,
            reason="transfer_in_with_swap_but_no_payment_leg",
            tx_hash=tx_hash,
        )
    elif non_quote_sent and not received:
        return EvmDecodeResult(
            ok=False,
            reason="transfer_out_with_swap_but_no_receive_leg",
            tx_hash=tx_hash,
        )
    else:
        # Only quote tokens moved relative to wallet — skip
        return EvmDecodeResult(
            ok=False,
            reason="quote_only_wallet_legs",
            tx_hash=tx_hash,
            evidence={"deltas": {k: str(v) for k, v in deltas.items()}},
        )

    return EvmDecodeResult(
        ok=True,
        reason="classified",
        direction=direction,
        wallet=wallet,
        token=token,
        token_amount=token_amount,
        quote_token=quote_token,
        quote_amount=quote_amount,
        tx_hash=tx_hash,
        log_index=log_index,
        block_number=block_number,
        protocol=protocol,
        topic0=topic0,
        evidence={
            "deltas": {k: str(v) for k, v in deltas.items()},
            "swap_pool": swap_logs[0].get("pool"),
            "swap_count": len(swap_logs),
            "classification": "transfer_correlation_with_swap_event",
        },
    )


def decode_result_to_payload(
    result: EvmDecodeResult,
    *,
    timestamp: Optional[int] = None,
    chain_hint: Optional[str] = None,
) -> Optional[dict]:
    if not result.ok or not result.direction or not result.wallet or not result.token:
        return None
    return {
        "chain": chain_hint,
        "hash": result.tx_hash,
        "blockNumber": result.block_number,
        "status": "0x1",
        "timestamp": timestamp or int(datetime.now(timezone.utc).timestamp()),
        "swap": {
            "wallet": result.wallet,
            "token": result.token,
            "direction": result.direction,
            "token_amount_raw": str(int(result.token_amount)) if result.token_amount is not None else None,
            "quote_amount_raw": str(int(result.quote_amount)) if result.quote_amount is not None else None,
            # token_amount left unset until decimals resolved — avoid unit confusion
            "token_amount": None,
            "quote_amount": None,
            "quote_token": result.quote_token,
            "protocol": result.protocol or "Uniswap",
            "log_index": result.log_index or 0,
            "topic0": result.topic0,
            "amount_status": "raw_pending_decimals",
        },
        "decode_reason": result.reason,
        "decode_evidence": result.evidence,
    }
