"""StreamingAgent — fused collection -> processing -> generation.

The default pipeline runs one stage to completion before starting the next
(see :mod:`dataforge.agents.orchestrator`). That is easy to reason about and
gives the CLI a natural place to prompt between stages, but it serialises the
two slowest stages: nothing is generated until the very last URL has been
scraped. On a large site the LLM sits idle for the whole crawl, then the
network sits idle for the whole generation run.

This agent runs those three stages concurrently as bounded worker pools joined
by queues::

    urls -> [scrape] -> pages -> [process] -> chunks -> [generate] -> samples

A page that finishes scraping is chunked immediately, and its chunks enter
generation while the crawler is still fetching. Quality and export stay batch
stages downstream — quality deduplicates across the whole sample set, so it
genuinely needs all of them.

Queues are bounded, so a fast crawler cannot run the process pool out of
memory; it simply blocks until generation drains the backlog.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import select

from dataforge.collectors import HTTPClient
from dataforge.generators import LLMClient, generate_from_chunk
from dataforge.processors import clean, is_content_rich
from dataforge.processors.formatter import DataRecord
from dataforge.storage import (
    DiscoveredURL,
    ProcessedChunk,
    ScrapedPage,
    SyntheticSample,
    open_session,
)
from dataforge.utils import concurrency_ceiling
from dataforge.utils.errors import (
    LLMConnectionError,
    MissingCredentialError,
    show_error,
    show_warning,
)

from .base import BaseAgent, PipelineContext
from .generator import persist_sample
from .processor import _process_page_sync
from .scraper import ScraperAgent

# Sentinel pushed onto a queue once per consumer to signal end-of-stream.
_DONE = object()


@dataclass
class StreamCounters:
    """Live totals surfaced to the CLI progress display."""
    urls_total: int = 0
    scraped: int = 0
    scrape_failed: int = 0
    pages_processed: int = 0
    chunks: int = 0
    samples: int = 0
    gen_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {
            "urls_total":      self.urls_total,
            "scraped":         self.scraped,
            "scrape_failed":   self.scrape_failed,
            "pages_processed": self.pages_processed,
            "chunks":          self.chunks,
            "samples":         self.samples,
            "gen_skipped":     self.gen_skipped,
        }


class StreamingAgent(BaseAgent):
    """Runs collection, processing and generation as one concurrent phase."""

    name = "streaming"

    def __init__(self, context: PipelineContext, progress_cb=None) -> None:
        super().__init__(context)
        self._progress_cb = progress_cb
        self.counters = StreamCounters()
        # Set when generation hits an unrecoverable error (bad/missing key,
        # provider unreachable). Generation workers then switch to draining the
        # chunk queue instead of exiting — if they simply stopped, the queue
        # would fill and block the process pool behind it forever.
        self._gen_fatal: str = ""

    # -- Entry point ---------------------------------------------------------

    async def run(self) -> PipelineContext:
        s = self.ctx.settings
        pending_urls, pending_pages, pending_chunks = self._seed_work()

        if not (pending_urls or pending_pages or pending_chunks):
            self.log.warning("Nothing to stream — no URLs, pages or chunks pending")
            return self.ctx

        self.counters.urls_total = len(pending_urls)

        gen_enabled = self._generation_available()
        if not gen_enabled:
            self.log.warning(
                "Generation disabled (no LLM credentials) — streaming scrape+chunk only"
            )

        n_scrape = max(1, min(concurrency_ceiling(), len(pending_urls) or 1))
        n_process = max(1, min(concurrency_ceiling(), 4))
        n_gen = max(1, s.stream_generate_workers)

        qsize = max(n_process, s.stream_queue_size)
        url_q: asyncio.Queue[object] = asyncio.Queue()
        page_q: asyncio.Queue[object] = asyncio.Queue(maxsize=qsize)
        chunk_q: asyncio.Queue[object] = asyncio.Queue(maxsize=qsize)

        self.log.info(
            f"Streaming {len(pending_urls)} URLs "
            f"(scrape={n_scrape}, process={n_process}, "
            f"generate={n_gen if gen_enabled else 0})"
        )

        has_cap = self.ctx.max_llm_calls is not None or self.ctx.max_cost_usd is not None
        budget = self.ctx.get_budget() if has_cap else None
        llm = (
            LLMClient(model_override=self.ctx.generation_model, budget=budget)
            if gen_enabled else None
        )
        limiter = self.ctx.get_rate_limiter()
        raw_dir = self._stage_dir("raw")
        processed_dir = self._stage_dir("processed")
        scraper = ScraperAgent(self.ctx)

        # Pre-seed resumable work so a resumed session picks up mid-flight
        # instead of re-scraping pages it already has on disk.
        for page_id in pending_pages:
            page_q.put_nowait(page_id)
        for chunk_id in pending_chunks:
            chunk_q.put_nowait(chunk_id)

        async with HTTPClient(limiter, ignore_robots=self.ctx.ignore_robots) as client:
            scrapers = [
                asyncio.create_task(
                    self._scrape_worker(url_q, page_q, client, scraper, raw_dir)
                )
                for _ in range(n_scrape)
            ]
            processors = [
                asyncio.create_task(self._process_worker(page_q, chunk_q, processed_dir))
                for _ in range(n_process)
            ]
            generators = [
                asyncio.create_task(self._generate_worker(chunk_q, llm, gen_enabled))
                for _ in range(n_gen)
            ]

            try:
                for idx, url in enumerate(pending_urls):
                    url_q.put_nowait((url, idx))
                for _ in scrapers:
                    url_q.put_nowait(_DONE)
                await asyncio.gather(*scrapers)

                for _ in processors:
                    await page_q.put(_DONE)
                await asyncio.gather(*processors)

                for _ in generators:
                    await chunk_q.put(_DONE)
                await asyncio.gather(*generators)
            except (KeyboardInterrupt, asyncio.CancelledError):
                self.ctx.pause_requested = True
                self.log.info(
                    "Streaming interrupted — cancelling workers, partial results saved"
                )
                for t in (*scrapers, *processors, *generators):
                    t.cancel()
                await asyncio.gather(
                    *scrapers, *processors, *generators, return_exceptions=True
                )

        self.ctx.page_cache.clear()  # pages not selected for scraping (#65)
        self._finalise(llm)
        return self.ctx

    # -- Workers -------------------------------------------------------------

    async def _scrape_worker(
        self, url_q, page_q, client, scraper: ScraperAgent, raw_dir: Path
    ) -> None:
        while True:
            item = await url_q.get()
            if item is _DONE:
                return
            if self.ctx.pause_requested:
                continue  # drain remaining URLs so the producer never blocks
            url, idx = item
            try:
                page_id = await scraper.fetch_and_store(client, url, raw_dir, idx)
            except Exception as exc:  # one bad page must not kill the pool
                self.log.warning(f"Scrape failed for {url}: {exc}")
                self.counters.scrape_failed += 1
                await self._report(f"scrape error {url}")
                continue
            if page_id is None:
                self.counters.scrape_failed += 1
                await self._report(f"skipped {url}")
                continue
            self.counters.scraped += 1
            self.ctx.scraped_page_ids.append(page_id)
            await self._report(f"scraped {url}")
            await page_q.put(page_id)

    async def _process_worker(self, page_q, chunk_q, processed_dir: Path) -> None:
        s = self.ctx.settings
        while True:
            item = await page_q.get()
            if item is _DONE:
                return
            if self.ctx.pause_requested:
                continue
            page_id = item
            try:
                chunk_ids = await self._chunk_page(page_id, processed_dir, s)
            except Exception as exc:
                self.log.warning(f"Processing failed for page {page_id}: {exc}")
                await self._report(f"process error page {page_id}")
                continue
            self.counters.pages_processed += 1
            self.counters.chunks += len(chunk_ids)
            self.ctx.processed_chunk_ids.extend(chunk_ids)
            await self._report(f"chunked page {page_id}")
            for cid in chunk_ids:
                await chunk_q.put(cid)

    async def _generate_worker(
        self, chunk_q, llm: LLMClient | None, gen_enabled: bool
    ) -> None:
        while True:
            item = await chunk_q.get()
            if item is _DONE:
                return
            # Drain (do not generate) when generation is off, already failed
            # fatally, or the user asked to pause. Draining rather than
            # returning keeps the upstream process pool unblocked.
            if not gen_enabled or self._gen_fatal or self.ctx.pause_requested or llm is None:
                self.counters.gen_skipped += 1
                continue

            record = self._load_record(item)
            if record is None:
                continue
            try:
                samples = await generate_from_chunk(
                    llm,
                    record,
                    format=self.ctx.format,
                    goal=self.ctx.goal,
                    n_per_chunk=self.ctx.n_per_chunk,
                    custom_system=self.ctx.custom_system_prompt,
                )
            except (MissingCredentialError, LLMConnectionError) as exc:
                # Unrecoverable for every worker, not just this one.
                self._gen_fatal = str(exc)
                if isinstance(exc, MissingCredentialError):
                    show_error(exc.credential)
                else:
                    show_error("LLM_CONNECTION", extra=str(exc))
                self.counters.gen_skipped += 1
                continue
            except Exception as exc:
                self.log.warning(f"Generation failed for chunk {item}: {exc}")
                self.counters.gen_skipped += 1
                continue

            for sample in samples:
                sid = persist_sample(
                    self.ctx.settings.db_path, self.ctx.session_id, sample
                )
                if sid:
                    self.ctx.synthetic_sample_ids.append(sid)
                    self.counters.samples += 1
            await self._report(f"generated from chunk {item}")

    # -- Helpers -------------------------------------------------------------

    async def _chunk_page(self, page_id: int, processed_dir: Path, s) -> list[int]:
        with open_session(s.db_path) as db:
            page = db.get(ScrapedPage, page_id)
            if page is None or not page.raw_path:
                return []
            url, title = page.url, page.title
            author, date, raw_path = page.author, page.published_date, page.raw_path

        try:
            raw_text = Path(raw_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return []

        cleaned = clean(raw_text)
        if not is_content_rich(cleaned):
            self.log.debug(f"Skipping low-content page: {url}")
            return []

        return await asyncio.to_thread(
            _process_page_sync,
            cleaned,
            page_id,
            url,
            title,
            author,
            date,
            self.ctx.session_id,
            s.chunk_size,
            s.chunk_overlap,
            s.db_path,
            processed_dir,
        )

    def _load_record(self, chunk_id: int) -> DataRecord | None:
        with open_session(self.ctx.settings.db_path) as db:
            c = db.get(ProcessedChunk, chunk_id)
            if c is None or c.id is None:
                return None
            meta = c.parsed_meta()
            return DataRecord(
                chunk_id=c.id,
                source_url=meta.get("source_url", ""),
                title=meta.get("title", ""),
                content=c.content,
                token_count=c.token_count,
                metadata=meta,
            )

    def _seed_work(self) -> tuple[list[str], list[int], list[int]]:
        """Return (urls to scrape, pages awaiting chunking, chunks awaiting generation).

        On a fresh run only the URL list is non-empty. On resume the two
        backlogs carry work that was already persisted but not yet consumed by
        the next stage — the queue equivalent of replaying from a stored offset.
        """
        urls = list(self.ctx.selected_urls or self.ctx.discovered_urls)
        sid = self.ctx.session_id

        with open_session(self.ctx.settings.db_path) as db:
            scraped_urls = {
                r.url
                for r in db.exec(
                    select(DiscoveredURL)
                    .where(DiscoveredURL.session_id == sid)
                    .where(DiscoveredURL.scraped)
                ).all()
            }
            pages = db.exec(
                select(ScrapedPage).where(ScrapedPage.session_id == sid)
            ).all()
            chunks = db.exec(
                select(ProcessedChunk).where(ProcessedChunk.session_id == sid)
            ).all()
            samples = db.exec(
                select(SyntheticSample).where(SyntheticSample.session_id == sid)
            ).all()

        chunked_page_ids = {c.page_id for c in chunks}
        generated_chunk_ids = {s.chunk_id for s in samples}

        pending_urls = [u for u in urls if u not in scraped_urls]
        pending_pages = [
            p.id for p in pages if p.id is not None and p.id not in chunked_page_ids
        ]
        pending_chunks = [
            c.id for c in chunks if c.id is not None and c.id not in generated_chunk_ids
        ]

        if scraped_urls or chunked_page_ids:
            self.log.info(
                f"Resuming stream: {len(pending_urls)} URLs, "
                f"{len(pending_pages)} pages, {len(pending_chunks)} chunks pending"
            )
        return pending_urls, pending_pages, pending_chunks

    def _generation_available(self) -> bool:
        from dataforge.cli.preflight import check_llm_credentials

        ok, _ = check_llm_credentials()
        return ok

    async def _report(self, item: str) -> None:
        if self._progress_cb:
            await self._progress_cb(self.counters.as_dict(), item)

    def _finalise(self, llm: LLMClient | None) -> None:
        c = self.counters
        if llm is not None:
            self.ctx.llm_usage = {
                "total_calls":       llm.usage.total_calls,
                "prompt_tokens":     llm.usage.prompt_tokens,
                "completion_tokens": llm.usage.completion_tokens,
                "cost_usd":          llm.usage.cost_usd,
                "errors":            llm.usage.errors,
            }
        if self._gen_fatal:
            show_warning(
                f"Generation stopped early: {self._gen_fatal}",
                f"{c.samples} samples saved, {c.gen_skipped} chunks left ungenerated. "
                f"Fix the issue and resume: dataforge resume {self.ctx.session_id[:8]}",
            )
        cost = f"  (cost: ${llm.usage.cost_usd:.4f})" if llm is not None else ""
        self.log.info(
            f"Stream complete: {c.scraped} pages scraped, {c.chunks} chunks, "
            f"{c.samples} samples{cost}"
        )
