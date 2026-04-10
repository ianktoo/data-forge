"""ScraperAgent — rate-limited async web collection."""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlmodel import select

from dataforge.collectors import HTTPClient, extract
from dataforge.storage import DiscoveredURL, ScrapedPage, open_session
from dataforge.utils import RateLimiter, concurrency_ceiling

from .base import BaseAgent, PipelineContext


class ScraperAgent(BaseAgent):
    name = "scraper"

    def __init__(self, context: PipelineContext, progress_cb=None) -> None:
        super().__init__(context)
        self._progress_cb = progress_cb  # optional async callback(done, total, url)

    async def run(self) -> PipelineContext:
        urls = self.ctx.selected_urls or self.ctx.discovered_urls
        if not urls:
            self.log.warning("No URLs to scrape")
            return self.ctx

        raw_dir = self._stage_dir("raw")
        limiter  = RateLimiter(self.ctx.settings.rate_limit)
        concur   = min(concurrency_ceiling(), len(urls))
        sem      = asyncio.Semaphore(concur)
        self.log.info(f"Scraping {len(urls)} URLs (concurrency={concur})")

        done = 0
        async with HTTPClient(limiter, ignore_robots=self.ctx.ignore_robots) as client:
            tasks = [self._scrape_one(client, sem, url, raw_dir, i)
                     for i, url in enumerate(urls)]
            for coro in asyncio.as_completed(tasks):
                page_id = await coro
                done += 1
                if page_id:
                    self.ctx.scraped_page_ids.append(page_id)
                if self._progress_cb:
                    await self._progress_cb(done, len(urls))

        self.log.info(f"Scraped {len(self.ctx.scraped_page_ids)}/{len(urls)} pages")
        return self.ctx

    async def _scrape_one(
        self, client, sem: asyncio.Semaphore, url: str, raw_dir: Path, idx: int
    ) -> int | None:
        async with sem:
            resp = await client.get_safe(url)
            if resp is None:
                self.ctx.add_error(f"Failed to fetch {url}")
                return None

            content = extract(resp.text, url)
            if not content.text.strip():
                return None

            # Save raw text to disk
            raw_path = raw_dir / f"page_{idx:05d}.md"
            raw_path.write_text(content.markdown, encoding="utf-8")

            # Persist metadata
            with open_session(self.ctx.settings.db_path) as db:
                # Mark URL as scraped
                url_rec = db.exec(
                    select(DiscoveredURL)
                    .where(DiscoveredURL.session_id == self.ctx.session_id)
                    .where(DiscoveredURL.url == url)
                ).first()
                if url_rec:
                    url_rec.scraped = True
                    url_rec.http_status = resp.status_code

                page = ScrapedPage(
                    session_id=self.ctx.session_id,
                    url_id=url_rec.id if url_rec else 0,
                    url=url,
                    title=content.title,
                    author=content.author,
                    published_date=content.published_date,
                    raw_path=str(raw_path),
                    word_count=content.word_count,
                )
                db.add(page)
                db.commit()
                db.refresh(page)
                return page.id
