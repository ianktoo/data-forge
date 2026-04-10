"""Typer CLI application — all commands and the interactive pipeline wizard."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Literal, Optional

import typer
from rich.console import Console
from sqlmodel import select

from dataforge.agents import Orchestrator, PipelineContext
from dataforge.agents.exporter import ExporterAgent
from dataforge.config import PROVIDER_INFO, get_settings
from dataforge.storage import (
    DataFormat,
    DiscoveredURL,
    PipelineSession,
    PipelineStage,
    SessionStatus,
    SyntheticSample,
    init_db,
    open_session,
)
from dataforge.utils import get_logger, setup_logging, system_info

from . import prompts, ui

def _typer_error_handler(error: Exception) -> None:
    """Called by Typer when an unknown subcommand is entered."""
    msg = str(error)
    if "No such command" in msg or "no such option" in msg.lower():
        _VALID_COMMANDS = [
            "pipeline", "explore", "resume", "sessions",
            "export", "config", "providers", "info", "test-llm", "update",
        ]
        # Try to find the closest match
        import difflib
        parts = msg.split("'")
        bad = parts[1] if len(parts) >= 2 else ""
        closest = difflib.get_close_matches(bad, _VALID_COMMANDS, n=1, cutoff=0.5)
        hint = f"Did you mean [bold cyan]{closest[0]}[/]?" if closest else ""
        from rich.console import Console as RC
        from rich.panel import Panel
        from rich.text import Text
        from rich import box
        c = RC(stderr=True)
        body = Text()
        body.append(f"'{bad}' is not a valid command.\n\n", style="white")
        if hint:
            body.append(f"{hint}\n\n", style="yellow")
        body.append("Valid commands: " + "  ".join(f"[cyan]{x}[/cyan]" for x in _VALID_COMMANDS))
        c.print(Panel(body, title="[bold red]Unknown command[/]",
                      border_style="red", box=box.ROUNDED, padding=(0, 1)))
    raise SystemExit(2)


app = typer.Typer(
    name="dataforge",
    help="LLM data collection and synthetic fine-tuning pipeline.",
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
log = get_logger("cli")

# Type alias for step result sentinels
StepResult = Literal["next", "back", "back_to_urls", "back_to_config", "home", "exit"]


def _bootstrap() -> None:
    from dataforge.cli.preflight import check_env_file
    check_env_file()
    s = get_settings()
    setup_logging(s.logs_dir(), s.log_level)
    init_db(s.db_path)


# ── Default: interactive pipeline ─────────────────────────────────────────────

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Launch the interactive guided pipeline (default when no subcommand given)."""
    if ctx.invoked_subcommand is None:
        _bootstrap()
        ui.banner()
        asyncio.run(_interactive_pipeline())


# ── pipeline command (alias for interactive mode) ─────────────────────────────

@app.command()
def pipeline() -> None:
    """Start a new interactive pipeline."""
    _bootstrap()
    ui.banner()
    asyncio.run(_interactive_pipeline())


# ── explore command ───────────────────────────────────────────────────────────

@app.command()
def explore(url: str = typer.Argument(..., help="URL or sitemap URL to explore")) -> None:
    """Quickly discover and display URLs from a sitemap."""
    _bootstrap()
    asyncio.run(_run_explore(url))


async def _run_explore(url: str) -> None:
    from dataforge.collectors import HTTPClient, discover_sitemap_url, parse_sitemap
    from dataforge.utils import RateLimiter

    s = get_settings()
    limiter = RateLimiter(s.rate_limit)
    ui.section("URL Discovery")

    async with HTTPClient(limiter) as client:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        if url.endswith(".xml"):
            sitemap_url = url
        else:
            sitemap_url = await discover_sitemap_url(client, base)

        if sitemap_url:
            ui.info(f"Sitemap: {sitemap_url}")
            urls = await parse_sitemap(client, sitemap_url)
        else:
            ui.warn("No sitemap found. Showing seed URL only.")
            urls = [url]

    ui.success(f"Found {len(urls)} URLs")
    ui.url_table(urls)


# ── resume command ────────────────────────────────────────────────────────────

@app.command()
def resume(session_id: str = typer.Argument(..., help="Session ID to resume")) -> None:
    """Resume a paused pipeline session."""
    _bootstrap()
    asyncio.run(_resume_session(session_id))


