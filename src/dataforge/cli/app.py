"""Typer CLI application — all commands and the interactive pipeline wizard."""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from typing import Literal, Optional

import typer
from rich.console import Console
from sqlmodel import select

from dataforge.agents import Orchestrator, PipelineContext
from dataforge.agents.exporter import ExporterAgent
from dataforge.collectors import filter_urls
from dataforge.config import PROVIDER_INFO, get_settings
from dataforge.storage import (
    DataFormat,
    DiscoveredURL,
    PipelineSession,
    PipelineStage,
    ProcessedChunk,
    ScrapedPage,
    SessionStatus,
    SyntheticSample,
    init_db,
    open_session,
    persist_url_selection,
)
from dataforge.utils import get_logger, setup_logging, system_info

from . import prompts, ui
from .url_review import run_url_review

def _typer_error_handler(error: Exception) -> None:
    """Called by Typer when an unknown subcommand is entered."""
    msg = str(error)
    if "No such command" in msg or "no such option" in msg.lower():
        _VALID_COMMANDS = [
            "pipeline", "explore", "resume", "sessions",
            "export", "view", "config", "providers", "info", "test-llm", "update", "plan",
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

# Global output-mode state (set by callback options before any command runs)
_JSON_OUTPUT: bool = False
_QUIET: bool = False


def _version_callback(value: bool) -> None:
    if value:
        from dataforge import __version__
        typer.echo(__version__)
        raise typer.Exit()

# Type alias for step result sentinels
StepResult = Literal["next", "back", "back_to_urls", "back_to_config", "home", "exit"]


def _bootstrap() -> None:
    from dataforge.cli.preflight import check_env_file
    from dataforge.cli.dataforge_file import find_project_file, load_project
    check_env_file()
    s = get_settings()
    # If a .dataforge project file exists (anywhere up the directory tree), use
    # the absolute paths it records so 'resume', 'sessions', etc. work from any CWD.
    pf = find_project_file(Path.cwd())
    if pf:
        try:
            proj = load_project(pf)
            s.db_path    = Path(proj["db_path"])
            s.output_dir = Path(proj["output_dir"])
        except Exception:
            pass  # Malformed file — ignore and fall back to defaults
    setup_logging(s.logs_dir(), s.log_level)
    init_db(s.db_path)


# ── Default: interactive pipeline ─────────────────────────────────────────────

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None, "--version", "-V",
        callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
    no_color: bool = typer.Option(
        False, "--no-color",
        envvar="NO_COLOR",
        help="Disable ANSI colour output (also honoured via NO_COLOR env var).",
        is_eager=True,
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q",
        help="Suppress banners, tips, and decorative output.",
        is_eager=True,
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="Emit machine-readable JSON for 'sessions' and 'view' commands.",
        is_eager=True,
    ),
) -> None:
    """Launch the interactive guided pipeline (default when no subcommand given)."""
    global _JSON_OUTPUT, _QUIET
    _JSON_OUTPUT = json_output
    _QUIET = quiet
    if no_color:
        # Rich respects the NO_COLOR env var; set it so all consoles pick it up
        os.environ["NO_COLOR"] = "1"
    if ctx.invoked_subcommand is None:
        _bootstrap()
        if not quiet:
            ui.banner()
        asyncio.run(_interactive_pipeline())


# ── pipeline command (alias for interactive mode) ─────────────────────────────

@app.command()
def pipeline() -> None:
    """Start a new interactive pipeline."""
    _bootstrap()
    if not _QUIET:
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
def resume(
    session_id: Optional[str] = typer.Argument(
        None,
        help="Session ID (or prefix) to resume. Omit to auto-detect from .dataforge.",
    ),
) -> None:
    """Resume a paused pipeline session."""
    _bootstrap()
    asyncio.run(_resume_session(session_id))


