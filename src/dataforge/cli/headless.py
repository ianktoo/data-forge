"""Headless execution of a recipe file — no prompts, CI-friendly exit codes.

This is the non-interactive counterpart to the wizard in :mod:`dataforge.cli.app`.
Every decision the wizard would ask about comes from the recipe, and every
between-stage menu is replaced by a printed summary, so the run is safe to
launch from cron, a Makefile, or a CI job.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from dataforge.agents import PipelineContext
from dataforge.config import get_settings
from dataforge.storage import PipelineStage, init_db, open_session, persist_url_selection
from dataforge.utils import get_logger

from . import ui
from .recipe import Recipe, RecipeError, load_recipe

log = get_logger("headless")

# Exit codes — distinct so CI can branch on the outcome.
EXIT_OK = 0
EXIT_RECIPE_INVALID = 2
EXIT_NO_URLS = 3
EXIT_PAUSED = 4
EXIT_NO_SAMPLES = 5


async def run_recipe(recipe_path: str | Path, *, dry_run: bool = False) -> int:
    """Execute a recipe. Returns a process exit code."""
    try:
        recipe = load_recipe(recipe_path)
    except RecipeError as exc:
        ui.error("Invalid recipe")
        for line in str(exc).splitlines():
            ui.warn(f"  {line}")
        return EXIT_RECIPE_INVALID

    base_dir = Path(recipe_path).expanduser().resolve().parent
    try:
        seed_urls = recipe.resolve_seed_urls(base_dir)
    except RecipeError as exc:
        ui.error("Invalid recipe")
        for line in str(exc).splitlines():
            ui.warn(f"  {line}")
        return EXIT_RECIPE_INVALID

    s = get_settings()
    recipe.apply_to_settings(s)

    if dry_run:
        _print_plan(recipe, seed_urls, s)
        return EXIT_OK

    s.output_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
    init_db(s.db_path)

    session_id = str(uuid.uuid4())
    ctx = PipelineContext(
        session_id=session_id,
        session_name=recipe.name,
        goal=recipe.generation.goal,
        format=recipe.data_format(),
        seed_urls=seed_urls,
        settings=s,
        custom_system_prompt=recipe.generation.system_prompt,
        n_per_chunk=recipe.generation.n_per_chunk,
        ignore_robots=recipe.source.ignore_robots,
        skip_known=recipe.source.skip_known,
        quality_threshold=recipe.quality.threshold,
        quality_llm_judge=recipe.quality.llm_judge,
        quality_min_judge_score=recipe.quality.min_judge_score,
        generation_model=recipe.generation.model,
        quality_model=recipe.quality.model,
    )

    _print_plan(recipe, seed_urls, s)
    ui.info(f"Session ID: [bold]{session_id[:8]}[/]")
    ui.info(f"Database: [dim]{s.db_path.resolve()}[/]")

    return await _drive(ctx, recipe)


async def resume_recipe(ctx: PipelineContext, recipe: Recipe, start_from: str) -> int:
    """Resume an existing session under a recipe's configuration."""
    recipe.apply_to_settings(ctx.settings)
    return await _drive(ctx, recipe, start_from=start_from)


