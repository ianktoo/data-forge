"""Sitemap XML parser — handles sitemap index files and plain sitemaps."""
from __future__ import annotations

from urllib.parse import urljoin, urlparse

import xmltodict

from dataforge.utils import get_logger

log = get_logger("sitemap")

_SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap/sitemap.xml"]


def _strip_www(domain: str) -> str:
    """Normalize domain by removing 'www.' prefix for consistent matching."""
    return domain.removeprefix("www.")


async def discover_sitemap_url(client, base_url: str) -> str | None:
    """Try common sitemap paths; also check robots.txt Sitemap directive."""
    # robots.txt
    try:
        r = await client.get(urljoin(base_url, "/robots.txt"), check_robots=False)
        for line in r.text.splitlines():
            if line.lower().startswith("sitemap:"):
                url = line.split(":", 1)[1].strip()
                log.info(f"Found sitemap in robots.txt: {url}")
                return url
    except Exception:
        pass

    for path in _SITEMAP_PATHS:
        url = urljoin(base_url, path)
        r = await client.get_safe(url)
        if r and r.status_code == 200 and ("<urlset" in r.text or "<sitemapindex" in r.text):
            log.info(f"Found sitemap: {url}")
            return url
    return None


async def parse_sitemap(client, sitemap_url: str, visited: set[str] | None = None) -> list[str]:
    """Recursively parse sitemap/index, return all page URLs."""
    if visited is None:
        visited = set()
    if sitemap_url in visited:
        return []
    visited.add(sitemap_url)

    r = await client.get_safe(sitemap_url)
    if not r or r.status_code != 200:
        return []

    try:
        data = xmltodict.parse(r.text)
    except Exception as exc:
        log.warning(f"XML parse error for {sitemap_url}: {exc}")
        return []

    urls: list[str] = []

    # Sitemap index — recurse into child sitemaps
    if "sitemapindex" in data:
        sitemaps = data["sitemapindex"].get("sitemap", [])
        if isinstance(sitemaps, dict):
            sitemaps = [sitemaps]
        for sm in sitemaps:
            child = sm.get("loc", "")
            if child:
                urls.extend(await parse_sitemap(client, child, visited))
        return urls

    # Plain sitemap
    if "urlset" in data:
        entries = data["urlset"].get("url", [])
        if isinstance(entries, dict):
            entries = [entries]
        for entry in entries:
            loc = entry.get("loc", "")
            if loc:
                urls.append(loc)

    log.info(f"Parsed {len(urls)} URLs from {sitemap_url}")
    return urls


def filter_urls(urls: list[str], pattern: str | None, base_domain: str | None) -> list[str]:
    """Filter URLs by optional substring pattern and optionally same domain."""
    result = urls
    if base_domain:
        canonical = _strip_www(base_domain)
        result = [u for u in result if _strip_www(urlparse(u).netloc) == canonical]
    if pattern:
        result = [u for u in result if pattern in u]
    return list(dict.fromkeys(result))  # deduplicate, preserve order