async def _resume_session(session_id: str | None) -> None:
    from dataforge.cli.dataforge_file import find_project_file, get_project_sessions
    s = get_settings()
    session = None

    # ── Resolve session record ────────────────────────────────────────────────
    if not session_id:
        # Auto-detect from .dataforge project file
        pf = find_project_file(Path.cwd())
        if not pf:
            ui.error(
                "No session ID given and no .dataforge project file found.\n"
                "  Run 'dataforge resume <session-id>' or start a new pipeline first."
            )
            return
        tracked = get_project_sessions(pf)
        tracked_ids = {entry["id"] for entry in tracked}
        with open_session(s.db_path) as db:
            all_s = db.exec(select(PipelineSession)).all()
        paused = [x for x in all_s if x.id in tracked_ids and x.status == SessionStatus.paused]
        if not paused:
            active = [x for x in all_s if x.id in tracked_ids and x.status == SessionStatus.active]
            if active:
                ui.warn("No paused sessions in this project — session is still active.")
            else:
                ui.info("No paused sessions found. Run 'dataforge pipeline' to start one.")
            return
        if len(paused) == 1:
            session = paused[0]
        else:
            import questionary
            choice = await questionary.select(
                "Multiple paused sessions — select one to resume:",
                choices=[
                    questionary.Choice(f"{x.name}  [{x.id[:8]}]  stage={x.stage}", value=x.id)
                    for x in paused
                ],
            ).ask_async()
            with open_session(s.db_path) as db:
                session = db.get(PipelineSession, choice)
    else:
        with open_session(s.db_path) as db:
            session = db.get(PipelineSession, session_id)
            if not session:
                all_s = db.exec(select(PipelineSession)).all()
                matches = [x for x in all_s if x.id.startswith(session_id)]
                if len(matches) == 1:
                    session = matches[0]
                elif len(matches) > 1:
                    ui.error("Ambiguous session ID prefix — be more specific")
                    return
                else:
                    ui.error(
                        f"Session '{session_id}' not found.\n"
                        "  Tip: run from the project directory that has a .dataforge file, "
                        "or use 'dataforge sessions' to list all sessions."
                    )
                    return

    # ── Validate & build context ──────────────────────────────────────────────
    if session is None:
        ui.error("Could not resolve session")
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

    # Re-hydrate prior-stage data from DB so _checkpoint() doesn't zero out saved counts
    with open_session(s.db_path) as db:
        disc = db.exec(select(DiscoveredURL).where(DiscoveredURL.session_id == session.id)).all()
        ctx.discovered_urls = [u.url for u in disc]
        ctx.selected_urls   = [u.url for u in disc if u.selected]
        pages  = db.exec(select(ScrapedPage).where(ScrapedPage.session_id == session.id)).all()
        ctx.scraped_page_ids = [p.id for p in pages]
        chunks = db.exec(select(ProcessedChunk).where(ProcessedChunk.session_id == session.id)).all()
        ctx.processed_chunk_ids = [c.id for c in chunks]
        samps  = db.exec(select(SyntheticSample).where(SyntheticSample.session_id == session.id)).all()
        ctx.synthetic_sample_ids = [s_.id for s_ in samps]
        ctx.approved_sample_ids  = [s_.id for s_ in samps if s_.approved]

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
        if _JSON_OUTPUT:
            typer.echo("[]")
        else:
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
    if _JSON_OUTPUT:
        typer.echo(json.dumps(rows, indent=2))
    else:
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


# ── view command ──────────────────────────────────────────────────────────────

@app.command()
def view(
    session_id: str = typer.Argument(..., help="Session ID (or prefix) to inspect"),
    stage: Optional[str] = typer.Option(
        None, "--stage", "-s",
        help="Stage to view: discovery | collection | processing | generation | quality",
    ),
    limit: int = typer.Option(
        5, "--limit", "-n",
        help="Number of records to show per stage (default: 5).",
    ),
) -> None:
    """View a sample of collected data at each pipeline stage for a session."""
    _bootstrap()
    asyncio.run(_view_session(session_id, stage, limit=limit))