async def _resume_session(session_id: str) -> None:
    s = get_settings()
    with open_session(s.db_path) as db:
        session = db.get(PipelineSession, session_id)
        if not session:
            # Allow prefix match
            all_s = db.exec(select(PipelineSession)).all()
            matches = [x for x in all_s if x.id.startswith(session_id)]
            if len(matches) == 1:
                session = matches[0]
            elif len(matches) > 1:
                ui.error("Ambiguous session ID prefix — be more specific")
                return
            else:
                ui.error(f"Session '{session_id}' not found")
                return

        if session.status == SessionStatus.completed:
            ui.warn("Session already completed. Use 'dataforge export' to re-export.")
            return

        ctx = PipelineContext(
            session_id=session.id,
            session_name=session.name,
            goal=session.goal,
            format=DataFormat(session.format),
            seed_urls=session.seed_url_list(),
            settings=s,
        )

    ui.banner()
    ui.info(f"Resuming session [bold]{session.name}[/] from stage [cyan]{session.stage}[/]")
    await _run_orchestrator(ctx, start_from=session.stage)


# ── sessions command ──────────────────────────────────────────────────────────

@app.command()
def sessions() -> None:
    """List all pipeline sessions."""
    _bootstrap()
    s = get_settings()
    with open_session(s.db_path) as db:
        all_sessions = db.exec(select(PipelineSession)).all()

    if not all_sessions:
        ui.info("No sessions found. Run 'dataforge pipeline' to start.")
        return

    rows = []
    for sess in sorted(all_sessions, key=lambda x: x.created_at, reverse=True):
        cfg = sess.config()
        rows.append({
            "id":      sess.id,
            "name":    sess.name,
            "stage":   sess.stage,
            "status":  sess.status,
            "urls":    cfg.get("discovered", 0),
            "samples": cfg.get("approved", 0),
            "created": sess.created_at.strftime("%Y-%m-%d %H:%M"),
        })
    ui.sessions_table(rows)


# ── export command ────────────────────────────────────────────────────────────

@app.command()
def export(
    session_id: str = typer.Argument(..., help="Session ID to export"),
    approved_only: bool = typer.Option(True, "--approved/--all", help="Export only approved samples"),
) -> None:
    """Export data from any stage of a session."""
    _bootstrap()
    asyncio.run(_export_session(session_id, approved_only))


async def _export_session(session_id: str, approved_only: bool) -> None:
    s = get_settings()
    with open_session(s.db_path) as db:
        session = db.get(PipelineSession, session_id)

    if not session:
        ui.error(f"Session '{session_id}' not found")
        raise typer.Exit(1)

    # Check what's available
    with open_session(s.db_path) as db:
        sample_count = len(db.exec(
            select(SyntheticSample).where(SyntheticSample.session_id == session_id)
        ).all())

    if sample_count == 0:
        ui.warn("No synthetic samples yet. Run at least through the generation stage.")
        raise typer.Exit(1)

    ui.info(f"{sample_count} samples available")
    targets = await prompts.ask_export_targets(
        hf_configured=bool(s.huggingface_token),
        kg_configured=bool(s.kaggle_username and s.kaggle_key),
    )

    export_kw: dict = {"targets": targets, "approved_only": approved_only,
                       "stage_snapshot": session.stage}

    if "huggingface" in targets:
        export_kw["hf_repo_id"] = await prompts.ask_hf_repo()
        export_kw["hf_private"] = await prompts.ask_hf_private()

    if "kaggle" in targets:
        export_kw["kaggle_slug"] = await prompts.ask_kaggle_slug(s.kaggle_username)
        export_kw["kaggle_title"] = session.name

    ctx = PipelineContext(
        session_id=session.id,
        session_name=session.name,
        goal=session.goal,
        format=DataFormat(session.format),
        seed_urls=session.seed_url_list(),
        settings=s,
    )
    agent = ExporterAgent(ctx, **export_kw)
    ctx = await agent.run()
    ui.export_summary(ctx.export_records)
    ui.success("Export complete")


# ── config command ────────────────────────────────────────────────────────────

@app.command()
def config() -> None:
    """Interactively configure LLM provider and defaults."""
    _bootstrap()
    asyncio.run(_configure())


