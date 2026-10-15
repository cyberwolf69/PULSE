"""Stub adapters for chains not yet live in this vertical slice."""

from __future__ import annotations

from typing import Optional

from sme.adapters.base import ChainAdapter
from sme.models.enums import ChainId, ProviderHealth
from sme.models.trade import NormalizedTrade


class UnconfiguredAdapter(ChainAdapter):
    def __init__(self, chain: ChainId):
        self.chain = chain
        self._health = ProviderHealth.UNCONFIGURED

    async def connect(self) -> None:
        self._health = ProviderHealth.UNCONFIGURED

    async def disconnect(self) -> None:
        pass

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.UNCONFIGURED

    async def get_latest_height(self) -> Optional[int]:
        return None

    async def parse_swap_from_payload(self, payload: dict) -> Optional[NormalizedTrade]:
        return None

    def limitations(self) -> list[str]:
        return [f"{self.chain.value} is registered but UNCONFIGURED in this release"]
