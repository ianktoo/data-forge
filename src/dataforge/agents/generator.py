"""GeneratorAgent — LLM-powered synthetic sample creation."""
from __future__ import annotations

import json

from sqlmodel import select

from dataforge.generators import LLMClient, GeneratedSample, generate_batch
from dataforge.processors.formatter import DataRecord
from dataforge.storage import ProcessedChunk, SyntheticSample, open_session
from dataforge.utils.errors import (
    LLMConnectionError,
    MissingCredentialError,
    show_error,
    show_warning,
)

from .base import BaseAgent, PipelineContext


class GeneratorAgent(BaseAgent):
    name = "generator"

    def __init__(self, context: PipelineContext, progress_cb=None) -> None:
        super().__init__(context)
        self._progress_cb = progress_cb

    async def run(self) -> PipelineContext:
        s = self.ctx.settings

        with open_session(s.db_path) as db:
            chunks = db.exec(
                select(ProcessedChunk)
                .where(ProcessedChunk.session_id == self.ctx.session_id)
            ).all()

        if not chunks:
            self.log.warning("No processed chunks to generate from")
            return self.ctx

        records = [
            DataRecord(
                chunk_id=c.id,
                source_url=c.parsed_meta().get("source_url", ""),
                title=c.parsed_meta().get("title", ""),
                content=c.content,
                token_count=c.token_count,
                metadata=c.parsed_meta(),
            )
            for c in chunks
        ]

        self.log.info(f"Generating samples from {len(records)} chunks "
                      f"(format={self.ctx.format}, n={self.ctx.n_per_chunk})")

        llm = LLMClient()
        sample_ids: list[int] = []
        done = 0

        try:
            async for sample in generate_batch(
                llm, records,
                format=self.ctx.format,
                goal=self.ctx.goal,
                n_per_chunk=self.ctx.n_per_chunk,
                custom_system=self.ctx.custom_system_prompt,
                concurrency=3,
            ):
                sid = self._persist(sample)
                if sid:
                    sample_ids.append(sid)
                done += 1
                if self._progress_cb:
                    await self._progress_cb(done, len(records) * self.ctx.n_per_chunk)
        except MissingCredentialError as exc:
            show_error(exc.credential)
            show_warning(
                f"Generation stopped after {len(sample_ids)} samples.",
                "Add your API key to .env and resume: dataforge resume <session-id>",
            )
        except LLMConnectionError as exc:
            show_error("LLM_CONNECTION", extra=str(exc))
            show_warning(
                f"Generation stopped after {len(sample_ids)} samples.",
                "Check your connection and resume: dataforge resume <session-id>",
            )
        except Exception as exc:
            show_warning(
                f"Generation interrupted: {exc}",
                f"Partial results saved ({len(sample_ids)} samples). "
                "Resume with: dataforge resume <session-id>",
            )
            self.log.error(f"Generation error: {exc}", exc_info=True)

        self.ctx.synthetic_sample_ids = sample_ids
        if sample_ids:
            self.log.info(f"Generated {len(sample_ids)} samples  "
                          f"(cost: ${llm.usage.cost_usd:.4f})")
        return self.ctx

    def _persist(self, sample: GeneratedSample) -> int | None:
        with open_session(self.ctx.settings.db_path) as db:
            row = SyntheticSample(
                session_id=self.ctx.session_id,
                chunk_id=sample.chunk_id,
                format=sample.format,
                system_prompt=sample.system_prompt,
                messages_json=json.dumps(sample.messages, ensure_ascii=False),
                quality_score=0.0,
                approved=False,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
