"""ProcessorAgent — clean, chunk, and structure scraped pages."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from sqlmodel import select

from dataforge.processors import chunk, clean, format_records, is_content_rich, token_count
from dataforge.storage import ProcessedChunk, ScrapedPage, open_session

from .base import BaseAgent, PipelineContext

_CPU_SEMAPHORE: asyncio.Semaphore | None = None


def _cpu_semaphore() -> asyncio.Semaphore:
    global _CPU_SEMAPHORE
    if _CPU_SEMAPHORE is None:
        workers = max(1, min(os.cpu_count() or 4, 8))
        _CPU_SEMAPHORE = asyncio.Semaphore(workers)
    return _CPU_SEMAPHORE


def _process_page_sync(
    raw_text: str,
    page_id: int,
    url: str,
    title: str | None,
    author: str | None,
    date: str | None,
    session_id: str,
    chunk_size: int,
    chunk_overlap: int,
    db_path: Path,
    processed_dir: Path,
) -> list[int]:
    """Pure-CPU work: clean → chunk → store. Runs in a thread pool worker."""
    cleaned = clean(raw_text)
    if not is_content_rich(cleaned):
        return []

    chunks = chunk(cleaned, size=chunk_size, overlap=chunk_overlap)
    tc = [token_count(c) for c in chunks]
    records = format_records(
        chunks,
        page_id=page_id,
        url=url,
        title=title or "",
        author=author or "",
        date=date or "",
        session_id=session_id,
        token_counts=tc,
    )

    db_chunks: list[ProcessedChunk] = []
    with open_session(db_path) as db:
        for rec in records:
            db_chunk = ProcessedChunk(
                session_id=session_id,
                page_id=page_id,
                content=rec.content,
                token_count=rec.token_count,
                chunk_index=rec.metadata["chunk_index"],
                metadata_json=json.dumps(rec.metadata),
            )
            db.add(db_chunk)
            db_chunks.append(db_chunk)
        db.flush()
        chunk_ids = [c.id for c in db_chunks if c.id is not None]
        db.commit()

    out_path = processed_dir / f"page_{page_id:05d}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(rec.to_jsonl() + "\n")

    return chunk_ids


class ProcessorAgent(BaseAgent):
    name = "processor"

    async def run(self) -> PipelineContext:
        s = self.ctx.settings
        processed_dir = self._stage_dir("processed")

        with open_session(s.db_path) as db:
            pages = db.exec(
                select(ScrapedPage)
                .where(ScrapedPage.session_id == self.ctx.session_id)
            ).all()

        self.log.info(f"Processing {len(pages)} scraped pages")

        async def _process(page: ScrapedPage) -> list[int]:
            if page.id is None or not page.raw_path or not Path(page.raw_path).exists():
                return []
            raw_text = Path(page.raw_path).read_text(encoding="utf-8")
            async with _cpu_semaphore():
                return await asyncio.to_thread(
                    _process_page_sync,
                    raw_text,
                    page.id,
                    page.url,
                    page.title,
                    page.author,
                    page.published_date,
                    self.ctx.session_id,
                    s.chunk_size,
                    s.chunk_overlap,
                    s.db_path,
                    processed_dir,
                )

        results = await asyncio.gather(*[_process(p) for p in pages], return_exceptions=True)
        chunk_ids = []
        for r in results:
            if isinstance(r, BaseException):
                self.log.warning(f"Page processing error (skipped): {r}")
            else:
                chunk_ids.extend(r)

        self.ctx.processed_chunk_ids = chunk_ids
        self.log.info(f"Processing complete: {len(chunk_ids)} chunks")
        return self.ctx
