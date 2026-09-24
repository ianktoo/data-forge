"""Rich UI components — panels, tables, progress, banners."""
from __future__ import annotations

import sys
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

# On Windows, reconfigure stdout with UTF-8 encoding to support emoji
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass  # Fallback: let Console handle it

console = Console()

# Re-export Progress so callers can type-hint with ui.Progress
__all__ = ["Progress"]


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


def _can_clear() -> bool:
    import os
    # DATAFORGE_NO_CLEAR=1 keeps the full scrollback (useful when debugging).
    return console.is_terminal and not os.getenv("DATAFORGE_NO_CLEAR")


def screen(title: str, subtitle: str = "") -> None:
    """Start a fresh screen: clear the terminal, then a one-line title bar.

    Every interactive screen follows the same layout so users always know
    where to look: title at the top, content in the middle, then status
    messages, then key hints directly above the prompt (see ``hints``).
    """
    if _can_clear():
        console.clear()
    else:
        console.print("")
    head = f"[bold cyan]DataForge[/]  [bold]{title}[/]"
    if subtitle:
        head += f"  [dim]{subtitle}[/]"
    console.print(head)
    console.rule(style="cyan")


def hints(text: str) -> None:
    """Key hints for the prompt below. Always the last thing before a prompt."""
    console.rule(style="dim")
    console.print(f"[dim]{text}[/]")


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
    scope = {
        "page": "Only the URL(s) above",
        "links": "The URL(s) above and the pages they link to",
        "site": "The whole site",
    }.get(state.get("discovery_scope", "site"), "")
    t.add_row("Scrape", scope)
    t.add_row("Session", state.get("session_name", ""))
    goal_text = state.get("goal", "")[:80]
    t.add_row("Goal", goal_text)
    t.add_row("Format", state.get("fmt", ""))
    t.add_row("Samples/chunk", str(state.get("n_per_chunk", 3)))
    console.print(Panel(t, title="[bold]Review & Confirm[/]", border_style="cyan"))


def tip(stage: str) -> None:
    """Print the next rotating tip for a pipeline stage."""
    from dataforge.cli import prefs
    from dataforge.cli.tips import GENERAL_TIPS, STAGE_TIPS

    tips = STAGE_TIPS.get(stage, GENERAL_TIPS)
    idx = prefs.next_tip_index(stage, len(tips))
    text = Text()
    text.append("Tip  ", style="bold yellow")
    text.append(tips[idx])
    console.print(Panel(text, border_style="dim yellow", box=box.SIMPLE, padding=(0, 1)))


def make_progress(description: str = "Working") -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,  # clear bar from terminal on stop, prevents cursor artifacts
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


# ── View command display functions ─────────────────────────────────────────────

def view_summary(stage_counts: dict) -> None:
    """Overview panel showing record counts per pipeline stage."""
    t = Table.grid(padding=(0, 3))
    t.add_column(style="bold cyan", min_width=16)
    t.add_column(justify="right")
    labels = {
        "discovered": "Discovered URLs",
        "scraped":    "Scraped pages",
        "chunks":     "Processed chunks",
        "samples":    "Generated samples",
        "approved":   "Approved samples",
        "exports":    "Exports",
    }
    for key, label in labels.items():
        count = stage_counts.get(key, 0)
        style = "green" if count else "dim"
        t.add_row(label, f"[{style}]{count}[/]")
    console.print(Panel(t, title="[bold]Session Overview[/]", border_style="cyan"))


