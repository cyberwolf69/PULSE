"""CLI entry points for R4ISE Smart Money Scanner (package: sme)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

from sme import __version__
from sme.app import SmartMoneyEngine
from sme.config import get_settings
from sme.db.session import init_db, close_db
from sme.observability.logging import setup_logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="sme",
        description="R4ISE Smart Money Scanner — on-chain intelligence, FOMO, performance leaderboards",
    )
    parser.add_argument("--version", action="version", version=f"sme {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="Create / migrate database tables")
    sub.add_parser("migrate", help="Apply pending schema migrations")
    sub.add_parser("validate-config", help="Validate configuration and print summary")
    sub.add_parser("health", help="Print operational health summary")
    sub.add_parser("chains", help="Show chain adapter status matrix")

    run_p = sub.add_parser("run", help="Start the engine")
    run_p.add_argument("--seconds", type=int, default=0, help="Run for N seconds then stop (0=forever)")
    run_p.add_argument(
        "--mode",
        choices=["live", "fixture"],
        default="live",
        help="live = real providers; fixture = offline structured payloads only",
    )

    inject = sub.add_parser("inject", help="Inject a structured swap payload (for testing)")
    inject.add_argument("--chain", required=True)
    inject.add_argument("--file", required=True, help="JSON payload file")

    lb = sub.add_parser("leaderboard", help="Generate historical performance leaderboard")
    lb.add_argument(
        "--horizon",
        required=True,
        choices=["1d", "3d", "7d", "30d"],
        help="Evaluation horizon",
    )
    lb.add_argument("--top", type=int, default=10, help="Top N (default 10)")
    lb.add_argument("--hit-target", type=float, default=2.0, help="Hit target multiple (default 2.0)")
    lb.add_argument(
        "--demo",
        action="store_true",
        help="Use synthetic demo data (does not claim live market data)",
    )

    sub.add_parser("performance", help="Print performance summary (tracked signals)")

    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)

    if args.cmd == "init-db":
        asyncio.run(_init_db(settings))
    elif args.cmd == "migrate":
        asyncio.run(_migrate(settings))
    elif args.cmd == "validate-config":
        _validate_config(settings)
    elif args.cmd == "health":
        asyncio.run(_health(settings))
    elif args.cmd == "chains":
        asyncio.run(_chains(settings))
    elif args.cmd == "run":
        if args.mode == "fixture":
            print("Fixture mode: live ingestion disabled; use `sme inject` for offline payloads.")
        asyncio.run(_run(settings, args.seconds))
    elif args.cmd == "inject":
        asyncio.run(_inject(settings, args.chain, args.file))
    elif args.cmd == "leaderboard":
        _leaderboard(args.horizon, args.top, args.hit_target, demo=args.demo)
    elif args.cmd == "performance":
        print("Performance summary requires tracked signals in the database.")
        print("Use `sme leaderboard --horizon 1d --demo` for a format demonstration.")
    else:
        parser.print_help()
        sys.exit(1)


def _validate_config(settings) -> None:
    try:
        settings.validate_security()
        print("Configuration OK")
        print(json.dumps({
            "app_name": settings.app_name,
            "app_env": settings.app_env,
            "database_url": settings.database_url.split("://")[0] + "://***",
            "solana_enabled": settings.solana_enabled,
            "base_enabled": settings.base_enabled,
            "bsc_enabled": settings.bsc_enabled,
            "telegram_enabled": settings.telegram_enabled,
            "discord_enabled": settings.discord_enabled,
            "min_signal_score": settings.min_signal_score,
        }, indent=2))
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)


async def _init_db(settings) -> None:
    await init_db(settings.database_url)
    print(f"Database initialized: {settings.database_url}")
    await close_db()


async def _migrate(settings) -> None:
    from sme.db.session import init_db
    from sme.db.migrations_runner import run_migrations
    engine = await init_db(settings.database_url)
    applied = await run_migrations(engine)
    print(f"Migrations applied: {applied or 'none (up to date)'}")
    await close_db()


async def _health(settings) -> None:
    engine = SmartMoneyEngine(settings)
    await engine.start()
    summary = engine.health_summary()
    from sme.adapters import build_all_adapters
    adapters = build_all_adapters(settings)
    chain_health = {}
    for chain, ad in adapters.items():
        h = await ad.health_check()
        chain_health[chain.value] = {
            "health": h.value,
            "protocols": ad.supported_protocols(),
            "limitations": ad.limitations(),
        }
    summary["chains"] = chain_health
    print(json.dumps(summary, indent=2, default=str))
    await engine.stop()


async def _chains(settings) -> None:
    from sme.adapters import build_all_adapters
    from sme.models.enums import ChainId
    adapters = build_all_adapters(settings)
    rows = []
    for chain in ChainId:
        ad = adapters.get(chain)
        if ad is None:
            rows.append({"chain": chain.value, "status": "UNSUPPORTED", "note": "no adapter class"})
            continue
        h = await ad.health_check()
        rows.append({
            "chain": chain.value,
            "status": h.value,
            "protocols": ad.supported_protocols(),
            "limitations": ad.limitations(),
        })
    print(json.dumps(rows, indent=2))


async def _run(settings, seconds: int) -> None:
    engine = SmartMoneyEngine(settings)
    await engine.start()
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _stop(*_):
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

    try:
        if seconds > 0:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        else:
            await stop_event.wait()
    except asyncio.TimeoutError:
        pass
    finally:
        await engine.stop()


async def _inject(settings, chain: str, path: str) -> None:
    with open(path) as f:
        payload = json.load(f)
    payload["_allow_offline"] = True
    engine = SmartMoneyEngine(settings)
    await engine.start()
    ok = await engine.submit_raw_payload(chain, payload)
    await asyncio.sleep(1.5)
    print(json.dumps({"submitted": ok, "stats": engine.stats}, indent=2))
    await engine.stop()


def _leaderboard(horizon: str, top_n: int, hit_target: float, *, demo: bool) -> None:
    from sme.leaderboard.engine import LeaderboardEngine, SignalEvalInput
    from sme.leaderboard.format import format_leaderboard_text
    from sme.performance.horizons import Horizon

    h = Horizon(horizon)
    engine = LeaderboardEngine(hit_target=hit_target, top_n=top_n)

    if not demo:
        print(
            "No persisted horizon evaluations found in this environment.\n"
            "Re-run with --demo to see the R4ISE report format using synthetic data.\n"
            "Live leaderboards require tracked signals + price observations in the database."
        )
        return

    # Explicitly synthetic demo — not live market data
    now = datetime.now(timezone.utc)
    baseline = now - timedelta(hours=30)
    demo_inputs = [
        SignalEvalInput(
            signal_uid=f"demo-{i}",
            chain="solana" if i % 2 == 0 else "base",
            token_address=f"DemoToken{i}",
            token_symbol=f"TOKEN_{chr(65+i)}",
            signal_category="SMART_MONEY",
            baseline_price=1.0,
            baseline_ts=baseline,
            observations=[
                (1.0, baseline),
                (mult, baseline + timedelta(hours=12)),
                (mult * 0.85, baseline + timedelta(hours=24)),
            ],
            data_quality="DEMO",
        )
        for i, mult in enumerate([4.0, 2.5, 1.5, 1.3, 1.2, 1.1, 0.9, 0.7])
    ]
    evals = [engine.evaluate_signal(inp, h) for inp in demo_inputs]
    report = engine.build_report(evals, h)
    print(format_leaderboard_text(report))
    print("\n[DEMO DATA — not live market results]")


# late import for signal
import signal  # noqa: E402

if __name__ == "__main__":
    main()
