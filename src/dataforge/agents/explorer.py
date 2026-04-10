"""ExplorerAgent — discovers URLs via sitemap and robots.txt."""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from sqlmodel import select

from dataforge.collectors import HTTPClient, crawl, discover_sitemap_url, filter_urls, parse_sitemap
from dataforge.storage import DiscoveredURL, URLSource, open_session
from dataforge.utils import RateLimiter

from .base import BaseAgent, PipelineContext


class ExplorerAgent(BaseAgent):
    name = "explorer"

    async def run(self) -> PipelineContext:
        self.log.info(f"Starting discovery for session {self.ctx.session_id}")
        limiter = RateLimiter(self.ctx.settings.rate_limit)
        all_urls: list[str] = []

        source_map: dict[str, str] = {}  # url -> URLSource value

        async with HTTPClient(limiter) as client:
            # Parallelize seed URL exploration
            tasks = [self._explore_seed(client, seed) for seed in self.ctx.seed_urls]
            results = await asyncio.gather(*tasks)
            for (urls, source) in results:
                for url in urls:
                    if url not in source_map:
                        source_map[url] = source
                        all_urls.append(url)

        # Deduplicate (already maintained by source_map insertion order)
        self.ctx.discovered_urls = all_urls

        # Persist
        self._save_to_db(all_urls, source_map)
        self.log.info(f"Discovery complete: {len(all_urls)} URLs found")
        return self.ctx

    async def _explore_seed(self, client, seed: str) -> tuple[list[str], str]:
        """Returns (urls, source) where source is a URLSource value."""
        parsed = urlparse(seed)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # 1. Check if seed itself is a sitemap URL
        if seed.endswith(".xml"):
            urls = await parse_sitemap(client, seed)
            return (urls if urls else [seed], URLSource.sitemap)

        # 2. Try to discover sitemap
        sitemap_url = await discover_sitemap_url(client, base)
        if sitemap_url:
            raw_urls = await parse_sitemap(client, sitemap_url)
            if raw_urls:
                # filter to same domain by default
                filtered = filter_urls(raw_urls, pattern=None, base_domain=parsed.netloc)
                if filtered:
                    return (filtered, URLSource.sitemap)
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
        )
        return (crawled if crawled else [seed], URLSource.crawl)

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