def stats_summary(stats: Any) -> None:
    """Render a SessionStats object: approval rate, rejection breakdown,
    score distribution, length stats, and realized split proportions."""
    overview = Table.grid(padding=(0, 3))
    overview.add_column(style="bold cyan", min_width=18)
    overview.add_column(justify="right")
    rate = (stats.approved / stats.total_samples * 100) if stats.total_samples else 0.0
    overview.add_row("Total samples", str(stats.total_samples))
    overview.add_row("Approved", f"[green]{stats.approved}[/] ({rate:.0f}%)")
    overview.add_row("Rejected", f"[red]{stats.rejected}[/]" if stats.rejected else "0")
    console.print(Panel(overview, title="[bold]Dataset Overview[/]", border_style="cyan"))

    if stats.rejection_reasons:
        t = Table(box=box.SIMPLE_HEAD, title="Rejection Breakdown")
        t.add_column("Reason")
        t.add_column("Count", justify="right")
        for reason, count in sorted(stats.rejection_reasons.items(), key=lambda kv: -kv[1]):
            t.add_row(reason, str(count))
        console.print(t)

    if stats.score.count:
        t = Table(box=box.SIMPLE_HEAD, title="Quality Score Distribution")
        t.add_column("Metric")
        t.add_column("Value", justify="right")
        t.add_row("Min",    f"{stats.score.min:.2f}")
        t.add_row("Median", f"{stats.score.median:.2f}")
        t.add_row("Mean",   f"{stats.score.mean:.2f}")
        t.add_row("Max",    f"{stats.score.max:.2f}")
        console.print(t)

        n = len(stats.score.histogram)
        hist = Table(box=box.SIMPLE_HEAD, title="Score Histogram")
        hist.add_column("Range")
        hist.add_column("Count", justify="right")
        hist.add_column("")
        max_count = max(stats.score.histogram) or 1
        for i, count in enumerate(stats.score.histogram):
            lo, hi = i / n, (i + 1) / n
            bar = "█" * int(count / max_count * 20)
            hist.add_row(f"{lo:.1f}–{hi:.1f}", str(count), f"[cyan]{bar}[/]")
        console.print(hist)

    if stats.question_length.count or stats.answer_length.count:
        t = Table(box=box.SIMPLE_HEAD, title="Length (words)")
        t.add_column("")
        t.add_column("Min", justify="right")
        t.add_column("Median", justify="right")
        t.add_column("Mean", justify="right")
        t.add_column("Max", justify="right")
        for label, ls in (("Question", stats.question_length), ("Answer", stats.answer_length)):
            t.add_row(label, str(ls.min), f"{ls.median:.0f}", f"{ls.mean:.1f}", str(ls.max))
        console.print(t)

    if stats.split_counts:
        total = sum(stats.split_counts.values()) or 1
        t = Table(box=box.SIMPLE_HEAD, title="Realized Split (most recent export)")
        t.add_column("Split")
        t.add_column("Samples", justify="right")
        t.add_column("Share", justify="right")
        for name, count in stats.split_counts.items():
            t.add_row(name, str(count), f"{count / total * 100:.1f}%")
        console.print(t)


def view_urls(rows: list[dict], max_rows: int = 50) -> None:
    """Table of discovered URLs."""
    t = Table(box=box.SIMPLE_HEAD, title=f"Discovered URLs ({len(rows)} total)")
    t.add_column("#", style="dim", width=5)
    t.add_column("URL", no_wrap=False)
    t.add_column("Source", width=10)
    t.add_column("Sel", width=5)
    t.add_column("Status", width=7, justify="right")
    for i, r in enumerate(rows[:max_rows]):
        sel = "[green]✓[/]" if r.get("selected") else "[dim]—[/]"
        status = str(r.get("http_status", "")) or "[dim]—[/]"
        t.add_row(str(i + 1), r.get("url", ""), r.get("source", ""), sel, status)
    if len(rows) > max_rows:
        t.add_row("…", f"[dim]+ {len(rows) - max_rows} more[/]", "", "", "")
    console.print(t)


def view_pages(rows: list[dict], max_rows: int = 50) -> None:
    """Table of scraped pages."""
    t = Table(box=box.SIMPLE_HEAD, title=f"Scraped Pages ({len(rows)} total)")
    t.add_column("#", style="dim", width=5)
    t.add_column("URL", no_wrap=False)
    t.add_column("Title")
    t.add_column("Words", justify="right", width=8)
    t.add_column("Scraped at", width=17)
    for i, r in enumerate(rows[:max_rows]):
        t.add_row(
            str(i + 1),
            r.get("url", ""),
            r.get("title", "") or "[dim]—[/]",
            str(r.get("word_count", 0)),
            r.get("scraped_at", ""),
        )
    if len(rows) > max_rows:
        t.add_row("…", f"[dim]+ {len(rows) - max_rows} more[/]", "", "", "")
    console.print(t)


