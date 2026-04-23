"""Interactive URL review step — paginated browser with filter, select, and inspect."""
from __future__ import annotations

import re
from urllib.parse import urlparse

import questionary
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style as PTStyle
from questionary import Style as QStyle
from rich import box
from rich.table import Table

from dataforge.collectors import filter_urls

from . import ui

_PAGE_SIZE = 30

_HELP_TEXT = (
    "[dim]  n[/dim]=next  [dim]p[/dim]=prev  "
    "[dim]f <pat>[/dim]=filter  "
    "[dim]x <#>[/dim]=deselect  [dim]+ <#>[/dim]=select  "
    "[dim]x <a-b>[/dim]=range  "
    "[dim]all[/dim]/[dim]none[/dim]  "
    "[dim]i <#>[/dim]=inspect  "
    "[dim]done[/dim]=proceed  [dim]?[/dim]=help"
)

_HELP_FULL = """\
URL Review — full command reference
────────────────────────────────────────────────────────────────────────────────
  n / next             Go to next page
  p / prev             Go to previous page
  <number>             Go to page number
  f <pattern>          Filter URLs  (substring, /path/* glob, re:<regex>)
  f                    Clear current filter — show all URLs
  x <#>                Deselect URL by row number on current page
  x <#>-<#>           Deselect a range of rows (e.g. x 3-8)
  + <#>                Select (re-include) URL by row number
  + <#>-<#>           Select a range of rows
  all                  Select all URLs in the current filtered view
  none                 Deselect all URLs in the current filtered view
  i <#>                Inspect URL — show full details for that row
  done                 Confirm selection and proceed to collection
  q / quit             Cancel — go back to the main menu
  ?                    Show this help
────────────────────────────────────────────────────────────────────────────────"""

_PT_STYLE = PTStyle.from_dict({
    "prompt":          "bold cyan",
    "auto-suggestion": "fg:ansibrightblack italic",
})

_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


def _label(url: str, max_len: int = 72) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    label = f"{parsed.netloc}{path}"
    if len(label) > max_len:
        label = label[: max_len - 4] + "…"
    if parsed.query:
        label += "[dim]?…[/dim]"
    return label


def _page_count(total: int) -> int:
    return max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)


