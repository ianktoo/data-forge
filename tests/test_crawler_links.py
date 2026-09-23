"""Regression tests for #53: a crawl without a sitemap must follow site
navigation, not only links inside the main content.

`extract()` (content extraction) strips <nav>/<header>/<footer> and reads
links from the main block only; that is right for content and must not
change. The crawler now reads links from the whole page via
`extract_links()`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dataforge.collectors import crawler
from dataforge.collectors.crawler import crawl
from dataforge.collectors.extractor import extract, extract_links

SITE = "https://example.org"


def page(title: str, nav: list[str] = (), body_links: list[str] = (), footer: list[str] = ()) -> str:
    a = lambda hrefs: " ".join(f'<a href="{h}">{h}</a>' for h in hrefs)  # noqa: E731
    return f"""<html><head><title>{title}</title></head><body>
<header><a href="/">Home</a></header>
<nav>{a(nav)}</nav>
<main><h1>{title}</h1><p>Short article text about {title}. {a(body_links)}</p></main>
<footer>{a(footer)}</footer>
</body></html>"""


# ── extract_links / extract ──────────────────────────────────────────────────

def test_extract_links_reads_the_whole_page():
    html = page("Home", nav=["/a", "/b"], body_links=["/c"], footer=["/privacy"])
    links = extract_links(html, f"{SITE}/")
    assert links == [f"{SITE}/", f"{SITE}/a", f"{SITE}/b", f"{SITE}/c", f"{SITE}/privacy"]


def test_extract_links_skips_non_navigable_hrefs_and_honours_base():
    html = """<html><head><base href="https://example.org/docs/"></head><body>
