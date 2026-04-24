"""BFS web crawler — fallback when no sitemap is available.

SPA detection: if a fetched page has very few links (< _SPA_LINK_THRESHOLD) but
a non-trivial body, it is likely a Single-Page Application that renders in JS.
We attempt a Playwright headless fetch as a fallback if the library is installed.
If Playwright is not installed the page is still recorded — it just won't yield
further links from that URL.
"""
from __future__ import annotations

from collections import deque
from urllib.parse import urlparse

from dataforge.utils import get_logger
from dataforge.utils.url_sanitiser import is_page_url, sanitise_many

from .extractor import extract
from .sitemap import filter_urls

log = get_logger("crawler")

# A page with fewer discovered links than this, but more than _SPA_MIN_BODY_LEN
# characters of body text, is treated as a potential SPA and retried with Playwright.
_SPA_LINK_THRESHOLD = 3
_SPA_MIN_BODY_LEN = 500


async def _playwright_fetch(url: str) -> str | None:
    """Fetch a URL with a headless Chromium browser and return the rendered HTML.

    Returns None if Playwright is not installed or the fetch fails.
    Playwright is an optional dependency — the crawler degrades gracefully without it.
    """
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        log.debug("Playwright not installed — skipping JS render for SPA detection")
        return None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=15_000)
            html = await page.content()
            await browser.close()
            return html
    except Exception as exc:
        log.debug(f"Playwright fetch failed for {url}: {exc}")
        return None


async def crawl(
    client,
    seed: str,
    *,
    max_pages: int = 50,
    max_depth: int = 3,
    url_pattern: str | None = None,
) -> list[str]:
    """BFS crawl starting from *seed*, staying on the same domain.

    Uses the existing HTTPClient (robots.txt + rate limiting already included).
    Falls back to Playwright for pages that look like SPAs (few links, rich body).
    Returns a deduplicated list of discovered URLs in visit order.
    """
    base_domain = urlparse(seed).netloc
    visited: set[str] = set()
    found: list[str] = []
    queue: deque[tuple[str, int]] = deque([(seed, 0)])

    while queue and len(found) < max_pages:
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        response = await client.get_safe(url)
        if not response or response.status_code != 200:
            continue

        content_type = response.headers.get("content-type", "")
        if "html" not in content_type:
            continue

        html = response.text
        found.append(url)
        log.debug(f"Crawled ({len(found)}/{max_pages}) depth={depth}: {url}")

        if depth >= max_depth:
            continue

        page = extract(html, url)
        # Sanitise extracted links before filtering — zero trust on page content
        clean_links = sanitise_many(page.links)
        same_domain = [u for u in filter_urls(clean_links, url_pattern, base_domain)
                       if is_page_url(u)]

        # SPA detection: suspiciously few links on a content-rich page → try JS render
        if len(same_domain) < _SPA_LINK_THRESHOLD and len(page.text) > _SPA_MIN_BODY_LEN:
            log.info(f"Possible SPA detected at {url} — attempting Playwright render")
            rendered = await _playwright_fetch(url)
            if rendered:
                rendered_page = extract(rendered, url)
                rendered_links = sanitise_many(rendered_page.links)
                rendered_same = [u for u in filter_urls(rendered_links, url_pattern, base_domain)
                                 if is_page_url(u)]
                if len(rendered_same) > len(same_domain):
                    log.info(f"Playwright found {len(rendered_same)} links vs {len(same_domain)} from static fetch")
                    same_domain = rendered_same

        for link in same_domain:
            if link not in visited:
                queue.append((link, depth + 1))

    log.info(f"Crawl complete: {len(found)} pages from {seed}")
    return found
