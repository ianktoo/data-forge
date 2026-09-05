"""Async HTTPX client with retry, robots.txt respect, and rate limiting."""
from __future__ import annotations

from collections import OrderedDict
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from dataforge.utils import RateLimiter, get_logger

log = get_logger("http")

_HEADERS = {
    "User-Agent": "DataForge/0.1 (+https://github.com/dataforge; research bot)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_ROBOTS_CACHE_MAX = 256
_robots_cache: OrderedDict[str, RobotFileParser] = OrderedDict()


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
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
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

        await self._limiter.wait(url)
        response = await self._client.get(url)
        log.debug(f"GET {url} → {response.status_code}")
        response.raise_for_status()
        return response

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