async def _view_session(session_id: str, stage: str | None, limit: int = 5) -> None:
    from dataforge.storage import ScrapedPage, ProcessedChunk, ExportRecord
    s = get_settings()

    # Resolve session (support prefix match)
    with open_session(s.db_path) as db:
        session = db.get(PipelineSession, session_id)
        if not session:
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

    sid = session.id
    ui.info(f"Session [bold]{session.name}[/]  [{sid[:8]}]  stage=[cyan]{session.stage}[/]  status={session.status}")

    if not stage:
        # Summary: count each stage
        with open_session(s.db_path) as db:
            n_discovered = len(db.exec(select(DiscoveredURL).where(DiscoveredURL.session_id == sid)).all())
            n_scraped    = len(db.exec(select(ScrapedPage).where(ScrapedPage.session_id == sid)).all())
            n_chunks     = len(db.exec(select(ProcessedChunk).where(ProcessedChunk.session_id == sid)).all())
            n_samples    = len(db.exec(select(SyntheticSample).where(SyntheticSample.session_id == sid)).all())
            n_approved   = len(db.exec(
                select(SyntheticSample)
                .where(SyntheticSample.session_id == sid)
                .where(SyntheticSample.approved == True)  # noqa: E712
            ).all())
            n_exports    = len(db.exec(select(ExportRecord).where(ExportRecord.session_id == sid)).all())
        summary = {
            "discovered": n_discovered,
            "scraped":    n_scraped,
            "chunks":     n_chunks,
            "samples":    n_samples,
            "approved":   n_approved,
            "exports":    n_exports,
        }
        if _JSON_OUTPUT:
            typer.echo(json.dumps({"session_id": sid, "stage_counts": summary}, indent=2))
        else:
            ui.view_summary(summary)
            ui.info("Use [bold]--stage <name>[/] to drill into a specific stage.")
        return

    stage = stage.lower()
    if stage == "discovery":
        with open_session(s.db_path) as db:
            rows_db = db.exec(select(DiscoveredURL).where(DiscoveredURL.session_id == sid)).all()
        rows = [{"url": r.url, "source": r.source, "selected": r.selected,
                 "http_status": r.http_status} for r in rows_db]
        ui.info(f"Showing {min(limit, len(rows))} of {len(rows)} discovered URLs  [dim](use --limit to change)[/]")
        ui.view_urls(rows, max_rows=limit)

    elif stage == "collection":
        with open_session(s.db_path) as db:
            rows_db = db.exec(select(ScrapedPage).where(ScrapedPage.session_id == sid)).all()
        rows = [{"url": r.url, "title": r.title, "word_count": r.word_count,
                 "scraped_at": r.scraped_at.strftime("%Y-%m-%d %H:%M")} for r in rows_db]
        ui.info(f"Showing {min(limit, len(rows))} of {len(rows)} scraped pages  [dim](use --limit to change)[/]")
        ui.view_pages(rows, max_rows=limit)

    elif stage == "processing":
        with open_session(s.db_path) as db:
            rows_db = db.exec(select(ProcessedChunk).where(ProcessedChunk.session_id == sid)).all()
        rows = [{"chunk_index": r.chunk_index, "token_count": r.token_count,
                 "content": r.content,
                 "source_url": r.parsed_meta().get("source_url", "")} for r in rows_db]
        ui.info(f"Showing {min(limit, len(rows))} of {len(rows)} chunks  [dim](use --limit to change)[/]")
        ui.view_chunks(rows, max_rows=limit)

    elif stage in ("generation", "quality"):
        with open_session(s.db_path) as db:
            query = select(SyntheticSample).where(SyntheticSample.session_id == sid)
            if stage == "quality":
                query = query.where(SyntheticSample.approved == True)  # noqa: E712
            rows_db = db.exec(query).all()
        rows = [{"format": r.format, "quality_score": r.quality_score,
                 "approved": r.approved, "messages": r.messages()} for r in rows_db]
        title = "Approved Samples" if stage == "quality" else "Generated Samples"
        ui.info(f"Showing {min(limit, len(rows))} of {len(rows)} samples  [dim](use --limit to change)[/]")
        ui.view_samples(rows, title=title, max_rows=limit)

    else:
        ui.error(f"Unknown stage '{stage}'. Valid: discovery, collection, processing, generation, quality")


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
    """Update DataForge to the latest version."""
    import subprocess
    import sys
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    try:
        current_ver = _pkg_version("llm-web-crawler")
    except PackageNotFoundError:
        current_ver = "unknown"

    ui.info(f"Current version: [bold]{current_ver}[/]")

    # Standalone executable — cannot self-update, direct user to releases page
    if getattr(sys, "frozen", False):
        ui.warn(
            "Running as a standalone executable. "
            "Download the latest release from: [cyan]https://github.com/ianktoo/data-forge/releases[/]"
        )
        return

    ui.info("Checking for updates…")

    def _try_update(cmd: list[str]) -> tuple[bool, str]:
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.returncode == 0, r.stdout + r.stderr

    # 1. Try uv tool upgrade (preferred for uv-installed tools)
    ok, out = _try_update(["uv", "tool", "upgrade", "llm-web-crawler"])
    if ok:
        if "already" in out.lower() or "up-to-date" in out.lower():
            ui.success(f"Already up to date (v{current_ver})")
        else:
            try:
                new_ver = _pkg_version("llm-web-crawler")
            except PackageNotFoundError:
                new_ver = current_ver
            ui.success(
                f"Updated via uv: [dim]{current_ver}[/] → [bold green]{new_ver}[/]"
                if new_ver != current_ver else f"Already up to date (v{current_ver})"
            )
        return

    # 2. Fall back to pip
    ok, out = _try_update(
        [sys.executable, "-m", "pip", "install", "--upgrade", "llm-web-crawler"]
    )
    if ok:
        new_ver = current_ver
        for line in out.splitlines():
            if "Successfully installed" in line:
                for token in line.split():
                    if token.lower().startswith("llm-web-crawler-"):
                        new_ver = token[len("llm-web-crawler-"):]
                        break
        if new_ver != current_ver and current_ver != "unknown":
            ui.success(f"Updated: [dim]{current_ver}[/] → [bold green]{new_ver}[/]")
        else:
            ui.success(f"Already up to date (v{current_ver})")
        return

    ui.error("Update failed. Try manually:")
    ui.console.print("  uv tool upgrade llm-web-crawler", style="dim")
    ui.console.print("  pip install --upgrade llm-web-crawler", style="dim")


