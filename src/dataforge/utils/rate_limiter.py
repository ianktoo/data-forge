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
            effective_rate = max(self.rate, 1e-6)
            if self.tokens < 1:
                wait = (1 - self.tokens) / effective_rate
                await asyncio.sleep(wait)
                self.tokens = 0
                # The sleep already paid for this token; restart the clock after
                # it, or the next call counts the sleep as refill time and goes
                # through free (which ran crawls at ~2x the configured rate).
                self.last = time.monotonic()
            else:
                self.tokens -= 1
            # jitter ±15 %; only the positive half sleeps, and the clock restarts
            # after it so the next request can't count the jitter as waiting
            # time. Otherwise individual gaps fell below the interval (8.6s
            # against a 10s Crawl-delay) even though the average held.
            jitter = (1 / effective_rate) * random.uniform(-0.15, 0.15)
            if jitter > 0:
                await asyncio.sleep(jitter)
                self.last = time.monotonic()


class RateLimiter:
    """Domain-aware rate limiter.  Instantiate once, share across scrapers."""

    def __init__(self, default_rps: float = 2.0) -> None:
        self._default_rps = default_rps
        self.default_rps = default_rps   # read-only view for callers tightening a domain
        self._buckets: dict[str, _Bucket] = defaultdict(self._make_bucket)

        # Domains whose robots.txt Crawl-delay has been applied to *this* limiter.
        self.crawl_delay_domains: set[str] = set()

    @staticmethod
    def _capacity(rps: float) -> float:
        # Burst of at most one second's worth, and never less than one request,
        # so a Crawl-delay of 10s (0.1 rps) means one request, then one per 10s.
        return max(1.0, rps)

    def _make_bucket(self) -> _Bucket:
        return _Bucket(rate=self._default_rps, capacity=self._capacity(self._default_rps))

    def set_domain_limit(self, domain: str, rps: float) -> None:
        self._buckets[domain] = _Bucket(rate=rps, capacity=self._capacity(rps))

    async def wait(self, url: str) -> None:
        domain = urlparse(url).netloc
        await self._buckets[domain].acquire()
