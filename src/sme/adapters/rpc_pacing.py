"""Process-local Alchemy pacing shared by all chains, clients and API keys.

Alchemy throughput is account-scoped; conservatively share one gate even when
several keys are configured. Multiple processes need an external coordinator.
"""
import asyncio
import math
import time
import weakref
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit


def retry_after_seconds(response):
    raw = response.headers.get('Retry-After')
    if not isinstance(raw, str):
        return 0.0
    try:
        seconds = float(raw)
    except ValueError:
        try:
            date = parsedate_to_datetime(raw)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 0.0
    return max(0.0, seconds) if math.isfinite(seconds) else 0.0


def is_rate_limited(response):
    if response.status_code == 429:
        return True
    if response.status_code != 200:
        return False
    try:
        body = response.json()
        error = body.get('error') if isinstance(body, dict) else None
        return isinstance(error, dict) and error.get('code') in (429, -32005, -32016)
    except (ValueError, TypeError):
        return False


class AlchemyGate:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.next_at = 0.0
        self.penalty = 0.0
        self.requests = 0
        self.rate_limits = 0

    async def post(self, client, url, payload, settings):
        # Keep the gate through the response so a 429 pauses queued peers too.
        async with self.lock:
            await asyncio.sleep(max(0.0, self.next_at - time.monotonic()))
            self.next_at = time.monotonic() + settings.alchemy_min_interval_seconds
            self.requests += 1
            response = await client.post(url, json=payload)
            if is_rate_limited(response):
                self.rate_limits += 1
                self.penalty = min(60.0, max(settings.alchemy_rate_limit_cooldown_seconds,
                                            self.penalty * 2))
                self.next_at = max(self.next_at, time.monotonic() +
                                   max(self.penalty, retry_after_seconds(response)))
            elif response.status_code == 200:
                self.penalty = 0.0
            return response


_gates = weakref.WeakKeyDictionary()


def pacing_snapshot():
    try:
        gate = _gates.get(asyncio.get_running_loop())
    except RuntimeError:
        gate = None
    return {'scope': 'process_event_loop', 'requests': gate.requests if gate else 0,
            'rate_limits': gate.rate_limits if gate else 0}


async def rpc_post(client, url, payload, settings):
    host = (urlsplit(url).hostname or '').lower()
    if not host.endswith('.g.alchemy.com'):
        return await client.post(url, json=payload)
    loop = asyncio.get_running_loop()
    gate = _gates.get(loop)
    if gate is None:
        gate = _gates[loop] = AlchemyGate()
    return await gate.post(client, url, payload, settings)
