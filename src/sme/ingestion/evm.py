"""Confirmed EVM polling with durable per-chain checkpoints and replay on failure.

One poller process per database is supported. submit_fn must acknowledge durable
processing, not merely queue insertion. Deep reorgs stop progress for operator
reconciliation; this module does not reverse already-persisted trades/signals.
"""

import logging
import re

from sme.adapters.evm_decode import ERC20_TRANSFER, decode_evm_swap_for_wallet, decode_result_to_payload
from sme.adapters.evm_rpc import EvmRpcError, quantity
from sme.db.models import EvmPollCheckpoint
from sme.db.session import get_session

logger = logging.getLogger(__name__)
CHAIN_IDS = {"ethereum": 1, "bsc": 56, "base": 8453, "robinhood": 4663}
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")


class EvmPoller:
    def __init__(self, settings, chain, rpc, wallets, roles, submit_fn):
        self.settings, self.chain, self.rpc = settings, chain, rpc
        self.wallets, self.roles, self.submit_fn = wallets, roles, submit_fn
        self.state = "unknown"
        self.last_error = None
        self.decode_rejections = {}

    async def _header(self, height):
        block = await self.rpc("eth_getBlockByNumber", [hex(height), False])
        if (not isinstance(block, dict) or quantity(block.get("number")) != height
                or not block.get("hash") or not block.get("parentHash")):
            raise EvmRpcError("missing or inconsistent block")
        quantity(block.get("timestamp"))
        return block

    async def _checkpoint(self):
        async with get_session() as session:
            row = await session.get(EvmPollCheckpoint, self.chain)
            if row is None:
                return None
            if row.chain_id != CHAIN_IDS[self.chain]:
                raise EvmRpcError("checkpoint chain identity mismatch")
            if row.blocked_reason:
                self.state = "reorg_blocked"
                raise EvmRpcError("persisted reorg block; operator reconciliation required")
            checkpoint = row.block_number, row.block_hash
            pending = row.pending_block_number, row.pending_block_hash
        # The journal is written before the first durable submission. Even a
        # crash mid-block cannot hide an orphaned partial block after restart.
        if pending[0] is not None:
            if (await self._header(pending[0]))["hash"] != pending[1]:
                await self._block_reorg("pending_block_hash_changed")
                raise EvmRpcError("pending block reorg; reconcile persisted trades")
        return checkpoint

    async def _begin_block(self, height, block_hash):
        async with get_session() as session:
            row = await session.get(EvmPollCheckpoint, self.chain)
            if row is None:
                row = EvmPollCheckpoint(chain=self.chain, chain_id=CHAIN_IDS[self.chain],
                                        block_number=-1, block_hash="")
                session.add(row)
            row.pending_block_number, row.pending_block_hash = height, block_hash

    async def _block_reorg(self, reason):
        self.state = "reorg_blocked"
        async with get_session() as session:
            row = await session.get(EvmPollCheckpoint, self.chain)
            if row is None:
                row = EvmPollCheckpoint(chain=self.chain, chain_id=CHAIN_IDS[self.chain],
                                        block_number=-1, block_hash="")
                session.add(row)
            row.blocked_reason = reason

    async def _save(self, number, block_hash):
        async with get_session() as session:
            row = await session.get(EvmPollCheckpoint, self.chain)
            if row is None:
                row = EvmPollCheckpoint(chain=self.chain, chain_id=CHAIN_IDS[self.chain])
                session.add(row)
            row.block_number, row.block_hash = number, block_hash
            row.pending_block_number, row.pending_block_hash = None, None

    async def poll_once(self):
        # Verify on every cycle, including reconnect/failover behind the same URL.
        if quantity(await self.rpc("eth_chainId", [])) != CHAIN_IDS[self.chain]:
            self.state = "chain_mismatch"
            raise EvmRpcError("RPC chain identity mismatch")
        safe_head = quantity(await self.rpc("eth_blockNumber", [])) - self.settings.evm_confirmation_depth
        if safe_head < 0:
            return
        checkpoint = await self._checkpoint()
        if checkpoint is not None:
            last, last_hash = checkpoint
            if safe_head < last:
                raise EvmRpcError("RPC head behind checkpoint")
            if last >= 0:
                previous = await self._header(last)
                if previous["hash"] != last_hash:
                    await self._block_reorg("checkpoint_hash_changed")
                    raise EvmRpcError("checkpoint reorg; operator reconciliation required")
            else:
                last_hash = None
        else:
            start = getattr(self.settings, f"{self.chain}_start_block")
            last = safe_head if start is None else start - 1
            if last > safe_head:
                return
            last_hash = None
            if last >= 0:
                last_hash = (await self._header(last))["hash"]
                await self._save(last, last_hash)

        raw_wallets = self.wallets()
        if any(not ADDRESS.fullmatch(w) for w in raw_wallets):
            raise EvmRpcError("invalid watch wallet address")
        wallets = sorted({w.lower() for w in raw_wallets})
        discover = self.settings.discovery_enabled and self.settings.evm_discovery_enabled
        if not wallets and not discover:
            self.state = "standby"
            return
        end = min(safe_head, last + self.settings.evm_max_blocks_per_cycle)
        for height in range(last + 1, end + 1):
            block = await self._header(height)
            if last_hash is not None and block["parentHash"] != last_hash:
                await self._block_reorg("parent_hash_changed")
                raise EvmRpcError("block parent mismatch")
            # Gather both directions before emitting anything. Any incomplete RPC
            # response holds the checkpoint so the entire block can be retried.
            candidates = set()
            for wallet in wallets:
                topic = "0x" + wallet[2:].zfill(64)
                for topics in ([ERC20_TRANSFER, topic], [ERC20_TRANSFER, None, topic]):
                    logs = await self.rpc("eth_getLogs", [{
                        "fromBlock": hex(height), "toBlock": hex(height), "topics": topics,
                    }])
                    if not isinstance(logs, list):
                        raise EvmRpcError("invalid log response")
                    for log in logs:
                        if (not isinstance(log, dict) or log.get("removed")
                                or log.get("blockHash") != block["hash"]
                                or quantity(log.get("blockNumber")) != height
                                or not log.get("transactionHash")):
                            raise EvmRpcError("inconsistent or removed log")
                        candidates.add((log["transactionHash"].lower(), wallet))
            receipts, payloads = {}, []
            discovered = {}
            if discover:
                from sme.discovery.evm import EvmDiscovery, has_direct_pair_legs
                discovered, receipts = await EvmDiscovery(self.settings, self.chain, self.rpc).scan(height, block["hash"])
                candidates.update(discovered)

            for tx_hash, wallet in sorted(candidates):
                if tx_hash not in receipts:
                    receipt = await self.rpc("eth_getTransactionReceipt", [tx_hash])
                else:
                    receipt = receipts[tx_hash]
                if (not isinstance(receipt, dict)
                        or (receipt.get("transactionHash") or "").lower() != tx_hash
                        or receipt.get("blockHash") != block["hash"]
                        or quantity(receipt.get("blockNumber")) != height
                        or receipt.get("status") not in ("0x0", "0x1")
                        or not isinstance(receipt.get("logs"), list)):
                    raise EvmRpcError("missing or inconsistent receipt")
                for log in receipt["logs"]:
                    if (log.get("removed") or log.get("blockHash") != block["hash"]
                            or quantity(log.get("blockNumber")) != height
                            or (log.get("transactionHash") or "").lower() != tx_hash):
                        raise EvmRpcError("inconsistent receipt log")
                receipts[tx_hash] = receipt
                tx_index = quantity(receipts[tx_hash].get("transactionIndex"))
                result = decode_evm_swap_for_wallet(receipts[tx_hash], wallet=wallet, chain=self.chain)
                if not result.ok:
                    self.decode_rejections[result.reason] = self.decode_rejections.get(result.reason, 0) + 1
                    continue
                proof = None
                if (tx_hash, wallet) in discovered and wallet not in wallets:
                    proof = next((p for p in discovered[(tx_hash, wallet)]
                                  if result.evidence.get("swap_pool") == p["pool"]
                                  and result.token in p["tokens"] and result.quote_token in p["tokens"]
                                  and has_direct_pair_legs(receipt, wallet, p)), None)
                    if proof is None:
                        continue
                payload = decode_result_to_payload(result, timestamp=quantity(block["timestamp"]), chain_hint=self.chain)
                payload.update({
                    "wallet_roles": self.roles(wallet, self.chain),
                    "_source": "live_poller", "chain_id": CHAIN_IDS[self.chain],
                    "confirmations": self.settings.evm_confirmation_depth,
                    "blockHash": block["hash"],
                    "transactionIndex": tx_index,
                })
                if proof is not None:
                    payload["_discovery_candidate"] = proof
                    payload["wallet_roles"] = []
                payloads.append(payload)
            if (await self._header(height))["hash"] != block["hash"]:
                raise EvmRpcError("block changed during scan")
            await self._begin_block(height, block["hash"])
            # Positions must observe chain transaction order, not hash order.
            payloads.sort(key=lambda p: (p["transactionIndex"], p["swap"]["log_index"], p["swap"]["wallet"]))
            for payload in payloads:
                if not await self.submit_fn(self.chain, payload):
                    raise EvmRpcError("ingest not durably acknowledged")
            if (await self._header(height))["hash"] != block["hash"]:
                await self._block_reorg("block_changed_during_processing")
                raise EvmRpcError("block changed during processing; reconcile persisted trades")
            await self._save(height, block["hash"])
            last_hash = block["hash"]
        self.state, self.last_error = "ok", None
