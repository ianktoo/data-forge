"""Per-domain token-bucket rate limiter with jitter."""
from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class _Bucket:
    rate: float          # tokens per second
    capacity: float
    tokens: float = field(init=False)
    last: float = field(init=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.last = time.monotonic()

    async def acquire(self) -> None:
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.last
            self.last = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            if self.tokens < 1:
                wait = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait)
                self.tokens = 0
            else:
                self.tokens -= 1
            # jitter ±15 %
            jitter = (1 / self.rate) * random.uniform(-0.15, 0.15)
            if jitter > 0:
                await asyncio.sleep(jitter)


class RateLimiter:
    """Domain-aware rate limiter.  Instantiate once, share across scrapers."""

    def __init__(self, default_rps: float = 2.0) -> None:
        self._default_rps = default_rps
        self._buckets: dict[str, _Bucket] = defaultdict(self._make_bucket)

    def _make_bucket(self) -> _Bucket:
        return _Bucket(rate=self._default_rps, capacity=self._default_rps * 2)

    def set_domain_limit(self, domain: str, rps: float) -> None:
        self._buckets[domain] = _Bucket(rate=rps, capacity=rps * 2)

    async def wait(self, url: str) -> None:
        domain = urlparse(url).netloc
        await self._buckets[domain].acquire()
