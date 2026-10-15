"""
FOMO leaderboard + Smart Money / KOL source layer.

Pulls top traders from FOMO API (fomoapi.io), resolves wallets,
and feeds them into the live watchlist with FOMO_TOP_TRADER roles.

Also supports static curated lists (smart money / KOL) from JSON files
so the engine works without an API key for those sources.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from sme.config import Settings
from sme.models.enums import WalletRole

logger = logging.getLogger(__name__)


@dataclass
class TrackedTrader:
    handle: str
    chain: str  # solana | base | evm
    wallet: str
    roles: list[str] = field(default_factory=list)
    pnl_usd: Optional[float] = None
    rank: Optional[int] = None
    source: str = "unknown"
    user_id: Optional[str] = None


class FomoLeaderboardClient:
    """Client for fomoapi.io leaderboard (requires FOMO_API_KEY)."""

    BASE = "https://api.fomoapi.io"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = (settings.fomo_api_key or "").strip()
        self.enabled = bool(settings.fomo_enabled and self.api_key)
        self._client: Optional[httpx.AsyncClient] = None
        self.status = "ready" if self.enabled else ("missing_key" if settings.fomo_enabled else "disabled")
        self.last_http_status = None

    async def connect(self) -> None:
        if self.enabled:
            self._client = httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def fetch_leaderboard(self, window: str = "24h", limit: int = 50) -> list[TrackedTrader]:
        if not self.enabled or not self._client:
            logger.warning("FOMO leaderboard inactive: %s", self.status)
            return []

        url = f"{self.BASE}/v2/leaderboard/{window}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        params = {"limit": limit}
        try:
            resp = await self._client.get(url, headers=headers, params=params)
            self.last_http_status = resp.status_code
            if resp.status_code in (401, 402, 403, 429):
                self.status = {401: "invalid_key", 402: "credits_exhausted", 403: "forbidden", 429: "rate_limited"}[resp.status_code]
                logger.warning("FOMO leaderboard unavailable: %s", self.status)
                return []
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            self.status = "request_error"
            logger.warning("FOMO leaderboard request failed (%s)", type(exc).__name__)
            return []

        if not isinstance(data, dict):
            self.status = "invalid_response"
            return []
        traders_raw = data.get("traders", data.get("data", []))
        if not isinstance(traders_raw, list):
            self.status = "invalid_response"
            return []
        out: list[TrackedTrader] = []
        for row in traders_raw[:max(1, min(limit, 150))]:
            if not isinstance(row, dict):
                continue
            wallets = row.get("wallets") or {}
            if not isinstance(wallets, dict):
                continue
            handle = str(row.get("handle") or row.get("username") or "")
            sol = wallets.get("solana") or row.get("solana") or row.get("solanaWallet")
            evm = wallets.get("evm") or row.get("evm") or row.get("evmWallet")
            try:
                pnl_raw = row.get("pnlUsd", row.get("pnl"))
                pnl = float(pnl_raw) if pnl_raw is not None else None
                if pnl is not None and not math.isfinite(pnl):
                    pnl = None
            except (ValueError, TypeError, OverflowError):
                pnl = None
            try:
                rank = int(row["rank"]) if row.get("rank") is not None else None
            except (ValueError, TypeError, OverflowError):
                rank = None
            targets = []
            if (isinstance(sol, str) and re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", sol)
                    and not is_placeholder_wallet(sol) and self.settings.solana_enabled):
                targets.append(("solana", sol))
            if (isinstance(evm, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", evm)
                    and int(evm, 16)):
                # A shared EVM address is a monitoring target, not proof of
                # activity or chain-specific performance on each network.
                for chain in ("ethereum", "base", "bsc", "robinhood"):
                    if getattr(self.settings, f"{chain}_enabled"):
                        targets.append((chain, evm.lower()))
            for chain, wallet in targets:
                out.append(TrackedTrader(
                    handle=handle, chain=chain, wallet=wallet,
                    roles=[WalletRole.FOMO_TOP_TRADER.value], pnl_usd=pnl,
                    rank=rank, source=f"fomo_leaderboard_{window}",
                    user_id=row.get("userId") or row.get("user_id"),
                ))
        self.status = "ok" if out else "empty"
        logger.info(
            "FOMO leaderboard %s: %d trader-wallet rows (from %d traders)",
            window,
            len(out),
            len(traders_raw),
        )
        return out



def is_placeholder_wallet(addr: str) -> bool:
    """Reject template / example addresses that must never be watched in production."""
    if not addr:
        return True
    u = addr.upper()
    return (
        "REPLACE" in u
        or "EXAMPLE" in u
        or "YOUR_" in u
        or u.startswith("0XYOUR")
        or "PLACEHOLDER" in u
    )


def load_curated_list(path: Optional[str], default_role: str) -> list[TrackedTrader]:
    """Load static JSON list of smart money / KOL wallets."""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        logger.warning("Curated list not found: %s", path)
        return []
    try:
        data = json.loads(p.read_text())
    except Exception as exc:
        logger.warning("Failed to read curated list %s: %s", path, exc)
        return []

    items = data if isinstance(data, list) else data.get("wallets") or data.get("traders") or []
    out: list[TrackedTrader] = []
    for item in items:
        if isinstance(item, str):
            out.append(
                TrackedTrader(
                    handle="",
                    chain="solana",
                    wallet=item,
                    roles=[default_role],
                    source="curated_file",
                )
            )
            continue
        wallet = item.get("wallet") or item.get("address")
        if not wallet:
            continue
        chain = (item.get("chain") or "solana").lower()
        roles = item.get("roles") or [default_role]
        if isinstance(roles, str):
            roles = [roles]
        out.append(
            TrackedTrader(
                handle=item.get("handle") or item.get("label") or "",
                chain=chain,
                wallet=wallet,
                roles=list(roles),
                source="curated_file",
            )
        )
    out = [t for t in out if t.wallet and not is_placeholder_wallet(t.wallet)]
    logger.info("Loaded %d entries from %s (role=%s)", len(out), path, default_role)
    return out


class TraderRegistry:
    """
    Merges FOMO leaderboard + curated smart money + KOL lists.
    Exposes wallet sets and role lookup for the live poller.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.fomo = FomoLeaderboardClient(settings)
        self._by_wallet: dict[str, TrackedTrader] = {}  # key: chain:wallet lower
        self._last_refresh: Optional[datetime] = None

    async def connect(self) -> None:
        await self.fomo.connect()
        await self.refresh()

    async def disconnect(self) -> None:
        await self.fomo.disconnect()

    def _key(self, chain: str, wallet: str) -> str:
        return f"{chain.lower()}:{wallet.lower() if wallet.startswith('0x') else wallet}"

    def _add(self, t: TrackedTrader) -> None:
        if not t.wallet or is_placeholder_wallet(t.wallet):
            return
        k = self._key(t.chain, t.wallet)
        existing = self._by_wallet.get(k)
        if existing:
            # merge roles
            roles = sorted(set(existing.roles) | set(t.roles))
            existing.roles = roles
            if t.handle and not existing.handle:
                existing.handle = t.handle
            if t.pnl_usd is not None:
                existing.pnl_usd = t.pnl_usd
            if t.rank is not None:
                existing.rank = t.rank
        else:
            self._by_wallet[k] = t

    async def refresh(self) -> int:
        """Rebuild watchlist from curated/FOMO/env/promoted sources.

        Clears the in-memory map first so expired/demoted wallets are dropped.
        LivePoller reads wallets_for_chain() each cycle, so updates apply without
        process restart when the chain is enabled and RPC configured; supervisors
        start in standby even with an empty watchlist.
        """
        self._by_wallet.clear()

        # Curated always available
        for t in load_curated_list(self.settings.smart_money_list_path, WalletRole.SMART_MONEY.value):
            self._add(t)
        for t in load_curated_list(self.settings.kol_list_path, WalletRole.KOL.value):
            self._add(t)

        # FOMO leaderboard
        window = self.settings.fomo_leaderboard_window or "24h"
        limit = int(self.settings.fomo_leaderboard_limit or 50)
        for t in await self.fomo.fetch_leaderboard(window=window, limit=limit):
            self._add(t)

        # Manual env wallets still supported
        for w in (self.settings.watch_wallets_solana or "").split(","):
            w = w.strip()
            if w and not is_placeholder_wallet(w):
                self._add(
                    TrackedTrader(
                        handle="",
                        chain="solana",
                        wallet=w,
                        roles=[WalletRole.SMART_MONEY.value],
                        source="env_watchlist",
                    )
                )
        for chain in ("base", "ethereum", "bsc", "robinhood"):
            for w in (getattr(self.settings, f"watch_wallets_{chain}") or "").split(","):
                w = w.strip()
                if w and not is_placeholder_wallet(w):
                    self._add(TrackedTrader(
                        handle="", chain=chain, wallet=w.lower(),
                        roles=[WalletRole.SMART_MONEY.value], source="env_watchlist",
                    ))

        # Promoted discovery candidates (role DISCOVERED_WATCHLIST, not SMART_MONEY)
        try:
            from sme.db.session import get_session
            from sme.db import repository as repo

            async with get_session() as session:
                promoted = await repo.list_promoted_wallets(session)
            for chain, addr in promoted:
                if not addr or is_placeholder_wallet(addr):
                    continue
                self._add(
                    TrackedTrader(
                        handle="",
                        chain=chain,
                        wallet=addr if chain != "base" else addr.lower(),
                        roles=[WalletRole.DISCOVERED_WATCHLIST.value],
                        source="discovery_promoted",
                    )
                )
        except Exception as exc:
            logger.debug("Promoted candidates not loaded: %s", exc)

        self._last_refresh = datetime.now(timezone.utc)
        logger.info("TraderRegistry: %d unique wallets tracked", len(self._by_wallet))
        return len(self._by_wallet)

    def wallets_for_chain(self, chain: str) -> list[str]:
        chain = chain.lower()
        return [t.wallet for t in self._by_wallet.values() if t.chain == chain]

    def roles_for(self, chain: str, wallet: str) -> list[str]:
        t = self._by_wallet.get(self._key(chain, wallet))
        return list(t.roles) if t else []  # unknown wallet: no SM/KOL label

    def handle_for(self, chain: str, wallet: str) -> str:
        t = self._by_wallet.get(self._key(chain, wallet))
        return t.handle if t else ""

    def summary(self) -> dict[str, Any]:
        by_source: dict[str, int] = {}
        by_role: dict[str, int] = {}
        for t in self._by_wallet.values():
            by_source[t.source] = by_source.get(t.source, 0) + 1
            for r in t.roles:
                by_role[r] = by_role.get(r, 0) + 1
        return {
            "total_wallets": len(self._by_wallet),
            "by_source": by_source,
            "by_role": by_role,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "fomo_enabled": self.fomo.enabled,
            "fomo_status": self.fomo.status,
            "fomo_http_status": self.fomo.last_http_status,
        }
