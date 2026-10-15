"""
Live automatic ingestion.

Watches configured wallets via RPC polling and feeds parsed swaps
into the existing ingest queue → signal engine → Telegram/Discord.

This is the path that makes `sme run` produce alerts without manual inject.
"""

from __future__ import annotations

import asyncio
import random
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import httpx

from sme.config import Settings
from sme.models.enums import (
    ChainId,
    ConfirmationState,
    EnrichmentStatus,
    ProviderHealth,
    TradeDirection,
)

logger = logging.getLogger(__name__)

WSOL = "So11111111111111111111111111111111111111112"
# Quote-like mints we treat as "sold/bought against" for direction
QUOTE_MINTS = {
    WSOL,
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


class LivePoller:
    """
    Polls watched wallets for new transactions and emits structured
    payloads into the engine ingest queue.
    """

    def __init__(self, settings: Settings, submit_fn, registry=None, evm_submit_fn=None):
        """
        submit_fn: async (chain: str, payload: dict) -> bool
        registry: TraderRegistry — FOMO / smart money / KOL wallets
        """
        self.settings = settings
        self.submit_fn = submit_fn
        self.evm_submit_fn = evm_submit_fn or submit_fn
        self.evm_pollers = {}
        self.registry = registry
        self._client: Optional[httpx.AsyncClient] = None
        self._running = False
        self._seen_sigs: set[str] = set()
        self._tasks: list[asyncio.Task] = []
        # Per-wallet last signature cursor
        self._cursors: dict[str, Optional[str]] = {}
        # RPC health for diagnostics (never stores secrets)
        self.rpc_state: str = "unknown"  # unknown|ok|rate_limited|timeout|error
        self.rpc_last_error: Optional[str] = None
        self.rpc_failures: int = 0

    def _solana_wallets(self) -> list[str]:
        if self.registry:
            w = self.registry.wallets_for_chain("solana")
            if w:
                return w
        raw = self.settings.watch_wallets_solana or ""
        return [x.strip() for x in raw.split(",") if x.strip()]

    def _evm_wallets(self, chain: str) -> list[str]:
        wallets = list(self.registry.wallets_for_chain(chain)) if self.registry else []
        wallets.extend((getattr(self.settings, f"watch_wallets_{chain}") or "").split(","))
        return sorted({w.strip().lower() for w in wallets if w.strip()})

    def _base_wallets(self) -> list[str]:
        return self._evm_wallets("base")

    def _has_task(self, name: str) -> bool:
        return any((not t.done()) and t.get_name() == name for t in self._tasks)

    async def start(self) -> None:
        """Start chain poller supervisors.

        Solana/EVM supervisors start when the chain is enabled and RPC is set,
        even if the watchlist is empty. The loop idles until wallets appear
        (env, registry refresh, or discovery promote) so a process restart is
        not required for the first wallet.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds)
        self._running = True
        interval = max(3.0, float(self.settings.poll_interval_seconds))

        if self.settings.solana_enabled and self.settings.solana_rpc_http:
            if not self._has_task("solana-poller"):
                self._tasks.append(
                    asyncio.create_task(self._solana_loop(interval), name="solana-poller")
                )
            n = len(self._solana_wallets())
            if n:
                logger.info(
                    "Live Solana poller ON — watching %d wallet(s), interval=%.1fs",
                    n,
                    interval,
                )
            else:
                logger.info(
                    "Live Solana poller standby (no wallets yet) — will poll when "
                    "watchlist/registry gains addresses; interval=%.1fs",
                    interval,
                )
        elif self.settings.solana_enabled:
            logger.warning(
                "Solana enabled but SOLANA_RPC_HTTP is missing — poller not started."
            )

        from sme.ingestion.evm import EvmPoller, CHAIN_IDS
        for chain in CHAIN_IDS:
            if not getattr(self.settings, f"{chain}_enabled"):
                continue
            if not getattr(self.settings, f"{chain}_rpc_http"):
                logger.warning("%s enabled but RPC missing", chain)
                continue
            name = f"{chain}-poller"
            if not self._has_task(name):
                async def rpc(method, params, chain=chain):
                    return await self._rpc_evm(chain, method, params)
                self.evm_pollers[chain] = EvmPoller(
                    self.settings, chain, rpc,
                    lambda chain=chain: self._evm_wallets(chain),
                    self._roles_for, self.evm_submit_fn,
                )
                self._tasks.append(asyncio.create_task(self._evm_loop(chain, interval), name=name))
                logger.info("Live %s poller enabled (%d wallets)", chain, len(self._evm_wallets(chain)))

        if not self._tasks:
            logger.warning(
                "No live pollers started. Enable a chain and set RPC endpoints "
                "for automatic signal alerts."
            )

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._client:
            await self._client.aclose()
            self._client = None

    def _rpc_host_label(self) -> str:
        """Host-only label for logs (no path/query/credentials)."""
        from urllib.parse import urlparse
        raw = self.settings.solana_rpc_http or ""
        try:
            return urlparse(raw).hostname or "solana-rpc"
        except Exception:
            return "solana-rpc"

    async def _rpc_solana(self, method: str, params: list[Any]) -> Any:
        """JSON-RPC with bounded retries on 429/5xx/timeout. No aggressive multi-layer retry."""
        assert self._client and self.settings.solana_rpc_http
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        max_retries = max(0, min(3, self.settings.provider_max_retries))
        base = float(getattr(self.settings, "provider_backoff_base", 0.5) or 0.5)
        host = self._rpc_host_label()
        last_exc: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            try:
                from sme.adapters.rpc_pacing import rpc_post, is_rate_limited, retry_after_seconds
                resp = await rpc_post(self._client, self.settings.solana_rpc_http, payload, self.settings)
                if is_rate_limited(resp):
                    self.rpc_state = "rate_limited"
                    self.rpc_last_error = f"429 on {method}"
                    self.rpc_failures += 1
                    if attempt >= max_retries:
                        raise RuntimeError(f"Solana RPC rate limited ({host}) method={method}")
                    delay = max(retry_after_seconds(resp), base * (2 ** attempt) + random.uniform(0, 0.25))
                    logger.warning(
                        "Solana RPC 429 host=%s method=%s attempt=%s sleep=%.2fs",
                        host, method, attempt + 1, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                if resp.status_code >= 500:
                    self.rpc_state = "error"
                    self.rpc_last_error = f"HTTP {resp.status_code} on {method}"
                    self.rpc_failures += 1
                    if attempt >= max_retries:
                        resp.raise_for_status()
                    delay = base * (2 ** attempt) + random.uniform(0, 0.25)
                    logger.warning(
                        "Solana RPC HTTP %s host=%s method=%s attempt=%s",
                        resp.status_code, host, method, attempt + 1,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    self.rpc_state = "error"
                    self.rpc_last_error = f"rpc_error on {method}"
                    self.rpc_failures += 1
                    raise RuntimeError("Solana RPC error")
                self.rpc_state = "ok"
                self.rpc_last_error = None
                return data.get("result")
            except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                last_exc = exc
                self.rpc_state = "timeout"
                self.rpc_last_error = f"timeout on {method}"
                self.rpc_failures += 1
                if attempt >= max_retries:
                    raise
                delay = base * (2 ** attempt) + random.uniform(0, 0.25)
                logger.warning(
                    "Solana RPC timeout host=%s method=%s attempt=%s sleep=%.2fs",
                    host, method, attempt + 1, delay,
                )
                await asyncio.sleep(delay)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                self.rpc_state = "error"
                self.rpc_last_error = f"HTTP {exc.response.status_code} on {method}"
                self.rpc_failures += 1
                raise
            except RuntimeError:
                raise
            except Exception as exc:
                last_exc = exc
                self.rpc_state = "error"
                self.rpc_last_error = f"{type(exc).__name__} on {method}"
                self.rpc_failures += 1
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Solana RPC failed method={method}")

    async def _rpc_evm(self, chain, method, params):
        from sme.adapters.evm_rpc import evm_rpc
        assert self._client
        return await evm_rpc(self._client, getattr(self.settings, f"{chain}_rpc_http"),
                             method, params, self.settings)

    async def _rpc_base(self, method, params):
        return await self._rpc_evm("base", method, params)

    async def _prime_solana_wallet(self, wallet: str) -> None:
        """Set cursor to newest sig so we only process txs after this wallet is watched."""
        if wallet in self._cursors:
            return
        try:
            sigs = await self._rpc_solana(
                "getSignaturesForAddress",
                [wallet, {"limit": 1, "commitment": self.settings.solana_commitment}],
            )
            if sigs:
                self._cursors[wallet] = sigs[0].get("signature")
                self._seen_sigs.add(sigs[0].get("signature", ""))
            else:
                self._cursors[wallet] = None  # marked primed, no history
        except Exception as exc:
            logger.warning("Solana cursor prime failed for %s: %s", wallet[:8], exc)
            # Do not mark primed on failure — retry next cycle

    async def _solana_loop(self, interval: float) -> None:
        """Supervisor loop: idle when watchlist empty; poll when wallets appear."""
        while self._running:
            try:
                wallets = self._solana_wallets()
                if not wallets:
                    await asyncio.sleep(interval)
                    continue
                for wallet in wallets:
                    await self._prime_solana_wallet(wallet)
                for wallet in wallets:
                    if wallet in self._cursors:
                        await self._poll_solana_wallet(wallet)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Solana poll cycle error: %s", exc)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def _poll_solana_wallet(self, wallet: str) -> None:
        opts: dict[str, Any] = {
            "limit": int(self.settings.poll_tx_limit),
            "commitment": self.settings.solana_commitment,
        }
        until = self._cursors.get(wallet)
        # After first run we use 'until' inverted logic: fetch newest, skip seen
        try:
            sigs = await self._rpc_solana("getSignaturesForAddress", [wallet, opts])
        except Exception as exc:
            logger.warning("getSignaturesForAddress %s: %s", wallet[:8], exc)
            return

        if not sigs:
            return

        # Process oldest-first among the batch so order is natural
        for entry in reversed(sigs):
            sig = entry.get("signature")
            if not sig or sig in self._seen_sigs:
                continue
            if entry.get("err") is not None:
                self._seen_sigs.add(sig)
                continue

            try:
                tx = await self._rpc_solana(
                    "getTransaction",
                    [
                        sig,
                        {
                            "encoding": "jsonParsed",
                            "commitment": self.settings.solana_commitment,
                            "maxSupportedTransactionVersion": 1,
                        },
                    ],
                )
            except Exception as exc:
                logger.debug("getTransaction %s failed: %s", sig[:12], exc)
                continue

            self._seen_sigs.add(sig)
            if len(self._seen_sigs) > 50_000:
                # bound memory
                self._seen_sigs = set(list(self._seen_sigs)[-25_000:])

            payload = self._solana_tx_to_payload(wallet, sig, tx)
            if payload:
                # Mark offline allow not needed — this is live path
                ok = await self.submit_fn("solana", payload)
                if ok:
                    logger.info(
                        "LIVE Solana trade queued wallet=%s sig=%s dir=%s token=%s",
                        wallet[:8],
                        sig[:12],
                        payload.get("swap", {}).get("direction"),
                        payload.get("swap", {}).get("token_symbol")
                        or payload.get("swap", {}).get("token_mint", "")[:8],
                    )

        # Advance cursor to newest
        if sigs:
            self._cursors[wallet] = sigs[0].get("signature")

    def _solana_tx_to_payload(
        self, wallet: str, signature: str, tx: Optional[dict]
    ) -> Optional[dict]:
        """
        Build structured swap payload from jsonParsed transaction via shared decoder.
        """
        from sme.adapters.solana_decode import decode_solana_swap, decode_result_to_swap_payload

        if not tx:
            return None
        result = decode_solana_swap(tx, wallet=wallet, signature=signature)
        if not result.ok:
            logger.debug(
                "Solana tx unclassified sig=%s reason=%s",
                signature[:12],
                result.reason,
            )
            return None
        payload = decode_result_to_swap_payload(result)
        if not payload:
            return None
        payload["wallet_roles"] = self._roles_for(wallet, "solana")
        payload["_source"] = "live_poller"
        return payload

    def _roles_for(self, wallet: str, chain: str = "solana") -> list[str]:
        if self.registry:
            roles = self.registry.roles_for(chain, wallet)
            if roles:
                return roles
        raw = self.settings.watch_wallet_roles or ""
        roles = ["SMART_MONEY"]
        for part in raw.split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            addr, r = part.split(":", 1)
            if addr.strip() == wallet or addr.strip().lower() == wallet.lower():
                roles = [x.strip() for x in r.replace("|", ",").split(",") if x.strip()]
                break
        return roles

    async def _evm_loop(self, chain, interval):
        from sme.adapters.evm_rpc import EvmRpcError
        poller = self.evm_pollers[chain]
        while self._running:
            try:
                await poller.poll_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                # Unknown exceptions may contain URLs/provider secrets.
                poller.last_error = str(exc) if isinstance(exc, EvmRpcError) else type(exc).__name__
                if poller.state not in ("reorg_blocked", "chain_mismatch"):
                    poller.state = "error"
                logger.warning("%s poll cycle failed (%s)", chain, poller.last_error)
                if poller.state == "reorg_blocked":
                    return  # explicit operator reconciliation before restart
            await asyncio.sleep(interval)

    async def _base_loop(self, interval):
        await self._evm_loop("base", interval)
