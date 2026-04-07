"""ExplorerAgent — discovers URLs via sitemap and robots.txt."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from sqlmodel import select

from dataforge.collectors import HTTPClient, discover_sitemap_url, filter_urls, parse_sitemap
from dataforge.storage import DiscoveredURL, URLSource, open_session
from dataforge.utils import RateLimiter

from .base import BaseAgent, PipelineContext


class ExplorerAgent(BaseAgent):
    name = "explorer"

    async def run(self) -> PipelineContext:
        self.log.info(f"Starting discovery for session {self.ctx.session_id}")
        limiter = RateLimiter(self.ctx.settings.rate_limit)
        all_urls: list[str] = []

        async with HTTPClient(limiter) as client:
            for seed in self.ctx.seed_urls:
                discovered = await self._explore_seed(client, seed)
                all_urls.extend(discovered)

        # Deduplicate
        all_urls = list(dict.fromkeys(all_urls))
        self.ctx.discovered_urls = all_urls

        # Persist
        self._save_to_db(all_urls)
        self.log.info(f"Discovery complete: {len(all_urls)} URLs found")
        return self.ctx

    async def _explore_seed(self, client, seed: str) -> list[str]:
        parsed = urlparse(seed)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # 1. Check if seed itself is a sitemap URL
        if seed.endswith(".xml"):
            urls = await parse_sitemap(client, seed)
            return urls if urls else [seed]

        # 2. Try to discover sitemap
        sitemap_url = await discover_sitemap_url(client, base)
        if sitemap_url:
            urls = await parse_sitemap(client, sitemap_url)
            if urls:
                # filter to same domain by default
                return filter_urls(urls, pattern=None, base_domain=parsed.netloc)

        # 3. Fall back to the seed URL itself
        self.log.info(f"No sitemap found for {base}, using seed URL directly")
        return [seed]

    def _save_to_db(self, urls: list[str]) -> None:
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
                        source=URLSource.sitemap,
                        selected=True,
                    ))
            db.commit()
