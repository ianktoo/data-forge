"""Async HTTPX client with retry, robots.txt respect, and rate limiting."""
from __future__ import annotations

import asyncio
import functools
import ssl
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

@functools.cache
def ssl_context() -> ssl.SSLContext:
    """TLS context that verifies against the operating system's trust store.

    Many sites send an incomplete certificate chain (leaf only, no
    intermediate) or chain to a root missing from certifi's bundle. Browsers
    cope; plain Python did not, so e.g. www.uonbi.ac.ke and www.health.go.ke
    failed with CERTIFICATE_VERIFY_FAILED. truststore (as pip uses) verifies
    with the OS store, which on Windows and macOS also fetches missing
    intermediates. Verification is never disabled.
    """
    try:
        import truststore
    except ImportError:
        return ssl.create_default_context()
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


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


async def _robots(
    client: httpx.AsyncClient, base_url: str, limiter: RateLimiter | None = None
) -> RobotFileParser:
    """Fetch and parse a site's robots.txt, following RFC 9309 on failures:
    a 4xx means no rules (allow all); a 5xx, 429 or network error means the
    rules are unknown, so everything is disallowed rather than guessed at.
    """
    if base_url in _robots_cache:
        _robots_cache.move_to_end(base_url)
        return _robots_cache[base_url]
    parser = RobotFileParser()
    robots_url = urljoin(base_url, "/robots.txt")
    try:
        if limiter is not None:
            # The robots.txt request is a request to the site like any other.
            await limiter.wait(robots_url)
        r = await client.get(robots_url, timeout=10)
        if r.status_code >= 500 or r.status_code == 429:
            log.warning(
                f"robots.txt for {base_url} returned {r.status_code}; "
                "treating the whole site as disallowed (RFC 9309)"
            )
            parser.disallow_all = True
        elif r.status_code >= 400:
            parser.allow_all = True
        else:
            parser.parse(r.text.splitlines())
            delay = parser.crawl_delay("DataForge") or parser.crawl_delay("*")
            if delay:
                log.debug(f"robots.txt crawl-delay for {base_url}: {delay}s")
    except Exception as exc:
        log.warning(
            f"robots.txt for {base_url} unreachable ({type(exc).__name__}); "
            "treating the whole site as disallowed (RFC 9309)"
        )
        parser.disallow_all = True
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
            verify=ssl_context(),
            timeout=_TIMEOUT,
            follow_redirects=True,
            http2=True,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    def has_crawl_delay(self, domain: str) -> bool:
        """True if this client is honouring a robots.txt Crawl-delay for *domain*."""
        return self._limiter.has_domain_limit(domain)

    async def prepare(self, url: str, *, check_robots: bool = True) -> None:
        """Everything a request to *url* must pass before it is sent: the
        robots.txt check (raising PermissionError if disallowed), the site's
        Crawl-delay, and the rate limiter. Used by get() and by anything that
        fetches a URL outside this client, such as the Playwright fallback.
        """
        assert self._client, "Use as async context manager"
        parsed = urlparse(url)
        if check_robots and not self._ignore_robots:
            robots = await _robots(self._client, f"{parsed.scheme}://{parsed.netloc}", self._limiter)
            if not robots.can_fetch("DataForge", url):
                log.warning(f"robots.txt disallows {url}")
                raise PermissionError(f"robots.txt disallows {url}")
            self._apply_crawl_delay(robots, parsed.netloc)
        await self._limiter.wait(url)

    @retry(
        retry=retry_if_exception_type(
            (httpx.TransportError, httpx.TimeoutException, RetryableHTTPError)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def get(self, url: str, *, check_robots: bool = True) -> httpx.Response:
        await self.prepare(url, check_robots=check_robots)
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
        # Tracked on the limiter, not per process: each stage (discovery,
        # scraping, streaming) builds its own limiter, and a process-wide flag
        # meant only the first one ever received the delay.
        if domain in self._limiter.crawl_delay_domains:
            return
        self._limiter.crawl_delay_domains.add(domain)
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
