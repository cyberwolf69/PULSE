"""Persist, evaluate, and optionally promote wallet discovery candidates."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from sme.config import Settings
from sme.db import repository as repo
from sme.intelligence.fomo import is_placeholder_wallet
from sme.models.enums import WalletRole
from sme.discovery.evaluate_perf import evaluate_buy_trade, aggregate_wallet_performance

logger = logging.getLogger(__name__)


class DiscoveryService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _env_watch_count(self) -> int:
        """Manual env watchlist size (solana + base)."""
        n = 0
        for raw in (
            getattr(self.settings, "watch_wallets_solana", None) or "",
            getattr(self.settings, "watch_wallets_base", None) or "",
            getattr(self.settings, "watch_wallets_ethereum", None) or "",
            getattr(self.settings, "watch_wallets_bsc", None) or "",
            getattr(self.settings, "watch_wallets_robinhood", None) or "",
        ):
            n += sum(1 for x in raw.split(",") if x.strip())
        return n

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "discovery_enabled", False))

    async def record_candidates(
        self,
        session: AsyncSession,
        *,
        chain: str,
        candidates: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Upsert candidates with evidence dedup. Respects resource limits."""
        counts = {"added": 0, "evidence": 0, "skipped_limit": 0, "skipped_dup_tx": 0, "rejected": 0}
        if not self.enabled or not candidates:
            return counts

        total = await repo.count_candidates(session)
        max_total = int(getattr(self.settings, "discovery_max_candidates", 200) or 200)
        max_day = int(getattr(self.settings, "discovery_max_new_per_day", 20) or 20)
        ttl_days = int(getattr(self.settings, "discovery_candidate_ttl_days", 14) or 14)
        new_today = await repo.count_candidates_created_since(
            session, datetime.now(timezone.utc) - timedelta(days=1)
        )

        for c in candidates:
            addr = (c.get("address") or "").strip()
            if not addr or is_placeholder_wallet(addr):
                counts["rejected"] += 1
                continue
            sig = c.get("signature") or ""
            existing = await repo.get_candidate(session, chain, addr)
            if existing is not None:
                # Evidence dedup by tx signature
                txs = []
                try:
                    txs = json.loads(existing.evidence_tx_json or "[]")
                except Exception:
                    txs = []
                if sig and sig in txs:
                    counts["skipped_dup_tx"] += 1
                    continue
                if sig:
                    txs.append(sig)
                existing.evidence_tx_json = json.dumps(txs[-50:])  # bound
                existing.evidence_count = len(txs)
                existing.last_evidence_tx = sig or existing.last_evidence_tx
                existing.last_seen = datetime.now(timezone.utc)
                existing.updated_at = datetime.now(timezone.utc)
                if existing.status == "INSUFFICIENT_DATA":
                    existing.status = "CANDIDATE"
                counts["evidence"] += 1
                continue

            if total >= max_total:
                counts["skipped_limit"] += 1
                logger.info("Discovery max_candidates=%s reached — stop adding", max_total)
                break
            if new_today >= max_day:
                counts["skipped_limit"] += 1
                logger.info("Discovery max_new_per_day=%s reached — stop adding", max_day)
                break

            expires = datetime.now(timezone.utc) + timedelta(days=ttl_days)
            before = await repo.get_candidate(session, chain, addr)
            row = await repo.create_candidate(
                session,
                chain=chain,
                address=addr,
                source=c.get("source") or "solana_swap_coactivity",
                evidence_tx=sig,
                expires_at=expires,
            )
            if before is None and row is not None and (row.evidence_count or 0) <= 1:
                total += 1
                new_today += 1
                counts["added"] += 1
            else:
                counts["skipped_dup_tx"] += 1

        return counts


    async def evaluate_candidate_performance(
        self,
        session: AsyncSession,
        *,
        chain: str,
        address: str,
        as_of=None,
    ) -> dict:
        """Compute horizon performance from persisted BUY trades + price observations."""
        from datetime import datetime, timezone

        as_of = as_of or datetime.now(timezone.utc)
        min_samples = int(getattr(self.settings, "discovery_min_perf_samples", 3) or 3)
        hit_target = float(getattr(self.settings, "discovery_perf_hit_target", 2.0) or 2.0)
        primary = str(getattr(self.settings, "discovery_perf_primary_horizon", "1d") or "1d")

        trades = await repo.list_trades_for_wallet(session, chain, address, limit=100)
        buys = [tr for tr in trades if (tr.direction or "").upper() == "BUY"]
        trade_results = []
        for tr in buys:
            obs = await repo.list_price_observations_for_token(
                session,
                chain,
                tr.token_address,
                since=tr.timestamp,
                limit=500,
            )
            trade_results.append(
                evaluate_buy_trade(
                    entry_price=tr.price,
                    entry_ts=tr.timestamp,
                    observations=obs,
                    as_of=as_of,
                    hit_target_multiple=hit_target,
                )
            )
        summary = aggregate_wallet_performance(
            trade_results, min_samples=min_samples, primary_horizon=primary
        )
        summary["buy_trades"] = len(buys)
        summary["evaluated_at"] = as_of.isoformat()
        return summary

    async def evaluate_and_promote(
        self, session: AsyncSession
    ) -> dict[str, int]:
        """
        Promote CANDIDATE → PROMOTED when evidence_count >= min and auto_promote on.
        Does NOT assign SMART_MONEY or KOL roles.
        """
        counts = {"promoted": 0, "insufficient": 0, "expired": 0, "skipped": 0}
        if not self.enabled:
            return counts

        min_ev = int(getattr(self.settings, "discovery_min_evidence", 3) or 3)
        auto = bool(getattr(self.settings, "discovery_auto_promote", False))
        max_promoted = int(getattr(self.settings, "discovery_max_promoted", 25) or 25)
        max_watched = int(getattr(self.settings, "discovery_max_watched_total", 100) or 100)
        now = datetime.now(timezone.utc)

        # Apply TTL to any non-terminal row (including stale PROMOTED)
        for row in await repo.list_candidates(session, limit=500):
            if row.status in ("REJECTED",):
                continue
            if row.expires_at is not None:
                exp = row.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp < now:
                    row.status = "REJECTED"
                    row.reject_reason = "expired"
                    row.updated_at = now
                    counts["expired"] += 1
        await session.flush()

        promoted_n = await repo.count_candidates(session, status="PROMOTED")
        require_perf = bool(getattr(self.settings, "discovery_require_perf_for_promote", False))
        rows = await repo.list_candidates(session, status="CANDIDATE", limit=100)
        rows += await repo.list_candidates(session, status="INSUFFICIENT_DATA", limit=100)

        for row in rows:
            # Always attempt performance evaluation from persisted data (may be empty)
            try:
                perf = await self.evaluate_candidate_performance(
                    session, chain=row.chain, address=row.address, as_of=now
                )
            except Exception as exc:
                logger.debug("Perf eval failed %s: %s", row.address[:12], exc)
                perf = {"quality": "ERROR", "error": str(exc)[:200]}

            notes = {
                "evidence_count": row.evidence_count,
                "perf": perf,
            }
            row.eval_notes = json.dumps(notes)
            row.updated_at = now

            if (row.evidence_count or 0) < min_ev:
                row.status = "INSUFFICIENT_DATA"
                counts["insufficient"] += 1
                continue

            evm_candidate = row.source == "evm_verified_v2_swap"
            if require_perf or evm_candidate:
                if perf.get("quality") == "ERROR":
                    row.status = "INSUFFICIENT_DATA"
                    counts["insufficient"] += 1
                    continue
                if perf.get("quality") == "INSUFFICIENT_DATA" or not perf.get("sufficient_samples"):
                    row.status = "INSUFFICIENT_DATA"
                    counts["insufficient"] += 1
                    continue

            if evm_candidate and (perf.get("hit_rate") is None
                    or perf["hit_rate"] < self.settings.evm_discovery_min_hit_rate):
                counts["skipped"] += 1
                continue

            # Enough evidence — remain CANDIDATE unless auto-promote
            if row.status == "INSUFFICIENT_DATA":
                row.status = "CANDIDATE"

            if not auto:
                counts["skipped"] += 1
                continue
            if promoted_n >= max_promoted:
                counts["skipped"] += 1
                logger.info("Discovery max_promoted=%s reached", max_promoted)
                break
            watched_total = promoted_n + self._env_watch_count()
            if watched_total >= max_watched:
                counts["skipped"] += 1
                logger.info(
                    "Discovery max_watched_total=%s reached (promoted=%s env=%s)",
                    max_watched,
                    promoted_n,
                    self._env_watch_count(),
                )
                break
            # Role is DISCOVERED_WATCHLIST only — never SMART_MONEY/KOL
            row.status = "PROMOTED"
            row.promoted_at = now
            notes["promoted"] = "DISCOVERED_WATCHLIST"
            row.eval_notes = json.dumps(notes)
            row.updated_at = now
            await repo.upsert_wallet(
                session,
                row.chain,
                row.address,
                roles=[WalletRole.DISCOVERED_WATCHLIST.value],
            )
            promoted_n += 1
            counts["promoted"] += 1
            counts["perf_evaluated"] = counts.get("perf_evaluated", 0) + 1

        return counts
