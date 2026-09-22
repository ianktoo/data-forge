"""Async HTTPX client with retry, robots.txt respect, and rate limiting."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from dataforge import __version__
from dataforge.utils import RateLimiter, get_logger

log = get_logger("http")

# Identifies the crawler to site operators: real version and a working project URL.
USER_AGENT = f"DataForge/{__version__} (+https://github.com/ianktoo/data-forge; research bot)"

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Status codes worth retrying: the server is telling us "later", not "no".
# A 404/403 is permanent and must NOT be retried — that just wastes the crawl
# budget and hammers the origin.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_AFTER = 120.0   # cap an absurd Retry-After so one URL cannot stall a run

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_ROBOTS_CACHE_MAX = 256
_robots_cache: OrderedDict[str, RobotFileParser] = OrderedDict()
# Domains whose robots.txt Crawl-delay has already been applied to the limiter.
_crawl_delay_applied: set[str] = set()


class RetryableHTTPError(Exception):
    """A transient HTTP status (429/5xx) that should be retried."""

    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url


def _parse_retry_after(value: str | None) -> float:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP-date)."""
    if not value:
        return 0.0
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0.0, (when - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return 0.0


async def _robots(client: httpx.AsyncClient, base_url: str) -> RobotFileParser:
    if base_url in _robots_cache:
        _robots_cache.move_to_end(base_url)
        return _robots_cache[base_url]
    parser = RobotFileParser()
    robots_url = urljoin(base_url, "/robots.txt")
    try:
        r = await client.get(robots_url, timeout=10)
        parser.parse(r.text.splitlines())
        delay = parser.crawl_delay("DataForge") or parser.crawl_delay("*")
        if delay:
            log.debug(f"robots.txt crawl-delay for {base_url}: {delay}s")
    except Exception:
        pass
    _robots_cache[base_url] = parser
    if len(_robots_cache) > _ROBOTS_CACHE_MAX:
        _robots_cache.popitem(last=False)
    return parser


class HTTPClient:
    def __init__(self, limiter: RateLimiter, *, ignore_robots: bool = False) -> None:
        self._limiter = limiter
        self._ignore_robots = ignore_robots
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> HTTPClient:
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            timeout=_TIMEOUT,
            follow_redirects=True,
            http2=True,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    @retry(
        retry=retry_if_exception_type(
            (httpx.TransportError, httpx.TimeoutException, RetryableHTTPError)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def get(self, url: str, *, check_robots: bool = True) -> httpx.Response:
        assert self._client, "Use as async context manager"
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        if check_robots and not self._ignore_robots:
            robots = await _robots(self._client, base)
            if not robots.can_fetch("DataForge", url):
                log.warning(f"robots.txt disallows {url}")
                raise PermissionError(f"robots.txt disallows {url}")
            self._apply_crawl_delay(robots, parsed.netloc)

        await self._limiter.wait(url)
        response = await self._client.get(url)
        log.debug(f"GET {url} → {response.status_code}")

        if response.status_code in _RETRY_STATUS:
            # Honour Retry-After when the server sends one; otherwise fall
            # through to tenacity's exponential backoff.
            wait = min(_parse_retry_after(response.headers.get("retry-after")),
                       _MAX_RETRY_AFTER)
            if wait:
                log.info(f"{response.status_code} for {url} — Retry-After {wait:.0f}s")
                await asyncio.sleep(wait)
            raise RetryableHTTPError(response.status_code, url)

        response.raise_for_status()
        return response

    def _apply_crawl_delay(self, robots: RobotFileParser, domain: str) -> None:
        """Slow the limiter to the domain's declared Crawl-delay.

        Previously this value was read and logged but never acted on, so a site
        asking for one request every 15 seconds (FEMA does) was still crawled at
        the configured default. Only ever slows down, never speeds up.
        """
        if domain in _crawl_delay_applied:
            return
        _crawl_delay_applied.add(domain)
        try:
            delay = robots.crawl_delay("DataForge") or robots.crawl_delay("*")
        except Exception:
            return
        if not delay:
            return
        allowed_rps = 1.0 / float(delay)
        if allowed_rps < self._limiter.default_rps:
            log.info(
                f"robots.txt Crawl-delay for {domain}: {delay}s "
                f"— limiting to {allowed_rps:.3f} req/s"
            )
            self._limiter.set_domain_limit(domain, allowed_rps)

    async def get_safe(self, url: str) -> httpx.Response | None:
        """Return None on any error instead of raising."""
        try:
            return await self.get(url)
        except PermissionError as exc:
            log.debug(f"Blocked by robots.txt: {url} — {exc}")
            return None
        except Exception as exc:
            log.warning(f"Failed {url}: {exc}")
            return None
