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

class _CountingFrontier(crawler._Frontier):
    pushed = 0
    peak = 0

    def push(self, url, depth, **kw):
        ok = super().push(url, depth, **kw)
        if ok:
            type(self).pushed += 1
            type(self).peak = max(type(self).peak, len(self))
        return ok


async def test_each_page_is_queued_once_even_when_linked_everywhere(monkeypatch):
    """A fully linked site (every page links to every other, as with a big
    navigation menu): the queue must hold each page once, O(pages), not
    once per link, O(pages^2)."""
    n = 12
    paths_ = ["/"] + [f"/p{i}" for i in range(1, n)]
    site = {p: page(p, nav=paths_) for p in paths_}
    _CountingFrontier.pushed = _CountingFrontier.peak = 0
    monkeypatch.setattr(crawler, "_Frontier", _CountingFrontier)

    found = await crawl(FakeClient(site), f"{SITE}/", max_pages=100, max_depth=3)

    assert sorted(paths(found)) == sorted(paths_)
    assert _CountingFrontier.pushed == n         # every page once, seed included
    assert _CountingFrontier.peak <= n


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
    """The same budget without the filter, as before #63: other pages crowd
    out what the recipe wants. (Best-first ordering, #64, already reaches
    the shallow /hazards hub, but none of the pages under it.)"""
    found = paths(await crawl(FakeClient(FILTER_SITE), f"{SITE}/", max_pages=4, max_depth=3))
    assert len([p for p in found if _only_hazards(p)]) <= 1


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


# ── #64: best-first frontier ─────────────────────────────────────────────────

async def test_traps_are_demoted_under_a_tight_budget():
    site = {
        "/": page("Home", nav=["/blog/page/2", "/2024/05/13", "/calendar", "/search?q=x",
                               "/login", "/list?sort=asc", "/guide", "/about"]),
        "/guide": page("Guide"), "/about": page("About"),
        "/blog/page/2": page("P2"), "/2024/05/13": page("Day"), "/calendar": page("Cal"),
        "/search": page("S"), "/login": page("L"), "/list": page("List"),
    }
    found = paths(await crawl(FakeClient(site), f"{SITE}/", max_pages=3, max_depth=2))
    assert found == ["/", "/guide", "/about"]


async def test_traps_are_still_reached_when_the_budget_allows():
    site = {"/": page("Home", nav=["/blog/page/2", "/guide"]),
            "/guide": page("Guide"), "/blog/page/2": page("P2")}
    found = paths(await crawl(FakeClient(site), f"{SITE}/", max_pages=10, max_depth=2))
    assert found == ["/", "/guide", "/blog/page/2"]


async def test_shallower_paths_first_within_a_depth():
    site = {"/": page("Home", nav=["/a/b/c", "/a/b", "/a"]),
            "/a": page("A"), "/a/b": page("AB"), "/a/b/c": page("ABC")}
    found = paths(await crawl(FakeClient(site), f"{SITE}/", max_pages=2, max_depth=2))
    assert found == ["/", "/a"]


async def test_depth_still_comes_first():
    """A trap at depth 1 is still visited before any page at depth 2, so
    max_depth and level-by-level coverage mean what they did."""
    site = {"/": page("Home", nav=["/guide", "/blog/page/2"]),
            "/guide": page("Guide", nav=["/guide/deep"]),
            "/guide/deep": page("Deep"), "/blog/page/2": page("P2")}
    found = paths(await crawl(FakeClient(site), f"{SITE}/", max_pages=10, max_depth=3))
    assert found == ["/", "/guide", "/blog/page/2", "/guide/deep"]


async def test_crawl_is_deterministic():
    runs = [paths(await crawl(FakeClient(FILTER_SITE), f"{SITE}/", max_pages=6, max_depth=3))
            for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


@pytest.mark.parametrize("url, trap", [
    ("https://e.org/blog/page/3", True), ("https://e.org/2023/11", True),
    ("https://e.org/2023/11/02/", True), ("https://e.org/calendar/june", True),
    ("https://e.org/events/2025/x", True), ("https://e.org/search", True),
    ("https://e.org/list?page=2", True), ("https://e.org/list?sort=name", True),
    ("https://e.org/login", True), ("https://e.org/feed/", True),
    ("https://e.org/guides/floods", False), ("https://e.org/events/community-day", False),
    ("https://e.org/page/about-us", False), ("https://e.org/searching-for-shelter", False),
    ("https://e.org/list?id=4", False), ("https://e.org/2023-annual-report", False),
])
def test_trap_detection(url, trap):
    assert crawler._is_trap(url) is trap


# ── #65: the crawl hands its downloads to the scrape stage ───────────────────

async def test_crawl_caches_kept_pages_only():
    cache: dict[str, str] = {}
    found = await crawl(FakeClient(FILTER_SITE), f"{SITE}/", max_pages=10, max_depth=3,
                        keep=_only_hazards, cache=cache)
    from dataforge.utils import canonical_key
    assert set(cache) == {canonical_key(u) for u in found}       # hubs are not cached
    assert "<title>Flood</title>" in cache[canonical_key(f"{SITE}/hazards/flood")]


async def test_scraper_uses_the_cache_and_fetches_only_on_a_miss(tmp_path):
    from dataforge.agents.base import PipelineContext
    from dataforge.agents.scraper import ScraperAgent
    from dataforge.config.settings import Settings
    from dataforge.storage import init_db
    from dataforge.storage.models import DataFormat
    from dataforge.utils import canonical_key

    s = Settings(db_path=tmp_path / "s.db", output_dir=tmp_path / "out")
    init_db(s.db_path)
    ctx = PipelineContext(session_id="s", session_name="s", goal="", format=DataFormat.qa,
                          seed_urls=[], settings=s)
    ctx.page_cache[canonical_key(f"{SITE}/hazards/flood/")] = page("Flood")   # variant key
    client = FakeClient(FILTER_SITE)
    scraper = ScraperAgent(ctx)

    assert await scraper.fetch_and_store(client, f"{SITE}/hazards/flood", tmp_path, 0)
    assert client.fetched == []                   # served from the cache
    assert ctx.page_cache == {}                   # and removed from it

    assert await scraper.fetch_and_store(client, f"{SITE}/hazards/fire", tmp_path, 1)
    assert client.fetched == ["/hazards/fire"]    # a miss is fetched as before
