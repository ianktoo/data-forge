"""Best-first web crawler — fallback when no sitemap is available.

SPA detection: if a fetched page has very few links (< _SPA_LINK_THRESHOLD) but
a non-trivial body, it is likely a Single-Page Application that renders in JS.
We attempt a Playwright headless fetch as a fallback if the library is installed.
If Playwright is not installed the page is still recorded — it just won't yield
further links from that URL.
"""
from __future__ import annotations

import heapq
import itertools
import re
from collections.abc import Callable
from urllib.parse import parse_qsl, urlparse

from dataforge.utils import get_logger
from dataforge.utils.url_sanitiser import canonical_key, is_page_url, sanitise_many

from .extractor import extract, extract_links
from .http import USER_AGENT
from .sitemap import filter_urls

log = get_logger("crawler")

# A page with fewer discovered links than this, but more than _SPA_MIN_BODY_LEN
# characters of body text, is treated as a potential SPA and retried with Playwright.
_SPA_LINK_THRESHOLD = 3
# With a URL filter, at most this many fetches per page kept (see crawl()).
_FETCH_CAP_FACTOR = 4
_SPA_MIN_BODY_LEN = 500


# Resource types a JS render doesn't need; blocking them keeps one render
# closer to one request's worth of load on the site.
_BLOCKED_RESOURCES = frozenset({"image", "media", "font"})


async def _playwright_fetch(client, url: str) -> str | None:
    """Fetch a URL with a headless Chromium browser and return the rendered HTML.

    The browser makes its own requests, so it goes through the same gate as
    everything else first: robots.txt, the rate limiter, DataForge's
    User-Agent. A browser page load also triggers many sub-requests (scripts,
    API calls) that can't be spaced one per Crawl-delay, so rendering is
    skipped entirely on a site that declares one.

    Returns None if Playwright is not installed, the URL may not be fetched,
    or the fetch fails. Playwright is an optional dependency.
    """
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        log.debug("Playwright not installed — skipping JS render for SPA detection")
        return None
    try:
        await client.prepare(url)
    except PermissionError:
        return None
    if client.has_crawl_delay(urlparse(url).netloc):
        log.info(f"Not rendering {url} with a browser: the site declares a Crawl-delay")
        return None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page(user_agent=USER_AGENT)
            await page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in _BLOCKED_RESOURCES
                else route.continue_(),
            )
            await page.goto(url, wait_until="networkidle", timeout=15_000)
            html = await page.content()
            await browser.close()
            return html
    except Exception as exc:
        log.debug(f"Playwright fetch failed for {url}: {exc}")
        return None


# Likely crawl traps: endless or low-value URL spaces. Demoted within their
# depth (#64), never forbidden: a budget with room left still reaches them.
_TRAP_PATH = re.compile(
    r"/(page|p)/\d+/?$"                          # pagination: /page/2
    r"|/(19|20)\d{2}/\d{1,2}(/\d{1,2})?/?$"       # date archives: /2024/05(/13)
    r"|/(calendar|events?/(19|20)\d{2})"          # calendars
    r"|/(search|login|log-in|logout|signin|sign-in|register|signup|cart|checkout)\b"
    r"|/(print|feed|rss|tag|tags)(/|$)",
    re.IGNORECASE,
)
_TRAP_QUERY_KEYS = frozenset({
    "page", "p", "pg", "start", "offset", "limit", "sort", "order", "orderby",
    "dir", "filter", "q", "query", "search", "s", "view", "print", "format",
    "month", "year", "date", "replytocom", "share", "action",
})


