from sme.adapters.base import ChainAdapter
from sme.adapters.evm import (
    EvmAdapter,
    build_arbitrum_adapter,
    build_avalanche_adapter,
    build_base_adapter,
    build_bsc_adapter,
    build_ethereum_adapter,
    build_robinhood_adapter,
)
from sme.adapters.injective import InjectiveAdapter
from sme.adapters.solana import SolanaAdapter
from sme.adapters.stubs import UnconfiguredAdapter
from sme.adapters.sui import SuiAdapter
from sme.models.enums import ChainId

__all__ = [
    "ChainAdapter",
    "SolanaAdapter",
    "EvmAdapter",
    "SuiAdapter",
    "InjectiveAdapter",
    "build_base_adapter",
    "build_bsc_adapter",
    "build_ethereum_adapter",
    "build_arbitrum_adapter",
    "build_avalanche_adapter",
    "build_robinhood_adapter",
    "UnconfiguredAdapter",
    "build_all_adapters",
]


def build_all_adapters(settings):
    """
    Build adapters for all target chains.

    Status is determined at runtime by health_check():
      - UNCONFIGURED when RPC/LCD not set or chain disabled
      - HEALTHY after successful health RPC
      - LIVE_VERIFIED is never set automatically; requires external evidence
    """
    return {
        ChainId.SOLANA: SolanaAdapter(settings),
        ChainId.BASE: build_base_adapter(settings),
        ChainId.BSC: build_bsc_adapter(settings),
        ChainId.ETHEREUM: build_ethereum_adapter(settings),
        ChainId.ARBITRUM: build_arbitrum_adapter(settings),
        ChainId.AVALANCHE: build_avalanche_adapter(settings),
        ChainId.ROBINHOOD: build_robinhood_adapter(settings),
        ChainId.SUI: SuiAdapter(settings),
        ChainId.INJECTIVE: InjectiveAdapter(settings),
        ChainId.MONAD: UnconfiguredAdapter(ChainId.MONAD),
    }
