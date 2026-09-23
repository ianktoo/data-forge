"""ExplorerAgent — discovers URLs via sitemap and robots.txt."""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from sqlmodel import select

from dataforge.collectors import (
    HTTPClient,
    crawl,
    discover_sitemap_urls,
    filter_urls,
    parse_sitemap,
    parse_sitemaps,
)
from dataforge.storage import DiscoveredURL, URLSource, open_session
from dataforge.utils import canonical_key

from .base import BaseAgent, PipelineContext

# SQLite allows 32,766 bound parameters (999 before 3.32); stay well below.
_LOOKUP_BATCH = 500


class ExplorerAgent(BaseAgent):
    name = "explorer"

    async def run(self) -> PipelineContext:
        self.log.info(f"Starting discovery for session {self.ctx.session_id}")
        limiter = self.ctx.get_rate_limiter()
        all_urls: list[str] = []

        source_map: dict[str, str] = {}  # url -> URLSource value
        seen_keys: set[str] = set()      # canonical keys: URL variants are one page (#61)

        async with HTTPClient(limiter, ignore_robots=self.ctx.ignore_robots) as client:
            # Parallelize seed URL exploration
            tasks = [self._explore_seed(client, seed) for seed in self.ctx.seed_urls]
            results = await asyncio.gather(*tasks)
            for (urls, source) in results:
                for url in urls:
                    key = canonical_key(url)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        source_map[url] = source
                        all_urls.append(url)

        # Deduplicate (already maintained by source_map insertion order)
        discovered = all_urls

        # Optionally skip URLs already scraped in any previous session
        if self.ctx.skip_known and discovered:
            discovered = self._filter_already_scraped(discovered)

        self.ctx.discovered_urls = discovered

        # Persist
        self._save_to_db(discovered, source_map)
        self.log.info(f"Discovery complete: {len(discovered)} URLs found")
        return self.ctx

    async def _explore_seed(self, client, seed: str) -> tuple[list[str], str]:
        """Returns (urls, source) where source is a URLSource value."""
        parsed = urlparse(seed)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # 1. Check if seed itself is a sitemap URL
        if seed.endswith(".xml"):
            urls = await parse_sitemap(client, seed)
            return (urls if urls else [seed], URLSource.sitemap)

        # 2. Try to discover sitemaps (every one robots.txt lists, #60)
        sitemap_urls = await discover_sitemap_urls(client, base)
        sitemap_url = ", ".join(sitemap_urls)
        if sitemap_urls:
            raw_urls = await parse_sitemaps(client, sitemap_urls)
            if raw_urls:
                # filter to same domain by default
                filtered = filter_urls(raw_urls, pattern=None, base_domain=parsed.netloc)
                # A seed deeper than the site root (e.g. /3/tutorial/) that the
                # sitemap lists nothing under means the sitemap doesn't cover
                # what was asked for; crawl from the seed instead of returning
                # unrelated sitemap URLs and dropping the seed entirely.
                seed_path = parsed.path.rstrip("/")
                if filtered and seed_path and not any(
                    urlparse(u).path.rstrip("/").startswith(seed_path) for u in filtered
                ):
                    self.log.info(
                        f"Sitemap at {sitemap_url} lists nothing under {parsed.path}; "
                        "crawling from the seed instead."
                    )
                elif filtered:
                    return (filtered, URLSource.sitemap)
                else:
                    # Sitemap parsed successfully but all URLs were filtered out
                    self.log.warning(
                        f"Sitemap returned {len(raw_urls)} URLs but all were filtered by domain '{parsed.netloc}'. "
                        "Returning all discovered URLs without domain filter."
                    )
                    return (raw_urls, URLSource.sitemap)
            else:
                self.log.warning(f"Sitemap found at {sitemap_url} but parsed 0 URLs")

        # 3. Fall back to BFS crawl from seed URL
        self.log.info(
            f"No usable sitemap for {base}, starting BFS crawl "
            f"(max_pages={self.ctx.settings.max_crawl_pages}, max_depth={self.ctx.settings.max_crawl_depth})"
        )
        crawled = await crawl(
            client,
            seed,
            max_pages=self.ctx.settings.max_crawl_pages,
            max_depth=self.ctx.settings.max_crawl_depth,
            keep=self.ctx.url_filter,
            cache=self.ctx.page_cache,
        )
        return (crawled if crawled else [seed], URLSource.crawl)

    def _filter_already_scraped(self, urls: list[str]) -> list[str]:
        """Remove URLs that were successfully scraped in any prior session."""
        # Look up only the URLs just discovered (indexed on url, #59), in
        # batches under SQLite's parameter limit, instead of loading every URL
        # ever scraped: the cost follows this discovery, not the history.
        scraped: set[str] = set()
        with open_session(self.ctx.settings.db_path) as db:
            for i in range(0, len(urls), _LOOKUP_BATCH):
                batch = urls[i:i + _LOOKUP_BATCH]
                scraped.update(db.exec(
                    select(DiscoveredURL.url).where(
                        DiscoveredURL.url.in_(batch),  # type: ignore[attr-defined]
                        DiscoveredURL.scraped == True,  # noqa: E712
                        DiscoveredURL.session_id != self.ctx.session_id,
                    )
                ).all())
        before = len(urls)
        filtered = [u for u in urls if u not in scraped]
        skipped = before - len(filtered)
        if skipped:
            self.log.info(f"Skipped {skipped} already-scraped URL(s) from prior sessions")
        return filtered

    def _save_to_db(self, urls: list[str], source_map: dict[str, str]) -> None:
        with open_session(self.ctx.settings.db_path) as db:
            # Avoid duplicates for this session
            existing = db.exec(
                select(DiscoveredURL).where(DiscoveredURL.session_id == self.ctx.session_id)
            ).all()
            existing_urls = {r.url for r in existing}

            for url in urls:
                if url not in existing_urls:
                    db.add(DiscoveredURL(
                        session_id=self.ctx.session_id,
                        url=url,
                        source=source_map.get(url, URLSource.sitemap),
                        selected=True,
                    ))
            db.commit()