async def _configure() -> None:
    ui.section("Configuration")
    s = get_settings()

    provider = await prompts.ask_provider()
    info = PROVIDER_INFO[provider]
    model = await prompts.ask_model(info.models)

    env_path = Path(".env")
    if not env_path.exists():
        env_path.write_text("")

    lines = env_path.read_text().splitlines()
    updated = _set_env_var(lines, "DATAFORGE_LLM_PROVIDER", provider)
    updated = _set_env_var(updated, "DATAFORGE_LLM_MODEL", model)
    env_path.write_text("\n".join(updated) + "\n")

    # Persist provider/model to user prefs (cross-project)
    from dataforge.cli import prefs as user_prefs
    user_prefs.set("llm_provider", provider)
    user_prefs.set("llm_model", model)

    if info.requires_key:
        import getpass
        existing = os.getenv(info.key_env, "")
        masked = f"{existing[:8]}..." if len(existing) > 8 else ("set" if existing else "")
        prompt_label = (
            f"  {info.key_env} [{masked}] (leave blank to keep): "
            if existing else
            f"  {info.key_env} (paste your key, input hidden): "
        )
        key_value = getpass.getpass(prompt_label)
        if key_value:
            os.environ[info.key_env] = key_value
            save_globally = await prompts.ask_save_key_globally()
            if save_globally:
                user_prefs.set_api_key(info.key_env, key_value)
                ui.success(f"Saved {info.key_env} globally → {user_prefs._prefs_path()}")
            else:
                updated = _set_env_var(updated, info.key_env, key_value)
                env_path.write_text("\n".join(updated) + "\n")
                ui.success(f"Saved {info.key_env} to {env_path.resolve()}")
        elif not existing:
            ui.info(f"No key entered — set {info.key_env} via 'dataforge config' when ready")

    ui.success(f"Saved: provider={provider}, model={model}")


def _set_env_var(lines: list[str], key: str, value: str) -> list[str]:
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            return lines
    lines.append(f"{key}={value}")
    return lines


# ── providers command ─────────────────────────────────────────────────────────

@app.command()
def providers() -> None:
    """List available LLM providers and their models."""
    from rich.table import Table
    from rich import box
    t = Table(box=box.SIMPLE_HEAD, title="Available Providers")
    t.add_column("Provider")
    t.add_column("Models")
    t.add_column("API Key Required")
    for name, info in PROVIDER_INFO.items():
        t.add_row(info.name, "\n".join(info.models), "Yes" if info.requires_key else "No (local)")
    console.print(t)


# ── test-llm command ──────────────────────────────────────────────────────────

@app.command(name="test-llm")
def test_llm() -> None:
    """Send a test prompt to the configured LLM provider."""
    _bootstrap()
    asyncio.run(_test_llm())


async def _test_llm() -> None:
    from dataforge.generators import LLMClient
    s = get_settings()
    ui.info(f"Testing {s.llm_provider} / {s.llm_model}...")
    client = LLMClient()
    ok = await client.test_connection()
    if ok:
        ui.success("LLM connection successful")
    else:
        ui.error("LLM connection failed — check your API key and model name")


# ── update command ───────────────────────────────────────────────────────────

