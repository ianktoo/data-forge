"""BFS web crawler — fallback when no sitemap is available.

SPA detection: if a fetched page has very few links (< _SPA_LINK_THRESHOLD) but
a non-trivial body, it is likely a Single-Page Application that renders in JS.
We attempt a Playwright headless fetch as a fallback if the library is installed.
If Playwright is not installed the page is still recorded — it just won't yield
further links from that URL.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from urllib.parse import urlparse

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


def _crawl_links(content_links: list[str], html: str, url: str) -> list[str]:
    """Links to follow from a page: main-content links first, then the rest
    of the page (navigation, header, footer, sidebar).

    Following only main-content links missed site navigation, so a crawl
    without a sitemap often stopped at the home page (#53). Content links go
    first so that, under ``max_pages``, article links win over footer links.
    """
    return list(dict.fromkeys([*content_links, *extract_links(html, url)]))


async def crawl(
    client,
    seed: str,
    *,
    max_pages: int = 50,
    max_depth: int = 3,
    url_pattern: str | None = None,
    keep: Callable[[str], bool] | None = None,
    max_fetches: int | None = None,
) -> list[str]:
    """BFS crawl starting from *seed*, staying on the same domain.

    Uses the existing HTTPClient (robots.txt + rate limiting already included).
    Falls back to Playwright for pages that look like SPAs (few links, rich body).
    Returns a deduplicated list of discovered URLs in visit order.

    *keep* is the recipe's URL filter (#63). Pages that fail it are still
    visited when they can lead further (a hub such as the home page often
    links to what is wanted), but they are not returned and do not count
    toward *max_pages*. One that fails it at the depth limit could lead
    nowhere, so it is never fetched. *max_fetches* (default four times
    *max_pages*) caps every request, kept or not, so filtering cannot turn
    the crawl into an unbounded one.
    """
    base_domain = urlparse(seed).netloc
    # Canonical keys of every URL ever queued (#61: /a and /a/, www.,
    # http(s) are one page). Marked when a URL is queued, not when it is
    # visited, so a page linked from many others is queued once and the
    # queue grows with pages, not links (#62).
    seen: set[str] = {canonical_key(seed)}
    found: list[str] = []
    queue: deque[tuple[str, int]] = deque([(seed, 0)])

    fetch_cap = max_fetches if max_fetches is not None else max_pages * _FETCH_CAP_FACTOR
    fetches = 0

    while queue and len(found) < max_pages and fetches < fetch_cap:
        url, depth = queue.popleft()
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
            log.debug(f"Crawled ({len(found)}/{max_pages}) depth={depth}: {url}")
        else:
            log.debug(f"Visited (filtered out, following its links) depth={depth}: {url}")

        if depth >= max_depth:
            continue

        page = extract(html, url)
        # Sanitise extracted links before filtering — zero trust on page content
        clean_links = sanitise_many(_crawl_links(page.links, html, url))
        same_domain = [u for u in filter_urls(clean_links, url_pattern, base_domain)
                       if is_page_url(u)]

        # SPA detection: suspiciously few links on a content-rich page → try JS render
        if len(same_domain) < _SPA_LINK_THRESHOLD and len(page.text) > _SPA_MIN_BODY_LEN:
            log.info(f"Possible SPA detected at {url} — attempting Playwright render")
            rendered = await _playwright_fetch(client, url)
            if rendered:
                rendered_page = extract(rendered, url)
                rendered_links = sanitise_many(
                    _crawl_links(rendered_page.links, rendered, url))
                rendered_same = [u for u in filter_urls(rendered_links, url_pattern, base_domain)
                                 if is_page_url(u)]
                if len(rendered_same) > len(same_domain):
                    log.info(f"Playwright found {len(rendered_same)} links vs {len(same_domain)} from static fetch")
                    same_domain = rendered_same

        next_is_leaf = depth + 1 >= max_depth  # its links will not be followed
        for link in same_domain:
            key = canonical_key(link)
            if key in seen:
                continue
            if next_is_leaf and keep is not None and not keep(link):
                continue  # filtered out and could lead nowhere: never fetch it
            seen.add(key)
            queue.append((link, depth + 1))

    log.info(f"Crawl complete: {len(found)} pages from {seed}")
    return found