def view_chunks(rows: list[dict], max_rows: int = 50) -> None:
    """Table of processed chunks."""
    t = Table(box=box.SIMPLE_HEAD, title=f"Processed Chunks ({len(rows)} total)")
    t.add_column("#", style="dim", width=5)
    t.add_column("Chunk", width=7, justify="right")
    t.add_column("Tokens", width=7, justify="right")
    t.add_column("Source URL", no_wrap=False)
    t.add_column("Preview")
    for i, r in enumerate(rows[:max_rows]):
        preview = (r.get("content", "") or "")[:80].replace("\n", " ")
        t.add_row(
            str(i + 1),
            str(r.get("chunk_index", "")),
            str(r.get("token_count", 0)),
            r.get("source_url", "") or "[dim]—[/]",
            f"[dim]{preview}…[/]" if len(r.get("content", "")) > 80 else f"[dim]{preview}[/]",
        )
    if len(rows) > max_rows:
        t.add_row("…", "", "", f"[dim]+ {len(rows) - max_rows} more[/]", "")
    console.print(t)


def pipeline_overview_panel(current_stage: str | None = None, next_stage: str | None = None) -> None:
    """Print a full pipeline stage overview with descriptions."""

    STAGES = [
        ("discovery",  "1",  "Discovery",   "Crawls sitemaps and robots.txt to discover all URLs on the target site."),
        ("collection", "2",  "Collection",  "Fetches each URL and converts the page to clean Markdown text, respecting rate limits."),
        ("processing", "3",  "Processing",  "Splits pages into token-aware overlapping chunks and attaches source metadata."),
        ("generation", "4",  "Generation",  "Prompts the LLM to synthesise Q&A / instruction / conversation samples from each chunk."),
        ("quality",    "5",  "Quality",     "Asks the LLM to score every sample 1–5; only samples above the threshold are approved."),
        ("export",     "6",  "Export",      "Writes approved samples to local JSONL, HuggingFace Hub, or Kaggle datasets."),
    ]
    t = Table(box=box.SIMPLE_HEAD, title="[bold]Pipeline Stages[/]", show_header=True)
    t.add_column("Step", width=6, justify="center")
    t.add_column("Stage", width=14)
    t.add_column("What happens", no_wrap=False)

    for key, num, name, desc in STAGES:
        if key == current_stage:
            step_str = f"[bold cyan]► {num}[/]"
            name_str = f"[bold cyan]{name}[/]"
            desc_str = f"[cyan]{desc}[/]"
        elif key == next_stage:
            step_str = f"[yellow]{num}[/]"
            name_str = f"[yellow]{name}[/]"
            desc_str = f"[yellow]{desc}[/]"
        else:
            step_str = f"[dim]{num}[/]"
            name_str = f"[dim]{name}[/]"
            desc_str = f"[dim]{desc}[/]"
        t.add_row(step_str, name_str, desc_str)

    console.print(t)
    if current_stage:
        console.print(f"  [cyan]►[/] Currently at stage: [bold cyan]{current_stage}[/]")
    if next_stage:
        console.print(f"  [yellow]→[/] Next stage: [bold yellow]{next_stage}[/]")


def stage_description(stage: str, step: int, total: int, detail: str) -> None:
    """Print a rich stage header with what the stage will do."""
    from rich.text import Text as RText
    t = RText()
    t.append(f"Step {step}/{total}  ", style="bold cyan")
    t.append(stage.title(), style="bold white")
    t.append(f"\n{detail}", style="dim")
    console.print(Panel(t, border_style="cyan", box=box.ROUNDED, padding=(0, 2)))


def project_info_panel(project: dict) -> None:
    """Panel showing .dataforge project-level and folder-level details."""
    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold cyan", min_width=18)
    t.add_column()
    for k, v in project.items():
        t.add_row(k, str(v))
    console.print(Panel(t, title="[bold]Project & Folder Info[/]", border_style="cyan"))


def language_groups_panel(groups: dict[str, int], total: int) -> None:
    """Show detected language/locale groups in discovered URLs."""
    t = Table(box=box.SIMPLE_HEAD, title="[bold]Language Variants Detected[/]")
    t.add_column("Locale", style="bold cyan", width=10)
    t.add_column("URLs", justify="right", width=8)
    t.add_column("Share", width=10)
    t.add_column("", width=20)
    for lang, count in sorted(groups.items(), key=lambda x: -x[1]):
        pct = count / max(total, 1) * 100
        bar = "█" * int(pct / 5)
        t.add_row(lang, str(count), f"{pct:.0f}%", f"[cyan]{bar}[/]")
    console.print(t)


