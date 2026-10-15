"""Continuous price-observation worker.

Polls configured market-data providers (DexScreener by default) for every
token that has an immutable SignalBaseline. Persists timestamped
PriceObservation rows and evaluates milestones / live performance from those
baselines. Never substitutes demo or fabricated prices.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sme.config import Settings
from sme.db import repository as repo
from sme.db.models import MilestoneHit, SignalBaseline, SignalRecord
from sme.models.enums import ChainId
from sme.performance.engine import PerformanceEngine
from sme.performance.math import is_valid_baseline, is_valid_price, price_multiple, roi_pct
from sme.enrichment.price import fetch_token_price

logger = logging.getLogger(__name__)

# DexScreener chainId field values
_CHAIN_PATH = {
    "solana": "solana",
    "base": "base",
    "bsc": "bsc",
    "ethereum": "ethereum",
    "arbitrum": "arbitrum",
    "avalanche": "avalanche",
}


class PriceWorker:
    def __init__(
        self,
        settings: Settings,
        *,
        poll_interval_seconds: float = 60.0,
        outbox=None,
    ):
        self.settings = settings
        self.poll_interval = max(15.0, poll_interval_seconds)
        self.outbox = outbox
        self.perf = PerformanceEngine()
        self._client: Optional[httpx.AsyncClient] = None
        self.stats = {
            "polls": 0,
            "observations": 0,
            "milestones": 0,
            "errors": 0,
            "tokens_tracked": 0,
        }

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def poll_once(self, session: AsyncSession) -> dict[str, int]:
        """Fetch prices for all active baselines and persist observations."""
        self.stats["polls"] += 1
        baselines = await repo.list_baselines(session)
        counts = {"obs": 0, "milestones": 0, "errors": 0}

        # Tokens to price: signal baselines + (optional) discovery candidate trade tokens
        targets: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for bl in baselines:
            key = (bl.chain, bl.token_address)
            if key not in seen:
                seen.add(key)
                targets.append(key)
        if getattr(self.settings, "discovery_enabled", False):
            try:
                for key in await repo.list_candidate_trade_tokens(session, limit=50):
                    if key not in seen:
                        seen.add(key)
                        targets.append(key)
            except Exception as exc:
                logger.debug("Candidate token list skipped: %s", exc)

        self.stats["tokens_tracked"] = len(targets)

        for chain, token_address in targets:
            try:
                price, meta = await self._fetch_price(chain, token_address)
                if price is None or not is_valid_price(price):
                    continue
                now = datetime.now(timezone.utc)
                await repo.add_price_observation(
                    session,
                    chain=chain,
                    token_address=token_address,
                    price_usd=price,
                    observed_at=now,
                    provider=meta.get("provider", "dexscreener"),
                    confidence=meta.get("confidence", "MEDIUM"),
                    liquidity_usd=meta.get("liquidity_usd"),
                )
                counts["obs"] += 1
                self.stats["observations"] += 1

                token_baselines = [
                    b for b in baselines
                    if b.chain == chain and b.token_address == token_address
                ]
                for b in token_baselines:
                    n = await self._evaluate_milestones(session, b, price, now)
                    counts["milestones"] += n
                    self.stats["milestones"] += n
            except Exception as exc:
                logger.warning("Price poll failed %s/%s: %s", chain, token_address[:12], exc)
                counts["errors"] += 1
                self.stats["errors"] += 1

        return counts

    async def _fetch_price(self, chain: str, token_address: str) -> tuple:
        """Call configured providers; never fabricate."""
        data = await fetch_token_price(token_address, chain, self.settings, client=self._client)
        if not data:
            return None, {"provider": "unavailable"}
        return data["price_usd"], data

    async def _evaluate_milestones(
        self,
        session: AsyncSession,
        baseline: SignalBaseline,
        current_price: float,
        observed_at: datetime,
    ) -> int:
        """Record newly reached milestones; enqueue performance update if outbox present."""
        result = await session.execute(
            select(MilestoneHit.threshold_multiple).where(
                MilestoneHit.signal_uid == baseline.signal_uid
            )
        )
        already = {float(r[0]) for r in result.fetchall()}
        new_hits = self.perf.check_milestones(
            signal_uid=baseline.signal_uid,
            baseline_price=baseline.baseline_price,
            current_price=current_price,
            observed_at=observed_at,
            already_hit=already,
            source="dexscreener",
            data_quality="HIGH",
        )
        for hit in new_hits:
            session.add(
                MilestoneHit(
                    signal_uid=hit["signal_uid"],
                    threshold_multiple=hit["threshold_multiple"],
                    observed_price=hit["observed_price"],
                    observed_at=hit["observed_at"],
                    source=hit["source"],
                    data_quality=hit["data_quality"],
                    notified=False,
                )
            )
        await session.flush()

        # Enqueue performance update for Telegram/Discord
        if self.outbox is not None and (new_hits or True):
            status = self.perf.live_status(
                baseline_price=baseline.baseline_price,
                current_price=current_price,
            )
            # Recover existing perf message ids
            ids = await self.outbox.recover_message_ids(session, baseline.signal_uid)
            # Skip performance card when ROI unavailable (missing prices)
            if status.get("current_roi_pct") is None and not is_valid_price(current_price):
                return len(new_hits)
            base_payload = {
                "current_roi": status.get("current_roi_pct"),
                "peak_roi": status.get("peak_roi_pct"),
                "current_price": current_price,
                "entry_price": baseline.baseline_price,
            }
            try:
                await self.outbox.enqueue(
                    session,
                    alert_uid=baseline.signal_uid,
                    destination="telegram",
                    kind="performance",
                    payload={
                        **base_payload,
                        "existing_perf_message_id": ids.get("telegram_perf_message_id"),
                        "reply_to_message_id": ids.get("telegram_message_id"),
                    },
                )
                await self.outbox.enqueue(
                    session,
                    alert_uid=baseline.signal_uid,
                    destination="discord",
                    kind="performance",
                    payload={
                        **base_payload,
                        "existing_perf_message_id": ids.get("discord_perf_message_id"),
                    },
                )
            except Exception as exc:
                logger.debug("Performance outbox enqueue: %s", exc)

        return len(new_hits)
