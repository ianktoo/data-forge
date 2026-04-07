"""ProcessorAgent — clean, chunk, and structure scraped pages."""
from __future__ import annotations

import json
from pathlib import Path

from sqlmodel import select

from dataforge.processors import chunk, clean, format_records, is_content_rich, token_count
from dataforge.storage import ProcessedChunk, ScrapedPage, open_session

from .base import BaseAgent, PipelineContext


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
        chunk_ids: list[int] = []

        for page in pages:
            if not page.raw_path or not Path(page.raw_path).exists():
                continue
            raw_text = Path(page.raw_path).read_text(encoding="utf-8")
            cleaned = clean(raw_text)
            if not is_content_rich(cleaned):
                self.log.debug(f"Skipping low-content page: {page.url}")
                continue

            chunks = chunk(cleaned, size=s.chunk_size, overlap=s.chunk_overlap)
            token_counts = [token_count(c) for c in chunks]

            records = format_records(
                chunks,
                page_id=page.id,
                url=page.url,
                title=page.title,
                author=page.author,
                date=page.published_date,
                session_id=self.ctx.session_id,
                token_counts=token_counts,
            )

            with open_session(s.db_path) as db:
                for rec in records:
                    db_chunk = ProcessedChunk(
                        session_id=self.ctx.session_id,
                        page_id=page.id,
                        content=rec.content,
                        token_count=rec.token_count,
                        chunk_index=rec.metadata["chunk_index"],
                        metadata_json=json.dumps(rec.metadata),
                    )
                    db.add(db_chunk)
                    db.commit()
                    db.refresh(db_chunk)
                    chunk_ids.append(db_chunk.id)

            # Save processed chunks as JSONL
            out_path = processed_dir / f"page_{page.id:05d}.jsonl"
            with out_path.open("w", encoding="utf-8") as f:
                for rec in records:
                    f.write(rec.to_jsonl() + "\n")

        self.ctx.processed_chunk_ids = chunk_ids
        self.log.info(f"Processing complete: {len(chunk_ids)} chunks")
        return self.ctx
