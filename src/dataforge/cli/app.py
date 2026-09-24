"""Typer CLI application — all commands and the interactive pipeline wizard."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from dataforge.agents import PipelineContext

import typer
from rich.console import Console
from sqlmodel import select

# Storage and config are lightweight — import eagerly
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
    compute_session_stats,
    init_db,
    open_session,
    persist_url_selection,
)
from dataforge.utils import get_logger, setup_logging, system_info

from . import prompts, ui

# Heavy dependencies (litellm, httpx, tiktoken) are imported lazily inside the
# functions that need them so startup time stays fast.

def _typer_error_handler(error: Exception) -> None:
    """Called by Typer when an unknown subcommand is entered."""
    msg = str(error)
    if "No such command" in msg or "no such option" in msg.lower():
        _VALID_COMMANDS = [
            "pipeline", "explore", "resume", "sessions",
            "export", "view", "config", "providers", "info", "test-llm", "update", "uninstall", "plan",
            "scrape",
        ]
        # Try to find the closest match
        import difflib
        parts = msg.split("'")
        bad = parts[1] if len(parts) >= 2 else ""
        closest = difflib.get_close_matches(bad, _VALID_COMMANDS, n=1, cutoff=0.5)
        hint = f"Did you mean [bold cyan]{closest[0]}[/]?" if closest else ""
        from rich import box
        from rich.console import Console as RC
        from rich.panel import Panel
        from rich.text import Text
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


def _apply_project_file(s, cwd: Path) -> None:
    """Point *s* at the database and output folder a .dataforge project file
    records, so every command (run, sessions, stats, view, resume) uses the
    same ones from any CWD. An explicitly set DATAFORGE_DB_PATH or
    DATAFORGE_OUTPUT_DIR still wins, which is how the benchmark isolates sites.
    """
    from dataforge.cli.dataforge_file import find_project_file, load_project
    pf = find_project_file(cwd)
    if not pf:
        return
    try:
        proj = load_project(pf)
        if not os.getenv("DATAFORGE_DB_PATH"):
            s.db_path = Path(proj["db_path"])
        if not os.getenv("DATAFORGE_OUTPUT_DIR"):
            s.output_dir = Path(proj["output_dir"])
    except Exception:
        pass  # Malformed file: ignore and fall back to defaults


def _bootstrap(interactive: bool = False) -> None:
    from dataforge.cli.preflight import check_env_file
    check_env_file()
    s = get_settings()
    _apply_project_file(s, Path.cwd())
    level = s.log_level
    # The interactive screens already show progress; INFO log lines on top of
    # them are what made the terminal scroll away. They still go to the log
    # file. An explicit DATAFORGE_LOG_LEVEL wins.
    if interactive and not os.getenv("DATAFORGE_LOG_LEVEL"):
        level = "WARNING"
    setup_logging(s.logs_dir(), level)
    init_db(s.db_path)


# ── Default: interactive pipeline ─────────────────────────────────────────────

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool | None = typer.Option(
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
        _bootstrap(interactive=True)
        if not quiet:
            ui.banner()
        asyncio.run(_interactive_pipeline())


# ── pipeline command (alias for interactive mode) ─────────────────────────────

@app.command()
def pipeline() -> None:
    """Start a new interactive pipeline."""
    _bootstrap(interactive=True)
    if not _QUIET:
        ui.banner()
    asyncio.run(_interactive_pipeline())


# ── scrape command ────────────────────────────────────────────────────────────

@app.command()
def scrape(
    urls: list[str] = typer.Argument(..., help="One or more page URLs"),
    tables: bool = typer.Option(True, "--tables/--no-tables", help="Extract HTML tables as CSV/JSON"),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Output folder (default: ./scrape/<date-time>)"),
    fmt: list[str] = typer.Option(
        ["jsonl", "md", "csv"], "--format", "-f",
        help="What to write: jsonl (pages.jsonl, tables.jsonl), md (one file per page), "
             "csv (each table as CSV and JSON). Repeat to pick several."),
    check: bool = typer.Option(
        False, "--check", help="Rule-based checks, no AI: empty or very short pages, duplicate text"),
) -> None:
    """Fetch pages and save their text and tables. No AI, no API key, spends nothing.

    Obeys robots.txt, the rate limit and Crawl-delay like every other request.
    """
    import sys as _sys

    from loguru import logger

    from dataforge import scrape as qs

    bad = [f for f in fmt if f not in qs.FORMATS]
    if bad:
        _report_error(f"unknown --format {', '.join(bad)}; choose from {', '.join(qs.FORMATS)}")
        raise typer.Exit(code=2)
    # No session, database or log files: warnings only, to stderr.
    logger.remove()
    logger.add(_sys.stderr, level="WARNING", format="{level}: {message}")

    s = get_settings()
    pages = asyncio.run(qs.scrape_urls(urls, rate_limit=s.rate_limit, tables=tables, check=check))
    from datetime import datetime

    out_dir = out or Path("scrape") / datetime.now().strftime("%Y%m%d-%H%M%S")
    written = qs.write_outputs(pages, out_dir, tuple(fmt))

    ok = [p for p in pages if p.ok]
    if _JSON_OUTPUT:
        typer.echo(json.dumps({
            "output_dir": str(out_dir.resolve()),
            "pages": [p.to_dict(include_text=False) for p in pages],
            "files": written,
        }, indent=2, ensure_ascii=False))
    else:
        _print_scrape_results(pages, out_dir)
    if not ok:
        raise typer.Exit(code=1)


def _print_table_preview(table, title: str, max_rows: int = 8) -> None:
    from rich.table import Table as RichTable

    headers = table.headers or [f"column_{i + 1}" for i in range(table.width)]
    t = RichTable(title=title, show_lines=False, title_justify="left")
    for h in headers:
        t.add_column(h, overflow="fold")
    for row in table.rows[:max_rows]:
        t.add_row(*row)
    ui.console.print(t)
    if len(table.rows) > max_rows:
        ui.console.print(f"  … {len(table.rows) - max_rows} more row(s)", style="dim")


# ── explore command ───────────────────────────────────────────────────────────

@app.command()
def explore(url: str = typer.Argument(..., help="URL or sitemap URL to explore")) -> None:
    """Quickly discover and display URLs from a sitemap."""
    _bootstrap()
    asyncio.run(_run_explore(url))


async def _run_explore(url: str) -> None:
    from dataforge.collectors import HTTPClient, discover_sitemap_urls, parse_sitemaps
    from dataforge.utils import RateLimiter

    s = get_settings()
    limiter = RateLimiter(s.rate_limit)
    ui.section("URL Discovery")

    async with HTTPClient(limiter) as client:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        if url.endswith(".xml"):
            sitemap_urls = [url]
        else:
            sitemap_urls = await discover_sitemap_urls(client, base)

        if sitemap_urls:
            ui.info(f"Sitemap{'s' if len(sitemap_urls) > 1 else ''}: {', '.join(sitemap_urls)}")
            urls = await parse_sitemaps(client, sitemap_urls)
        else:
            ui.warn("No sitemap found. Showing seed URL only.")
            urls = [url]

    ui.success(f"Found {len(urls)} URLs")
    ui.url_table(urls)


# ── clear command ─────────────────────────────────────────────────────────────

@app.command()
def clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Delete the .dataforge project file to start fresh in this directory."""
    _bootstrap()
    asyncio.run(_clear_project(yes))


