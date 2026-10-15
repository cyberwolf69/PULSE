"""Application orchestrator — ingestion → signals → outbox delivery → leaderboards."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from sme.adapters import build_all_adapters
from sme.config import Settings, get_settings
from sme.db.session import close_db, get_session, init_db
from sme.db import repository as repo
from sme.delivery import DiscordDelivery, TelegramDelivery, DeliveryOutbox
from sme.enrichment.price import enrich_trade
from sme.enrichment.token_meta import finalize_evm_trade_amounts
from sme.models.enums import ProviderHealth
from sme.observability.logging import setup_logging
from sme.queue.bounded import BoundedQueue
from sme.signals.engine import process_trade_for_signals
from sme.ingestion.poller import LivePoller
from sme.intelligence.fomo import TraderRegistry
from sme.leaderboard.scheduler import LeaderboardScheduler
from sme.enrichment.price_worker import PriceWorker
from sme.discovery.service import DiscoveryService
from sme.discovery.extract import extract_solana_swap_candidates
from sme.db.migrations_runner import run_migrations

logger = logging.getLogger(__name__)


class SmartMoneyEngine:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.adapters = build_all_adapters(self.settings)
        self.ingest_q: BoundedQueue[dict[str, Any]] = BoundedQueue(
            self.settings.ingest_queue_size,
            name="ingest",
            drop_on_full=self.settings.drop_on_backpressure,
        )
        self.signal_q: BoundedQueue[dict[str, Any]] = BoundedQueue(
            self.settings.signal_queue_size,
            name="signals",
            drop_on_full=self.settings.drop_on_backpressure,
        )
        self.telegram = TelegramDelivery(self.settings)
        self.discord = DiscordDelivery(self.settings)
        self.outbox = DeliveryOutbox(self.telegram, self.discord)
        self.leaderboard = LeaderboardScheduler(
            hit_target=2.0,
            top_n=10,
            interval_seconds=float(getattr(self.settings, "leaderboard_interval_seconds", 3600) or 3600),
        )
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self.poller: LivePoller | None = None
        self.discovery = DiscoveryService(self.settings)
        self.registry: TraderRegistry | None = None
        self.price_worker: PriceWorker | None = None
        self.stats = {
            "events_received": 0,
            "events_parsed": 0,
            "events_rejected": 0,
            "events_deduplicated": 0,
            "signals_created": 0,
            "signals_suppressed": 0,
            "outbox_sent": 0,
            "leaderboard_runs": 0,
            "price_observations": 0,
        }

    async def start(self) -> None:
        self.settings.validate_security()
        setup_logging(self.settings.log_level)
        engine = await init_db(self.settings.database_url)
        applied = await run_migrations(engine)
        if applied:
            logger.info("Migrations applied: %s", applied)

        for adapter in self.adapters.values():
            await adapter.connect()
            health = await adapter.health_check()
            async with get_session() as session:
                await repo.update_provider_health(
                    session, adapter.chain.value, health.value
                )
            logger.info("Adapter %s → %s", adapter.chain.value, health.value)

        await self.telegram.connect()
        await self.discord.connect()

        self._running = True
        self.price_worker = PriceWorker(
            self.settings,
            poll_interval_seconds=float(getattr(self.settings, "price_poll_interval_seconds", 60) or 60),
            outbox=self.outbox,
        )
        await self.price_worker.connect()

        self._tasks = [
            asyncio.create_task(self._process_ingest_loop(), name="ingest-worker"),
            asyncio.create_task(self._process_signal_loop(), name="signal-worker"),
            asyncio.create_task(self._outbox_loop(), name="outbox-worker"),
            asyncio.create_task(self._leaderboard_loop(), name="leaderboard-worker"),
            asyncio.create_task(self._price_loop(), name="price-worker"),
        ]
        self.registry = TraderRegistry(self.settings)
        await self.registry.connect()
        logger.info("Trader registry: %s", self.registry.summary())

        self.poller = LivePoller(
            self.settings, self.submit_raw_payload, registry=self.registry,
            evm_submit_fn=self.submit_durable_payload,
        )
        await self.poller.start()
        self._tasks.append(
            asyncio.create_task(self._registry_refresh_loop(), name="registry-refresh")
        )
        # Restart-safe: refresh candidate evals from persisted trades/prices
        if self.discovery.enabled:
            try:
                async with get_session() as session:
                    await self.discovery.evaluate_and_promote(session)
                    if self.registry is not None:
                        await self.registry.refresh()
            except Exception as exc:
                logger.debug("Startup discovery re-eval skipped: %s", exc)
        logger.info("Smart Money Engine started")

    async def stop(self) -> None:
        # Stop producers before cancelling the consumer waiting on acknowledgements.
        if self.poller:
            await self.poller.stop()
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for adapter in self.adapters.values():
            await adapter.disconnect()
        if self.poller:
            await self.poller.stop()
        if self.registry:
            await self.registry.disconnect()
        if self.price_worker:
            await self.price_worker.disconnect()
        await self.telegram.disconnect()
        await self.discord.disconnect()
        await close_db()
        logger.info("Smart Money Engine stopped. stats=%s", self.stats)

    async def submit_raw_payload(self, chain: str, payload: dict) -> bool:
        self.stats["events_received"] += 1
        return await self.ingest_q.put({"chain": chain, "payload": payload})

    async def submit_durable_payload(self, chain: str, payload: dict) -> bool:
        """Acknowledge only after the ingest transaction commits; replay on failure."""
        if not self._running:
            return False
        self.stats["events_received"] += 1
        ack = asyncio.get_running_loop().create_future()
        accepted = await self.ingest_q.put({"chain": chain, "payload": payload, "ack": ack})
        if not accepted:
            return False
        return await ack

    async def _process_ingest_loop(self) -> None:
        while self._running:
            try:
                item = await asyncio.wait_for(self.ingest_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                handled = await self._handle_payload(item["chain"], item["payload"])
                if item.get("ack") is not None and not item["ack"].done():
                    item["ack"].set_result(handled is True)
            except Exception as exc:
                logger.exception("Ingest error: %s", exc)
                if item.get("ack") is not None and not item["ack"].done():
                    item["ack"].set_result(False)
            finally:
                if item.get("ack") is not None and not item["ack"].done():
                    item["ack"].set_result(False)
                self.ingest_q.task_done()

    async def _handle_payload(self, chain_key: str, payload: dict) -> bool:
        from sme.models.enums import ChainId

        try:
            chain = ChainId(chain_key)
        except ValueError:
            self.stats["events_rejected"] += 1
            return False

        adapter = self.adapters.get(chain)
        if adapter is None:
            self.stats["events_rejected"] += 1
            return False

        health = await adapter.health_check()
        if (payload.get("_source") == "live_poller" and chain.value in ("ethereum", "bsc", "base", "robinhood")
                and health != ProviderHealth.HEALTHY):
            self.stats["events_rejected"] += 1
            return False
        if health in (ProviderHealth.UNCONFIGURED, ProviderHealth.UNAVAILABLE, ProviderHealth.STALE):
            if not payload.get("_allow_offline") and payload.get("_source") != "live_poller":
                logger.debug("Skipping payload — provider %s is %s", chain.value, health.value)
                self.stats["events_rejected"] += 1
                return False

        trade = await adapter.parse_swap_from_payload(payload)
        if trade is None:
            self.stats["events_rejected"] += 1
            return False

        self.stats["events_parsed"] += 1

        try:
            trade = await enrich_trade(trade, self.settings)
        except Exception:
            pass

        roles = payload.get("wallet_roles") or []

        async with get_session() as session:
            # EVM amount finalization with DB-backed decimals cache
            try:
                rpc = None
                if trade.chain.value == "base":
                    rpc = self.settings.base_rpc_http if self.settings.base_enabled else None
                elif trade.chain.value == "bsc":
                    rpc = self.settings.bsc_rpc_http if self.settings.bsc_enabled else None
                elif trade.chain.value == "robinhood":
                    rpc = self.settings.robinhood_rpc_http if self.settings.robinhood_enabled else None
                elif trade.chain.value == "ethereum":
                    rpc = self.settings.ethereum_rpc_http if self.settings.ethereum_enabled else None
                trade = await finalize_evm_trade_amounts(
                    trade, self.settings, rpc_http=rpc, session=session
                )
            except Exception as exc:
                logger.debug("EVM amount finalize skipped: %s", exc)

            seen = await repo.is_event_seen(session, trade.event_id)
            if not seen and chain.value in ("base", "ethereum", "bsc", "arbitrum", "avalanche", "robinhood"):
                seen = await repo.is_legacy_evm_event_seen(session, trade)
            if seen:
                self.stats["events_deduplicated"] += 1
                return True

            if payload.get("_discovery_candidate"):
                from sme.discovery.evm import FACTORIES
                proof = payload["_discovery_candidate"]
                if (not self.discovery.enabled or not self.settings.evm_discovery_enabled
                        or payload.get("_source") != "live_poller"
                        or proof.get("factory") not in FACTORIES.get(chain.value, set())):
                    return False
                await self.discovery.record_candidates(session, chain=chain.value, candidates=[{
                    "address": trade.wallet_address, "signature": trade.tx_signature,
                    "source": "evm_verified_v2_swap",
                }])
                candidate = await repo.get_candidate(session, chain.value, trade.wallet_address)
                if candidate is None or candidate.status == "REJECTED":
                    return True  # explicit admission limits; not a lost ingest
                if candidate.status != "PROMOTED":
                    # Collect performance evidence without treating new candidates
                    # as smart money or allowing confluence to emit early alerts.
                    from datetime import datetime, timezone
                    if abs((datetime.now(timezone.utc) - trade.timestamp).total_seconds()) > self.settings.price_stale_after_seconds:
                        trade.price = None  # current spot is not historical entry price
                    trade.swap_evidence["discovery"] = proof
                    if await repo.record_event(session, trade):
                        await repo.persist_trade(session, trade)
                    self.stats["discovery_observations"] = self.stats.get("discovery_observations", 0) + 1
                    return True
                roles = ["DISCOVERED_WATCHLIST"]

            signal = await process_trade_for_signals(
                session, trade, self.settings, wallet_roles=roles
            )

            # Autonomous discovery: only from Solana payloads that still carry raw tx
            if (
                self.discovery.enabled
                and trade.chain.value == "solana"
                and payload.get("meta") is not None
            ):
                try:
                    watched = (
                        payload.get("watch_wallet")
                        or payload.get("wallet")
                        or trade.wallet_address
                    )
                    sig = trade.tx_signature
                    cands = extract_solana_swap_candidates(
                        payload, signature=sig, watched_wallet=watched
                    )
                    dcounts = await self.discovery.record_candidates(
                        session, chain="solana", candidates=cands
                    )
                    if dcounts.get("added") or dcounts.get("evidence"):
                        pcounts = await self.discovery.evaluate_and_promote(session)
                        self.stats["discovery_added"] = self.stats.get("discovery_added", 0) + dcounts.get("added", 0)
                        if pcounts.get("promoted") and self.registry is not None:
                            # Hot-update watchlist used by LivePoller._solana_wallets()
                            try:
                                await self.registry.refresh()
                            except Exception as rexc:
                                logger.debug("Registry refresh after promote: %s", rexc)
                except Exception as exc:
                    logger.debug("Discovery step skipped: %s", exc)

            if signal:
                self.stats["signals_created"] += 1
                # Create immutable baseline when entry price is available
                entry = signal.get("entry_price")
                if entry is not None:
                    try:
                        from datetime import datetime, timezone
                        await repo.save_baseline(
                            session,
                            {
                                "signal_uid": signal["signal_uid"],
                                "chain": signal["chain"],
                                "token_address": signal["token_address"],
                                "token_symbol": signal.get("token_symbol"),
                                "signal_category": signal.get("signal_type", "SMART_MONEY"),
                                "baseline_price": entry,
                                "baseline_ts": datetime.now(timezone.utc),
                                "price_source": "event",
                                "data_quality": "UNKNOWN",
                            },
                        )
                    except Exception as exc:
                        logger.warning("Baseline save failed: %s", exc)
                # Initial delivery intent commits with the trade and dedup marker.
                # A crash after acknowledgement must not lose the signal in RAM.
                for destination in ("telegram", "discord"):
                    await self.outbox.enqueue(
                        session, alert_uid=signal["signal_uid"], destination=destination,
                        kind="initial", payload=signal,
                    )
            else:
                self.stats["signals_suppressed"] += 1

        return True

    async def _process_signal_loop(self) -> None:
        """Enqueue initial alerts into durable outbox instead of fire-and-forget."""
        while self._running:
            try:
                signal = await asyncio.wait_for(self.signal_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                async with get_session() as session:
                    await self.outbox.enqueue(
                        session,
                        alert_uid=signal["signal_uid"],
                        destination="telegram",
                        kind="initial",
                        payload=signal,
                    )
                    await self.outbox.enqueue(
                        session,
                        alert_uid=signal["signal_uid"],
                        destination="discord",
                        kind="initial",
                        payload=signal,
                    )
            except Exception as exc:
                logger.exception("Signal outbox enqueue error: %s", exc)
            finally:
                self.signal_q.task_done()

    async def _outbox_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(2.0)
                async with get_session() as session:
                    counts = await self.outbox.process_pending(session)
                    self.stats["outbox_sent"] += counts.get("sent", 0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Outbox loop error: %s", exc)

    async def _leaderboard_loop(self) -> None:
        interval = max(60.0, self.leaderboard.interval_seconds)
        # First run after a short delay so DB is warm
        await asyncio.sleep(5.0)
        while self._running:
            try:
                async with get_session() as session:

                    async def _enqueue(sess, report_uid: str, text: str):
                        await self.outbox.enqueue(
                            sess,
                            alert_uid=report_uid,
                            destination="telegram",
                            kind="leaderboard",
                            payload={"text": text},
                        )
                        await self.outbox.enqueue(
                            sess,
                            alert_uid=report_uid,
                            destination="discord",
                            kind="leaderboard",
                            payload={"text": text},
                        )

                    results = await self.leaderboard.run_once(
                        session, enqueue_delivery=_enqueue
                    )
                    self.stats["leaderboard_runs"] += 1
                    logger.info("Leaderboard cycle: %s", results)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Leaderboard loop error: %s", exc)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def _price_loop(self) -> None:
        if not self.price_worker:
            return
        interval = self.price_worker.poll_interval
        await asyncio.sleep(3.0)
        while self._running:
            try:
                async with get_session() as session:
                    counts = await self.price_worker.poll_once(session)
                    self.stats["price_observations"] += counts.get("obs", 0)
                    # Periodic discovery re-eval (new prices AND time-elapsed PENDING→COMPLETE)
                    if self.discovery.enabled:
                        try:
                            await self.discovery.evaluate_and_promote(session)
                        except Exception as dexc:
                            logger.debug("Discovery re-eval after price cycle: %s", dexc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Price worker error: %s", exc)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def _registry_refresh_loop(self) -> None:

        interval = max(60.0, float(self.settings.fomo_refresh_seconds or 300))
        while self._running:
            try:
                await asyncio.sleep(interval)
                if self.registry:
                    n = await self.registry.refresh()
                    logger.info("Registry refreshed: %d wallets | %s", n, self.registry.summary())
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Registry refresh error: %s", exc)

    def discovery_readiness(self) -> dict[str, Any]:
        """Explain why discovery/polling is idle vs active (no secrets)."""
        s = self.settings
        reasons: list[str] = []
        if not s.discovery_enabled:
            reasons.append("discovery_disabled")
        if not s.solana_enabled:
            reasons.append("solana_disabled")
        if not s.solana_rpc_http:
            reasons.append("solana_rpc_missing")
        wallets = []
        if self.registry:
            wallets = self.registry.wallets_for_chain("solana")
        if not wallets and not (s.watch_wallets_solana or "").strip():
            reasons.append("no_solana_watch_wallets")
        if s.discovery_auto_promote:
            reasons.append("auto_promote_on")  # informational, not a blocker
        else:
            reasons.append("auto_promote_off")
        poller_tasks = []
        rpc_state = "no_poller"
        rpc_last_error = None
        if self.poller:
            poller_tasks = [t.get_name() for t in self.poller._tasks if not t.done()]
            rpc_state = getattr(self.poller, "rpc_state", "unknown")
            rpc_last_error = getattr(self.poller, "rpc_last_error", None)
            if rpc_state == "rate_limited":
                reasons.append("rpc_rate_limited")
            elif rpc_state == "timeout":
                reasons.append("rpc_timeout")
            elif rpc_state == "error":
                reasons.append("rpc_error")
        # Misconfig must never look "live-ready"
        hard_blockers = (
            "discovery_disabled",
            "solana_disabled",
            "solana_rpc_missing",
            "no_solana_watch_wallets",
        )
        return {
            "discovery_enabled": bool(s.discovery_enabled),
            "require_perf_for_promote": bool(s.discovery_require_perf_for_promote),
            "auto_promote": bool(s.discovery_auto_promote),
            "solana_enabled": bool(s.solana_enabled),
            "solana_rpc_configured": bool(s.solana_rpc_http),
            "watch_wallet_count": len(wallets) if wallets else len(
                [x for x in (s.watch_wallets_solana or "").split(",") if x.strip()]
            ),
            "poller_tasks": poller_tasks,
            "rpc_state": rpc_state,
            "rpc_last_error": rpc_last_error,
            "reasons": reasons,
            "pipeline_ready": not any(r in reasons for r in hard_blockers),
        }

    def health_summary(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "stats": dict(self.stats),
            "queues": {
                "ingest": self.ingest_q.stats(),
                "signals": self.signal_q.stats(),
            },
            "telegram_enabled": self.telegram.enabled,
            "discord_enabled": self.discord.enabled,
            "registry": self.registry.summary() if self.registry else {},
            "discovery": self.discovery_readiness(),
            "evm_discovery": {
                "enabled": self.discovery.enabled and self.settings.evm_discovery_enabled,
                "requires_manual_wallets": False,
                "scope": "verified_v2_sender_direct_erc20_swaps",
                "auto_promote": self.settings.discovery_auto_promote,
                "performance_required": True,
            },
            "evm_pollers": {chain: {"state": p.state, "last_error": p.last_error,
                                    "decode_rejections": dict(p.decode_rejections)}
                            for chain, p in self.poller.evm_pollers.items()} if self.poller else {},
        }
