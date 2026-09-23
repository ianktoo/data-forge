"""MCP server — exposes DataForge as tools to MCP clients (`dataforge mcp`).

Runs locally over stdio: the client (Claude Code, Claude Desktop, ...) starts
this process and talks to it on stdin/stdout, so nothing is hosted. stdout is
the protocol channel — nothing in this module may print to it.

Read-only tools shell out to the CLI's ``--json`` output so they stay in step
with it. Pipeline runs take minutes, longer than a client waits on one call,
so ``start_run`` launches ``dataforge run`` in the background and
``run_status`` polls it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from importlib.resources import files
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from dataforge import __version__
from dataforge.cli.recipe import RecipeError, load_recipe
from dataforge.config import get_settings

_GUIDE = files("dataforge").joinpath("agent_guide.md").read_text(encoding="utf-8")

_EXIT_MEANING = {
    0: "ok",
    2: "invalid recipe",
    3: "no URLs left after filters",
    4: "paused - resume with `dataforge resume <session-id>`",
    5: "completed with zero approved samples",
}

_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

mcp = MCPServer(
    name="dataforge",
    title="DataForge",
    description="Crawl a website and build a quality-filtered LLM fine-tuning dataset.",
    instructions=_GUIDE,
    version=__version__,
)

# run_id -> (process, log path). In memory only: a restarted server can still
# read a run's log, but no longer knows its exit code.
_runs: dict[str, tuple[subprocess.Popen, Path]] = {}


def _cli() -> list[str]:
    # A PyInstaller build is its own interpreter; `-m` only works from source/wheel installs.
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "dataforge.main"]


def _env() -> dict[str, str]:
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}


def _run_cli(*args: str, timeout: float = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*_cli(), "--quiet", "--no-color", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_env(), timeout=timeout, stdin=subprocess.DEVNULL,
    )


def _json_cli(*args: str) -> Any:
    """Run a CLI command with --json and return its parsed output, or
    {"error": reason} with the CLI's own message. Raising instead would reach
    the agent only as a bare "Error executing tool", with no reason."""
    proc = _run_cli("--json", *args)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and "error" in data:
        return data
    if proc.returncode != 0 or data is None:
        text = _clean(proc.stdout + "\n" + proc.stderr).strip()
        return {"error": text or f"dataforge exited with code {proc.returncode}"}
    return data


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# The live counter line, or a stage-completion line: whichever came last is
# the latest progress (a small run can finish a stage before any counter line).
_PROGRESS = re.compile(r"scraped \d+/\d+\s+chunks \d+\s+samples \d+|Stage '\w+' complete[^\n]*")


def _clean(text: str) -> str:
    """Strip terminal colour codes and loguru DEBUG lines, which carry nothing
    an agent needs (per-request and per-LLM-call chatter)."""
    lines = _ANSI.sub("", text).splitlines()
    return "\n".join(line for line in lines if "| DEBUG " not in line)


def _tail(path: Path, lines: int = 25) -> str:
    try:
        return "\n".join(_clean(path.read_text(encoding="utf-8", errors="replace")).splitlines()[-lines:])
    except OSError:
        return ""


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def explore_site(url: str, limit: int = 200) -> dict:
    """List the URLs a site's sitemap exposes, before writing a recipe.

    `url` is a site root or a sitemap URL. Fetches only robots.txt and sitemaps,
    never content pages. Returns at most `limit` URLs plus the total count.
    """
    from urllib.parse import urlparse

    from dataforge.collectors import HTTPClient, discover_sitemap_urls, parse_sitemaps
    from dataforge.utils import RateLimiter

    s = get_settings()
    async with HTTPClient(RateLimiter(s.rate_limit)) as client:
        parsed = urlparse(url)
        sitemaps = [url] if url.endswith(".xml") else await discover_sitemap_urls(
            client, f"{parsed.scheme}://{parsed.netloc}"
        )
        urls = await parse_sitemaps(client, sitemaps) if sitemaps else []
    # "sitemap" keeps its old meaning (the first, or None) for existing clients.
    return {"sitemap": sitemaps[0] if sitemaps else None, "sitemaps": sitemaps,
            "total": len(urls), "urls": urls[:limit]}


@mcp.tool(annotations=_READ_ONLY)
def validate_recipe(recipe_path: str) -> dict:
    """Validate a recipe and return the plan `dataforge run` would execute. Spends nothing."""
    proc = _run_cli("run", recipe_path, "--dry-run")
    return {
        "valid": proc.returncode == 0,
        "exit_code": proc.returncode,
        "output": (proc.stdout + proc.stderr).strip(),
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
def start_run(recipe_path: str, allow_uncapped_spend: bool = False) -> dict:
    """Start `dataforge run <recipe>` in the background; returns a run_id for run_status.

    This crawls a real website and spends LLM credits. Before calling it,
    confirm with the user that they reviewed the site's terms of use and agree
    to the recipe's budget. Refuses a recipe without `generation.max_cost_usd`
    or `generation.max_llm_calls` unless `allow_uncapped_spend` is true, and
    always refuses `source.ignore_robots: true`.
    """
    try:
        recipe = load_recipe(recipe_path)
    except RecipeError as exc:
        return {"started": False, "error": f"invalid recipe: {exc}"}
    if recipe.source.ignore_robots:
        return {
            "started": False,
            "error": "source.ignore_robots is true. An agent cannot verify the site owner's "
                     "permission; the user must run this recipe from their own terminal.",
        }
    capped = recipe.generation.max_cost_usd is not None or recipe.generation.max_llm_calls is not None
    if not capped and not allow_uncapped_spend:
        return {
            "started": False,
            "error": "recipe has no generation.max_cost_usd or generation.max_llm_calls. Add a "
                     "cap, or pass allow_uncapped_spend=true after the user agrees to uncapped spend.",
        }

    run_id = uuid.uuid4().hex[:12]
    log_dir = get_settings().output_dir / "mcp-runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_id}.log"
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [*_cli(), "--quiet", "--no-color", "run", recipe_path],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=_env(),
        )
    _runs[run_id] = (proc, log_path)
    return {"started": True, "run_id": run_id, "log": str(log_path)}


@mcp.tool(annotations=_READ_ONLY)
def run_status(run_id: str) -> dict:
    """Report a run started by start_run: running or finished, exit code, session ID, log tail."""
    if run_id in _runs:
        proc, log_path = _runs[run_id]
        code = proc.poll()
    else:
        log_path = get_settings().output_dir / "mcp-runs" / f"{run_id}.log"
        if not log_path.exists():
            return {"error": f"unknown run_id {run_id}"}
        code = None
    text = _clean(log_path.read_text(encoding="utf-8", errors="replace"))
    match = re.search(r"Session ID:\s*(\S+)", text)
    progress = _PROGRESS.findall(text)
    return {
        "state": ("running" if run_id in _runs else "unknown (server restarted)") if code is None else "finished",
        "exit_code": code,
        "meaning": _EXIT_MEANING.get(code, "") if code is not None else "",
        "session_id": match.group(1) if match else None,
        "progress": progress[-1] if progress else None,
        "log_tail": _tail(log_path),
    }


@mcp.tool(annotations=_READ_ONLY)
def list_sessions() -> list:
    """List pipeline sessions in the current project database."""
    return _json_cli("sessions")


@mcp.tool(annotations=_READ_ONLY)
def session_stats(session_id: str) -> dict:
    """Approval rate, rejection reasons, score and length stats, and split counts for a session."""
    return _json_cli("stats", session_id)


@mcp.tool(annotations=_READ_ONLY)
def view_samples(session_id: str, stage: str = "quality", limit: int = 5) -> Any:
    """Sample records from one stage: discovery | collection | processing | generation | quality."""
    return _json_cli("view", session_id, "--stage", stage, "--limit", str(limit))


@mcp.resource("dataforge://guide", name="guide", mime_type="text/markdown")
def guide() -> str:
    """The DataForge agent guide: commands, configuration, workflow and responsible-use rules."""
    return _GUIDE


def main() -> None:
    # Resolve the database and output folder the same way the CLI does, so
    # run logs land in the project's output folder.
    from dataforge.cli.app import _apply_project_file
    _apply_project_file(get_settings(), Path.cwd())
    mcp.run("stdio")
