"""Interactive URL review step — filter, select, and confirm discovered URLs."""
from __future__ import annotations

from urllib.parse import urlparse

import questionary
from questionary import Style

from dataforge.collectors import filter_urls

from . import ui

_STYLE = Style([
    ("qmark",      "fg:cyan bold"),
    ("question",   "bold"),
    ("answer",     "fg:cyan bold"),
    ("pointer",    "fg:cyan bold"),
    ("highlighted","fg:cyan bold"),
    ("selected",   "fg:green"),
    ("separator",  "fg:cyan"),
    ("instruction","fg:grey"),
])

# Warn the user before rendering huge checkbox lists.
_LARGE_SET_THRESHOLD = 150


def _label(url: str) -> str:
    """Shorten a URL for display while keeping it recognisable."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    # Truncate very long paths in the middle
    if len(path) > 70:
        path = path[:34] + "…" + path[-33:]
    return f"{parsed.netloc}{path}"


async def _ask_filter(urls: list[str]) -> list[str]:
    """Show a filter prompt and return the narrowed URL list."""
    total = len(urls)
    pattern = await questionary.text(
        f"Filter {total} URLs before review  (Enter to skip):",
        instruction="  substring · /path/*  glob · re:<regex>",
        default="",
        style=_STYLE,
    ).ask_async()

    if not pattern:
        return urls

    filtered = filter_urls(urls, pattern, base_domain=None)
    if not filtered:
        ui.warn(f"Pattern '{pattern}' matched 0 URLs — keeping all {total}.")
        return urls

    ui.info(f"Filter matched [bold]{len(filtered)}[/] / {total} URLs.")
    return filtered


async def _ask_checkbox(urls: list[str]) -> list[str] | None:
    """Render a questionary checkbox list. Returns selected URLs or None on abort."""
    choices = [
        questionary.Choice(title=_label(u), value=u, checked=True)
        for u in urls
    ]
    ui.info(
        "[dim]  [space][/dim] toggle  "
        "[dim][a][/dim] all  "
        "[dim][n][/dim] none  "
        "[dim][enter][/dim] confirm  "
        "[dim][ctrl-c][/dim] re-filter"
    )
    try:
        selected: list[str] | None = await questionary.checkbox(
            f"Select URLs to process  ({len(urls)} shown):",
            choices=choices,
            instruction=" (space=toggle, a=all, n=none, enter=confirm)",
            style=_STYLE,
        ).ask_async()
    except KeyboardInterrupt:
        return None  # caller interprets None as "go back to filter"
    return selected


async def run_url_review(urls: list[str]) -> list[str]:
    """Interactive URL review: filter → select → confirm.

    Returns the user-approved subset (may be empty if user deselects all and
    confirms, or the full list if no changes are made).
    """
    if not urls:
        return []

    current = list(urls)

    while True:
        # ── Filter step ───────────────────────────────────────────────────────
        if len(current) > _LARGE_SET_THRESHOLD:
            ui.warn(
                f"{len(current)} URLs discovered — consider applying a filter "
                f"(e.g. [bold]/blog/*[/]) to narrow the list before review."
            )

        current = await _ask_filter(current)

        # ── Checkbox review ───────────────────────────────────────────────────
        selected = await _ask_checkbox(current)

        if selected is None:
            # Ctrl-C in checkbox → restart from filter step with original list
            ui.info("Returning to filter step…")
            current = list(urls)
            continue

        # ── Confirm ───────────────────────────────────────────────────────────
        total = len(urls)
        picked = len(selected)

        if picked == 0:
            ui.warn("No URLs selected.")
            go_back = await questionary.confirm(
                "No URLs selected — go back to review?",
                default=True,
                style=_STYLE,
            ).ask_async()
            if go_back:
                current = list(urls)
                continue
            return []

        confirmed = await questionary.confirm(
            f"Proceed with {picked} / {total} URL{'s' if picked != 1 else ''}?",
            default=True,
            style=_STYLE,
        ).ask_async()

        if confirmed:
            return selected

        # User said no → loop back to filter
        current = list(urls)
