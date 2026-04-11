"""Questionary-based interactive prompts for the pipeline wizard."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import questionary
from questionary import Style
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style as PTStyle

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
        # Resolve alias
        value = all_aliases.get(value, value)
        if value in choices:
            return value
        # Fuzzy fallback: first choice that starts with input
        matches = [c for c in choices if c.startswith(value)]
        if len(matches) == 1:
            return matches[0]
        # Unknown — show inline error and re-prompt
        known = "  ".join(f"[cyan]{c}[/cyan]" for c in choices)
        print(f"\033[33m  Unknown command '{raw.strip()}'. Valid: {', '.join(choices)}\033[0m")


# ── Input collection ───────────────────────────────────────────────────────────

async def ask_input_method() -> str | None:
    answer = await questionary.select(
        "How would you like to provide URLs?",
        choices=[
            "Single URL",
            "Multiple URLs",
            "Text file",
            "Sitemap URL",
            questionary.Separator(),
            questionary.Choice("(b) Back to menu", value=None),
        ],
        **_q(),
    ).ask_async()
    return answer


async def ask_single_url() -> str | None:
    url = await questionary.text(
        "Enter URL:",
        validate=lambda v: _valid_url(v) or "Enter a valid http(s) URL",
        **_q(),
    ).ask_async()
    if url is None:
        return None
    return url.strip()


async def ask_multiple_urls() -> list[str] | None:
    raw = await questionary.text(
        "Enter URLs (one per line, blank line to finish):",
        multiline=True,
        **_q(),
    ).ask_async()
    if raw is None:
        return None
    return [u.strip() for u in raw.splitlines() if u.strip() and _valid_url(u.strip())]


async def ask_file_path() -> Path | None:
    path = await questionary.path(
        "Path to URL file:",
        validate=lambda v: Path(v).exists() or "File not found",
        **_q(),
    ).ask_async()
    if path is None:
        return None
    return Path(path)


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
    return answer.strip() or default


async def ask_session_name() -> str | None:
    answer = await questionary.text(
        "Session name (for reference):",
        default="my-dataset",
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

async def ask_url_filter(total: int) -> str:
    return await questionary.text(
        f"Filter URLs by substring (Enter to keep all {total}):",
        default="",
        **_q(),
    ).ask_async()


async def ask_confirm_urls(selected: int, total: int) -> bool:
    return await questionary.confirm(
        f"Proceed with {selected}/{total} URLs?",
        default=True,
        **_q(),
    ).ask_async()


# ── Stage checkpoints ─────────────────────────────────────────────────────────

async def ask_stage_action(stage: str) -> str:
    return await questionary.select(
        f"Stage '{stage}' complete. What next?",
        choices=[
            questionary.Choice("Continue to next stage", value="continue"),
            questionary.Choice("Export available data now", value="export"),
            questionary.Choice("Save and exit (resume later)", value="pause"),
        ],
        **_q(),
    ).ask_async()


# ── Export ────────────────────────────────────────────────────────────────────

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
    return [l.strip() for l in lines if l.strip() and not l.startswith("#") and _valid_url(l.strip())]