def quality_distribution_panel(scores: list[float], threshold: float) -> None:
    """Show quality score distribution with bucket counts."""
    if not scores:
        return
    buckets = {"Poor (<0.3)": 0, "Fair (0.3–0.5)": 0, "Good (0.5–0.8)": 0, "Excellent (≥0.8)": 0}
    for s in scores:
        if s < 0.3:
            buckets["Poor (<0.3)"] += 1
        elif s < 0.5:
            buckets["Fair (0.3–0.5)"] += 1
        elif s < 0.8:
            buckets["Good (0.5–0.8)"] += 1
        else:
            buckets["Excellent (≥0.8)"] += 1

    total = len(scores)
    approved = sum(1 for s in scores if s >= threshold)
    t = Table(box=box.SIMPLE_HEAD,
              title=f"[bold]Quality Distribution[/]  (threshold: {threshold:.1f}  |  approved: {approved}/{total})")
    t.add_column("Bucket", style="bold cyan", width=18)
    t.add_column("Count", justify="right", width=7)
    t.add_column("Share", width=8)
    t.add_column("", width=24)
    for label, count in buckets.items():
        pct = count / max(total, 1) * 100
        bar = "█" * int(pct / 4)
        style = "green" if count and label in ("Good (0.5–0.8)", "Excellent (≥0.8)") else "dim"
        t.add_row(label, str(count), f"{pct:.0f}%", f"[{style}]{bar}[/]")
    console.print(t)


def prompt_preview_panel(system_prompt: str, model: str) -> None:
    """Show the LLM system prompt and model that will be used for generation."""
    preview = system_prompt[:300] + ("…" if len(system_prompt) > 300 else "")
    t = Table.grid(padding=(0, 1))
    t.add_column(style="bold cyan", min_width=8)
    t.add_column()
    t.add_row("Model", f"[bold]{model}[/]")
    t.add_row("Prompt", f"[dim]{preview}[/]")
    console.print(Panel(t, title="[bold]Generation Prompt Preview[/]", border_style="cyan"))


def llm_answer_panel(provider: str, model: str, question: str, answer: str,
                      prompt_tokens: int, completion_tokens: int, cost_usd: float) -> None:
    """Show a test question/answer exchange with a configured model."""
    t = Table.grid(padding=(0, 1))
    t.add_column(style="bold cyan", min_width=10)
    t.add_column()
    t.add_row("Provider", provider)
    t.add_row("Model", f"[bold]{model}[/]")
    t.add_row("Question", question)
    t.add_row("Answer", answer.strip() or "[dim](empty response)[/]")
    t.add_row("Usage", f"[dim]{prompt_tokens}+{completion_tokens} tokens · ${cost_usd:.5f}[/]")
    console.print(Panel(t, title="[bold green]✓ LLM Test[/]", border_style="green"))


def view_samples(rows: list[dict], title: str = "Samples", max_rows: int = 30) -> None:
    """Table of synthetic samples."""
    t = Table(box=box.SIMPLE_HEAD, title=f"{title} ({len(rows)} total)")
    t.add_column("#", style="dim", width=5)
    t.add_column("Format", width=12)
    t.add_column("Score", width=6, justify="right")
    t.add_column("OK", width=4)
    t.add_column("Preview (first turn)")
    for i, r in enumerate(rows[:max_rows]):
        ok = "[green]✓[/]" if r.get("approved") else "[dim]—[/]"
        score = f"{r.get('quality_score', 0.0):.1f}" if r.get("quality_score") else "[dim]—[/]"
        msgs = r.get("messages", [])
        first_content = ""
        if msgs:
            first_content = (msgs[0].get("content", "") or "")[:100].replace("\n", " ")
        t.add_row(str(i + 1), r.get("format", ""), score, ok,
                  f"[dim]{first_content}[/]")
    if len(rows) > max_rows:
        t.add_row("…", "", "", "", f"[dim]+ {len(rows) - max_rows} more[/]")
    console.print(t)