def _is_trap(url: str) -> bool:
    parsed = urlparse(url)
    if _TRAP_PATH.search(parsed.path):
        return True
    keys = {k.lower() for k, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    return bool(keys & _TRAP_QUERY_KEYS)


class _Frontier:
    """The crawl frontier: a priority queue of URLs, each queued at most once.

    A binary heap (``heapq``, O(log n) per push/pop) keyed by
    ``(depth, is_trap, filtered_out, not_in_content, path_segments, order)``
    (#64): depth first, so ``max_depth`` and level-by-level coverage are as
    before; within a depth, real pages before likely traps, pages the recipe
    wants before hubs it does not, main-content links before navigation
    (#53), shallower paths first; ``order`` (a counter) keeps ties in
    document order, so a crawl is deterministic.

    ``seen`` holds canonical keys (#61) and is marked when a URL is pushed,
    so a page linked from many others is queued once: O(pages), not
    O(links) (#62).
    """

    def __init__(self) -> None:
        self._heap: list[tuple[int, int, int, int, int, int, str]] = []
        self._seen: set[str] = set()
        self._order = itertools.count()

    def __len__(self) -> int:
        return len(self._heap)

    def seen(self, url: str) -> bool:
        return canonical_key(url) in self._seen

    def push(self, url: str, depth: int, *, wanted: bool = True, in_content: bool = True) -> bool:
        key = canonical_key(url)
        if key in self._seen:
            return False
        self._seen.add(key)
        segments = len([s for s in urlparse(url).path.split("/") if s])
        heapq.heappush(self._heap, (
            depth, int(_is_trap(url)), int(not wanted), int(not in_content),
            segments, next(self._order), url,
        ))
        return True

    def pop(self) -> tuple[str, int]:
        entry = heapq.heappop(self._heap)
        return entry[-1], entry[0]


def _page_links(page_links: list[str], html: str, url: str) -> tuple[list[str], set[str]]:
    """Links to follow from a page, and the canonical keys of those in its
    main content.

    Following only main-content links missed site navigation, so a crawl
    without a sitemap often stopped at the home page (#53); navigation is
    followed too, and the frontier ranks main-content links first.
    """
    content = sanitise_many(page_links)
    everything = sanitise_many(list(dict.fromkeys([*page_links, *extract_links(html, url)])))
    return everything, {canonical_key(u) for u in content}


async def crawl(
    client,
    seed: str,
    *,
    max_pages: int = 50,
    max_depth: int = 3,
    url_pattern: str | None = None,
    keep: Callable[[str], bool] | None = None,
    max_fetches: int | None = None,
    cache: dict[str, str] | None = None,
) -> list[str]:
    """Best-first crawl from *seed*, staying on the same domain.

    Visits pages level by level (depth first in the frontier key), and
    within a level in priority order (see :class:`_Frontier`). Uses the
    existing HTTPClient (robots.txt + rate limiting already included).
    Falls back to Playwright for pages that look like SPAs (few links, rich
    body). Returns a deduplicated list of discovered URLs in visit order.

    *keep* is the recipe's URL filter (#63). Pages that fail it are still
    visited when they can lead further (a hub such as the home page often
    links to what is wanted), but they are not returned and do not count
    toward *max_pages*. One that fails it at the depth limit could lead
    nowhere, so it is never fetched. *max_fetches* (default four times
    *max_pages*) caps every request, kept or not, so filtering cannot turn
    the crawl into an unbounded one.

    *cache*, if given, receives the HTML of every page returned, keyed by
    :func:`canonical_key`, so the scrape stage need not download it again
    (#65). Filtered-out hubs are not cached: they are never scraped.
    """
    base_domain = urlparse(seed).netloc
    frontier = _Frontier()
    frontier.push(seed, 0)
    found: list[str] = []
    fetch_cap = max_fetches if max_fetches is not None else max_pages * _FETCH_CAP_FACTOR
    fetches = 0

    while frontier and len(found) < max_pages and fetches < fetch_cap:
        url, depth = frontier.pop()
        fetches += 1

        response = await client.get_safe(url)
        if not response or response.status_code != 200:
            continue

        content_type = response.headers.get("content-type", "")
        if "html" not in content_type:
            continue

        html = response.text
        if keep is None or keep(url):
            found.append(url)
            if cache is not None:
                cache[canonical_key(url)] = html
            log.debug(f"Crawled ({len(found)}/{max_pages}) depth={depth}: {url}")
        else:
            log.debug(f"Visited (filtered out, following its links) depth={depth}: {url}")

        if depth >= max_depth:
            continue

        page = extract(html, url)
        # Sanitise extracted links before filtering — zero trust on page content
        links, content_keys = _page_links(page.links, html, url)
        same_domain = [u for u in filter_urls(links, url_pattern, base_domain)
                       if is_page_url(u)]

        # SPA detection: suspiciously few links on a content-rich page → try JS render
        if len(same_domain) < _SPA_LINK_THRESHOLD and len(page.text) > _SPA_MIN_BODY_LEN:
            log.info(f"Possible SPA detected at {url} — attempting Playwright render")
            rendered = await _playwright_fetch(client, url)
            if rendered:
                rendered_page = extract(rendered, url)
                r_links, r_content = _page_links(rendered_page.links, rendered, url)
                rendered_same = [u for u in filter_urls(r_links, url_pattern, base_domain)
                                 if is_page_url(u)]
                if len(rendered_same) > len(same_domain):
                    log.info(f"Playwright found {len(rendered_same)} links vs {len(same_domain)} from static fetch")
                    same_domain, content_keys = rendered_same, r_content

        next_is_leaf = depth + 1 >= max_depth  # its links will not be followed
        for link in same_domain:
            wanted = keep is None or keep(link)
            if next_is_leaf and not wanted:
                continue  # filtered out and could lead nowhere: never fetch it
            frontier.push(link, depth + 1, wanted=wanted,
                          in_content=canonical_key(link) in content_keys)

    log.info(f"Crawl complete: {len(found)} pages from {seed}")
    return found
