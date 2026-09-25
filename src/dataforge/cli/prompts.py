"""Questionary-based interactive prompts for the pipeline wizard."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import questionary
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style as PTStyle
from questionary import Style

_STYLE = Style([
    ("qmark",     "fg:cyan bold"),
    ("question",  "bold"),
    ("answer",    "fg:cyan bold"),
    ("pointer",   "fg:cyan bold"),
    ("highlighted","fg:cyan bold"),
    ("selected",  "fg:cyan"),
    ("separator", "fg:cyan"),
    ("instruction","fg:grey"),
])


def _q(**kw):
    return {**kw, "style": _STYLE}


# questionary gives a Choice with value=None its title as the value, so a
# "Back" entry needs a real value that the caller then turns into None.
_BACK = "__back__"


def _none_if_back(answer):
    return None if answer == _BACK else answer


# ── Ghost-text command prompt ─────────────────────────────────────────────────

class _SuggestFromChoices(AutoSuggest):
    """Suggest the first matching choice as inline ghost text."""

    def __init__(self, choices: list[str]) -> None:
        self._choices = choices

    def get_suggestion(self, buffer, document) -> Suggestion | None:
        text = document.text_before_cursor
        if not text:
            return None
        for choice in self._choices:
            if choice.lower().startswith(text.lower()) and choice.lower() != text.lower():
                return Suggestion(choice[len(text):])
        return None


_CMD_PT_STYLE = PTStyle.from_dict({
    "prompt":      "bold cyan",
    "completion-menu.completion.current": "bg:ansicyan ansiwhite bold",
    "completion-menu.completion":         "bg:ansiblue ansiwhite",
    "auto-suggestion":                    "fg:ansibrightblack italic",
})

_COMMAND_ALIASES: dict[str, str] = {
    "start": "new",
    "quit":  "exit",
    "q":     "exit",
}


async def ask_command(choices: list[str], aliases: dict[str, str] | None = None) -> str | None:
    """Typed command prompt with faded ghost-text autocomplete and Tab completion.

    As the user types, the first matching command appears as faded ghost text.
    Tab accepts the suggestion; Enter submits. Returns the canonical choice value
    or None on Ctrl-C / Ctrl-D.
    """
    all_aliases = {**_COMMAND_ALIASES, **(aliases or {})}
    # Include aliases in the completer so Tab works for them too
    all_words = list(choices) + list(all_aliases.keys())
    completer = WordCompleter(all_words, ignore_case=True, sentence=True)
    session: PromptSession[str] = PromptSession(
        completer=completer,
        auto_suggest=_SuggestFromChoices(all_words),
        style=_CMD_PT_STYLE,
        complete_while_typing=False,  # ghost text only; Tab opens dropdown
    )
    hint = "/".join(choices)
    while True:
        try:
            raw = await session.prompt_async(
                HTML(f"<prompt>dataforge</prompt> [<ansicyan>{hint}</ansicyan>]: "),
            )
        except (KeyboardInterrupt, EOFError):
            return None
        value = raw.strip().lower()
        if not value:
            continue
        if value in ("exit", "quit"):
            raise SystemExit(0)
        # Resolve alias
        value = all_aliases.get(value, value)
        if value in choices:
            return value
        # Fuzzy fallback: first choice that starts with input
        matches = [c for c in choices if c.startswith(value)]
        if len(matches) == 1:
            return matches[0]
        # Unknown — show inline error and re-prompt
        print(f"\033[33m  Unknown command '{raw.strip()}'. Valid: {', '.join(choices)}\033[0m")


# ── Input collection ───────────────────────────────────────────────────────────

SCRAPE_FOLDER = "Pages I scraped earlier"


async def ask_input_method(allow_scrape_folder: bool = False) -> str | None:
    choices = ["Single URL", "Multiple URLs", "Text file", "Sitemap URL"]
    if allow_scrape_folder:
        choices.append(questionary.Choice(
            SCRAPE_FOLDER, value=SCRAPE_FOLDER,
            description="Use a folder saved by 'Scrape pages (no AI)': "
                        "no fetching, goes straight to processing.",
        ))
    answer = await questionary.select(
        "How would you like to provide URLs?",
        choices=[*choices, questionary.Separator(),
                 questionary.Choice("(b) Back to menu", value=_BACK)],
        **_q(),
    ).ask_async()
    return _none_if_back(answer)


async def ask_scrape_dir(default: str = "") -> Path | None:
    """Folder written by a no-AI scrape (pages.jsonl or page_NNN.md files)."""
    def _ok(v: str) -> bool | str:
        d = Path(clean_input(v)).expanduser()
        if not d.is_dir():
            return f"Folder not found: {clean_input(v)}"
        if not ((d / "pages.jsonl").is_file() or any(d.glob("page_*.md"))):
            return "No pages.jsonl or page_NNN.md files in that folder"
        return True

    path = await questionary.path(
        "Scrape folder:",
        default=default,
        only_directories=True,
        instruction="(Enter for the latest; drag a folder here or paste its path)",
        validate=_ok,
        **_q(),
    ).ask_async()
    if path is None:
        return None
    return Path(clean_input(path)).expanduser()


async def ask_discovery_scope(n_urls: int) -> str | None:
    """How far to go from the URLs given: just them, their links, or the whole site."""
    one = n_urls == 1
    answer = await questionary.select(
        "Scrape just this page, or search for more?" if one
        else "Scrape just these pages, or search for more?",
        choices=[
            questionary.Choice(
                "Just this page" if one else "Just these pages", value="page",
                description="No discovery: go straight to scraping "
                            f"{'this URL' if one else 'these URLs'}.",
            ),
            questionary.Choice(
                "Deep link search: follow links from " + ("this page" if one else "these pages"),
                value="links",
                description="Crawl the pages they link to on the same site, "
                            "then pick which to keep.",
            ),
            questionary.Choice(
                "The whole site", value="site",
                description="Read the site's sitemap (or crawl it when there is none), "
                            "then pick which pages to keep.",
            ),
            questionary.Separator(),
            questionary.Choice("(b) Back", value=_BACK),
        ],
        **_q(),
    ).ask_async()
    return _none_if_back(answer)


async def ask_single_url() -> str | None:
    url = await questionary.text(
        "Enter URL:",
        instruction="(paste it; quotes are fine)",
        validate=lambda v: _valid_url(clean_input(v)) or "Enter a valid http(s) URL, e.g. https://example.com",
        **_q(),
    ).ask_async()
    if url is None:
        return None
    return clean_input(url)


def _multiline_url_keys():
    """Enter adds a line; Enter on an empty line finishes (as the prompt says).

    questionary's multiline text only finishes on Alt+Enter or Esc then Enter,
    so a plain Enter on a blank line looked like the prompt had hung.
    """
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("enter")
    def _(event) -> None:
        buf = event.current_buffer
        if buf.document.current_line.strip():
            buf.insert_text("\n")
        else:
            buf.validate_and_handle()

    return kb


def _has_url(text: str) -> bool | str:
    urls = _split_urls(text)
    if any(_valid_url(u) for u in urls):
        return True
    if urls:
        return f"'{urls[0]}' is not a valid http(s) URL"
    return "Enter at least one URL (or Ctrl+C to go back)"


async def ask_multiple_urls() -> list[str] | None:
    """Several URLs, one per line (or separated by spaces or commas).

    Returns every entry given; the caller drops and reports invalid ones.
    """
    raw = await questionary.text(
        "Enter URLs:",
        multiline=True,
        instruction="(one per line; press Enter on an empty line to finish, Ctrl+C to go back)",
        validate=_has_url,
        key_bindings=_multiline_url_keys(),
        **_q(),
    ).ask_async()
    if raw is None:
        return None
    return _split_urls(raw)


async def ask_file_path() -> Path | None:
    path = await questionary.path(
        "Path to URL file:",
        instruction="(drag the file here or paste its path; quotes are fine)",
        validate=lambda v: _path_exists(v) or f"File not found: {clean_input(v)}",
        **_q(),
    ).ask_async()
    if path is None:
        return None
    return Path(clean_input(path)).expanduser()


async def ask_goal() -> str | None:
    answer = await questionary.text(
        "Describe the goal of this dataset (e.g. 'customer support Q&A for a SaaS product'):",
        validate=lambda v: len(v.strip()) > 10 or "Please be more descriptive",
        **_q(),
    ).ask_async()
    return answer


async def ask_format() -> str | None:
    choice = await questionary.select(
        "Select dataset format:",
        choices=[
            questionary.Choice("Q&A pairs  (RAG / question-answering)", value="qa"),
            questionary.Choice("Instructions  (Alpaca / instruction-following)", value="instruction"),
            questionary.Choice("Conversations  (ChatML / chat fine-tuning)", value="conversation"),
            questionary.Choice("Custom  (provide your own system prompt)", value="custom"),
        ],
        **_q(),
    ).ask_async()
    return choice


async def ask_custom_system_prompt() -> str | None:
    answer = await questionary.text(
        "Enter your custom system prompt for the LLM generator:",
        multiline=True,
        **_q(),
    ).ask_async()
    return answer


async def ask_n_per_chunk() -> int | None:
    val = await questionary.text(
        "Samples to generate per content chunk:",
        default="3",
        validate=lambda v: v.isdigit() and 1 <= int(v) <= 10 or "Enter a number 1–10",
        **_q(),
    ).ask_async()
    if val is None:
        return None
    return int(val)


async def ask_ignore_robots() -> bool:
    answer = await questionary.confirm(
        "Ignore robots.txt restrictions? (only enable on sites you own or have permission to scrape)",
        default=False,
        **_q(),
    ).ask_async()
    return bool(answer)


async def ask_save_key_globally() -> bool:
    answer = await questionary.confirm(
        "Save API key globally (persists across all directories, no .env needed)?",
        default=True,
        **_q(),
    ).ask_async()
    return bool(answer)


async def ask_output_dir(default: str = "./output") -> str | None:
    answer = await questionary.text(
        "Output directory (where files and the database will be saved):",
        default=default,
        **_q(),
    ).ask_async()
    if answer is None:
        return None
    return clean_input(answer) or default


async def ask_session_name() -> str | None:
    from datetime import datetime
    default = datetime.now().strftime("dataset-%Y-%m-%d-%H%M")
    answer = await questionary.text(
        "Session name (for reference):",
        default=default,
        **_q(),
    ).ask_async()
    return answer


async def ask_review_action() -> str:
    """Ask user to review and confirm before launching the pipeline."""
    return await questionary.select(
        "Ready to launch?",
        choices=[
            questionary.Choice("Start pipeline", value="start"),
            questionary.Choice("Edit URLs", value="edit_urls"),
            questionary.Choice("Edit configuration", value="edit_config"),
            questionary.Choice("Cancel (main menu)", value="cancel"),
        ],
        **_q(),
    ).ask_async()


# ── URL selection ──────────────────────────────────────────────────────────────

async def ask_url_filter_pattern(total: int) -> str:
    """Ask for an optional filter pattern before showing the review checklist."""
    return await questionary.text(
        f"Filter {total} URLs before review (Enter to skip):",
        instruction="  substring · /path/*  glob · re:<regex>",
        default="",
        **_q(),
    ).ask_async() or ""


async def ask_skip_known(domain: str) -> bool:
    """Ask whether to exclude URLs already scraped in previous sessions."""
    return await questionary.confirm(
        f"Skip URLs already scraped in a previous session for {domain}?",
        default=False,
        **_q(),
    ).ask_async() or False


# ── Stage checkpoints ─────────────────────────────────────────────────────────

async def ask_stage_action(stage: str, next_stage: str = "") -> str:
    """Checkpoint menu between stages. Each choice explains itself below the list."""
    nxt = f": {next_stage}" if next_stage else ""
    return await questionary.select(
        f"{stage} finished. What next?",
        choices=[
            questionary.Choice(
                f"Continue{nxt}", value="continue",
                description=f"Run the next step{nxt}.",
            ),
            questionary.Choice(
                "Export what I have so far", value="export",
                description="Save pages, chunks or samples as Markdown, text, JSON or CSV. "
                            "You can keep going afterwards.",
            ),
            questionary.Choice(
                "What happens next?", value="explain",
                description="Explain every step and what the next one will do.",
            ),
            questionary.Choice(
                "Change model or output folder", value="adjust",
                description="Takes effect from the next step.",
            ),
            questionary.Choice(
                "Stop here (resume later)", value="pause",
                description="Everything is saved. Pick 'Continue a paused project' from the menu later.",
            ),
        ],
        **_q(),
    ).ask_async() or "pause"


async def ask_adjust_settings_target() -> str | None:
    """Which mid-session setting to change. Returns None on cancel/back."""
    answer = await questionary.select(
        "Adjust which setting?",
        choices=[
            questionary.Choice("Generation model", value="generation_model"),
            questionary.Choice("Quality model", value="quality_model"),
            questionary.Choice("Output directory", value="output_dir"),
            questionary.Choice("← Back", value=_BACK),
        ],
        **_q(),
    ).ask_async()
    return _none_if_back(answer)


# ── Export ────────────────────────────────────────────────────────────────────

async def ask_export_what(options: list[tuple[str, str, str]]) -> list[str]:
    """Pick what to export. ``options`` is [(label, value, description)]."""
    answer = await questionary.checkbox(
        "What do you want to export?",
        choices=[
            questionary.Choice(label, value=value, description=desc, checked=(i == 0))
            for i, (label, value, desc) in enumerate(options)
        ],
        instruction="(Space to tick, Enter to confirm)",
        **_q(),
    ).ask_async()
    return answer or []


async def ask_export_formats(label: str, formats: tuple[str, ...], names: dict[str, str]) -> list[str]:
    answer = await questionary.checkbox(
        f"{label}: which formats?",
        choices=[
            questionary.Choice(names.get(f, f), value=f, checked=(i == 0))
            for i, f in enumerate(formats)
        ],
        instruction="(Space to tick, Enter to confirm)",
        validate=lambda v: bool(v) or "Tick at least one format",
        **_q(),
    ).ask_async()
    return answer or []


async def ask_export_targets(hf_configured: bool, kg_configured: bool) -> list[str]:
    choices = [questionary.Choice("Local files (JSONL / Parquet / CSV)", value="local")]
    if hf_configured:
        choices.append(questionary.Choice("HuggingFace Hub", value="huggingface"))
    if kg_configured:
        choices.append(questionary.Choice("Kaggle", value="kaggle"))
    return await questionary.checkbox(
        "Export targets:", choices=choices, **_q()
    ).ask_async()


async def ask_hf_repo() -> str:
    return await questionary.text(
        "HuggingFace dataset repo (e.g. username/my-dataset):",
        validate=lambda v: "/" in v or "Use format username/dataset-name",
        **_q(),
    ).ask_async()


async def ask_hf_private() -> bool:
    return await questionary.confirm("Make dataset private?", default=True, **_q()).ask_async()


async def ask_kaggle_slug(username: str) -> str:
    return await questionary.text(
        f"Kaggle dataset slug (e.g. {username}/my-dataset):",
        default=f"{username}/dataforge-dataset",
        **_q(),
    ).ask_async()


async def ask_confirm(msg: str, default: bool = True) -> bool:
    return await questionary.confirm(msg, default=default, **_q()).ask_async()


# ── Config ────────────────────────────────────────────────────────────────────

async def ask_provider() -> str:
    return await questionary.select(
        "LLM provider:",
        choices=["openai", "anthropic", "google", "groq", "together", "ollama"],
        **_q(),
    ).ask_async()


async def ask_model(choices: list[str]) -> str:
    _CUSTOM = "(custom) Enter model ID manually"
    selection = await questionary.select(
        "Model:",
        choices=choices + [questionary.Separator(), _CUSTOM],
        **_q(),
    ).ask_async()
    if selection == _CUSTOM:
        return await questionary.text(
            "Model ID (e.g. openai/o3, groq/llama-3.3-70b-versatile):",
            validate=lambda v: bool(v.strip()) or "Model ID cannot be empty",
            **_q(),
        ).ask_async()
    return selection


# ── Quality / model overrides ─────────────────────────────────────────────────

async def ask_quality_threshold() -> float | None:
    val = await questionary.text(
        "Minimum quality score to approve samples (0.0–1.0):",
        default="0.5",
        validate=lambda v: (
            v.replace(".", "", 1).isdigit() and 0.0 <= float(v) <= 1.0
        ) or "Enter a decimal between 0.0 and 1.0",
        **_q(),
    ).ask_async()
    if val is None:
        return None
    return float(val)


async def ask_generation_model(current: str) -> str | None:
    val = await questionary.text(
        "Generation model (leave blank to keep current):",
        default=current,
        **_q(),
    ).ask_async()
    if val is None:
        return None
    return val.strip() or current


async def ask_quality_model(generation_model: str) -> str | None:
    val = await questionary.text(
        "Quality model (leave blank to reuse generation model):",
        default=generation_model,
        **_q(),
    ).ask_async()
    if val is None:
        return None
    return val.strip() or generation_model


# ── Helpers ───────────────────────────────────────────────────────────────────

def _valid_url(v: str) -> bool:
    try:
        p = urlparse(v)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def read_url_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        u for line in lines
        if not line.strip().startswith("#")
        for u in _split_urls(line)
        if _valid_url(u)
    ]


# Straight and curly quotes people paste around paths and URLs.
_QUOTE_PAIRS = {'"': '"', "'": "'", "`": "`", "“": "”", "‘": "’", "<": ">"}


def clean_input(value: str) -> str:
    r"""Normalise a pasted path or URL.

    Handles what terminals and file managers add around a path: surrounding
    quotes (Windows "Copy as path"), PowerShell's drag-and-drop form
    ``& 'C:\dir\file.txt'``, and stray whitespace.
    """
    v = (value or "").strip()
    if v.startswith("& "):
        v = v[2:].strip()
    while len(v) >= 2 and _QUOTE_PAIRS.get(v[0]) == v[-1]:
        v = v[1:-1].strip()
    return v


def _split_urls(text: str) -> list[str]:
    """URLs from free text: one per line, or separated by commas or spaces."""
    parts = text.replace(",", " ").split()
    return [c for c in (clean_input(p) for p in parts) if c]


def _path_exists(value: str) -> bool:
    v = clean_input(value)
    return bool(v) and Path(v).expanduser().exists()