# ── plan command ─────────────────────────────────────────────────────────────

@app.command()
def plan() -> None:
    """Show the full pipeline overview and current project status."""
    _bootstrap()
    _show_pipeline_plan()


# ── info command ──────────────────────────────────────────────────────────────

@app.command()
def info() -> None:
    """Show system information, environment status, and folder-level project info."""
    _bootstrap()
    _show_info()


def _show_info() -> None:
    from dataforge.cli.dataforge_file import find_project_file, get_project_sessions
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
        "Logs":          str(s.logs_dir()),
        "Database":      str(s.db_path),
        "HF token":      "set" if s.huggingface_token else "not set",
        "Kaggle":        "configured" if s.kaggle_username else "not configured",
    }
    ui.stats_panel(stats)

    # ── Folder / project level ────────────────────────────────────────────────
    pf = find_project_file(Path.cwd())
    if pf:
        try:
            proj_sessions = get_project_sessions(pf)
            db_path = Path(pf.parent / "dataforge.db")
            db_size = f"{db_path.stat().st_size / 1024:.1f} KB" if db_path.exists() else "not found"
            out_dir = s.output_dir
            try:
                out_size_bytes = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file())
                out_size = f"{out_size_bytes / (1024 * 1024):.1f} MB"
            except Exception:
                out_size = "unavailable"

            # Count sessions by status from DB
            with open_session(s.db_path) as db:
                all_s = db.exec(select(PipelineSession)).all()
            tracked_ids = {e["id"] for e in proj_sessions}
            proj_s = [x for x in all_s if x.id in tracked_ids]
            status_counts = {}
            for sess in proj_s:
                status_counts[sess.status] = status_counts.get(sess.status, 0) + 1
            status_str = "  ".join(f"{k}={v}" for k, v in status_counts.items()) or "none"

            proj_info = {
                ".dataforge file":    str(pf),
                "Project directory":  str(pf.parent),
                "Sessions tracked":   str(len(proj_sessions)),
                "Session status":     status_str,
                "Database size":      db_size,
                "Output dir size":    out_size,
                "Output directory":   str(out_dir),
            }
        except Exception as exc:
            proj_info = {".dataforge": str(pf), "Parse error": str(exc)}
        ui.project_info_panel(proj_info)
    else:
        ui.info(
            "No [bold].dataforge[/] project file found in this directory or parents.\n"
            "  Start a pipeline to create one: [bold]dataforge pipeline[/]\n"
            "  [dim](On Windows, .dataforge is a hidden file — enable 'Show hidden items' in Explorer)[/]"
        )


# ── Pipeline plan helper ──────────────────────────────────────────────────────