class _URLReviewer:
    """Stateful paginated URL review session."""

    def __init__(self, urls: list[str]) -> None:
        self._all: list[str] = list(urls)       # full discovered set (never mutated)
        self._view: list[str] = list(urls)      # current filtered view
        self._selected: set[str] = set(urls)    # selected URLs (mutable)
        self._page: int = 0                      # 0-indexed current page
        self._filter: str = ""                   # active filter pattern

    # ── Display ───────────────────────────────────────────────────────────────

    def _page_slice(self) -> list[str]:
        start = self._page * _PAGE_SIZE
        return self._view[start : start + _PAGE_SIZE]

    def _render(self) -> None:
        page_urls = self._page_slice()
        total_pages = _page_count(len(self._view))
        n_selected = len(self._selected & set(self._view))

        # Header line
        filter_hint = f"  [dim]filter: {self._filter}[/]" if self._filter else ""
        ui.console.print(
            f"\n[bold cyan]URL Review[/]"
            f"  Page [bold]{self._page + 1}[/] / {total_pages}"
            f"  [green]{n_selected}[/] / {len(self._view)} selected"
            f"{filter_hint}"
        )

        table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("✓", width=3, justify="center")
        table.add_column("URL", no_wrap=True)

        start = self._page * _PAGE_SIZE
        for i, url in enumerate(page_urls, start=1):
            row_num = start + i
            check = "[green]✓[/]" if url in self._selected else "[dim]·[/]"
            table.add_row(str(row_num), check, _label(url))

        ui.console.print(table)
        ui.info(_HELP_TEXT)

    # ── Command handlers ──────────────────────────────────────────────────────

    def _resolve_rows(self, spec: str) -> list[int]:
        """Parse a row spec ('3', '3-8') into 0-based indices within self._view."""
        m = _RANGE_RE.match(spec.strip())
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            return list(range(a - 1, b))
        try:
            n = int(spec.strip())
            return [n - 1]
        except ValueError:
            return []

    def _do_filter(self, pattern: str) -> str:
        if not pattern:
            self._view = list(self._all)
            self._filter = ""
            self._page = 0
            return f"Filter cleared — showing all {len(self._all)} URLs."

        filtered = filter_urls(self._all, pattern, base_domain=None)
        if not filtered:
            return f"Pattern '{pattern}' matched 0 URLs — filter not applied."

        self._view = filtered
        self._filter = pattern
        self._page = 0
        return f"Filter matched {len(filtered)} / {len(self._all)} URLs."

    def _do_toggle(self, spec: str, select: bool) -> str:
        indices = self._resolve_rows(spec)
        if not indices:
            return f"Invalid row spec: '{spec}'"
        changed = 0
        start = self._page * _PAGE_SIZE
        page_urls = self._page_slice()
        for idx in indices:
            local = idx - start
            if 0 <= local < len(page_urls):
                url = page_urls[local]
                if select:
                    self._selected.add(url)
                else:
                    self._selected.discard(url)
                changed += 1
        action = "Selected" if select else "Deselected"
        return f"{action} {changed} URL(s)."

    def _do_inspect(self, spec: str) -> str:
        try:
            n = int(spec.strip())
        except ValueError:
            return f"Invalid row number: '{spec}'"
        start = self._page * _PAGE_SIZE
        local = n - 1 - start
        page_urls = self._page_slice()
        if not (0 <= local < len(page_urls)):
            return f"Row {n} not on current page."
        url = page_urls[local]
        parsed = urlparse(url)
        selected = "Yes" if url in self._selected else "No"
        ui.console.print(
            f"\n[bold]URL #{n}[/]\n"
            f"  Full URL:  [cyan]{url}[/]\n"
            f"  Scheme:    {parsed.scheme}\n"
            f"  Host:      {parsed.netloc}\n"
            f"  Path:      {parsed.path or '/'}\n"
            f"  Query:     {parsed.query or '(none)'}\n"
            f"  Selected:  {'[green]Yes[/]' if url in self._selected else '[dim]No[/]'}"
        )
        return ""

    def handle(self, raw: str) -> tuple[str, bool]:
        """Process one command. Returns (message, done).

        done=True means the user typed 'done' and we should exit the loop.
        done=None means the user typed 'q' — returned as (msg, None).
        """
        cmd = raw.strip().lower()
        if not cmd:
            return "", False

        # Navigation
        if cmd in ("n", "next"):
            total = _page_count(len(self._view))
            if self._page < total - 1:
                self._page += 1
                return "", False
            return "Already on the last page.", False

        if cmd in ("p", "prev"):
            if self._page > 0:
                self._page -= 1
                return "", False
            return "Already on the first page.", False

        try:
            pg = int(cmd)
            total = _page_count(len(self._view))
            if 1 <= pg <= total:
                self._page = pg - 1
                return "", False
            return f"Page must be between 1 and {total}.", False
        except ValueError:
            pass

        # Filter
        if cmd == "f" or cmd.startswith("f "):
            pattern = raw.strip()[1:].strip()
            return self._do_filter(pattern), False

        # Select / deselect
        if cmd.startswith("x "):
            return self._do_toggle(cmd[2:], select=False), False
        if cmd.startswith("+ "):
            return self._do_toggle(cmd[2:], select=True), False

        if cmd == "all":
            self._selected.update(self._view)
            return f"Selected all {len(self._view)} URLs.", False
        if cmd == "none":
            self._selected -= set(self._view)
            return f"Deselected all {len(self._view)} URLs.", False

        # Inspect
        if cmd.startswith("i "):
            return self._do_inspect(cmd[2:]), False

        # Done / quit
        if cmd in ("done", "d"):
            return "", True
        if cmd in ("q", "quit"):
            return "", None  # type: ignore[return-value]

        if cmd == "?":
            ui.console.print(_HELP_FULL)
            return "", False

        return f"Unknown command '{raw.strip()}' — type ? for help.", False

    def selected_urls(self) -> list[str]:
        """Return selected URLs in original discovery order."""
        order = {u: i for i, u in enumerate(self._all)}
        return sorted(self._selected, key=lambda u: order.get(u, 0))


async def run_url_review(urls: list[str]) -> list[str]:
    """Interactive URL review: paginated browser with filter, select, and inspect.

    Returns the user-approved subset in original discovery order.
    """
    if not urls:
        return []

    reviewer = _URLReviewer(urls)

    session: PromptSession[str] = PromptSession(style=_PT_STYLE)

    while True:
        reviewer._render()

        try:
            raw = await session.prompt_async("  review> ")
        except (KeyboardInterrupt, EOFError):
            # Ctrl-C / Ctrl-D → treat as cancel
            ui.info("Review cancelled.")
            return []

        msg, done = reviewer.handle(raw)

        if done is None:
            ui.info("Returning to menu.")
            return []

        if msg:
            ui.info(msg)

        if done:
            selected = reviewer.selected_urls()
            total = len(urls)
            picked = len(selected)

            if picked == 0:
                ui.warn("No URLs selected — returning to review.")
                continue

            ui.info(
                f"[bold]{picked}[/] / {total} URL{'s' if picked != 1 else ''} selected."
            )

            _QSTYLE = QStyle([
                ("qmark", "fg:cyan bold"), ("question", "bold"),
                ("answer", "fg:cyan bold"), ("pointer", "fg:cyan bold"),
            ])
            confirmed = await questionary.confirm(
                f"Proceed with {picked} URL{'s' if picked != 1 else ''}?",
                default=True,
                style=_QSTYLE,
            ).ask_async()

            if confirmed:
                return selected
            # User said no — keep browsing