@app.command()
def update() -> None:
    """Update DataForge to the latest version via pip."""
    import subprocess
    import sys
    ui.info("Checking for updates...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "dataforge"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        ui.success("DataForge is up to date.")
        if "Successfully installed" in result.stdout:
            # Extract installed version from pip output
            for line in result.stdout.splitlines():
                if "Successfully installed" in line:
                    ui.info(line.strip())
                    break
    else:
        ui.error("Update failed.")
        console.print(result.stderr.strip(), style="dim red")


# ── info command ──────────────────────────────────────────────────────────────

@app.command()
def info() -> None:
    """Show system information and environment status."""
    _bootstrap()
    s = get_settings()
    sysinfo = system_info()
    stats = {
        "OS":            f"{sysinfo['os']} {sysinfo['os_version'][:40]}",
        "Python":        sysinfo["python"],
        "CPU cores":     sysinfo["cpu_cores"],
        "RAM available": f"{sysinfo['ram_available_gb']} GB",
        "Disk free":     f"{sysinfo['disk_free_gb']} GB",
        "Provider":      s.llm_provider,
        "Model":         s.llm_model,
        "Rate limit":    f"{s.rate_limit} req/s",
        "Output dir":    str(s.output_dir),
        "Database":      str(s.db_path),
        "HF token":      "set" if s.huggingface_token else "not set",
        "Kaggle":        "configured" if s.kaggle_username else "not configured",
    }
    ui.stats_panel(stats)


# ── Wizard step functions ─────────────────────────────────────────────────────

async def _step_urls(state: dict) -> StepResult:
    """Step 1: collect seed URLs. Populates state['seed_urls']."""
    try:
        urls = await _collect_urls()
    except KeyboardInterrupt:
        return "home"
    result = None if urls is None else urls
    if result is None:
        return "back"
    if not result:
        ui.error("No valid URLs provided")
        return "back"
    state["seed_urls"] = result
    return "next"


async def _step_config(state: dict) -> StepResult:
    """Step 2: collect session config. Populates state keys for name/goal/fmt/n."""
    try:
        session_name = await prompts.ask_session_name()
        if session_name is None:
            return "back"
        state["session_name"] = session_name

        goal = await prompts.ask_goal()
        if goal is None:
            return "back"
        state["goal"] = goal

        fmt = await prompts.ask_format()
        if fmt is None:
            return "back"
        state["fmt"] = fmt

        state["custom_sys"] = ""
        if state["fmt"] == "custom":
            custom_sys = await prompts.ask_custom_system_prompt()
            if custom_sys is None:
                return "back"
            state["custom_sys"] = custom_sys

        n_per_chunk = await prompts.ask_n_per_chunk()
        if n_per_chunk is None:
            return "back"
        state["n_per_chunk"] = n_per_chunk

        ignore_robots = await prompts.ask_ignore_robots()
        if ignore_robots:
            ui.warn("robots.txt enforcement disabled — ensure you have permission to scrape this site.")
        state["ignore_robots"] = ignore_robots
    except KeyboardInterrupt:
        return "back"
    return "next"


async def _step_review(state: dict) -> StepResult:
    """Step 3: summary panel + confirm/edit choice."""
    ui.review_panel(state)
    try:
        action = await prompts.ask_review_action()
    except KeyboardInterrupt:
        return "back"
    if action is None:
        return "back"
    if action == "start":
        return "next"
    if action == "edit_urls":
        return "back_to_urls"
    if action == "edit_config":
        return "back_to_config"
    if action == "cancel":
        return "home"
    return "next"


# ── Interactive pipeline wizard ────────────────────────────────────────────────

async def _interactive_pipeline() -> None:
    from dataforge.cli import prefs as user_prefs
    s = get_settings()

    # Show where files will land so the user always knows what's happening
    ui.info(f"Output:   [dim]{s.output_dir.resolve()}[/]")
    ui.info(f"Database: [dim]{s.db_path.resolve()}[/]")
    ui.info(f"Config:   [dim]{user_prefs._prefs_path()}[/]")

    # ── Main menu loop (allows returning here without restarting the process) ──
    while True:
        action = await _main_menu()
        if action == "resume":
            await _pick_and_resume()
            continue
        if action == "sessions":
            with open_session(s.db_path) as db:
                all_s = db.exec(select(PipelineSession)).all()
            rows = [{"id": x.id, "name": x.name, "stage": x.stage,
                     "status": x.status, "urls": 0, "samples": 0,
                     "created": x.created_at.strftime("%Y-%m-%d %H:%M")} for x in all_s]
            ui.sessions_table(rows)
            continue
        if action == "config":
            await _configure()
            continue
        if action == "info":
            sysinfo = system_info()
            ui.stats_panel(sysinfo)
            continue
        if action == "update":
            update()
            continue
        if action == "exit":
            raise typer.Exit()
        break  # action == "new"

    # ── Wizard state-machine ──────────────────────────────────────────────────
    state: dict = {}
    STEPS = ["urls", "config", "review"]
    step_idx = 0

    while step_idx < len(STEPS):
        step = STEPS[step_idx]

        if step == "urls":
            ui.section("Input")
            result = await _step_urls(state)
        elif step == "config":
            ui.section("Configuration")
            result = await _step_config(state)
        elif step == "review":
            result = await _step_review(state)

        if result == "next":
            step_idx += 1
        elif result == "back":
            step_idx = max(0, step_idx - 1)
        elif result == "back_to_urls":
            step_idx = STEPS.index("urls")
        elif result == "back_to_config":
            step_idx = STEPS.index("config")
        elif result == "home":
            return await _interactive_pipeline()
        elif result == "exit":
            raise typer.Exit()

    # ── Launch pipeline ───────────────────────────────────────────────────────
    session_id = str(uuid.uuid4())
    ctx = PipelineContext(
        session_id=session_id,
        session_name=state["session_name"],
        goal=state["goal"],
        format=DataFormat(state["fmt"]),
        seed_urls=state["seed_urls"],
        settings=s,
        custom_system_prompt=state.get("custom_sys", ""),
        n_per_chunk=state["n_per_chunk"],
        ignore_robots=state.get("ignore_robots", False),
    )
    ui.info(f"Session ID: [bold]{session_id[:8]}[/]")
    ui.info(f"Session directory: [dim]{ctx.session_dir()}[/]")
    ui.info(f"Database: [dim]{s.db_path.resolve()}[/]")
    await _run_orchestrator(ctx)


async def _run_orchestrator(ctx: PipelineContext, start_from: str | None = None) -> None:
    s = ctx.settings
    _stage_map = {
        PipelineStage.discovery:  ("Discovery",  1),
        PipelineStage.collection: ("Collection", 2),
        PipelineStage.processing: ("Processing", 3),
        PipelineStage.generation: ("Generation", 4),
        PipelineStage.quality:    ("Quality",    5),
        PipelineStage.export:     ("Export",     6),
    }

    scraper_progress = _make_progress_cb("Scraping")
    gen_progress     = _make_progress_cb("Generating")

    async def stage_hook(stage: str, context: PipelineContext) -> bool:
        name, step = _stage_map.get(stage, (stage, 0))

        # Show summary + contextual tip
        _print_stage_summary(stage, context)
        ui.tip(stage)

        # Always offer export after collection+ stages
        if stage in (PipelineStage.collection, PipelineStage.processing,
                     PipelineStage.generation, PipelineStage.quality):
            action = await prompts.ask_stage_action(name)
            if action == "export":
                await _quick_export(context, stage)
                cont = await prompts.ask_confirm("Continue pipeline after export?")
                return cont
            if action == "pause":
                ui.info(f"Session saved. Resume with: [bold]dataforge resume {context.session_id[:8]}[/]")
                return False
        return True

    # URL selection hook (after discovery)
    async def post_discovery_hook(stage: str, context: PipelineContext) -> bool:
        if stage != PipelineStage.discovery:
            return True

        ui.success(f"Discovered {len(context.discovered_urls)} URLs")

        if len(context.discovered_urls) == 0:
            ui.warn("Discovery returned 0 URLs. Check that the site has a reachable sitemap or provide a direct sitemap URL.")
            return False

        ui.url_table(context.discovered_urls)

        pattern = await prompts.ask_url_filter(len(context.discovered_urls))
        if pattern:
            from dataforge.collectors import filter_urls
            from urllib.parse import urlparse
            domain = urlparse(context.seed_urls[0]).netloc if context.seed_urls else None
            filtered = filter_urls(context.discovered_urls, pattern or None, base_domain=None)
            context.selected_urls = filtered
            ui.info(f"Filtered to {len(filtered)} URLs")
        else:
            context.selected_urls = context.discovered_urls

        confirmed = await prompts.ask_confirm_urls(len(context.selected_urls),
                                                    len(context.discovered_urls))
        if not confirmed:
            ui.info("Cancelled")
            return False

        return await stage_hook(stage, context)

    async def combined_hook(stage: str, context: PipelineContext) -> bool:
        if stage == PipelineStage.discovery:
            return await post_discovery_hook(stage, context)
        return await stage_hook(stage, context)

    orch = Orchestrator(
        ctx,
        stage_hook=combined_hook,
        scraper_progress_cb=scraper_progress,
        generator_progress_cb=gen_progress,
        export_kwargs=await _ask_export_config(s),
    )
    ctx = await orch.run(start_from=start_from)

    if ctx.export_records:
        ui.export_summary(ctx.export_records)

    ui.success("Pipeline complete!")
    ui.info(f"Session: [bold]{ctx.session_id[:8]}[/]  |  "
            f"Approved samples: [bold]{len(ctx.approved_sample_ids)}[/]")


def _make_progress_cb(label: str):
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn
    _prog: Progress | None = None
    _task = None

    async def cb(done: int, total: int) -> None:
        nonlocal _prog, _task
        if _prog is None:
            _prog = ui.make_progress(label)
            _prog.start()
            _task = _prog.add_task(label, total=total)
        _prog.update(_task, completed=done)
        if done >= total and _prog:
            _prog.stop()
            _prog = None

    return cb


def _print_stage_summary(stage: str, ctx: PipelineContext) -> None:
    summaries = {
        PipelineStage.discovery:  {"Discovered URLs": len(ctx.discovered_urls)},
        PipelineStage.collection: {"Scraped pages":   len(ctx.scraped_page_ids)},
        PipelineStage.processing: {"Chunks":          len(ctx.processed_chunk_ids)},
        PipelineStage.generation: {"Samples":         len(ctx.synthetic_sample_ids)},
        PipelineStage.quality:    {"Approved":        len(ctx.approved_sample_ids),
                                   "Rejected":        len(ctx.synthetic_sample_ids) - len(ctx.approved_sample_ids)},
    }
    if stage in summaries:
        ui.stats_panel(summaries[stage])


async def _quick_export(ctx: PipelineContext, stage: str) -> None:
    s = ctx.settings
    targets = await prompts.ask_export_targets(
        hf_configured=bool(s.huggingface_token),
        kg_configured=bool(s.kaggle_username),
    )
    export_kw: dict = {"targets": targets, "stage_snapshot": stage}
    if "huggingface" in targets:
        export_kw["hf_repo_id"] = await prompts.ask_hf_repo()
        export_kw["hf_private"] = await prompts.ask_hf_private()
    if "kaggle" in targets:
        export_kw["kaggle_slug"] = await prompts.ask_kaggle_slug(s.kaggle_username)
        export_kw["kaggle_title"] = ctx.session_name

    agent = ExporterAgent(ctx, **export_kw)
    ctx_out = await agent.run()
    ui.info("Export complete!")
    ui.export_summary(ctx_out.export_records)


async def _ask_export_config(s) -> dict:
    """Ask export config upfront so orchestrator is fully configured."""
    # We'll ask at the export stage via stage_hook — return empty defaults
    return {"targets": ["local"], "approved_only": True}


async def _collect_urls() -> list[str] | None:
    from dataforge.utils import sanitise, sanitise_many

    method = await prompts.ask_input_method()
    if method is None:
        return None
    if method == "Single URL":
        url = await prompts.ask_single_url()
        if url is None:
            return None
        clean = sanitise(url)
        if not clean:
            ui.error(f"'{url}' is not a valid URL — skipping.")
            return None
        if clean != url:
            ui.info(f"URL corrected to: [dim]{clean}[/]")
        return [clean]
    if method == "Multiple URLs":
        urls = await prompts.ask_multiple_urls()
        if urls is None:
            return None
        clean = sanitise_many(urls)
        dropped = len(urls) - len(clean)
        if dropped:
            ui.warn(f"{dropped} invalid URL(s) removed.")
        return clean
    if method == "Text file":
        path = await prompts.ask_file_path()
        if path is None:
            return None
        urls = prompts.read_url_file(path)
        clean = sanitise_many(urls)
        dropped = len(urls) - len(clean)
        if dropped:
            ui.warn(f"{dropped} invalid URL(s) removed from file.")
        ui.info(f"Loaded {len(clean)} URLs from [dim]{path.resolve()}[/]")
        return clean
    if method == "Sitemap URL":
        url = await prompts.ask_single_url()
        if url is None:
            return None
        clean = sanitise(url)
        if not clean:
            ui.error(f"'{url}' is not a valid URL — skipping.")
            return None
        if clean != url:
            ui.info(f"URL corrected to: [dim]{clean}[/]")
        return [clean]
    return []


async def _main_menu() -> str:
    _COMMANDS = ["new", "resume", "sessions", "config", "info", "update", "exit"]
    _DESCRIPTIONS = {
        "new":      "Start new pipeline",
        "resume":   "Resume a paused session",
        "sessions": "List all sessions",
        "config":   "Configure LLM provider & keys",
        "info":     "System info & env status",
        "update":   "Update DataForge to latest",
        "exit":     "Exit",
    }
    # Print command hints above the prompt
    ui.console.print("")
    for cmd, desc in _DESCRIPTIONS.items():
        ui.console.print(f"  [bold cyan]{cmd:<10}[/]  [dim]{desc}[/]")
    ui.console.print("")

    result = await prompts.ask_command(_COMMANDS)
    if result is None:
        raise typer.Exit()
    return result


async def _pick_and_resume() -> None:
    s = get_settings()
    with open_session(s.db_path) as db:
        pausable = db.exec(
            select(PipelineSession).where(PipelineSession.status == SessionStatus.paused)
        ).all()

    if not pausable:
        ui.info("No paused sessions found")
        return

    import questionary
    choice = await questionary.select(
        "Select session to resume:",
        choices=[
            questionary.Choice(f"{s.name}  [{s.id[:8]}]  stage={s.stage}", value=s.id)
            for s in pausable
        ],
    ).ask_async()

    await _resume_session(choice)