async def _drive(ctx: PipelineContext, recipe: Recipe, start_from: str | None = None) -> int:
    from dataforge.agents import Orchestrator

    no_urls = False

    async def stage_hook(stage: str, context: PipelineContext) -> bool:
        nonlocal no_urls
        if stage == PipelineStage.discovery:
            total = len(context.discovered_urls)
            selected = recipe.filter_urls(context.discovered_urls)
            ui.info(
                f"Discovery: {total} URL(s) found, {len(selected)} kept after "
                f"include/exclude/max_urls filters."
            )
            if not selected:
                ui.warn(
                    "No URLs survived the filters. Loosen source.include / "
                    "source.exclude in the recipe."
                )
                no_urls = True
                return False
            context.selected_urls = selected
            with open_session(context.settings.db_path) as db:
                persist_url_selection(db, context.session_id, set(selected))
        else:
            _print_stage_line(stage, context)
        return True

    orch = Orchestrator(
        ctx,
        stage_hook=stage_hook,
        stream_progress_cb=_stream_progress(),
        scraper_progress_cb=_count_progress("Scraping"),
        generator_progress_cb=_count_progress("Generating"),
        export_kwargs=recipe.export_kwargs(),
        stream=recipe.stream,
    )

    ctx = await orch.run(start_from=start_from)

    if no_urls:
        return EXIT_NO_URLS
    if ctx.pause_requested:
        ui.warn(f"Paused. Resume with: dataforge resume {ctx.session_id[:8]}")
        return EXIT_PAUSED

    if ctx.export_records:
        ui.export_summary(ctx.export_records)

    approved = len(ctx.approved_sample_ids)
    ui.success(
        f"Run complete — session {ctx.session_id[:8]}, "
        f"{approved} approved sample(s)."
    )
    if ctx.llm_usage:
        ui.info(
            f"LLM usage: {ctx.llm_usage.get('total_calls', 0)} calls, "
            f"${ctx.llm_usage.get('cost_usd', 0.0):.4f}"
        )
    if ctx.errors:
        ui.warn(f"{len(ctx.errors)} non-fatal error(s) recorded — see the session log.")

    return EXIT_OK if approved else EXIT_NO_SAMPLES


# -- Progress (line-based, safe for non-TTY logs) ----------------------------


def _stream_progress(every: int = 25):
    """Log a compact counter line periodically rather than animating a bar."""
    state = {"n": 0}

    async def cb(counters: dict, item: str = "") -> None:
        state["n"] += 1
        if state["n"] % every:
            return
        ui.info(
            f"  scraped {counters['scraped']}/{counters['urls_total']}  "
            f"chunks {counters['chunks']}  samples {counters['samples']}"
            + (f"  skipped {counters['gen_skipped']}" if counters["gen_skipped"] else "")
        )

    return cb


def _count_progress(label: str, every: int = 25):
    state = {"n": 0}

    async def cb(done: int, total: int, item: str = "") -> None:
        state["n"] += 1
        if state["n"] % every == 0 or done == total:
            ui.info(f"  {label}: {done}/{total}")

    return cb


# -- Reporting ---------------------------------------------------------------


def _print_plan(recipe: Recipe, seed_urls: list[str], s) -> None:
    mode = "streaming (overlapped)" if recipe.stream else "batch (sequential)"
    ui.section(f"Recipe: {recipe.name}")
    lines = [
        f"Mode:        {mode}",
        f"Seeds:       {len(seed_urls)} URL(s)",
        f"Format:      {recipe.generation.format}  ({recipe.generation.n_per_chunk} per chunk)",
        f"Model:       {recipe.generation.model or s.llm_model}",
        f"Rate limit:  {s.rate_limit} req/s",
        f"Threshold:   {recipe.quality.threshold}"
        + (f"  + LLM judge (min {recipe.quality.min_judge_score}/5)" if recipe.quality.llm_judge else "  (heuristic only)"),
        f"Export:      {', '.join(recipe.export.targets)}",
        f"Output:      {s.output_dir}",
    ]
    if recipe.source.language:
        lines.append(f"Language:    {recipe.source.language} (locale-prefixed URLs dropped)")
    if recipe.source.include:
        lines.append(f"Include:     {', '.join(recipe.source.include)}")
    if recipe.source.exclude:
        lines.append(f"Exclude:     {', '.join(recipe.source.exclude)}")
    if recipe.source.max_urls:
        lines.append(f"Max URLs:    {recipe.source.max_urls}")
    for line in lines:
        ui.info(f"  {line}")


def _print_stage_line(stage: str, ctx: PipelineContext) -> None:
    counts = {
        PipelineStage.streaming: (
            f"{len(ctx.scraped_page_ids)} pages, {len(ctx.processed_chunk_ids)} chunks, "
            f"{len(ctx.synthetic_sample_ids)} samples"
        ),
        PipelineStage.collection: f"{len(ctx.scraped_page_ids)} pages",
        PipelineStage.processing: f"{len(ctx.processed_chunk_ids)} chunks",
        PipelineStage.generation: f"{len(ctx.synthetic_sample_ids)} samples",
        PipelineStage.quality: f"{len(ctx.approved_sample_ids)} approved",
    }
    detail = counts.get(stage, "")
    ui.success(f"Stage '{stage}' complete" + (f" — {detail}" if detail else ""))
