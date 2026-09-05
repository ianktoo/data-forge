"""OrchestratorAgent — pipeline state machine with checkpointing."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Callable, Awaitable

from sqlmodel import select

from dataforge.cli.preflight import check_stage
from dataforge.storage import PipelineSession, PipelineStage, SessionStatus, open_session
from dataforge.utils import get_logger
from dataforge.utils.errors import LLMConnectionError, show_error, show_warning

from .base import BaseAgent, PipelineContext
from .explorer import ExplorerAgent
from .exporter import ExporterAgent
from .generator import GeneratorAgent
from .processor import ProcessorAgent
from .quality import QualityAgent
from .reviewer import ReviewerAgent
from .scraper import ScraperAgent

log = get_logger("orchestrator")

# stage_name → next_stage (plain strings throughout so log/error formatting
# and DB round-trips — where PipelineSession.stage is stored as a bare str —
# stay consistent instead of drifting between "quality" and "PipelineStage.quality")
_STAGE_FLOW: dict[str, str] = {
    PipelineStage.discovery.value:  PipelineStage.collection.value,
    PipelineStage.collection.value: PipelineStage.processing.value,
    PipelineStage.processing.value: PipelineStage.generation.value,
    PipelineStage.generation.value: PipelineStage.quality.value,
    PipelineStage.quality.value:    PipelineStage.export.value,
    PipelineStage.export.value:     PipelineStage.completed.value,
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
        pre_stage_hook: StageHook | None = None,
        scraper_progress_cb=None,
        generator_progress_cb=None,
        export_kwargs: dict | None = None,
        enable_review: bool = False,
        review_cost_cap: float = 1.0,
        review_min_score: int = 3,
    ) -> None:
        self.ctx = context
        self._hook = stage_hook
        self._pre_hook = pre_stage_hook
        self._scraper_cb = scraper_progress_cb
        self._gen_cb = generator_progress_cb
        self._export_kw = export_kwargs or {}
        self._enable_review  = enable_review
        self._review_cap     = review_cost_cap
        self._review_min     = review_min_score

    async def run(self, start_from: str | None = None) -> PipelineContext:
        s = self.ctx.settings
        self._init_session()

        stage = start_from or PipelineStage.discovery.value

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
                if self._pre_hook:
                    await self._pre_hook(stage, self.ctx)
                agent = self._build_agent(stage)
                self.ctx = await agent.run()
            except KeyboardInterrupt:
                log.info(f"Interrupted at stage '{stage}' — marking session paused")
                self._update_session_status(SessionStatus.paused)
                return self.ctx
            except Exception as exc:
                log.error(f"Stage '{stage}' failed: {exc}", exc_info=True)
                self.ctx.add_error(f"Stage '{stage}' error: {exc}")
                self._update_session_status(SessionStatus.paused)
                error_key = "LLM_CONNECTION" if isinstance(exc, LLMConnectionError) else stage
                show_error(error_key, extra=str(exc), stage=stage)
                return self.ctx

            self._checkpoint()

            # Mid-stage pause signal (e.g. scraper stopped early on Ctrl+C)
            if self.ctx.pause_requested:
                log.info(f"Pipeline paused after stage '{stage}' (pause_requested)")
                self._update_session_status(SessionStatus.paused)
                return self.ctx

            if self._hook:
                # The hook runs interactive prompts (continue/export/pause/adjust) —
                # an interrupt or error here must be handled the same way as one
                # during agent.run(), or the session is left status=active in the
                # DB with no process actually running it (invisible to `dataforge
                # resume`'s auto-detect, which only looks for status=paused).
                try:
                    proceed = await self._hook(stage, self.ctx)
                except KeyboardInterrupt:
                    log.info(f"Interrupted after stage '{stage}' — marking session paused")
                    self._update_session_status(SessionStatus.paused)
                    return self.ctx
                except Exception as exc:
                    log.error(f"Stage-hook after '{stage}' failed: {exc}", exc_info=True)
                    self.ctx.add_error(f"Stage-hook error after '{stage}': {exc}")
                    self._update_session_status(SessionStatus.paused)
                    show_error(stage, extra=str(exc), stage=stage)
                    return self.ctx
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
        """Persist current context summary to the session record.

        Uses a max-merge strategy: keeps the higher of the existing vs new count
        for each key, so a resumed pipeline with a partially-populated context
        never zeros out counts saved by earlier stages.

        Gated on ``settings.autosave`` (default True) — the per-record DB writes
        each agent already does (scraped pages, chunks, samples) are unaffected;
        this only controls the summary checkpoint used to display/report progress.
        """
        if not self.ctx.settings.autosave:
            return
        with open_session(self.ctx.settings.db_path) as db:
            session = db.get(PipelineSession, self.ctx.session_id)
            if session:
                existing = json.loads(session.config_json or "{}")
                new = {
                    "discovered": len(self.ctx.discovered_urls),
                    "selected":   len(self.ctx.selected_urls),
                    "scraped":    len(self.ctx.scraped_page_ids),
                    "chunks":     len(self.ctx.processed_chunk_ids),
                    "samples":    len(self.ctx.synthetic_sample_ids),
                    "approved":   len(self.ctx.approved_sample_ids),
                }
                merged = {k: max(existing.get(k, 0), v) for k, v in new.items()}
                session.updated_at = datetime.now(UTC)
                session.config_json = json.dumps(merged)
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
