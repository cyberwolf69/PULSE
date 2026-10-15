"""Bounded async queue with backpressure / drop policy."""

from __future__ import annotations

import asyncio
import logging
from typing import Generic, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BoundedQueue(Generic[T]):
    def __init__(self, maxsize: int, *, name: str = "queue", drop_on_full: bool = True):
        self._q: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)
        self.name = name
        self.drop_on_full = drop_on_full
        self.dropped = 0
        self.enqueued = 0

    async def put(self, item: T) -> bool:
        """Return True if enqueued, False if dropped."""
        try:
            self._q.put_nowait(item)
            self.enqueued += 1
            return True
        except asyncio.QueueFull:
            if self.drop_on_full:
                self.dropped += 1
                logger.warning("%s full — dropped item (total dropped=%s)", self.name, self.dropped)
                return False
            await self._q.put(item)
            self.enqueued += 1
            return True

    async def get(self) -> T:
        return await self._q.get()

    def task_done(self) -> None:
        self._q.task_done()

    def qsize(self) -> int:
        return self._q.qsize()

    def stats(self) -> dict:
        return {
            "name": self.name,
            "size": self.qsize(),
            "enqueued": self.enqueued,
            "dropped": self.dropped,
        }
