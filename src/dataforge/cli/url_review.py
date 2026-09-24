"""Interactive URL review step: a keyboard-driven list with filter, language and inspect.

Arrow keys move through the list, Space ticks or unticks a page, Left and Right
turn pages, ``l`` switches between the site's languages, ``/`` filters as you
type. The typed commands of earlier versions (``x 3-8``, ``f blog``, ...) are
still available after ``:``.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import questionary
from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    ConditionalContainer,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style as PTStyle
from questionary import Style as QStyle

from dataforge.cli.recipe import url_locale
from dataforge.collectors import filter_urls

from . import ui

_PAGE_SIZE = 30
# Lines the screen uses besides the URL rows: title, rule, column header,
# pager, status, two hint lines, input line and some slack.
_CHROME_LINES = 10

_HINTS = (
    "↑↓ move   space tick/untick   ←→ page   a tick/untick all shown   "
    "l language   / filter\n"
    "i details   : type a command   ? help   enter done   esc back"
)

_HELP_FULL = """\
Keys
  ↑ ↓ / j k            Move up and down
  space                Tick or untick the page under the cursor
  ← → / PgUp PgDn      Previous / next page of the list
  Home / End           First / last URL
  a                    Tick all shown URLs (untick them if all are ticked)
  l                    Show one language at a time (cycles through them)
  /                    Filter as you type (substring, /path/* glob, re:<regex>)
  i                    Details of the URL under the cursor
  enter                Done: scrape the ticked pages
  esc / q              Back to the main menu

Commands (press : first)
  x 3 / x 3-8          Untick row 3 / rows 3 to 8
  + 3 / + 3-8          Tick them again
  all / none           Tick / untick every shown URL
  f <pattern> / f      Filter / clear the filter
  <number>             Go to that page
  done / back          Same as enter / esc"""

_PT_STYLE = PTStyle.from_dict({
    "title":    "bold ansicyan",
    "subtitle": "ansibrightblack",
    "rule":     "ansicyan",
    "header":   "bold",
    "rownum":   "ansibrightblack",
    "tick":     "ansigreen bold",
    "untick":   "ansibrightblack",
    "cursor":   "reverse",
    "notice":   "ansiyellow",
    "message":  "ansicyan",
    "pager":    "ansicyan",
    "pager.off": "ansibrightblack",
    "hint":     "ansibrightblack",
    "detail":   "",
    "prompt":   "bold ansicyan",
})

_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")

_LANG_NAMES = {
    "ar": "Arabic", "bn": "Bengali", "de": "German", "el": "Greek", "en": "English",
    "es": "Spanish", "fa": "Farsi", "fr": "French", "ht": "Haitian Creole",
    "hi": "Hindi", "id": "Indonesian", "it": "Italian", "ja": "Japanese",
    "ko": "Korean", "nl": "Dutch", "pl": "Polish", "pt": "Portuguese",
    "ru": "Russian", "so": "Somali", "sw": "Swahili", "tl": "Tagalog",
    "th": "Thai", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu",
    "vi": "Vietnamese", "zh": "Chinese",
}


# ── Languages ─────────────────────────────────────────────────────────────────

def url_language(url: str) -> str:
    """The language code a URL is marked with, or "" when it has none.

    Looks at the first path segment (/es/..., /pt-br/...) and then at a
    lang, language or locale query parameter.
    """
    locale = url_locale(url)
    if locale:
        return locale
    query = parse_qs(urlparse(url).query)
    for key in ("lang", "language", "locale"):
        if query.get(key):
            return query[key][0].lower()[:5]
    return ""


def language_groups(urls: list[str]) -> dict[str, int]:
    """{language code: count} over the URLs marked with a language."""
    counts: dict[str, int] = {}
    for url in urls:
        lang = url_language(url)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return counts


def language_label(code: str) -> str:
    if not code:
        return "No language in the URL (usually the site's main language)"
    name = _LANG_NAMES.get(code.split("-")[0])
    return f"{name} ({code})" if name else code


def _languages_by_count(urls: list[str]) -> list[tuple[str, int]]:
    """Every language present, "" (no marker) included, most pages first."""
    counts: dict[str, int] = {}
    for url in urls:
        lang = url_language(url)
        counts[lang] = counts.get(lang, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _label(url: str, max_len: int = 72) -> str:
    parsed = urlparse(url)
    label = f"{parsed.netloc}{parsed.path or '/'}"
    if parsed.query:
        label += f"?{parsed.query}"
    if len(label) > max_len:
        label = label[: max(8, max_len - 1)] + "…"
    return label


def _page_count(total: int, page_size: int = _PAGE_SIZE) -> int:
    return max(1, (total + page_size - 1) // page_size)


class _URLReviewer:
    """State of one review: the full list, the shown subset, the ticks and the cursor."""

    def __init__(self, urls: list[str]) -> None:
        self._all: list[str] = list(urls)       # full discovered set (never mutated)
        self._view: list[str] = list(urls)      # current filtered view
        self._selected: set[str] = set(urls)    # selected URLs (mutable)
        self._page: int = 0                      # 0-indexed current page
        self._filter: str = ""                   # active filter pattern
        self._lang: str | None = None            # language shown; None = all
        self._cursor: int = 0                    # index into self._view
        self._page_size: int = _PAGE_SIZE
        self._detail: str = ""                   # extra block for the next render (help, inspect)
        self._languages = [code for code, _ in _languages_by_count(self._all)]

    # ── View ──────────────────────────────────────────────────────────────────

    def _page_slice(self) -> list[str]:
        start = self._page * self._page_size
        return self._view[start : start + self._page_size]

    def _recompute_view(self, pattern: str) -> list[str]:
        view = self._all
        if self._lang is not None:
            view = [u for u in view if url_language(u) == self._lang]
        if pattern:
            view = filter_urls(view, pattern, base_domain=None)
        return view

    def _set_view(self, view: list[str]) -> None:
        self._view = view
        self._page = 0
        self._cursor = 0

    def set_page_size(self, size: int) -> None:
        """Fit the page to the terminal, keeping the cursor on screen."""
        size = max(3, size)
        if size != self._page_size:
            self._page_size = size
            self._page = self._cursor // size

    def move(self, delta: int) -> None:
        if not self._view:
            return
        self._cursor = max(0, min(len(self._view) - 1, self._cursor + delta))
        self._page = self._cursor // self._page_size

    def turn_page(self, delta: int) -> str:
        total = _page_count(len(self._view), self._page_size)
        target = self._page + delta
        if not 0 <= target < total:
            return "Already on the last page." if delta > 0 else "Already on the first page."
        self._page = target
        self._cursor = min(target * self._page_size, max(0, len(self._view) - 1))
        return ""

    def current(self) -> str | None:
        return self._view[self._cursor] if self._view else None

    def toggle_current(self) -> None:
        url = self.current()
        if url is None:
            return
        if url in self._selected:
            self._selected.discard(url)
        else:
            self._selected.add(url)

    def toggle_all_shown(self) -> str:
        shown = set(self._view)
        if shown <= self._selected:
            self._selected -= shown
            return f"Unticked all {len(shown)} shown URLs."
        self._selected |= shown
        return f"Ticked all {len(shown)} shown URLs."

    def cycle_language(self) -> str:
        if len(self._languages) < 2:
            return "All URLs are in one language."
        order: list[str | None] = [None, *self._languages]
        self._lang = order[(order.index(self._lang) + 1) % len(order)]
        self._set_view(self._recompute_view(self._filter))
        if self._lang is None:
            return "Showing all languages."
        return f"Showing {language_label(self._lang)}: {len(self._view)} URLs. Press a to tick/untick them all."

    def keep_language(self, lang: str) -> None:
        """Tick only the URLs in *lang*; the others stay listed, unticked."""
        self._selected = {u for u in self._all if url_language(u) == lang}

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

    def _do_filter(self, pattern: str, live: bool = False) -> str:
        """Apply a filter. A typed command that matches nothing is not applied;
        while typing (``live``) the empty result is shown so the user sees why."""
        if not pattern:
            self._filter = ""
            self._set_view(self._recompute_view(""))
            return f"Filter cleared, showing {len(self._view)} URLs."

        filtered = self._recompute_view(pattern)
        if not filtered and not live:
            return f"Pattern '{pattern}' matched 0 URLs, filter not applied."

        self._filter = pattern
        self._set_view(filtered)
        if not filtered:
            return f"No URLs match '{pattern}'."
        return f"Filter matched {len(filtered)} / {len(self._all)} URLs."

    def _do_toggle(self, spec: str, select: bool) -> str:
        indices = self._resolve_rows(spec)
        if not indices:
            return f"Invalid row spec: '{spec}'"
        changed = 0
        for idx in indices:
            if 0 <= idx < len(self._view):
                url = self._view[idx]
                if select:
                    self._selected.add(url)
                else:
                    self._selected.discard(url)
                changed += 1
        action = "Selected" if select else "Deselected"
        return f"{action} {changed} URL(s)."

    def _inspect(self, index: int) -> None:
        url = self._view[index]
        parsed = urlparse(url)
        # Shown on the next redraw, above the key hints.
        self._detail = (
            f"URL #{index + 1}\n"
            f"  Full URL:  {url}\n"
            f"  Host:      {parsed.netloc}\n"
            f"  Path:      {parsed.path or '/'}\n"
            f"  Query:     {parsed.query or '(none)'}\n"
            f"  Language:  {language_label(url_language(url))}\n"
            f"  Ticked:    {'yes' if url in self._selected else 'no'}"
        )

    def _do_inspect(self, spec: str) -> str:
        try:
            n = int(spec.strip())
        except ValueError:
            return f"Invalid row number: '{spec}'"
        if not 1 <= n <= len(self._view):
            return f"No row {n}."
        self._inspect(n - 1)
        return ""

    def handle(self, raw: str) -> tuple[str, bool]:
        """Process one typed command. Returns (message, done).

        done=True means the user typed 'done'; done=None means 'back'.
        """
        cmd = raw.strip().lower()
        if not cmd:
            return "", False

        if cmd in ("n", "next"):
            return self.turn_page(1), False
        if cmd in ("p", "prev"):
            return self.turn_page(-1), False

        try:
            pg = int(cmd)
            total = _page_count(len(self._view), self._page_size)
            if 1 <= pg <= total:
                self.turn_page(pg - 1 - self._page)
                return "", False
            return f"Page must be between 1 and {total}.", False
        except ValueError:
            pass

        if cmd == "f" or cmd.startswith("f "):
            pattern = raw.strip()[1:].strip()
            return self._do_filter(pattern), False

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

        if cmd.startswith("i "):
            return self._do_inspect(cmd[2:]), False

        if cmd in ("done", "d"):
            return "", True
        if cmd in ("q", "quit", "back", "b"):
            return "", None  # type: ignore[return-value]

        if cmd in ("?", "h", "help"):
            self._detail = _HELP_FULL
            return "", False

        return f"Unknown command '{raw.strip()}', press ? for help.", False

    def selected_urls(self) -> list[str]:
        """Return selected URLs in original discovery order."""
        order = {u: i for i, u in enumerate(self._all)}
        return sorted(self._selected, key=lambda u: order.get(u, 0))


# ── Screen ────────────────────────────────────────────────────────────────────

class _Screen:
    """The full-screen review list. ``run`` returns "done" or "back"."""

    def __init__(self, reviewer: _URLReviewer, notice: str = "", message: str = "") -> None:
        self.r = reviewer
        self.notice = notice
        self.message = message
        self.mode: str | None = None       # None, "filter" or "command"
        self.input = Buffer(multiline=False, on_text_changed=self._on_input_changed)
        self.app = self._build()

    # Input line (filter / command)

    def _on_input_changed(self, _buf: Buffer) -> None:
        if self.mode == "filter":
            self.message = self.r._do_filter(self.input.text.strip(), live=True)

    def _open_input(self, mode: str) -> None:
        self.mode = mode
        self.input.text = self.r._filter if mode == "filter" else ""
        self.input.cursor_position = len(self.input.text)
        self.app.layout.focus(self.input)

    def _close_input(self) -> None:
        self.mode = None
        self.app.layout.focus(self.list_window)

    # Rendering

    def _text(self):
        r = self.r
        try:
            size = get_app().output.get_size()
            rows, cols = size.rows, size.columns
        except Exception:
            rows, cols = 40, 100
        notice_lines = self.notice.count("\n") + 1 if self.notice else 0
        detail_lines = r._detail.count("\n") + 1 if r._detail else 0
        r.set_page_size(rows - _CHROME_LINES - notice_lines - detail_lines)

        total_pages = _page_count(len(r._view), r._page_size)
        shown = set(r._view)
        n_ticked = len(r._selected & shown)
        parts: list[tuple[str, str]] = []
        add = parts.append

        sub = f"page {r._page + 1}/{total_pages}  ·  {n_ticked}/{len(r._view)} ticked"
        if len(r._view) != len(r._all):
            sub += f"  ·  {len(r._selected)}/{len(r._all)} ticked overall"
        if r._lang is not None:
            sub += f"  ·  language: {language_label(r._lang) if r._lang else 'no marker'}"
        if r._filter:
            sub += f"  ·  filter: {r._filter}"
        add(("class:title", "DataForge  Choose pages to scrape"))
        add(("class:subtitle", f"  {sub}\n"))
        add(("class:rule", "─" * max(10, cols - 1) + "\n"))
        if self.notice:
            add(("class:notice", self.notice + "\n"))

        num_w = len(str(len(r._view))) + 1
        add(("class:header", f"  {'#':>{num_w}}  ✓  URL\n"))
        start = r._page * r._page_size
        page_urls = r._page_slice()
        if not page_urls:
            add(("class:untick", "  (no URLs to show: change the filter with / or press l)\n"))
        label_w = max(20, cols - num_w - 10)
        for i, url in enumerate(page_urls):
            idx = start + i
            ticked = url in r._selected
            line_style = "class:cursor" if idx == r._cursor else ""
            pointer = "›" if idx == r._cursor else " "
            add((line_style, f"{pointer} "))
            add((f"{line_style} class:rownum", f"{idx + 1:>{num_w}}  "))
            add((f"{line_style} {'class:tick' if ticked else 'class:untick'}", "✓" if ticked else "·"))
            add((line_style, f"  {_label(url, label_w)}"))
            add(("", "\n"))

        # Pager: the arrows show which way there are more URLs.
        has_prev, has_next = r._page > 0, r._page < total_pages - 1
        add(("class:pager" if has_prev else "class:pager.off", "  ◀ ← prev"))
        add(("class:subtitle", f"   page {r._page + 1} of {total_pages}   "))
        add(("class:pager" if has_next else "class:pager.off", "next → ▶\n"))

        if r._detail:
            add(("class:detail", r._detail + "\n"))
        if self.message:
            add(("class:message", self.message + "\n"))
        add(("class:rule", "─" * max(10, cols - 1) + "\n"))
        add(("class:hint", _HINTS))
        return parts

    def _prompt_text(self):
        label = "filter> " if self.mode == "filter" else "command> "
        return [("class:prompt", f"  {label}")]

    # Keys

    def _build(self) -> Application:
        kb = KeyBindings()
        browsing = Condition(lambda: self.mode is None)
        typing = Condition(lambda: self.mode is not None)

        def act(fn):
            """Run a list action: clear the one-shot detail and message first."""
            def handler(event) -> None:
                self.r._detail = ""
                self.message = ""
                fn(event)
            return handler

        @kb.add("up", filter=browsing)
        @kb.add("k", filter=browsing)
        @act
        def _(event) -> None:
            self.r.move(-1)

        @kb.add("down", filter=browsing)
        @kb.add("j", filter=browsing)
        @act
        def _(event) -> None:
            self.r.move(1)

        @kb.add("space", filter=browsing)
        @act
        def _(event) -> None:
            self.r.toggle_current()
            self.r.move(1)

        @kb.add("right", filter=browsing)
        @kb.add("pagedown", filter=browsing)
        @act
        def _(event) -> None:
            self.message = self.r.turn_page(1)

        @kb.add("left", filter=browsing)
        @kb.add("pageup", filter=browsing)
        @act
        def _(event) -> None:
            self.message = self.r.turn_page(-1)

        @kb.add("home", filter=browsing)
        @act
        def _(event) -> None:
            self.r.move(-len(self.r._view))

        @kb.add("end", filter=browsing)
        @act
        def _(event) -> None:
            self.r.move(len(self.r._view))

        @kb.add("a", filter=browsing)
        @act
        def _(event) -> None:
            self.message = self.r.toggle_all_shown()

        @kb.add("l", filter=browsing)
        @act
        def _(event) -> None:
            self.message = self.r.cycle_language()

        @kb.add("i", filter=browsing)
        @act
        def _(event) -> None:
            if self.r._view:
                self.r._inspect(self.r._cursor)

        @kb.add("?", filter=browsing)
        @act
        def _(event) -> None:
            self.r._detail = _HELP_FULL

        @kb.add("/", filter=browsing)
        @act
        def _(event) -> None:
            self._open_input("filter")

        @kb.add(":", filter=browsing)
        @act
        def _(event) -> None:
            self._open_input("command")

        @kb.add("enter", filter=browsing)
        def _(event) -> None:
            event.app.exit(result="done")

        @kb.add("escape", filter=browsing)
        @kb.add("q", filter=browsing)
        @kb.add("c-c")
        def _(event) -> None:
            event.app.exit(result="back")

        @kb.add("enter", filter=typing)
        def _(event) -> None:
            text = self.input.text
            mode = self.mode
            self._close_input()
            if mode == "command":
                self.r._detail = ""
                self.message, done = self.r.handle(text)
                if done is True:
                    event.app.exit(result="done")
                elif done is None:
                    event.app.exit(result="back")

        @kb.add("escape", filter=typing)
        def _(event) -> None:
            if self.mode == "filter":
                self.message = self.r._do_filter("")
            self._close_input()

        self.list_window = Window(
            FormattedTextControl(self._text, focusable=True, show_cursor=False),
            wrap_lines=False,
        )
        input_row = ConditionalContainer(
            VSplit([
                Window(FormattedTextControl(self._prompt_text), dont_extend_width=True),
                Window(BufferControl(buffer=self.input), height=1),
            ]),
            filter=typing,
        )
        return Application(
            layout=Layout(HSplit([self.list_window, input_row]), focused_element=self.list_window),
            key_bindings=kb,
            style=_PT_STYLE,
            full_screen=True,
            mouse_support=False,
        )

    async def run(self) -> str:
        return await self.app.run_async() or "back"


# ── Entry point ───────────────────────────────────────────────────────────────

async def _ask_language(reviewer: _URLReviewer) -> str | None:
    """On a site in several languages, ask which one to tick.

    Returns a notice for the review screen, or None if the user backed out.
    """
    groups = _languages_by_count(reviewer._all)
    if len(groups) < 2:
        return ""
    choices = [
        questionary.Choice(f"{language_label(code)}: {n} pages", value=code)
        for code, n in groups
    ]
    choices.append(questionary.Choice(f"All languages: {len(reviewer._all)} pages", value="__all__"))
    picked = await questionary.select(
        "This site has pages in several languages. Which should be ticked?",
        choices=choices,
        instruction="(the others stay in the list, unticked)",
        style=_QSTYLE,
    ).ask_async()
    if picked is None:
        return None
    if picked == "__all__":
        return "Several languages found. Press l to look at one language at a time."
    reviewer.keep_language(picked)
    return (f"Only {language_label(picked)} pages are ticked. "
            "Press l to see the other languages, a to tick all shown.")


_QSTYLE = QStyle([
    ("qmark", "fg:cyan bold"), ("question", "bold"),
    ("answer", "fg:cyan bold"), ("pointer", "fg:cyan bold"),
])


async def run_url_review(urls: list[str], notice: str = "", ask_language: bool = True) -> list[str]:
    """Let the user choose which discovered URLs to scrape.

    ``notice`` is shown at the top of the list on every redraw. With
    ``ask_language`` off (browsing only), a multilingual site is not asked
    about. Returns the ticked URLs in discovery order, or [] if the user
    backed out.
    """
    if not urls:
        return []

    reviewer = _URLReviewer(urls)
    lang_notice = await _ask_language(reviewer) if ask_language else ""
    if lang_notice is None:
        ui.info("Returning to menu.")
        return []
    notice = "\n".join(n for n in (notice, lang_notice) if n)

    message = ""
    while True:
        result = await _Screen(reviewer, notice, message).run()
        if result != "done":
            ui.info("Returning to menu.")
            return []

        selected = reviewer.selected_urls()
        if not selected:
            message = "No URLs ticked yet. Tick some with space (or a for all shown), then press enter."
            continue

        picked, total = len(selected), len(urls)
        confirmed = await questionary.confirm(
            f"Scrape {picked} of {total} page{'s' if total != 1 else ''}?",
            default=True,
            style=_QSTYLE,
        ).ask_async()
        if confirmed:
            return selected
        message = "Selection kept. Keep editing, then press enter."