def _show_pipeline_plan() -> None:
    """Show pipeline stage overview + current project session status."""
    from dataforge.cli.dataforge_file import find_project_file, get_project_sessions
    from dataforge.storage import PipelineStage

    _STAGE_FLOW_LIST = [
        PipelineStage.discovery, PipelineStage.collection, PipelineStage.processing,
        PipelineStage.generation, PipelineStage.quality, PipelineStage.export,
    ]

    ui.section("Pipeline Plan")

    # Find current project sessions for context
    pf = find_project_file(Path.cwd())
    current_stage: str | None = None
    next_stage: str | None = None

    if pf:
        s = get_settings()
        try:
            proj_sessions = get_project_sessions(pf)
            tracked_ids = {e["id"] for e in proj_sessions}
            with open_session(s.db_path) as db:
                all_s = db.exec(select(PipelineSession)).all()
            active = [x for x in all_s if x.id in tracked_ids
                      and x.status in ("active", "paused")]
            if active:
                latest = sorted(active, key=lambda x: x.updated_at or x.created_at, reverse=True)[0]
                current_stage = latest.stage
                idx = _STAGE_FLOW_LIST.index(current_stage) if current_stage in _STAGE_FLOW_LIST else -1
                if idx >= 0 and idx + 1 < len(_STAGE_FLOW_LIST):
                    next_stage = _STAGE_FLOW_LIST[idx + 1]
                ui.info(
                    f"Project session [bold]{latest.name}[/]  [{latest.id[:8]}]"
                    f"  status=[yellow]{latest.status}[/]  at stage=[cyan]{current_stage}[/]"
                )
                if next_stage:
                    ui.info(f"Next stage to run: [bold yellow]{next_stage}[/]")
        except Exception:
            pass

    ui.pipeline_overview_panel(current_stage=current_stage, next_stage=next_stage)


# ── Explore menu (interactive data browser) ────────────────────────────────────

async def _explore_menu(limit: int = 5) -> None:
    """Interactive menu for exploring data collected in a session."""
    import questionary
    s = get_settings()

    with open_session(s.db_path) as db:
        all_s = db.exec(select(PipelineSession)).all()

    if not all_s:
        ui.info("No sessions found. Run [bold]dataforge pipeline[/] to start one.")
        return

    sorted_s = sorted(all_s, key=lambda x: x.created_at, reverse=True)
    choices = [
        questionary.Choice(
            f"{x.name}  [{x.id[:8]}]  stage={x.stage}  status={x.status}",
            value=x.id,
        )
        for x in sorted_s
    ]
    choices.append(questionary.Choice("[dim]← Back[/]", value="__back__"))

    session_id = await questionary.select(
        "Select a session to explore:", choices=choices
    ).ask_async()

    if not session_id or session_id == "__back__":
        return

    with open_session(s.db_path) as db:
        session = db.get(PipelineSession, session_id)
    if not session:
        ui.error("Session not found")
        return

    # Stage sub-menu loop
    _STAGE_OPTIONS = [
        ("discovery",  "Discovery  — discovered URLs"),
        ("collection", "Collection — scraped pages"),
        ("processing", "Processing — text chunks"),
        ("generation", "Generation — synthetic samples"),
        ("quality",    "Quality    — approved samples"),
    ]

    while True:
        ui.info(
            f"Exploring [bold]{session.name}[/]  [{session.id[:8]}]"
            f"  stage=[cyan]{session.stage}[/]  limit=[bold]{limit}[/]"
        )
        stage_choices = [questionary.Choice(label, value=key) for key, label in _STAGE_OPTIONS]
        stage_choices.append(questionary.Choice(f"Change sample limit  (current: {limit})", value="__limit__"))
        stage_choices.append(questionary.Choice("← Back to main menu", value="__back__"))

        picked = await questionary.select(
            "Which stage do you want to explore?", choices=stage_choices
        ).ask_async()

        if not picked or picked == "__back__":
            break

        if picked == "__limit__":
            raw = await questionary.text(
                f"Enter number of records to show (current: {limit}):",
                default=str(limit),
            ).ask_async()
            try:
                limit = max(1, int(raw or limit))
                ui.info(f"Sample limit set to [bold]{limit}[/]")
            except ValueError:
                ui.warn("Invalid number — keeping current limit")
            continue

        await _view_session(session.id, picked, limit=limit)


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


