"""#61: URL variants that are almost always one page share a canonical key,
so discovery and the crawl fetch and keep each page once. The URL itself is
never rewritten: the first-seen form is what gets fetched and stored."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from dataforge.collectors import crawler
from dataforge.collectors.crawler import crawl
from dataforge.collectors.sitemap import parse_sitemaps
from dataforge.utils import canonical_key


@pytest.mark.parametrize("a, b", [
    ("https://example.org/a", "https://example.org/a/"),             # trailing slash
    ("https://example.org/a", "https://www.example.org/a"),          # www.
    ("https://example.org/a", "HTTPS://EXAMPLE.ORG/a"),              # case of scheme/host
    ("https://example.org/a", "http://example.org/a"),               # scheme
    ("https://example.org/a", "https://example.org:443/a"),          # default port
    ("http://example.org/a", "http://example.org:80/a"),             # default port
    ("https://example.org/a", "https://example.org/a#section"),      # fragment
    ("https://example.org/a?x=1&y=2", "https://example.org/a?y=2&x=1"),  # param order
    ("https://example.org/a?x=1", "https://example.org/a?x=1&utm_source=nl"),  # tracking
    ("https://example.org", "https://example.org/"),                 # empty path = root
])
def test_variants_share_a_key(a, b):
    assert canonical_key(a) == canonical_key(b)


@pytest.mark.parametrize("a, b", [
    ("https://example.org/a", "https://example.org/b"),
    ("https://example.org/a", "https://example.org/A"),              # path case matters
    ("https://example.org/a?x=1", "https://example.org/a?x=2"),      # real query matters
    ("https://example.org/a", "https://example.org:8443/a"),         # non-default port
    ("https://example.org/a", "https://docs.example.org/a"),         # other subdomain
])
def test_distinct_pages_keep_distinct_keys(a, b):
    assert canonical_key(a) != canonical_key(b)


def test_root_keeps_its_slash():
    assert canonical_key("https://example.org/") == "example.org/"


# ── the crawl fetches each page once ────────────────────────────────────────

SITE = "https://example.org"


def _page(*links):
    return ("<html><body><nav>" + " ".join(f'<a href="{u}">x</a>' for u in links)
            + "</nav><main><p>text</p></main></body></html>")


class FakeClient:
    def __init__(self, pages):
        self.pages, self.fetched = pages, []

    async def get_safe(self, url):
        self.fetched.append(url)
        path = url.split("example.org", 1)[1].split("?")[0] or "/"
        body = self.pages.get(path.rstrip("/") or "/")
        if body is None:
            return SimpleNamespace(status_code=404, headers={}, text="")
        return SimpleNamespace(status_code=200, headers={"content-type": "text/html"}, text=body)


@pytest.fixture(autouse=True)
def no_playwright(monkeypatch):
    async def none(*a, **k):
        return None
    monkeypatch.setattr(crawler, "_playwright_fetch", none)


async def test_crawl_fetches_url_variants_once():
    site = {
        "/": _page("/a", "/a/", "https://www.example.org/a", "http://example.org/a#top",
                   "/b?x=1&y=2", "/b?y=2&x=1"),
        "/a": _page("/", "/a/"),
        "/b": _page(),
    }
    client = FakeClient(site)
    found = await crawl(client, f"{SITE}/", max_pages=50, max_depth=3)
    assert len(found) == 3, found            # "/", one /a, one /b
    assert found[1] == f"{SITE}/a"           # the first-seen form is kept
    assert len(client.fetched) == 3, client.fetched


async def test_sitemaps_merge_url_variants():
    xml = lambda *u: ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'  # noqa: E731
                      + "".join(f"<url><loc>{x}</loc></url>" for x in u) + "</urlset>")
    bodies = {
        "https://example.org/one.xml": xml("https://example.org/a", "https://example.org/b"),
        "https://example.org/two.xml": xml("https://www.example.org/a/", "https://example.org/c"),
    }
    client = MagicMock()

    async def get_safe(url, **kw):
        return SimpleNamespace(status_code=200, text=bodies[url])
    client.get_safe = AsyncMock(side_effect=get_safe)

    urls = await parse_sitemaps(client, list(bodies))
    assert urls == ["https://example.org/a", "https://example.org/b", "https://example.org/c"]