async def _clear_project(yes: bool) -> None:
    import questionary

    from dataforge.cli.dataforge_file import find_project_file
    cwd = Path.cwd()
    pf = find_project_file(cwd)
    if not pf or pf.parent != cwd:
        ui.info("No .dataforge project file found in the current directory — nothing to clear.")
        return
    if not yes:
        confirm = await questionary.confirm(
            f"Delete .dataforge in {cwd}? (session data in the database is kept)",
            default=False,
            **prompts._q(),
        ).ask_async()
        if not confirm:
            ui.info("Cancelled.")
            return
    pf.unlink()
    ui.success("Cleared project file. Run [bold]dataforge[/] to start fresh.")


# ── resume command ────────────────────────────────────────────────────────────

@app.command()
def resume(
    session_id: str | None = typer.Argument(
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
        # A session left status=active with no process actually running it (crash,
        # closed terminal, an interrupt outside the paths that mark it paused) is
        # otherwise invisible here — the user would only find it via `dataforge
        # sessions` + an explicit ID. Surface it as resumable, clearly labelled,
        # rather than silently hiding it behind "session is still active".
        stale_active = [x for x in all_s if x.id in tracked_ids and x.status == SessionStatus.active]
        candidates = paused + stale_active
        if not candidates:
            ui.info("No paused sessions found. Run 'dataforge pipeline' to start one.")
            return
        if len(candidates) == 1:
            session = candidates[0]
            if session in stale_active:
                ui.warn(
                    f"Session '{session.name}' is marked active but nothing is running it "
                    "(likely an interrupted run) — resuming from its last checkpoint."
                )
        else:
            import questionary

            def _label(x: PipelineSession) -> str:
                flag = "  [yellow](active — likely interrupted)[/]" if x in stale_active else ""
                return f"{x.name}  [{x.id[:8]}]  stage={x.stage}{flag}"

            choice = await questionary.select(
                "Multiple resumable sessions — select one:",
                choices=[questionary.Choice(_label(x), value=x.id) for x in candidates],
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
                    ui.error("Ambiguous session ID prefix; be more specific")
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

    # Continue in the folder the session started in (a recipe may have set
    # its own output_dir), not wherever the current settings point.
    recorded_output = session.config().get("output_dir")
    if recorded_output:
        s.output_dir = Path(recorded_output)

    from dataforge.agents import PipelineContext
    ctx = PipelineContext(
        session_id=session.id,
        session_name=session.name,
        goal=session.goal,
        format=DataFormat(session.format),
        seed_urls=session.seed_url_list(),
        settings=s,
        discovery_scope=session.config().get("discovery_scope", "site"),
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

@app.command(name="run")
def run_cmd(
    recipe: str = typer.Argument(..., help="Path to a YAML recipe file"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Validate the recipe and print the plan without running"
    ),
) -> None:
    """Run a pipeline end-to-end from a YAML recipe — no prompts.

    Configuration as code: every wizard answer lives in the file, so the run is
    reproducible, reviewable in a PR, and safe to launch from CI or cron.

    Exit codes: 0 ok | 2 invalid recipe | 3 no URLs after filters |
    4 paused | 5 completed with zero approved samples.
    """
    from .headless import run_recipe

    # Same database, output folder and logging as every other command.
    # Without this, run wrote to ./dataforge.db while sessions/stats/view read
    # the .dataforge project file's database, and logged DEBUG in colour.
    _bootstrap()
    code = asyncio.run(run_recipe(recipe, dry_run=dry_run))
    if code != 0:
        raise typer.Exit(code)


@app.command(name="init-recipe")
def init_recipe(
    path: str = typer.Argument("dataforge.yaml", help="Where to write the example recipe"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file"),
) -> None:
    """Write a fully annotated example recipe you can edit and run."""
    from .recipe import RecipeError, write_example_recipe

    try:
        written = write_example_recipe(path, force=force)
    except RecipeError as exc:
        ui.error(str(exc))
        raise typer.Exit(2) from exc

    ui.success(f"Example recipe written to [bold]{written}[/]")
    ui.info("Edit it, then run:  [bold]dataforge run " + str(written) + "[/]")
    ui.info("Validate without running:  [bold]dataforge run " + str(written) + " --dry-run[/]")


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

    # Any stage has something worth exporting (URLs, pages, chunks, samples).
    await _export_flow(_context_for(session), session.stage, approved_only=approved_only)


# ── view command ──────────────────────────────────────────────────────────────

@app.command()
def view(
    session_id: str = typer.Argument(..., help="Session ID (or prefix) to inspect"),
    stage: str | None = typer.Option(
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


async def _show_rows(sid: str, stage: str, rows: list, limit: int, render_fn, label: str) -> None:
    """--json: the first *limit* rows as JSON, never a pager (the MCP server and
    scripts read this). Otherwise the interactive pager, or just the first page
    when there is no terminal to page in."""
    if _JSON_OUTPUT:
        typer.echo(json.dumps(
            {"session_id": sid, "stage": stage, "total": len(rows), "rows": rows[:limit]},
            indent=2, default=str,
        ))
        return
    if not sys.stdin.isatty():
        if rows:
            render_fn(rows[:limit], max_rows=limit)
        else:
            ui.info(f"No {label} found.")
        return
    await _paged_view(rows, limit, render_fn, label)


async def _paged_view(rows: list, page_size: int, render_fn, label: str) -> None:
    """Display rows page by page with n/p/q controls."""
    from prompt_toolkit import PromptSession as _PS
    total = len(rows)
    if total == 0:
        ui.info(f"No {label} found.")
        return
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = 0
    ps: _PS[str] = _PS()
    while True:
        start = page * page_size
        page_rows = rows[start : start + page_size]
        ui.console.print(
            f"\n[dim]Page {page + 1}/{total_pages}  ({start + 1}–{min(start + page_size, total)} of {total} {label})[/]"
        )
        render_fn(page_rows, max_rows=page_size)
        if total_pages == 1:
            break
        ui.console.print(
            "[dim]  [n] next  [p] prev  [q] back[/]"
        )
        try:
            cmd = (await ps.prompt_async("  ")).strip().lower()
        except (KeyboardInterrupt, EOFError):
            break
        if cmd in ("q", "b", "back", ""):
            break
        if cmd == "n" and page < total_pages - 1:
            page += 1
        elif cmd == "p" and page > 0:
            page -= 1


def _resolve_session(db_path: Path, session_id: str) -> PipelineSession | None:
    """Resolve a session by exact ID or unambiguous prefix.

    Prints its own error and returns None on no-match or ambiguous-prefix,
    so callers can just check for None rather than duplicating messaging.
    """
    with open_session(db_path) as db:
        session = db.get(PipelineSession, session_id)
        if session:
            return session
        all_s = db.exec(select(PipelineSession)).all()
        matches = [x for x in all_s if x.id.startswith(session_id)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            _report_error("Ambiguous session ID prefix; be more specific")
            return None
        _report_error(f"Session '{session_id}' not found in {db_path}")
        return None


def _report_error(msg: str) -> None:
    """Print an error for a human, or as JSON on stdout under --json so a
    caller parsing the output (the MCP server, a script) gets the reason."""
    if _JSON_OUTPUT:
        typer.echo(json.dumps({"error": msg}))
    else:
        ui.error(msg)


async def _view_session(session_id: str, stage: str | None, limit: int = 5) -> None:
    from dataforge.storage import ExportRecord, ProcessedChunk, ScrapedPage
    s = get_settings()

    session = _resolve_session(s.db_path, session_id)
    if not session:
        raise typer.Exit(code=1)

    sid = session.id
    if not _JSON_OUTPUT:
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
        await _show_rows(sid, stage, rows, limit, ui.view_urls, "discovered URLs")

    elif stage == "collection":
        with open_session(s.db_path) as db:
            rows_db = db.exec(select(ScrapedPage).where(ScrapedPage.session_id == sid)).all()
        rows = [{"url": r.url, "title": r.title, "word_count": r.word_count,
                 "scraped_at": r.scraped_at.strftime("%Y-%m-%d %H:%M")} for r in rows_db]
        await _show_rows(sid, stage, rows, limit, ui.view_pages, "scraped pages")

    elif stage == "processing":
        with open_session(s.db_path) as db:
            rows_db = db.exec(select(ProcessedChunk).where(ProcessedChunk.session_id == sid)).all()
        rows = [{"chunk_index": r.chunk_index, "token_count": r.token_count,
                 "content": r.content,
                 "source_url": r.parsed_meta().get("source_url", "")} for r in rows_db]
        await _show_rows(sid, stage, rows, limit, ui.view_chunks, "chunks")

    elif stage in ("generation", "quality"):
        with open_session(s.db_path) as db:
            query = select(SyntheticSample).where(SyntheticSample.session_id == sid)
            if stage == "quality":
                query = query.where(SyntheticSample.approved == True)  # noqa: E712
            rows_db = db.exec(query).all()
        rows = [{"id": r.id, "chunk_id": r.chunk_id, "format": r.format,
                 "quality_score": r.quality_score, "approved": r.approved,
                 "rejection_reason": r.rejection_reason, "messages": r.messages()}
                for r in rows_db]
        title = "Approved Samples" if stage == "quality" else "Generated Samples"
        await _show_rows(sid, stage, rows, limit,
                         lambda r, max_rows: ui.view_samples(r, title=title, max_rows=max_rows), "samples")

    else:
        _report_error(f"Unknown stage '{stage}'. Valid: discovery, collection, processing, generation, quality")
        raise typer.Exit(code=1)


# ── stats command ─────────────────────────────────────────────────────────────

@app.command()
def stats(
    session_id: str = typer.Argument(..., help="Session ID (or prefix) to summarize"),
) -> None:
    """Show dataset statistics for a session: approval rate, rejection
    breakdown, quality-score distribution, length stats, and realized split
    proportions from the most recent export. Read-only — computed entirely
    from data the pipeline already produced during generation/quality/export.
    """
    _bootstrap()
    s = get_settings()
    session = _resolve_session(s.db_path, session_id)
    if not session:
        raise typer.Exit(code=1)

    result = compute_session_stats(s.db_path, session.id, session.session_dir(s.output_dir))

    if _JSON_OUTPUT:
        typer.echo(json.dumps({
            "session_id": session.id,
            "total_samples": result.total_samples,
            "approved": result.approved,
            "rejected": result.rejected,
            "rejection_reasons": result.rejection_reasons,
            "question_length": vars(result.question_length),
            "answer_length": vars(result.answer_length),
            "score": vars(result.score),
            "split_counts": result.split_counts,
        }, indent=2))
        return

    ui.info(f"Session [bold]{session.name}[/]  [{session.id[:8]}]")
    if result.total_samples == 0:
        ui.info("No samples generated yet for this session.")
        return
    ui.stats_summary(result)


# ── config command ────────────────────────────────────────────────────────────

@app.command()
def config() -> None:
    """Interactively configure LLM provider and defaults."""
    _bootstrap()
    asyncio.run(_configure())


async def _configure() -> None:
    ui.section("Configuration")

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
    # Settings is loaded once per process: without this, a change made from
    # the menu only took effect after a restart, and the next run still
    # checked the old provider's key.
    s = get_settings()
    s.llm_provider = provider
    s.llm_model = model

    if info.requires_key:
        import getpass
        existing = os.getenv(info.key_env, "") or user_prefs.get_api_key(info.key_env)
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
        elif existing:
            os.environ.setdefault(info.key_env, existing)
        else:
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
    from rich import box
    from rich.table import Table
    t = Table(box=box.SIMPLE_HEAD, title="Available Providers")
    t.add_column("Provider")
    t.add_column("Models")
    t.add_column("API Key Required")
    for name, info in PROVIDER_INFO.items():
        t.add_row(info.name, "\n".join(info.models), "Yes" if info.requires_key else "No (local)")
    console.print(t)


# ── test-llm command ──────────────────────────────────────────────────────────

_TEST_QUESTIONS = [
    "What is the capital of France?",
    "Name two prime numbers between 10 and 20.",
    "In one sentence, what does photosynthesis do?",
    "What year did the first human land on the Moon?",
    "Spell the word 'necessary' correctly.",
    "What is 17 multiplied by 6?",
    "Name one gas that makes up most of Earth's atmosphere.",
    "Who wrote the play 'Romeo and Juliet'?",
]


def _configured_providers() -> list[str]:
    """Providers with a usable key (env, .env-loaded settings, or saved prefs) — plus Ollama, which needs none."""
    from dataforge.cli import prefs as user_prefs
    s = get_settings()
    available = []
    for name, info in PROVIDER_INFO.items():
        if not info.requires_key:
            available.append(name)
            continue
        has_key = bool(
            os.getenv(info.key_env)
            or getattr(s, info.key_env.lower(), "")
            or user_prefs.get_api_key(info.key_env)
        )
        if has_key:
            available.append(name)
    return available


@app.command(name="test-llm")
def test_llm() -> None:
    """Pick a configured model and ask it a random test question."""
    _bootstrap()
    asyncio.run(_test_llm())


async def _test_llm() -> None:
    import random

    import questionary

    from dataforge.generators import LLMClient
    from dataforge.utils.errors import LLMConnectionError, MissingCredentialError, show_error

    available = _configured_providers()
    if not available:
        ui.warn(
            "No LLM provider is configured yet.\n"
            "Run 'dataforge config' to set one up (or 'dataforge config' → ollama for a local model)."
        )
        return

    provider = await questionary.select(
        "Which configured provider do you want to test?",
        choices=available,
    ).ask_async()
    if not provider:
        return

    model = await prompts.ask_model(PROVIDER_INFO[provider].models)
    if not model:
        return

    question = random.choice(_TEST_QUESTIONS)
    ui.info(f"Asking {provider}/{model}:  \"{question}\"")

    client = LLMClient(model_override=model, provider_override=provider)
    try:
        with console.status("[bold cyan]Waiting for response…[/]"):
            resp = await client.complete([{"role": "user", "content": question}])
    except MissingCredentialError as exc:
        show_error(exc.credential)
        return
    except LLMConnectionError as exc:
        show_error("LLM_CONNECTION", extra=str(exc))
        return
    except Exception as exc:
        show_error("test-llm", extra=str(exc))
        return

    ui.llm_answer_panel(
        provider, model, question, resp.content,
        resp.prompt_tokens, resp.completion_tokens, resp.cost_usd,
    )


# ── update command ───────────────────────────────────────────────────────────

# uv prints this once the new version is in the tool environment, even when a
# later step (refreshing the launcher in its bin directory) fails.
_UV_UPDATED_RE = re.compile(r"Updated llm-web-crawler v(\S+) -> v(\S+)")


def _output_tail(out: str, lines: int = 6) -> None:
    """Print the last few lines of an installer's output, unstyled."""
    for line in [ln for ln in out.splitlines() if ln.strip()][-lines:]:
        ui.console.print(f"  {line}", style="dim", markup=False, highlight=False)


def _run_update() -> bool:
    """Upgrade DataForge in place. Returns False only when the upgrade failed."""
    import subprocess
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
        return True

    ui.info("Checking for updates…")

    def _try_update(cmd: list[str]) -> tuple[bool, str]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            # e.g. `uv` isn't on PATH for a pip-installed user — not an error,
            # just means this update method isn't available; fall through.
            return False, f"{cmd[0]} not found"
        return r.returncode == 0, r.stdout + r.stderr

    # 1. Try uv tool upgrade (preferred for uv-installed tools)
    ok, uv_out = _try_update(["uv", "tool", "upgrade", "llm-web-crawler"])
    m = _UV_UPDATED_RE.search(uv_out)
    if m and m.group(1) != m.group(2):
        ui.success(f"Updated via uv: [dim]{m.group(1)}[/] → [bold green]{m.group(2)}[/]")
        if not ok:
            # The new version is installed; only a follow-up step failed. On
            # Windows this is uv failing to overwrite the dataforge.exe that
            # is running this very command (os error 32).
            ui.warn(
                "uv installed the new version but could not finish refreshing "
                "the `dataforge` launcher (usually because it was in use). "
                "DataForge works as normal; if a new command is missing, run: "
                "[cyan]uv tool install --force llm-web-crawler[/]"
            )
            _output_tail(uv_out, lines=3)
        return True
    if ok:
        if "pinned" in uv_out.lower():
            # Installed with an exact version (`uv tool install llm-web-crawler==X`),
            # so uv upgrades nothing; say so rather than claiming we're current.
            ui.warn(f"Not upgraded: v{current_ver} is pinned. uv says:")
            _output_tail(uv_out, lines=3)
        else:
            ui.success(f"Already up to date (v{current_ver})")
        return True

    # 2. Fall back to pip
    ok, pip_out = _try_update(
        [sys.executable, "-m", "pip", "install", "--upgrade", "llm-web-crawler"]
    )
    if ok:
        new_ver = current_ver
        for line in pip_out.splitlines():
            if "Successfully installed" in line:
                for token in line.split():
                    if token.lower().startswith("llm-web-crawler-"):
                        new_ver = token[len("llm-web-crawler-"):]
                        break
        if new_ver != current_ver and current_ver != "unknown":
            ui.success(f"Updated: [dim]{current_ver}[/] → [bold green]{new_ver}[/]")
        else:
            ui.success(f"Already up to date (v{current_ver})")
        return True

    ui.error("Update failed.")
    if uv_out != "uv not found":
        ui.console.print("  uv said:", style="dim")
        _output_tail(uv_out)
    if "No module named pip" not in pip_out:
        # A uv tool environment has no pip; that failure is expected noise.
        ui.console.print("  pip said:", style="dim")
        _output_tail(pip_out)
    ui.console.print("Try manually:", style="dim")
    ui.console.print("  uv tool upgrade llm-web-crawler", style="dim")
    ui.console.print("  pip install --upgrade llm-web-crawler", style="dim")
    return False


def _has_terminal() -> bool:
    """True when prompts can be shown. stdin alone is not enough: on Windows
    `NUL` (`< /dev/null`) reports isatty(), and prompts also need a console
    to draw on, which a piped stdout (agents, scripts) is not."""
    return sys.stdin.isatty() and sys.stdout.isatty()


_RELEASES_URL = "https://github.com/ianktoo/data-forge/releases"


def _self_manage_refused(action: str) -> bool:
    """Updating and uninstalling are for a person at a terminal, never an agent.

    A new release can change recipes, outputs or commands that an agent (and
    the prompts driving it) rely on, so someone should read the release notes
    before deciding to move. Returns True, after explaining, when refused.
    """
    if os.getenv("CLAUDECODE"):
        reason = "it is running inside an AI agent session (Claude Code)"
    elif not _has_terminal():
        reason = "there is no interactive terminal (scripts and AI agents)"
    else:
        return False
    ui.error(f"`dataforge {action}` refused: {reason}.")
    ui.info(
        "Updating and uninstalling are only done by a person, in their own terminal. "
        "A new version can change what recipes, outputs and commands look like, so "
        f"read the release notes first: [cyan]{_RELEASES_URL}[/]"
    )
    return True


def _warn_other_instances() -> bool:
    """Warn about other DataForge processes holding this install's files.
    Returns True if there were any."""
    from dataforge.cli import self_manage
    others = self_manage.other_instances()
    if not others:
        return False
    ui.warn(
        f"{len(others)} other DataForge process(es) are running from this install "
        "(for example an MCP client running `dataforge mcp`). Close them first, "
        "or the installer may fail with 'file in use':"
    )
    for pid, cmd in others:
        ui.console.print(f"  PID {pid}: {cmd}", style="dim", markup=False, highlight=False)
    return True


def _source_checkout_note() -> None:
    ui.info(
        "This DataForge runs from a source checkout (editable install). "
        "Update it with [bold]git pull[/] and [bold]uv sync[/]; remove it by "
        "deleting the cloned folder."
    )


async def _update_flow(in_place: bool) -> int:
    """Returns an exit code. Exits the process itself when handing off."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    import questionary

    from dataforge.cli import self_manage

    if _self_manage_refused("update"):
        return 2
    kind = self_manage.detect_install()
    if kind == "frozen":
        return 0 if _run_update() else 1  # prints the releases-page message
    if kind == "editable":
        _source_checkout_note()
        return 0

    try:
        current_ver = _pkg_version(self_manage.PACKAGE)
    except PackageNotFoundError:
        current_ver = "unknown"
    ui.info(f"Current version: [bold]{current_ver}[/]")
    with ui.console.status("[bold cyan]Checking PyPI for the latest release…[/]"):
        latest = self_manage.latest_version()
    if latest and current_ver != "unknown" and not self_manage.is_newer(latest, current_ver):
        ui.success(f"Already up to date (v{current_ver})")
        return 0
    if latest:
        ui.info(f"Latest release: [bold green]{latest}[/]")
        if current_ver != "unknown" and self_manage.is_major_jump(latest, current_ver):
            ui.warn(
                f"This is a major version change ({current_ver} → {latest}): expect "
                "breaking changes to recipes, outputs or commands."
            )
    else:
        ui.warn("Could not reach PyPI to check the latest release; trying anyway.")
    ui.info(f"Read the release notes before updating: [cyan]{_RELEASES_URL}[/]")

    choice = await questionary.select(
        "How should DataForge update?",
        choices=[
            questionary.Choice("Close DataForge, then update (recommended)", value="exit"),
            questionary.Choice("Update in place (may end with an error; check again after)",
                               value="in_place"),
            questionary.Choice("Cancel", value="cancel"),
        ],
        default="in_place" if in_place else "exit",
        **prompts._q(),
    ).ask_async()
    if choice in (None, "cancel"):
        ui.info("Cancelled.")
        return 0

    if choice == "in_place":
        ui.warn(
            "Updating DataForge while it is running. On Windows this often ends "
            "with an error about a file being in use even though the update "
            "worked. Afterwards, run [bold]dataforge update[/] again to check."
        )
        ok = _run_update()
        ui.info("Run [bold]dataforge update[/] again to confirm you are on the latest version.")
        return 0 if ok else 1

    cmd = self_manage.installer_command(kind, "update")
    if cmd is None:
        ui.error("No installer found for this environment (neither pip nor uv). Update manually:")
        ui.console.print(f"  pip install --upgrade {self_manage.PACKAGE}", style="dim")
        return 1
    _warn_other_instances()
    ui.warn(
        "DataForge will close now so the update can replace it"
        + (" (progress opens in a new window)." if os.name == "nt" else ".")
        + " When it finishes, run [bold]dataforge update[/] again to check."
    )
    self_manage.hand_off(
        cmd,
        start="Updating DataForge…",
        ok="Update finished. Run `dataforge update` again to check the version.",
        fail=("Update failed. Close every DataForge process (including MCP clients "
              "running `dataforge mcp`) and run: " + " ".join(cmd)),
    )
    raise typer.Exit(0)


@app.command()
def update(
    in_place: bool = typer.Option(
        False, "--in-place",
        help="Preselect updating while DataForge runs. On Windows expect a 'file in use' error; run update again to check.",
    ),
) -> None:
    """Update DataForge (a person at a terminal only; agents are refused)."""
    code = asyncio.run(_update_flow(in_place))
    if code:
        raise typer.Exit(code=code)


# ── uninstall command ────────────────────────────────────────────────────────

async def _uninstall_flow(keep_data: bool | None) -> int:
    """Returns an exit code. Exits the process itself when handing off."""
    import questionary

    from dataforge.cli import self_manage

    if _self_manage_refused("uninstall"):
        return 2
    kind = self_manage.detect_install()
    if kind == "frozen":
        ui.info(
            f"This is a standalone executable. To uninstall, delete it: [dim]{sys.executable}[/]\n"
            "  Your data (dataforge.db, output/, .dataforge in each project folder) is not touched."
        )
        return 0
    if kind == "editable":
        _source_checkout_note()
        return 0
    cmd = self_manage.installer_command(kind, "uninstall")
    if cmd is None:
        ui.error("No installer found for this environment (neither pip nor uv). Uninstall manually:")
        ui.console.print(f"  pip uninstall {self_manage.PACKAGE}", style="dim")
        return 1

    cwd = Path.cwd()
    s = get_settings()
    _apply_project_file(s, cwd)
    deletable, outside = self_manage.data_paths(cwd, s.db_path, s.output_dir)

    if deletable:
        ui.info(f"DataForge data in this folder ([dim]{cwd}[/]):")
        for p in deletable:
            ui.console.print(f"  {p}", style="dim", markup=False, highlight=False)
        if keep_data is None:
            keep_data = await questionary.confirm(
                "Keep your data?", default=True, **prompts._q(),
            ).ask_async()
            if keep_data is None:
                ui.info("Cancelled.")
                return 0
        if not keep_data:
            sure = await questionary.confirm(
                "Permanently delete the files above once DataForge is uninstalled?",
                default=False, **prompts._q(),
            ).ask_async()
            keep_data = not sure
    keep_data = True if keep_data is None else keep_data
    to_delete = [] if keep_data else deletable
    if outside:
        ui.info("Not deleted (outside this folder; remove by hand if you want):")
        for p in outside:
            ui.console.print(f"  {p}", style="dim", markup=False, highlight=False)
    ui.info("Your .env file is never deleted; it may hold keys other tools use.")

    _warn_other_instances()
    ui.warn(
        "DataForge will close now to uninstall itself"
        + (" (progress opens in a new window)." if os.name == "nt" else ".")
        + (" Data is kept." if keep_data else " The data listed above is deleted after the uninstall.")
    )
    go = await questionary.confirm("Uninstall DataForge now?", default=False, **prompts._q()).ask_async()
    if not go:
        ui.info("Cancelled.")
        return 0
    self_manage.hand_off(
        cmd,
        start="Uninstalling DataForge…",
        ok="DataForge is uninstalled.",
        fail=("Uninstall failed. Close every DataForge process (including MCP clients "
              "running `dataforge mcp`) and run: " + " ".join(cmd)),
        delete=to_delete,
    )
    raise typer.Exit(0)


@app.command()
def uninstall(
    delete_data: bool = typer.Option(
        None, "--delete-data/--keep-data",
        help="Preselect whether to delete this folder's DataForge data (dataforge.db, output/, .dataforge). Default: ask.",
        show_default=False,
    ),
) -> None:
    """Uninstall DataForge (a person at a terminal only; agents are refused)."""
    keep = None if delete_data is None else not delete_data
    code = asyncio.run(_uninstall_flow(keep))
    if code:
        raise typer.Exit(code=code)


# ── plan command ─────────────────────────────────────────────────────────────

@app.command()
def plan() -> None:
    """Show the full pipeline overview and current project status."""
    _bootstrap()
    _show_pipeline_plan()


# ── agent-guide command ───────────────────────────────────────────────────────

@app.command("agent-guide")
def agent_guide() -> None:
    """Print the usage guide for AI agents driving DataForge from a shell."""
    from importlib.resources import files
    typer.echo(files("dataforge").joinpath("agent_guide.md").read_text(encoding="utf-8"))


# ── mcp command ───────────────────────────────────────────────────────────────

@app.command()
def mcp() -> None:
    """Run DataForge as a local MCP server over stdio, for AI agents and MCP clients."""
    try:
        from dataforge.mcp_server import main
    except ImportError:
        typer.echo(
            "MCP support is an optional extra. Install it with:\n"
            "  pip install 'llm-web-crawler[mcp]'",
            err=True,
        )
        raise typer.Exit(code=1) from None
    main()


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

    ui.screen("Pipeline steps and project status")

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


async def _browse_discovered_urls(session_id: str) -> None:
    """Interactive paginated URL browser for a session's discovery stage."""
    from .url_review import run_url_review
    s = get_settings()
    with open_session(s.db_path) as db:
        rows = db.exec(select(DiscoveredURL).where(DiscoveredURL.session_id == session_id)).all()
    urls = [r.url for r in rows]
    if not urls:
        ui.info("No URLs discovered yet for this session.")
        return
    ui.info(f"[dim]Browsing {len(urls)} discovered URLs — changes are not saved[/]")
    await run_url_review(urls, ask_language=False)  # read-only: return value discarded


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
        stage_choices.append(questionary.Choice("Export this project's data", value="__export__"))
        stage_choices.append(questionary.Choice(f"Change sample limit  (current: {limit})", value="__limit__"))
        stage_choices.append(questionary.Choice("← Back to main menu", value="__back__"))

        picked = await questionary.select(
            "Which stage do you want to explore?", choices=stage_choices
        ).ask_async()

        if not picked or picked == "__back__":
            break

        if picked == "__export__":
            await _export_flow(_context_for(session), session.stage)
            continue

        if picked == "__limit__":
            raw = await questionary.text(
                f"Enter number of records to show per page (current: {limit}):",
                default=str(limit),
            ).ask_async()
            try:
                limit = max(1, int(raw or limit))
                ui.info(f"Page size set to [bold]{limit}[/]")
            except ValueError:
                ui.warn("Invalid number — keeping current limit")
            continue

        if picked == "discovery":
            await _browse_discovered_urls(session.id)
        else:
            await _view_session(session.id, picked, limit=limit)


# ── Wizard step functions ─────────────────────────────────────────────────────

async def _step_urls(state: dict) -> StepResult:
    """Step 1: collect seed URLs. Populates state['seed_urls']."""
    existing = state.get("seed_urls")
    if existing:
        import questionary
        ui.info("Current URLs:")
        for u in existing:
            ui.console.print(f"  [dim]{u}[/]")
        keep = await questionary.confirm(
            "Keep these URLs and continue?",
            default=True,
            **prompts._q(),
        ).ask_async()
        if keep is None:
            return "home"
        if keep:
            return "next"

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
    # A sitemap already lists the pages to choose from; anything else could
    # mean one page or a whole site, so ask rather than always crawling.
    if all(u.lower().endswith(".xml") for u in result):
        state["discovery_scope"] = "site"
    else:
        scope = await prompts.ask_discovery_scope(len(result))
        if scope is None:
            return "back"
        state["discovery_scope"] = scope
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
        # Pages named one by one are scraped even if seen before.
        if state.get("discovery_scope", "site") != "page":
            state["skip_known"] = await prompts.ask_skip_known(seed_domain)
        else:
            state["skip_known"] = False

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
            if action == "back":
                continue
            if action == "clear":
                ui.screen("Main menu")
                continue
            if action == "help":
                _show_help()
                continue
            if action == "scrape":
                await _quick_scrape_flow()
                continue
            if action == "export":
                await _export_menu()
                continue
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
                ui.screen("All projects")
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
                ui.screen("System info")
                _show_info()
                continue
            if action == "update":
                # Not update(): a failed upgrade must not exit the menu.
                await _update_flow(in_place=False)
                continue
            if action == "uninstall":
                await _uninstall_flow(keep_data=None)
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
        from dataforge.agents import PipelineContext
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
            discovery_scope=state.get("discovery_scope", "site"),
            quality_threshold=state.get("quality_threshold", 0.5),
            generation_model=state.get("generation_model", ""),
            quality_model=state.get("quality_model", ""),
        )

        # Write / update .dataforge project file so 'resume' always works
        from dataforge.cli.dataforge_file import add_session, create_project, find_project_file
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
            ui.screen("New dataset", "step 1 of 4: which site?")
            result = await _step_urls(state)
        elif step == "output":
            ui.screen("New dataset", "step 2 of 4: where to save")
            result = await _step_output(state)
        elif step == "config":
            ui.screen("New dataset", "step 3 of 4: what kind of data")
            result = await _step_config(state)
        elif step == "review":
            ui.screen("New dataset", "step 4 of 4: check and start")
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

_NEXT_STAGE = {
    PipelineStage.discovery:  PipelineStage.collection,
    PipelineStage.collection: PipelineStage.processing,
    PipelineStage.processing: PipelineStage.generation,
    PipelineStage.generation: PipelineStage.quality,
    PipelineStage.quality:    PipelineStage.export,
}

async def _adjust_settings(context: PipelineContext) -> None:
    """Mid-session settings menu — change the model or output dir between stages.

    Agents are rebuilt fresh at the start of each stage and read
    ``context.generation_model`` / ``context.quality_model`` /
    ``context.settings.output_dir`` at that point, so a change made here
    takes effect starting with the *next* stage.
    """
    s = context.settings
    target = await prompts.ask_adjust_settings_target()
    if target is None:
        return

    if target == "generation_model":
        current = context.generation_model or s.llm_model
        new_model = await prompts.ask_generation_model(current)
        if new_model and new_model != current:
            context.generation_model = new_model
            ui.success(f"Generation model set to [bold]{new_model}[/] for the next stage onward.")

    elif target == "quality_model":
        current = context.quality_model or context.generation_model or s.llm_model
        new_model = await prompts.ask_quality_model(current)
        if new_model and new_model != current:
            context.quality_model = new_model
            ui.success(f"Quality model set to [bold]{new_model}[/] for the next stage onward.")

    elif target == "output_dir":
        current = str(s.output_dir.resolve())
        chosen = await prompts.ask_output_dir(current)
        if chosen:
            new_dir = Path(chosen).expanduser().resolve()
            if new_dir != s.output_dir:
                # Deliberately NOT touching s.db_path — the running session's data
                # lives in the existing database; only new artifacts (exports, logs)
                # should follow the new output directory.
                s.output_dir = new_dir
                s.output_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
                s.logs_dir().mkdir(parents=True, exist_ok=True)
                ui.success(f"Output directory set to [bold]{new_dir}[/] for new exports/artifacts.")
                ui.info("The session database location is unchanged — existing session data stays where it is.")


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
        nxt = _NEXT_STAGE.get(stage)
        next_name = _stage_map[nxt][0] if nxt in _stage_map else ""

        # Results first, then the tip, then the menu: the helper text always
        # sits directly above the prompt instead of scrolling off the top.
        _print_stage_summary(stage, context)
        ui.tip(stage)

        # Always offer export after collection+ stages
        if stage in (PipelineStage.collection, PipelineStage.processing,
                     PipelineStage.generation, PipelineStage.quality):
            while True:
                action = await prompts.ask_stage_action(name, next_name)
                if action == "adjust":
                    await _adjust_settings(context)
                    continue  # re-show the same menu so the user can continue/export/pause next
                if action == "explain":
                    ui.pipeline_overview_panel(current_stage=stage, next_stage=nxt)
                    if nxt in _STAGE_PRE_DESCRIPTIONS:
                        ui.stage_description(next_name, _stage_map[nxt][1], _STAGE_TOTAL,
                                             _STAGE_PRE_DESCRIPTIONS[nxt])
                    continue
                if action == "export":
                    await _export_flow(context, stage)
                    continue  # back to the checkpoint: continue, export more, or stop
                if action == "pause":
                    ui.info(f"Session saved. Resume with: [bold]dataforge resume {context.session_id[:8]}[/]")
                    return False
                return True
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

        if context.discovery_scope == "page":
            # The user named the pages; there is nothing to choose from.
            selected = list(context.discovered_urls)
        else:
            from .url_review import run_url_review
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
        # Each stage starts on a fresh screen so progress output from the
        # previous one does not pile up.
        ui.screen(f"Step {step} of {_STAGE_TOTAL}: {name}", context.session_name)
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

    from dataforge.agents import Orchestrator
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


async def _export_flow(ctx: PipelineContext, stage: str = "", approved_only: bool = True) -> None:
    """Export whatever the session has so far: training data and/or plain files.

    Works at every stage. Before generation there are no samples, but the
    discovered URLs, scraped pages and chunks are still worth having as
    Markdown, text or JSON.
    """
    from datetime import datetime

    from dataforge.exporters.stage_data import FORMAT_LABELS, KINDS, available_data

    s = ctx.settings
    have = available_data(s.db_path, ctx.session_id)
    if not have:
        ui.warn("Nothing to export yet: this project has no URLs, pages or samples.")
        return

    with open_session(s.db_path) as db:
        n_approved = len(db.exec(
            select(SyntheticSample.id)
            .where(SyntheticSample.session_id == ctx.session_id)
            .where(SyntheticSample.approved == True)  # noqa: E712
        ).all())
    n_training = n_approved if approved_only else have.get("samples", 0)

    options: list[tuple[str, str, str]] = []
    if n_training:
        options.append((
            f"Training dataset ({n_training} samples)", "training",
            "JSONL, Parquet, CSV and Unsloth files ready for fine-tuning. "
            "Can also upload to HuggingFace or Kaggle.",
        ))
    descriptions = {
        "urls":    "The list of links found on the site.",
        "pages":   "The text of each page, cleaned. Good for reading, search or RAG.",
        "chunks":  "Pages split into smaller passages, with their source URL.",
        "samples": "Every generated sample, approved or not, with its quality score.",
    }
    # Latest stage first: that is usually what people want right now.
    for kind in reversed(list(KINDS)):
        if kind in have:
            options.append((f"{KINDS[kind].label} ({have[kind]})", kind, descriptions[kind]))

    picked = await prompts.ask_export_what(options)
    if not picked:
        ui.info("Nothing exported.")
        return

    export_dir = ctx.session_dir() / "exports" / datetime.now().strftime("%Y%m%d_%H%M%S")
    written: list[tuple[str, Path]] = []
    for kind in picked:
        if kind == "training":
            continue
        fmts = await prompts.ask_export_formats(KINDS[kind].label, KINDS[kind].formats, FORMAT_LABELS)
        if not fmts:
            continue
        from dataforge.exporters.stage_data import export_stage_data
        paths = export_stage_data(s.db_path, ctx.session_id, kind, fmts, export_dir)
        written += [(f"{KINDS[kind].label} ({fmt})", p) for fmt, p in paths.items()]

    if "training" in picked:
        targets = await prompts.ask_export_targets(
            hf_configured=bool(s.huggingface_token),
            kg_configured=bool(s.kaggle_username and s.kaggle_key),
        ) or ["local"]
        export_kw: dict = {"targets": targets, "approved_only": approved_only,
                           "stage_snapshot": stage or ctx.current_stage}
        if "huggingface" in targets:
            export_kw["hf_repo_id"] = await prompts.ask_hf_repo()
            export_kw["hf_private"] = await prompts.ask_hf_private()
        if "kaggle" in targets:
            export_kw["kaggle_slug"] = await prompts.ask_kaggle_slug(s.kaggle_username)
            export_kw["kaggle_title"] = ctx.session_name
        from dataforge.agents.exporter import ExporterAgent
        ctx_out = await ExporterAgent(ctx, **export_kw).run()
        ui.export_summary(ctx_out.export_records)

    if written:
        ui.info(f"Files saved in [bold]{export_dir}[/]")
    for label, path in written:
        suffix = "/" if path.is_dir() else ""
        ui.success(f"{label}  [dim]{path.name}{suffix}[/]")


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


async def _pick_session(question: str) -> PipelineSession | None:
    """Choose any session, newest first. None on back/cancel or when there are none."""
    import questionary
    s = get_settings()
    with open_session(s.db_path) as db:
        rows = sorted(db.exec(select(PipelineSession)).all(),
                      key=lambda x: x.created_at, reverse=True)
    if not rows:
        ui.info("No projects yet. Start one from the main menu.")
        return None
    choices = [
        questionary.Choice(
            f"{x.name}  [{x.id[:8]}]", value=x.id,
            description=f"Reached: {x.stage}  ·  status: {x.status}  ·  "
                        f"started {x.created_at.strftime('%Y-%m-%d %H:%M')}",
        )
        for x in rows
    ]
    choices.append(questionary.Choice("← Back", value="__back__"))
    sid = await questionary.select(question, choices=choices, **prompts._q()).ask_async()
    if not sid or sid == "__back__":
        return None
    return next(x for x in rows if x.id == sid)


def _context_for(session: PipelineSession) -> PipelineContext:
    from dataforge.agents import PipelineContext
    return PipelineContext(
        session_id=session.id,
        session_name=session.name,
        goal=session.goal,
        format=DataFormat(session.format),
        seed_urls=session.seed_url_list(),
        settings=get_settings(),
    )


async def _export_menu() -> None:
    session = await _pick_session("Export data from which project?")
    if session:
        await _export_flow(_context_for(session), session.stage)


async def _quick_scrape_flow() -> None:
    """Menu version of `dataforge scrape`: pages and tables, no AI, no API key."""
    from datetime import datetime

    import questionary

    from dataforge import scrape as qs

    ui.screen("Scrape pages", "no AI, no API key needed")
    ui.info("Fetches the exact pages you give it (it does not crawl the whole site) "
            "and saves their text and tables.")
    urls = await _collect_urls()
    if not urls:
        return
    fmts = await questionary.checkbox(
        "Save as:",
        choices=[
            questionary.Choice("Markdown, one .md file per page", value="md", checked=True),
            questionary.Choice("JSON Lines (pages.jsonl, tables.jsonl)", value="jsonl", checked=True),
            questionary.Choice("Tables as CSV and JSON", value="csv", checked=True),
        ],
        instruction="(Space to tick, Enter to confirm)",
        validate=lambda v: bool(v) or "Tick at least one format",
        **prompts._q(),
    ).ask_async()
    if not fmts:
        return
    default_out = str(Path("scrape") / datetime.now().strftime("%Y%m%d-%H%M%S"))
    out = await prompts.ask_output_dir(default_out)
    if out is None:
        return
    s = get_settings()
    with ui.console.status(f"Fetching {len(urls)} page(s)…"):
        pages = await qs.scrape_urls(urls, rate_limit=s.rate_limit, tables="csv" in fmts)
    out_dir = Path(out).expanduser()
    qs.write_outputs(pages, out_dir, tuple(fmts))
    _print_scrape_results(pages, out_dir)


def _print_scrape_results(pages: list, out_dir: Path) -> None:
    ok = [p for p in pages if p.ok]
    for p in pages:
        if p.ok:
            ui.success(f"{p.url}  [dim]{p.title[:60]}[/]  {p.word_count} words, "
                       f"{len(p.tables)} table{'s' if len(p.tables) != 1 else ''}")
            for w in p.warnings:
                ui.warn(f"  {w}")
        else:
            ui.error(f"{p.url}  {p.status}")
    for p in ok:
        for k, t in enumerate(p.tables[:3], 1):
            _print_table_preview(t, f"{p.title or p.url}: table {k}")
    n_tables = sum(len(p.tables) for p in ok)
    ui.info(f"{len(ok)}/{len(pages)} page(s), {n_tables} table(s) saved to "
            f"[bold]{out_dir.resolve()}[/]")


_MENU_HELP = """\
[bold]What each option does[/]

  [bold cyan]Scrape pages (no AI)[/]
      Give it one or more page URLs; get their text as Markdown/JSON and any
      tables as CSV. No API key, costs nothing.

  [bold cyan]Build an AI training dataset[/]
      The full pipeline. Finds every page on a site, lets you pick which to
      keep, scrapes them, splits the text into chunks, has an LLM write
      training examples, filters them for quality, and exports.
      You can stop after any step and export what you have so far
      (pages as Markdown or text, chunks as JSON, and so on).
      Needs an LLM: an API key, or a local model through Ollama.

  [bold cyan]Continue a paused project[/]
      Pick up a project you stopped, from the step where it stopped.

  [bold cyan]Browse my data[/]
      Look through the URLs, pages, chunks and samples of any project.

  [bold cyan]Export data[/]
      Save any project's data as Markdown, text, JSON, CSV or a training set.

  [bold cyan]Settings and tools[/]
      Set your LLM provider and API key, see system info, update or uninstall.

[bold]Moving around[/]
  Arrow keys to move, Enter to choose, Space to tick in lists with boxes.
  Ctrl+C goes back or stops safely; your progress is always saved.
  Set DATAFORGE_NO_CLEAR=1 to keep all output instead of clearing the screen."""


def _show_help() -> None:
    ui.screen("Help")
    ui.console.print(_MENU_HELP)
    ui.console.print("")
    ui.pipeline_overview_panel()


async def _settings_menu() -> str:
    """Less common actions, grouped so the main menu stays short."""
    import questionary
    return await questionary.select(
        "Settings and tools",
        choices=[
            questionary.Choice("LLM provider and API key", value="config",
                               description="Choose OpenAI, Anthropic, Gemini, Groq, Ollama… and save your key."),
            questionary.Choice("List all projects", value="sessions",
                               description="Every project in this folder's database, with its status."),
            questionary.Choice("Pipeline steps and project status", value="plan",
                               description="What each step does and where your latest project is."),
            questionary.Choice("System info", value="info",
                               description="Version, paths, provider and machine resources."),
            questionary.Choice("Update DataForge", value="update"),
            questionary.Choice("Uninstall DataForge", value="uninstall"),
            questionary.Choice("← Back", value="back"),
        ],
        **prompts._q(),
    ).ask_async() or "back"


async def _main_menu() -> str:
    import questionary
    s = get_settings()
    try:
        with open_session(s.db_path) as db:
            paused_count = len(db.exec(
                select(PipelineSession).where(PipelineSession.status == SessionStatus.paused)
            ).all())
            total_sessions = len(db.exec(select(PipelineSession)).all())
    except Exception:
        paused_count, total_sessions = 0, 0

    # Worded as tasks, each with a one-line explanation that questionary shows
    # in the same spot under the list as the user moves through it.
    choices = [
        questionary.Choice(
            "Scrape pages (no AI)", value="scrape",
            description="Save the text and tables of pages you choose. No API key needed.",
        ),
        questionary.Choice(
            "Build an AI training dataset", value="new",
            description="Crawl a site, pick pages, and turn them into training examples. "
                        "You can stop and export after any step.",
        ),
    ]
    if paused_count:
        choices.append(questionary.Choice(
            f"Continue a paused project  ({paused_count} waiting)", value="resume",
            description="Pick up where you stopped. Nothing is lost.",
        ))
    if total_sessions:
        choices += [
            questionary.Choice(
                "Browse my data", value="explore",
                description="Look through the URLs, pages, chunks and samples of a project.",
            ),
            questionary.Choice(
                "Export data", value="export",
                description="Save a project's data as Markdown, text, JSON, CSV or a training set.",
            ),
        ]
    choices += [
        questionary.Choice(
            "Settings and tools", value="settings",
            description="LLM provider and API key, system info, update, uninstall.",
        ),
        questionary.Choice("Help", value="help", description="What every option does, and how the pipeline works."),
        questionary.Choice("Clear the screen", value="clear", description="Tidy up the terminal and show this menu again."),
        questionary.Choice("Exit", value="exit"),
    ]

    ui.console.print("")
    result = await questionary.select(
        "What would you like to do?",
        choices=choices,
        instruction="(arrows to move, Enter to choose)",
        **prompts._q(),
    ).ask_async()

    if result is None:
        raise typer.Exit()
    if result == "settings":
        return await _settings_menu()
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