async def _step_output(state: dict) -> StepResult:
    """Step 2: confirm (or change) the output directory."""
    s = get_settings()
    default = str(s.output_dir.resolve())
    try:
        chosen = await prompts.ask_output_dir(default)
    except KeyboardInterrupt:
        return "back"
    if chosen is None:
        return "back"
    chosen_path = Path(chosen).expanduser().resolve()
    state["output_dir"] = chosen_path
    # Apply immediately so later wizard steps see the right path
    s.output_dir = chosen_path
    s.db_path    = chosen_path / "dataforge.db"
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

        from urllib.parse import urlparse as _urlparse
        seed_domain = _urlparse(state.get("seed_urls", [""])[0]).netloc or "this site"
        skip_known = await prompts.ask_skip_known(seed_domain)
        state["skip_known"] = skip_known

        threshold = await prompts.ask_quality_threshold()
        if threshold is None:
            return "back"
        state["quality_threshold"] = threshold

        s = get_settings()
        gen_model = await prompts.ask_generation_model(s.llm_model)
        if gen_model is None:
            return "back"
        state["generation_model"] = gen_model

        quality_model = await prompts.ask_quality_model(gen_model)
        if quality_model is None:
            return "back"
        state["quality_model"] = quality_model
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

    # ── Outer application loop — returns to menu after each pipeline run ───────
    while True:
        # ── Main menu loop ────────────────────────────────────────────────────
        while True:
            action = await _main_menu()
            if action == "new":
                break  # fall through to wizard
            if action == "resume":
                await _pick_and_resume()
                continue
            if action == "explore":
                await _explore_menu()
                continue
            if action == "plan":
                _show_pipeline_plan()
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
                _show_info()
                continue
            if action == "update":
                update()
                continue
            if action == "exit":
                raise typer.Exit()

        # ── Wizard ────────────────────────────────────────────────────────────
        state: dict = {}
        wizard_result = await _run_wizard(state)

        if wizard_result == "home":
            continue  # back to main menu
        if wizard_result == "exit":
            raise typer.Exit()
        # wizard_result == "done" — fall through to launch

        # ── Launch pipeline ───────────────────────────────────────────────────
        # Re-read settings in case output dir was updated during wizard
        s = get_settings()
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
            skip_known=state.get("skip_known", False),
            quality_threshold=state.get("quality_threshold", 0.5),
            generation_model=state.get("generation_model", ""),
            quality_model=state.get("quality_model", ""),
        )

        # Write / update .dataforge project file so 'resume' always works
        from dataforge.cli.dataforge_file import find_project_file, create_project, add_session
        cwd = Path.cwd()
        pf = find_project_file(cwd)
        if pf and pf.parent == cwd:
            add_session(pf, session_id, state["session_name"])
            ui.info(f"Session recorded in [bold].dataforge[/] → [dim]{pf}[/]")
        else:
            pf = create_project(cwd, s.db_path, s.output_dir, session_id, state["session_name"])
            ui.info(
                f"[bold].dataforge[/] project file created → [dim]{pf}[/]\n"
                "  Tracks sessions so [bold]dataforge resume[/] works from this directory.\n"
                "  [dim](Windows: hidden file — enable 'Show hidden items' in Explorer to see it)[/]"
            )
        # Re-initialise DB at the (possibly new) path chosen during the wizard
        from dataforge.storage import init_db
        init_db(s.db_path)

        ui.info(f"Session ID: [bold]{session_id[:8]}[/]")
        ui.info(f"Session directory: [dim]{ctx.session_dir()}[/]")
        ui.info(f"Database: [dim]{s.db_path.resolve()}[/]")

        await _run_orchestrator(ctx)

        # ── Post-pipeline: offer explore or loop back to menu ─────────────────
        ui.section("Pipeline Complete")
        ui.info("Returning to main menu — use [bold]explore[/] to inspect your data.")
        # Loop back to the top of the outer while True (shows main menu again)


async def _run_wizard(state: dict) -> str:
    """Run the pipeline setup wizard. Returns 'done' | 'home' | 'exit'."""
    STEPS = ["urls", "output", "config", "review"]
    step_idx = 0

    while step_idx < len(STEPS):
        step = STEPS[step_idx]

        if step == "urls":
            ui.section("Input")
            result = await _step_urls(state)
        elif step == "output":
            ui.section("Output Location")
            result = await _step_output(state)
        elif step == "config":
            ui.section("Configuration")
            result = await _step_config(state)
        elif step == "review":
            result = await _step_review(state)
        else:
            result = "next"

        if result == "next":
            step_idx += 1
        elif result == "back":
            step_idx = max(0, step_idx - 1)
        elif result == "back_to_urls":
            step_idx = STEPS.index("urls")
        elif result == "back_to_config":
            step_idx = STEPS.index("config")
        elif result == "home":
            return "home"
        elif result == "exit":
            return "exit"

    return "done"


