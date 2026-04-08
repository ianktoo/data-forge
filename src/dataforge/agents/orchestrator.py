"""OrchestratorAgent — pipeline state machine with checkpointing."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Callable, Awaitable

from sqlmodel import select

from dataforge.cli.preflight import check_stage
from dataforge.storage import PipelineSession, PipelineStage, SessionStatus, open_session
from dataforge.utils import get_logger
from dataforge.utils.errors import NoContentError, show_warning

from .base import BaseAgent, PipelineContext
from .explorer import ExplorerAgent
from .exporter import ExporterAgent
from .generator import GeneratorAgent
from .processor import ProcessorAgent
from .quality import QualityAgent
from .reviewer import ReviewerAgent
from .scraper import ScraperAgent

log = get_logger("orchestrator")

# stage_name → next_stage
_STAGE_FLOW: dict[str, str] = {
    PipelineStage.discovery:  PipelineStage.collection,
    PipelineStage.collection: PipelineStage.processing,
    PipelineStage.processing: PipelineStage.generation,
    PipelineStage.generation: PipelineStage.quality,
    PipelineStage.quality:    PipelineStage.export,
    PipelineStage.export:     PipelineStage.completed,
}

# Checkpoint hook: called after each stage with (stage, context)
StageHook = Callable[[str, PipelineContext], Awaitable[bool]]  # returns True = continue


class Orchestrator:
    """Runs the pipeline stage by stage, calling hooks between stages."""

    def __init__(
        self,
        context: PipelineContext,
        *,
        stage_hook: StageHook | None = None,
        scraper_progress_cb=None,
        generator_progress_cb=None,
        export_kwargs: dict | None = None,
        enable_review: bool = False,
        review_cost_cap: float = 1.0,
        review_min_score: int = 3,
    ) -> None:
        self.ctx = context
        self._hook = stage_hook
        self._scraper_cb = scraper_progress_cb
        self._gen_cb = generator_progress_cb
        self._export_kw = export_kwargs or {}
        self._enable_review  = enable_review
        self._review_cap     = review_cost_cap
        self._review_min     = review_min_score

    async def run(self, start_from: str | None = None) -> PipelineContext:
        s = self.ctx.settings
        self._init_session()

        stage = start_from or PipelineStage.discovery

        while stage != PipelineStage.completed:
            log.info(f"▶ Stage: {stage}")
            self._update_session_stage(stage)
            self.ctx.current_stage = stage

            # ── Pre-flight check ──────────────────────────────────────────────
            preflight = check_stage(stage)
            if not preflight.ok:
                if preflight.skip:
                    # Skippable stage (e.g. generation without LLM key)
                    log.warning(f"Skipping stage '{stage}': {preflight.error_key}")
                    stage = _STAGE_FLOW.get(stage, PipelineStage.completed)
                    continue
                else:
                    # Hard requirement — pause
                    self._update_session_status(SessionStatus.paused)
                    return self.ctx

            # ── Guard: skip quality/export if nothing was generated ────────────
            if stage == PipelineStage.quality and not self.ctx.synthetic_sample_ids:
                show_warning(
                    "No synthetic samples to evaluate — generation was skipped or produced nothing.",
                    "You can still export the processed chunks: dataforge export <session-id>",
                )
                stage = _STAGE_FLOW.get(stage, PipelineStage.completed)
                continue

            try:
                agent = self._build_agent(stage)
                self.ctx = await agent.run()
            except Exception as exc:
                log.error(f"Stage '{stage}' failed: {exc}", exc_info=True)
                self.ctx.add_error(f"Stage '{stage}' error: {exc}")
                self._update_session_status(SessionStatus.paused)
                from dataforge.utils.errors import show_error
                show_error("LLM_CONNECTION" if "llm" in stage else stage,
                           extra=str(exc))
                return self.ctx

            self._checkpoint()

            if self._hook:
                proceed = await self._hook(stage, self.ctx)
                if not proceed:
                    log.info(f"Pipeline paused at stage: {stage}")
                    self._update_session_status(SessionStatus.paused)
                    return self.ctx

            stage = _STAGE_FLOW.get(stage, PipelineStage.completed)

        self._update_session_status(SessionStatus.completed)
        log.info("Pipeline completed successfully")
        return self.ctx

    def _build_agent(self, stage: str) -> BaseAgent:
        if stage == PipelineStage.discovery:
            return ExplorerAgent(self.ctx)
        if stage == PipelineStage.collection:
            return ScraperAgent(self.ctx, progress_cb=self._scraper_cb)
        if stage == PipelineStage.processing:
            return ProcessorAgent(self.ctx)
        if stage == PipelineStage.generation:
            return GeneratorAgent(self.ctx, progress_cb=self._gen_cb)
        if stage == PipelineStage.quality:
            return QualityAgent(self.ctx)
        if stage == PipelineStage.export:
            return ExporterAgent(self.ctx, **self._export_kw)
        raise ValueError(f"Unknown stage: {stage}")

    def _init_session(self) -> None:
        s = self.ctx.settings
        s.output_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        s.logs_dir().mkdir(parents=True, exist_ok=True)

        with open_session(s.db_path) as db:
            existing = db.get(PipelineSession, self.ctx.session_id)
            if not existing:
                db.add(PipelineSession(
                    id=self.ctx.session_id,
                    name=self.ctx.session_name,
                    goal=self.ctx.goal,
                    format=self.ctx.format,
                    stage=PipelineStage.discovery,
                    status=SessionStatus.active,
                    seed_urls=json.dumps(self.ctx.seed_urls),
                ))
                db.commit()

    def _checkpoint(self) -> None:
        """Persist current context summary to the session record."""
        with open_session(self.ctx.settings.db_path) as db:
            session = db.get(PipelineSession, self.ctx.session_id)
            if session:
                session.updated_at = datetime.now(UTC)
                session.config_json = json.dumps({
                    "discovered": len(self.ctx.discovered_urls),
                    "selected":   len(self.ctx.selected_urls),
                    "scraped":    len(self.ctx.scraped_page_ids),
                    "chunks":     len(self.ctx.processed_chunk_ids),
                    "samples":    len(self.ctx.synthetic_sample_ids),
                    "approved":   len(self.ctx.approved_sample_ids),
                })
                db.add(session)
                db.commit()

    def _update_session_stage(self, stage: str) -> None:
        with open_session(self.ctx.settings.db_path) as db:
            session = db.get(PipelineSession, self.ctx.session_id)
            if session:
                session.stage = stage
                session.status = SessionStatus.active
                db.add(session)
                db.commit()

    def _update_session_status(self, status: str) -> None:
        with open_session(self.ctx.settings.db_path) as db:
            session = db.get(PipelineSession, self.ctx.session_id)
            if session:
                session.status = status
                db.add(session)
                db.commit()
