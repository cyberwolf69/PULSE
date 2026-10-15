"""Lightweight versioned schema migrations (no Alembic dependency required).

Upgrade procedure:
  1. Backup the database file (or pg dump).
  2. Run:  sme migrate
  3. Verify: sme health

Each migration is idempotent. Applied versions are stored in schema_migrations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection

logger = logging.getLogger(__name__)

# (version, description, upgrade_sql_statements)
MIGRATIONS: list[tuple[str, str, list[str]]] = [
    (
        "001_core_tables",
        "Core tables created via metadata.create_all on first init",
        [],  # handled by Base.metadata.create_all
    ),
    (
        "002_performance_tables",
        "Price observations, baselines, milestones, horizon evals, leaderboards, outbox",
        [
            # SQLite-compatible CREATE IF NOT EXISTS for tables that may pre-exist
            """
            CREATE TABLE IF NOT EXISTS price_observations (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                chain VARCHAR(32) NOT NULL,
                token_address VARCHAR(128) NOT NULL,
                price_usd FLOAT NOT NULL,
                observed_at DATETIME NOT NULL,
                provider VARCHAR(64) DEFAULT 'unknown',
                confidence VARCHAR(32) DEFAULT 'UNKNOWN',
                liquidity_usd FLOAT,
                volume_24h_usd FLOAT,
                pool_id VARCHAR(128),
                created_at DATETIME
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_price_token_time ON price_observations (chain, token_address, observed_at)",
            """
            CREATE TABLE IF NOT EXISTS signal_baselines (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                signal_uid VARCHAR(64) NOT NULL UNIQUE,
                chain VARCHAR(32) NOT NULL,
                token_address VARCHAR(128) NOT NULL,
                token_symbol VARCHAR(64),
                signal_category VARCHAR(64) NOT NULL,
                baseline_price FLOAT NOT NULL,
                baseline_ts DATETIME NOT NULL,
                price_source VARCHAR(64) DEFAULT 'event',
                methodology VARCHAR(64) DEFAULT 'event_time',
                confidence VARCHAR(32) DEFAULT 'UNKNOWN',
                wallet_attribution TEXT,
                data_quality VARCHAR(32) DEFAULT 'UNKNOWN',
                created_at DATETIME
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_baseline_token ON signal_baselines (chain, token_address)",
            """
            CREATE TABLE IF NOT EXISTS milestone_hits (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                signal_uid VARCHAR(64) NOT NULL,
                horizon VARCHAR(8),
                threshold_multiple FLOAT NOT NULL,
                observed_price FLOAT NOT NULL,
                observed_at DATETIME NOT NULL,
                source VARCHAR(64) DEFAULT 'unknown',
                data_quality VARCHAR(32) DEFAULT 'UNKNOWN',
                notified BOOLEAN DEFAULT 0,
                created_at DATETIME,
                UNIQUE (signal_uid, threshold_multiple)
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_milestone_signal ON milestone_hits (signal_uid)",
            """
            CREATE TABLE IF NOT EXISTS horizon_evaluations (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                signal_uid VARCHAR(64) NOT NULL,
                horizon VARCHAR(8) NOT NULL,
                status VARCHAR(32) DEFAULT 'PENDING',
                max_multiple FLOAT,
                max_price FLOAT,
                max_observed_at DATETIME,
                endpoint_multiple FLOAT,
                endpoint_price FLOAT,
                endpoint_observed_at DATETIME,
                endpoint_delta_seconds INTEGER,
                hit_1_5x BOOLEAN DEFAULT 0,
                hit_2x BOOLEAN DEFAULT 0,
                hit_3x BOOLEAN DEFAULT 0,
                hit_5x BOOLEAN DEFAULT 0,
                hit_10x BOOLEAN DEFAULT 0,
                baseline_price FLOAT NOT NULL,
                evaluated_at DATETIME,
                created_at DATETIME,
                UNIQUE (signal_uid, horizon)
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_horizon_status ON horizon_evaluations (horizon, status)",
            """
            CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                horizon VARCHAR(8) NOT NULL,
                ranking_metric VARCHAR(32) DEFAULT 'max_multiple',
                hit_target FLOAT DEFAULT 2.0,
                top_n INTEGER DEFAULT 10,
                report_uid VARCHAR(64) NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                calls INTEGER DEFAULT 0,
                hit_rate FLOAT,
                median_multiple FLOAT,
                top_return FLOAT,
                average_return FLOAT,
                generated_at DATETIME,
                delivered_telegram BOOLEAN DEFAULT 0,
                delivered_discord BOOLEAN DEFAULT 0
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_lb_horizon_time ON leaderboard_snapshots (horizon, generated_at)",
            """
            CREATE TABLE IF NOT EXISTS alert_deliveries (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                alert_uid VARCHAR(64) NOT NULL,
                destination VARCHAR(32) NOT NULL,
                kind VARCHAR(32) NOT NULL,
                status VARCHAR(32) DEFAULT 'PENDING',
                remote_message_id VARCHAR(64),
                payload_json TEXT,
                attempts INTEGER DEFAULT 0,
                last_error TEXT,
                created_at DATETIME,
                updated_at DATETIME,
                UNIQUE (alert_uid, destination, kind)
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_delivery_status ON alert_deliveries (status)",
        ],
    ),
    (
        "003_wallet_candidates",
        "Autonomous wallet discovery candidates",
        [
            """
            CREATE TABLE IF NOT EXISTS wallet_candidates (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                chain VARCHAR(32) NOT NULL,
                address VARCHAR(128) NOT NULL,
                status VARCHAR(32) DEFAULT 'CANDIDATE',
                source VARCHAR(64) DEFAULT 'solana_swap_coactivity',
                evidence_count INTEGER DEFAULT 0,
                evidence_tx_json TEXT,
                last_evidence_tx VARCHAR(128),
                first_seen DATETIME,
                last_seen DATETIME,
                reject_reason VARCHAR(256),
                eval_notes TEXT,
                promoted_at DATETIME,
                expires_at DATETIME,
                created_at DATETIME,
                updated_at DATETIME
            )
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_candidate ON wallet_candidates (chain, address)",
            "CREATE INDEX IF NOT EXISTS ix_candidate_status ON wallet_candidates (status)",
            "CREATE INDEX IF NOT EXISTS ix_candidate_chain ON wallet_candidates (chain)",
        ],
    ),
    (
        "004_price_obs_unique",
        "Unique price observations per chain+token+timestamp; dedupe existing",
        [
            """
            DELETE FROM price_observations
            WHERE id NOT IN (
                SELECT MAX(id) FROM price_observations
                GROUP BY chain, token_address, observed_at
            )
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_price_obs_chain_token_ts ON price_observations (chain, token_address, observed_at)",
        ],
    ),
    (
        "005_evm_poll_checkpoints",
        "Confirmed EVM ingestion checkpoints, independent of provider health",
        ["""CREATE TABLE IF NOT EXISTS evm_poll_checkpoints (
            chain VARCHAR(32) PRIMARY KEY,
            chain_id INTEGER NOT NULL,
            block_number INTEGER NOT NULL,
            block_hash VARCHAR(66) NOT NULL,
            blocked_reason VARCHAR(64),
            pending_block_number INTEGER,
            pending_block_hash VARCHAR(66)
        )"""],
    ),
]


async def ensure_migrations_table(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(64) PRIMARY KEY,
                description VARCHAR(256),
                applied_at DATETIME
            )
            """
        )
    )


async def applied_versions(conn: AsyncConnection) -> set[str]:
    result = await conn.execute(text("SELECT version FROM schema_migrations"))
    return {row[0] for row in result.fetchall()}


async def run_migrations(engine: AsyncEngine) -> list[str]:
    """Apply pending migrations. Returns list of newly applied version ids."""
    applied: list[str] = []
    async with engine.begin() as conn:
        await ensure_migrations_table(conn)
        done = await applied_versions(conn)
        for version, description, statements in MIGRATIONS:
            if version in done:
                continue
            logger.info("Applying migration %s — %s", version, description)
            for stmt in statements:
                if stmt.strip():
                    await conn.execute(text(stmt))
            await conn.execute(
                text(
                    "INSERT INTO schema_migrations (version, description, applied_at) "
                    "VALUES (:v, :d, :t)"
                ),
                {
                    "v": version,
                    "d": description,
                    "t": datetime.now(timezone.utc).isoformat(),
                },
            )
            applied.append(version)
    return applied