_STAGE_PRE_DESCRIPTIONS = {
    PipelineStage.discovery:  (
        "Crawling sitemaps and robots.txt to build the full URL list for this site. "
        "No pages are downloaded yet — this only maps what's available."
    ),
    PipelineStage.collection: (
        "Fetching each selected URL and converting page HTML to clean Markdown. "
        "Rate limiting is applied so the target server is not overloaded."
    ),
    PipelineStage.processing: (
        "Splitting pages into token-aware overlapping chunks. "
        "Each chunk gets source metadata so samples can be traced back to their origin."
    ),
    PipelineStage.generation: (
        "Prompting the LLM to generate synthetic training samples from each chunk. "
        "Format, system prompt, and samples-per-chunk follow your session settings."
    ),
    PipelineStage.quality: (
        "Asking the LLM to score every sample on a 1–5 quality scale. "
        "Only samples at or above the threshold are marked approved and included in exports."
    ),
    PipelineStage.export: (
        "Writing approved samples to the configured destinations "
        "(local JSONL, HuggingFace Hub, or Kaggle)."
    ),
}

_STAGE_TOTAL = 6

_LANG_RE = re.compile(
    r"/([a-z]{2}(?:-[a-z]{2})?)/|[?&]lang(?:uage)?=([a-z]{2})|[?&]locale=([a-z]{2})",
    re.IGNORECASE,
)


def _detect_language_groups(urls: list[str]) -> dict[str, int]:
    """Return {locale_code: count} for URLs containing language/locale patterns."""
    counts: dict[str, int] = {}
    for url in urls:
        m = _LANG_RE.search(url)
        if m:
            lang = next(g for g in m.groups() if g).lower()
            counts[lang] = counts.get(lang, 0) + 1
    return counts


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

        total = len(context.discovered_urls)
        ui.success(f"Discovered {total} URL{'s' if total != 1 else ''}")

        if total == 0:
            ui.warn("Discovery returned 0 URLs. Check that the site has a reachable sitemap or provide a direct sitemap URL.")
            return False

        ui.url_table(context.discovered_urls)

        # Locale hint
        lang_groups = _detect_language_groups(context.discovered_urls)
        if len(lang_groups) >= 2:
            ui.language_groups_panel(lang_groups, total)
            ui.warn(
                "Multiple language variants detected — use the filter "
                "(e.g. [bold]/en/[/]) in the review step to keep one locale."
            )

        selected = await run_url_review(context.discovered_urls)

        if not selected:
            ui.warn("No URLs selected — returning to menu.")
            return False

        context.selected_urls = selected
        ui.info(f"Selected [bold]{len(selected)}[/] / {total} URLs for collection.")

        # Persist selection so resume re-hydrates correctly
        with open_session(context.settings.db_path) as db:
            persist_url_selection(db, context.session_id, set(selected))

        return await stage_hook(stage, context)

    async def combined_hook(stage: str, context: PipelineContext) -> bool:
        if stage == PipelineStage.discovery:
            return await post_discovery_hook(stage, context)
        return await stage_hook(stage, context)

    async def pre_stage_hook(stage: str, context: PipelineContext) -> bool:
        name, step = _stage_map.get(stage, (stage, 0))
        detail = _STAGE_PRE_DESCRIPTIONS.get(stage, "")
        ui.stage_description(name, step, _STAGE_TOTAL, detail)
        if stage == PipelineStage.generation:
            from dataforge.generators.templates import build_prompt
            prompt = build_prompt(
                "[your content here]",
                context.format,
                context.goal,
                n=context.n_per_chunk,
                custom_system=context.custom_system_prompt,
            )
            model = context.generation_model or context.settings.llm_model
            ui.prompt_preview_panel(prompt.system, model)
        return True

    orch = Orchestrator(
        ctx,
        stage_hook=combined_hook,
        pre_stage_hook=pre_stage_hook,
        scraper_progress_cb=scraper_progress,
        generator_progress_cb=gen_progress,
        export_kwargs=await _ask_export_config(s),
    )
    try:
        ctx = await orch.run(start_from=start_from)
    except (KeyboardInterrupt, asyncio.CancelledError):
        ui.warn(
            f"Pipeline paused.  Resume: [bold]dataforge resume {ctx.session_id[:8]}[/]"
        )
        return

    if ctx.pause_requested:
        ui.warn(
            f"Pipeline paused after partial scrape.  "
            f"Resume: [bold]dataforge resume {ctx.session_id[:8]}[/]"
        )
        return

    if ctx.export_records:
        ui.export_summary(ctx.export_records)

    ui.success(
        f"Pipeline complete!  Session [bold]{ctx.session_id[:8]}[/]  |  "
        f"Approved samples: [bold]{len(ctx.approved_sample_ids)}[/]"
    )
    ui.info(
        f"Use [bold]explore[/] from the menu to browse results, "
        f"or [bold]dataforge view {ctx.session_id[:8]} --stage generation[/] from the terminal."
    )