<a href="#top">top</a><a href="mailto:x@example.org">mail</a><a href="tel:123">tel</a>
<a href="javascript:void(0)">js</a><a href=" guide.html ">guide</a><a href="guide.html">dup</a>
</body></html>"""
    assert extract_links(html, f"{SITE}/index.html") == [f"{SITE}/docs/guide.html"]


def test_extract_content_links_unchanged():
    """Content extraction still ignores navigation: only the scraper-facing
    behaviour of extract() is guarded here, so fixing the crawler cannot
    change what text ends up in the dataset."""
    html = page("Home", nav=["/a", "/b"], body_links=["/c"], footer=["/privacy"])
    content = extract(html, f"{SITE}/")
    assert content.links == [f"{SITE}/c"]
    assert "Short article text" in content.text
    assert "/privacy" not in content.text


# ── crawl ────────────────────────────────────────────────────────────────────

# Links live only in <nav>. Depth from "/": 1 = /a, /b; 2 = /a/1, /b/1; 3 = /a/1/x.
NAV_SITE = {
    "/": page("Home", nav=["/a", "/b", "https://elsewhere.test/x"], footer=["/logo.png"]),
    "/a": page("A", nav=["/a/1"]),
    "/b": page("B", nav=["/b/1"]),
    "/a/1": page("A1", nav=["/a/1/x"]),
    "/b/1": page("B1"),
    "/a/1/x": page("A1X"),
}


class FakeClient:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.fetched: list[str] = []

    async def get_safe(self, url: str):
        path = url[len(SITE):] or "/"
        self.fetched.append(path)
        if path not in self.pages:
            return SimpleNamespace(status_code=404, headers={}, text="")
        return SimpleNamespace(status_code=200, headers={"content-type": "text/html"},
                               text=self.pages[path])


@pytest.fixture(autouse=True)
def no_playwright(monkeypatch):
    async def none(*a, **k):
        return None
    monkeypatch.setattr(crawler, "_playwright_fetch", none)


def paths(urls):
    return [u[len(SITE):] or "/" for u in urls]


async def test_crawl_follows_navigation_links():
    found = await crawl(FakeClient(NAV_SITE), f"{SITE}/", max_pages=50, max_depth=3)
    assert set(paths(found)) == set(NAV_SITE)


async def test_crawl_respects_max_depth():
    found = paths(await crawl(FakeClient(NAV_SITE), f"{SITE}/", max_pages=50, max_depth=2))
    assert set(found) == {"/", "/a", "/b", "/a/1", "/b/1"}  # /a/1/x is depth 3


async def test_crawl_depth_limit_does_not_fetch_beyond_it():
    client = FakeClient(NAV_SITE)
    await crawl(client, f"{SITE}/", max_pages=50, max_depth=1)
    assert set(client.fetched) == {"/", "/a", "/b"}


async def test_crawl_respects_max_pages():
    found = await crawl(FakeClient(NAV_SITE), f"{SITE}/", max_pages=3, max_depth=3)
    assert paths(found) == ["/", "/a", "/b"]  # breadth first


async def test_crawl_stays_on_domain_and_skips_files():
    client = FakeClient(NAV_SITE)
    await crawl(client, f"{SITE}/", max_pages=50, max_depth=3)
    assert "/logo.png" not in client.fetched
    assert not any("elsewhere" in p for p in client.fetched)


async def test_content_links_are_queued_before_navigation():
    """Under a tight page cap, article links win over nav/footer links."""
    site = {
        "/": page("Home", nav=["/about", "/login"], body_links=["/guide"]),
        "/guide": page("Guide"),
        "/about": page("About"),
        "/login": page("Login"),
    }
    found = paths(await crawl(FakeClient(site), f"{SITE}/", max_pages=2, max_depth=2))
    assert found == ["/", "/guide"]


# ── #62: each page is queued once ────────────────────────────────────────────

class _CountingDeque(crawler.deque):  # type: ignore[misc]
    appended = 0
    peak = 0

    def append(self, item):
        type(self).appended += 1
        super().append(item)
        type(self).peak = max(type(self).peak, len(self))


async def test_each_page_is_queued_once_even_when_linked_everywhere(monkeypatch):
    """A fully linked site (every page links to every other, as with a big
    navigation menu): the queue must hold each page once, O(pages), not
    once per link, O(pages^2)."""
    n = 12
    paths_ = ["/"] + [f"/p{i}" for i in range(1, n)]
    site = {p: page(p, nav=paths_) for p in paths_}
    _CountingDeque.appended = _CountingDeque.peak = 0
    monkeypatch.setattr(crawler, "deque", _CountingDeque)

    found = await crawl(FakeClient(site), f"{SITE}/", max_pages=100, max_depth=3)

    assert sorted(paths(found)) == sorted(paths_)
    assert _CountingDeque.appended == n - 1      # every page but the seed, once
    assert _CountingDeque.peak <= n - 1


# ── #63: the recipe's URL filter is applied during the crawl ─────────────────

# A hub home page links to news and about pages (filtered out by the recipe)
# and to /hazards, whose pages are what the recipe wants.
FILTER_SITE = {
    "/": page("Home", nav=["/news/1", "/news/2", "/news/3", "/about", "/hazards"]),
    "/news/1": page("N1"), "/news/2": page("N2"), "/news/3": page("N3"),
    "/about": page("About", nav=["/about/team"]),
    "/about/team": page("Team"),
    "/hazards": page("Hazards", nav=["/hazards/flood", "/hazards/fire", "/hazards/heat"]),
    "/hazards/flood": page("Flood"), "/hazards/fire": page("Fire"), "/hazards/heat": page("Heat"),
}


def _only_hazards(url: str) -> bool:
    return "/hazards" in url


async def test_filtered_crawl_spends_its_budget_on_wanted_pages():
    found = paths(await crawl(FakeClient(FILTER_SITE), f"{SITE}/", max_pages=4,
                              max_depth=3, keep=_only_hazards))
    # The home page is visited as a hub but not returned or counted.
    assert found == ["/hazards", "/hazards/flood", "/hazards/fire", "/hazards/heat"]


async def test_unfiltered_crawl_would_waste_the_same_budget():
    """The same budget without the filter, as before #63: news pages crowd
    out what the recipe wants, and the post-crawl filter keeps one page."""
    found = paths(await crawl(FakeClient(FILTER_SITE), f"{SITE}/", max_pages=4, max_depth=3))
    assert [p for p in found if _only_hazards(p)] == []


async def test_filtered_out_pages_at_the_depth_limit_are_never_fetched():
    client = FakeClient(FILTER_SITE)
    await crawl(client, f"{SITE}/", max_pages=50, max_depth=1, keep=_only_hazards)
    # Depth 1 is the limit: /news/* and /about could lead nowhere, so they
    # are skipped outright; /hazards is kept.
    assert sorted(client.fetched) == ["/", "/hazards"]


async def test_fetch_cap_bounds_a_filter_that_matches_nothing():
    client = FakeClient(FILTER_SITE)
    found = await crawl(client, f"{SITE}/", max_pages=50, max_depth=3,
                        keep=lambda u: False, max_fetches=3)
    assert found == []
    assert len(client.fetched) == 3


def test_recipe_url_matches_agrees_with_filter_urls():
    from dataforge.cli.recipe import Recipe
    r = Recipe.model_validate({
        "version": 1, "name": "t",
        "source": {"urls": ["https://example.org/"], "language": "en",
                   "include": ["/hazards", "re:/kit$"], "exclude": ["/hazards/old"]},
    })
    urls = ["https://example.org/hazards/flood", "https://example.org/es/hazards/flood",
            "https://example.org/hazards/old", "https://example.org/build/kit",
            "https://example.org/news"]
    assert r.filter_urls(urls) == [u for u in urls if r.url_matches(u)]
    assert r.filter_urls(urls) == ["https://example.org/hazards/flood", "https://example.org/build/kit"]
