"""Seedless V2 discovery. Trust factory getPair, never a pool's claim alone.

Sources for the chain-specific allowlist (checked 2026-10-13):
https://developers.uniswap.org/docs/protocols/v2/deployments
https://developer.pancakeswap.finance/contracts/v2/addresses
Coverage: direct ERC-20 economic legs of the transaction sender, EOAs only.
"""
import re

from sme.adapters.evm_decode import UNISWAP_V2_SWAP, ERC20_TRANSFER
from sme.adapters.evm_rpc import EvmRpcError, quantity

FACTORIES = {
    # Official Uniswap v2 deployments, checked 2026-10-15.
    'robinhood': {'0x8bceaa40b9acdfaedf85adf4ff01f5ad6517937f'},
    'ethereum': {'0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f',
                 '0x1097053fd2ea711dad45caccc45eff7548fcb362'},
    'bsc': {'0xca143ce32fe78f1f7019d7d551a6402fc5350c73',
            '0x8909dc15e40173ff4699343b6eb8132c65e18ec6'},
    'base': {'0x8909dc15e40173ff4699343b6eb8132c65e18ec6',
             '0x02a84c1b3bbd7401a5f7fa98a384ebc70bb5749e'},
}
ADDRESS = re.compile(r'0x[0-9a-fA-F]{40}\Z')


def abi_address(raw):
    if not isinstance(raw, str) or not re.fullmatch(r'0x0{24}[0-9a-fA-F]{40}', raw):
        return None
    address = '0x' + raw[-40:].lower()
    return address if int(address, 16) else None


class EvmDiscovery:
    def __init__(self, settings, chain, rpc):
        self.settings, self.chain, self.rpc = settings, chain, rpc

    async def pair(self, pool, height):
        if not isinstance(pool, str) or not ADDRESS.fullmatch(pool):
            return None
        async def call(address, data):
            try:
                return abi_address(await self.rpc('eth_call', [{'to': address, 'data': data}, hex(height)]))
            except EvmRpcError as exc:
                if exc.contract_reverted:
                    return None  # an untrusted emitter may deliberately revert
                raise  # outages must not advance the checkpoint
        factory = await call(pool, '0xc45a0155')  # factory()
        if factory not in FACTORIES[self.chain]:
            return None
        token0 = await call(pool, '0x0dfe1681')
        token1 = await call(pool, '0xd21220a7')
        if not token0 or not token1 or token0 == token1:
            return None
        registered = await call(factory, '0xe6a43905' + token0[2:].zfill(64) + token1[2:].zfill(64))
        if registered != pool.lower():
            return None
        return {'factory': factory, 'pool': pool.lower(), 'tokens': [token0, token1]}

    async def scan(self, height, block_hash):
        logs = await self.rpc('eth_getLogs', [{
            'fromBlock': hex(height), 'toBlock': hex(height), 'topics': [UNISWAP_V2_SWAP],
        }])
        if not isinstance(logs, list):
            raise EvmRpcError('invalid discovery logs')
        if len(logs) > self.settings.evm_discovery_max_logs_per_block:
            raise EvmRpcError('discovery block exceeds configured log budget')
        proofs, receipts, pairs = {}, {}, {}
        for log in logs:
            if (not isinstance(log, dict) or log.get('removed')
                    or log.get('blockHash') != block_hash
                    or quantity(log.get('blockNumber')) != height):
                raise EvmRpcError('inconsistent discovery log')
            topics = log.get('topics') or []
            if (len(topics) != 3 or topics[0] != UNISWAP_V2_SWAP
                    or not re.fullmatch(r'0x[0-9a-fA-F]{256}', log.get('data') or '')):
                continue
            pool = (log.get('address') or '').lower()
            if pool not in pairs:
                pairs[pool] = await self.pair(pool, height)
            proof = pairs[pool]
            if not proof:
                continue
            tx = (log.get('transactionHash') or '').lower()
            if not re.fullmatch(r'0x[0-9a-f]{64}', tx):
                raise EvmRpcError('invalid discovery transaction hash')
            if tx not in receipts:
                receipts[tx] = await self.rpc('eth_getTransactionReceipt', [tx])
            receipt = receipts[tx]
            if (not isinstance(receipt, dict) or receipt.get('blockHash') != block_hash
                    or quantity(receipt.get('blockNumber')) != height
                    or (receipt.get('transactionHash') or '').lower() != tx
                    or not isinstance(receipt.get('logs'), list)):
                raise EvmRpcError('missing or inconsistent discovery receipt')
            if receipt.get('status') == '0x0':
                continue
            if receipt.get('status') != '0x1':
                raise EvmRpcError('missing discovery receipt status')
            identity = ('address', 'topics', 'data', 'logIndex', 'transactionHash', 'blockHash', 'blockNumber')
            if not any(all(lg.get(k) == log.get(k) for k in identity) for lg in receipt['logs']):
                raise EvmRpcError('discovery log absent from receipt')
            wallet = (receipt.get('from') or '').lower()
            if not ADDRESS.fullmatch(wallet) or not int(wallet, 16):
                continue
            code = await self.rpc('eth_getCode', [wallet, hex(height)])
            if not isinstance(code, str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})*', code):
                raise EvmRpcError('invalid account code response')
            if code != '0x':
                continue
            proofs.setdefault((tx, wallet), []).append(proof)
        return proofs, receipts


def has_direct_pair_legs(receipt, wallet, proof):
    """Require payment and receive legs linking this sender to this verified pair."""
    incoming, outgoing = set(), set()
    for log in receipt['logs']:
        topics = log.get('topics') or []
        if len(topics) != 3 or topics[0] != ERC20_TRANSFER:
            continue
        token = (log.get('address') or '').lower()
        if token not in proof['tokens']:
            continue
        if not all(isinstance(t, str) and re.fullmatch(r'0x[0-9a-fA-F]{64}', t) for t in topics[1:]):
            continue
        raw = log.get('data') or ''
        if not re.fullmatch(r'0x[0-9a-fA-F]{64}', raw) or int(raw, 16) <= 0:
            continue
        frm, to = ('0x' + t[-40:].lower() for t in topics[1:])
        if frm == wallet and to == proof['pool']:
            outgoing.add(token)
        if frm == proof['pool'] and to == wallet:
            incoming.add(token)
    return any(a != b for a in incoming for b in outgoing)
