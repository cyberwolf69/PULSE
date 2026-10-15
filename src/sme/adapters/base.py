"""Chain adapter interface — all chains implement this contract."""

from __future__ import annotations

import abc
from typing import AsyncIterator, Optional

from sme.models.enums import ChainId, ProviderHealth
from sme.models.trade import NormalizedTrade


class ChainAdapter(abc.ABC):
    """
    Chain-agnostic adapter boundary.

    Core signal engine never sees raw RPC payloads — only NormalizedTrade.
    """

    chain: ChainId

    @abc.abstractmethod
    async def connect(self) -> None:
        """Establish provider connection / validate config."""

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """Graceful teardown."""

    @abc.abstractmethod
    async def health_check(self) -> ProviderHealth:
        """Actual provider interaction. Not a static flag."""

    @abc.abstractmethod
    async def get_latest_height(self) -> Optional[int]:
        """Slot / block number or None if unavailable."""

    @abc.abstractmethod
    async def parse_swap_from_payload(self, payload: dict) -> Optional[NormalizedTrade]:
        """
        Parse a provider/transaction payload into NormalizedTrade.

        Must reject:
        - failed transactions
        - plain transfers without swap evidence
        - insufficient context
        """

    async def subscribe_swaps(self) -> AsyncIterator[NormalizedTrade]:
        """
        Live subscription stream (WebSocket or polling).

        Default raises NotImplementedError — override in live adapters.
        Yields only validated NormalizedTrade instances.
        """
        raise NotImplementedError(f"{self.chain.value} live subscription not implemented")
        yield  # pragma: no cover

    def supported_protocols(self) -> list[str]:
        return []

    def limitations(self) -> list[str]:
        return []