def _make_progress_cb(label: str):
    _prog: ui.Progress | None = None
    _task = None

    async def cb(done: int, total: int, item: str = "") -> None:
        nonlocal _prog, _task
        if _prog is None:
            _prog = ui.make_progress(label)
            _prog.start()
            _task = _prog.add_task(label, total=total)
        desc = f"[cyan]{label}[/]"
        if item:
            short = item if len(item) <= 60 else "…" + item[-57:]
            desc += f"  [dim]{short}[/]"
        _prog.update(_task, description=desc, completed=done)
        if done >= total and _prog:
            _prog.stop()
            ui.console.print("")  # reset cursor to fresh line after live display clears
            _prog = None

    return cb


def _print_stage_summary(stage: str, ctx: PipelineContext) -> None:
    summaries: dict = {
        PipelineStage.discovery:  {"Discovered URLs": len(ctx.discovered_urls)},
        PipelineStage.collection: {"Scraped pages":   len(ctx.scraped_page_ids)},
        PipelineStage.processing: {"Chunks":          len(ctx.processed_chunk_ids)},
        PipelineStage.generation: {"Samples":         len(ctx.synthetic_sample_ids)},
        PipelineStage.quality:    {
            "Approved": len(ctx.approved_sample_ids),
            "Rejected": len(ctx.synthetic_sample_ids) - len(ctx.approved_sample_ids),
            "Threshold": f"{ctx.quality_threshold:.1f}",
        },
    }
    if stage not in summaries:
        return
    stats = summaries[stage]
    # Append LLM usage if available (generation / quality stages)
    if stage in (PipelineStage.generation, PipelineStage.quality) and ctx.llm_usage:
        u = ctx.llm_usage
        pt  = u.get("prompt_tokens", 0)
        ct  = u.get("completion_tokens", 0)
        cost = u.get("cost_usd", 0.0)
        if pt or ct:
            stats["Prompt tokens"]     = f"{pt:,}"
            stats["Completion tokens"] = f"{ct:,}"
        if cost:
            stats["LLM cost"]          = f"${cost:.4f}"
    ui.stats_panel(stats)

    # Show quality score distribution after quality stage
    if stage == PipelineStage.quality and ctx.synthetic_sample_ids:
        from dataforge.storage import SyntheticSample, open_session
        with open_session(ctx.settings.db_path) as db:
            from sqlmodel import select as _select
            rows = db.exec(
                _select(SyntheticSample)
                .where(SyntheticSample.session_id == ctx.session_id)
            ).all()
        scores = [r.quality_score for r in rows if r.quality_score is not None]
        if scores:
            ui.quality_distribution_panel(scores, ctx.quality_threshold)


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
    s = get_settings()
    # Peek at session counts to drive context-sensitive hints
    try:
        with open_session(s.db_path) as db:
            paused_count = len(db.exec(
                select(PipelineSession).where(PipelineSession.status == SessionStatus.paused)
            ).all())
            total_sessions = len(db.exec(select(PipelineSession)).all())
    except Exception:
        paused_count, total_sessions = 0, 0

    _COMMANDS = ["new", "resume", "explore", "plan", "sessions", "config", "info", "update", "exit"]

    ui.console.print("")
    entries = [
        ("new",      "Start a new pipeline",                   True),
        ("resume",   f"Resume a paused session"
                     + (f"  [bold cyan]({paused_count} waiting)[/]" if paused_count else ""),
                     True),
        ("explore",  "Browse data collected in a session",     total_sessions > 0),
        ("plan",     "Show pipeline stages & project status",  True),
        ("sessions", "List all sessions",                      total_sessions > 0),
        ("config",   "Configure LLM provider & API keys",      True),
        ("info",     "System & folder info",                   True),
        ("update",   "Update DataForge to latest version",     True),
        ("exit",     "Exit",                                   True),
    ]
    for cmd, desc, active in entries:
        if active:
            ui.console.print(f"  [bold cyan]{cmd:<10}[/]  [dim]{desc}[/]")
        else:
            ui.console.print(f"  [dim]{cmd:<10}  {desc}[/]")
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
