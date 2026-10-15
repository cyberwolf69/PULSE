"""Read engine state without additional RPC requests or credentials in snapshots."""
from __future__ import annotations
import asyncio
from sme.ingestion.evm import CHAIN_IDS
from sme.models.enums import ProviderHealth

CHAINS = ('solana', 'ethereum', 'base', 'bsc', 'arbitrum', 'avalanche',
          'robinhood', 'hyperevm', 'arc', 'sui', 'injective', 'monad')
POLLING_CHAINS = {'solana', *CHAIN_IDS}
RPC_STATES = {'unknown', 'ok', 'standby', 'rate_limited', 'timeout', 'error',
              'reorg_blocked', 'chain_mismatch'}
COUNTERS = ('events_received', 'events_parsed', 'events_rejected',
            'events_deduplicated', 'signals_created', 'signals_suppressed',
            'outbox_sent', 'leaderboard_runs', 'price_observations',
            'discovery_observations', 'discovery_added')
FOMO_STATES = {'ready', 'missing_key', 'disabled', 'invalid_key', 'credits_exhausted',
               'forbidden', 'rate_limited', 'request_error', 'invalid_response', 'ok', 'empty'}

def sync_engine(telemetry, engine):
    """Call on the engine event loop, not the HTTP thread. Strict allowlist only."""
    settings = engine.settings
    adapters = {k.value: v for k, v in engine.adapters.items()}
    poller = engine.poller
    tasks = {t.get_name() for t in poller._tasks if not t.done()} if poller else set()
    registry = engine.registry
    chains = []
    for name in CHAINS:
        endpoint = 'injective_lcd' if name == 'injective' else f'{name}_rpc_http'
        adapter = adapters.get(name)
        health = getattr(adapter, '_health', ProviderHealth.UNCONFIGURED)
        health = getattr(health, 'value', health)
        if health not in {h.value for h in ProviderHealth}:
            health = 'UNCONFIGURED'
        state = 'unknown'
        if poller and name == 'solana':
            state = poller.rpc_state
        elif poller and name in poller.evm_pollers:
            state = poller.evm_pollers[name].state
        chains.append(dict(id=name, enabled=bool(getattr(settings, f'{name}_enabled', False)),
                           rpc_configured=bool(getattr(settings, endpoint, None)),
                           adapter_available=adapter is not None and name != 'monad',
                           poller_supported=name in POLLING_CHAINS,
                           poller_active=f'{name}-poller' in tasks,
                           health=health, rpc_state=state if state in RPC_STATES else 'unknown',
                           watched_wallets=len(registry.wallets_for_chain(name)) if registry else 0))
    metrics = {key: int(engine.stats.get(key, 0)) for key in COUNTERS}
    metrics['ingest_queue'] = engine.ingest_q.qsize()
    metrics['signal_queue'] = engine.signal_q.qsize()
    fomo = registry.fomo if registry else None
    status = fomo.status if fomo else ('starting' if settings.fomo_enabled else 'disabled')
    if settings.fomo_enabled and not (settings.fomo_api_key or '').strip():
        status = 'missing_key'
    if status not in FOMO_STATES | {'starting'}:
        status = 'unknown'
    summary = registry.summary() if registry else {}
    services = {
        'fomo': {'enabled': bool(settings.fomo_enabled), 'status': status,
                 'wallet_targets': int(summary.get('by_role', {}).get('FOMO_TOP_TRADER', 0)),
                 'registry_ready': bool(registry and registry._last_refresh)},
        'discovery': {'enabled': bool(engine.discovery.enabled),
                      'evm_enabled': bool(engine.discovery.enabled and settings.evm_discovery_enabled),
                      'auto_promote': bool(settings.discovery_auto_promote)},
    }
    with telemetry.lock:
        telemetry.chains = chains
        telemetry.metrics = metrics
        telemetry.services = services
        telemetry.heartbeat()

class OfficeObserver:
    """One observer per engine process. Installs and restores async wrappers."""
    def __init__(self, telemetry, engine):
        self.telemetry, self.engine = telemetry, engine
        self.undo = []

    def before_start(self):
        import sme.app as app
        for obj, method, role in (
            (self.engine, '_handle_payload', 'scout'),
            (app, 'process_trade_for_signals', 'cipher'),
            (app, 'enrich_trade', 'sentry'),
            (app, 'finalize_evm_trade_amounts', 'sentry'),
            (self.engine.outbox, 'process_pending', 'relay'),
            (self.engine.leaderboard, 'run_once', 'atlas'),
            (self.engine.discovery, 'record_candidates', 'scout'),
            (self.engine.discovery, 'evaluate_and_promote', 'cipher'),
        ):
            self.undo.append(self.telemetry.wrap(obj, method, role))
        sync_engine(self.telemetry, self.engine)

    def after_start(self):
        engine = self.engine
        if engine.registry:
            self.undo.append(self.telemetry.wrap(engine.registry, 'refresh', 'echo'))
        if engine.price_worker:
            self.undo.append(self.telemetry.wrap(engine.price_worker, 'poll_once', 'atlas'))
        if engine.poller:
            for method in ('_rpc_solana', '_rpc_evm'):
                self.undo.append(self.telemetry.wrap(engine.poller, method, 'scout'))
        self.telemetry.set_running(True)
        sync_engine(self.telemetry, engine)

    def restore(self):
        self.telemetry.set_running(False)
        for undo in reversed(self.undo):
            undo()
        self.undo.clear()

async def run_observed(engine, telemetry, stop, started=None):
    observer = OfficeObserver(telemetry, engine)
    observer.before_start()
    heartbeat = None
    try:
        await engine.start()
        observer.after_start()
        if started is not None:
            started.set()
        async def update():
            while not stop.is_set():
                sync_engine(telemetry, engine)
                await asyncio.sleep(.5)
        heartbeat = asyncio.create_task(update(), name='office-heartbeat')
        await stop.wait()
    finally:
        telemetry.set_running(False)
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            await engine.stop()
        finally:
            observer.restore()
