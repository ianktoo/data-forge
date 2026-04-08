"""Rich UI components — panels, tables, progress, banners."""
from __future__ import annotations

from typing import Any

from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

import sys
import io

# On Windows, reconfigure stdout with UTF-8 encoding to support emoji
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass  # Fallback: let Console handle it

console = Console()


def banner() -> None:
    text = Text()
    text.append("DataForge", style="bold cyan")
    text.append("  LLM Data Pipeline", style="dim")
    console.print(Panel(Align.center(text), box=box.DOUBLE_EDGE,
                        border_style="cyan", padding=(0, 4)))


def stage_header(stage: str, step: int, total: int) -> None:
    console.print(f"\n[bold cyan]Step {step}/{total}:[/] [bold]{stage.title()}[/]")


def success(msg: str) -> None:
    console.print(f"[bold green]✓[/] {msg}")


def info(msg: str) -> None:
    console.print(f"[cyan]·[/] {msg}")


def warn(msg: str) -> None:
    console.print(f"[bold yellow]⚠[/]  {msg}")


def error(msg: str) -> None:
    console.print(f"[bold red]✗[/] {msg}")


def section(title: str) -> None:
    console.rule(f"[bold]{title}[/]", style="cyan")


def url_table(urls: list[str], selected: list[bool] | None = None, max_rows: int = 30) -> None:
    t = Table(box=box.SIMPLE_HEAD, show_footer=False)
    t.add_column("#", style="dim", width=5)
    t.add_column("URL", no_wrap=False)
    if selected is not None:
        t.add_column("Selected", width=9)

    for i, url in enumerate(urls[:max_rows]):
        row: list[Any] = [str(i + 1), url]
        if selected is not None:
            row.append("[green]✓[/]" if selected[i] else "[red]✗[/]")
        t.add_row(*[str(c) for c in row])

    if len(urls) > max_rows:
        t.add_row("...", f"[dim]+ {len(urls) - max_rows} more[/]",
                  *([""] if selected else []))
    console.print(t)


def sessions_table(sessions: list[dict]) -> None:
    t = Table(box=box.SIMPLE_HEAD, title="Sessions")
    for col in ["ID", "Name", "Stage", "Status", "URLs", "Samples", "Created"]:
        t.add_column(col)
    for s in sessions:
        status_style = {
            "active": "green", "paused": "yellow",
            "completed": "cyan", "failed": "red",
        }.get(s.get("status", ""), "white")
        t.add_row(
            s["id"][:8],
            s.get("name", ""),
            s.get("stage", ""),
            f"[{status_style}]{s.get('status', '')}[/]",
            str(s.get("urls", 0)),
            str(s.get("samples", 0)),
            s.get("created", ""),
        )
    console.print(t)


def stats_panel(stats: dict) -> None:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold cyan")
    t.add_column()
    for k, v in stats.items():
        t.add_row(k, str(v))
    console.print(Panel(t, title="[bold]Pipeline Stats[/]", border_style="cyan"))


def review_panel(state: dict) -> None:
    """Display a pre-launch summary of all wizard inputs."""
    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold cyan", min_width=14)
    t.add_column()
    seed_urls = state.get("seed_urls", [])
    t.add_row("URLs", f"{len(seed_urls)} seed URL(s)")
    for url in seed_urls[:5]:
        t.add_row("", url)
    if len(seed_urls) > 5:
        t.add_row("", f"[dim]... and {len(seed_urls) - 5} more[/]")
    t.add_row("Session", state.get("session_name", ""))
    goal_text = state.get("goal", "")[:80]
    t.add_row("Goal", goal_text)
    t.add_row("Format", state.get("fmt", ""))
    t.add_row("Samples/chunk", str(state.get("n_per_chunk", 3)))
    console.print(Panel(t, title="[bold]Review & Confirm[/]", border_style="cyan"))


def make_progress(description: str = "Working") -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn(f"[cyan]{description}[/]  "),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


def sample_preview(messages: list[dict], format: str, n: int = 1) -> None:
    t = Table(box=box.ROUNDED, title=f"[bold]Sample Preview[/] ({format})", expand=True)
    t.add_column("Role", style="bold cyan", width=12)
    t.add_column("Content")
    for msg in messages[:n * 4]:
        role = msg.get("role", "?")
        content = msg.get("content", "")[:400]
        t.add_row(role, content)
    console.print(t)


def export_summary(records: list[dict]) -> None:
    if not records:
        return
    t = Table(box=box.SIMPLE_HEAD, title="Exports")
    t.add_column("Destination")
    t.add_column("Samples", justify="right")
    t.add_column("Location")
    for r in records:
        t.add_row(r["dest"], str(r["count"]), r["url"])
    console.print(t)
