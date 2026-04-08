"""Questionary-based interactive prompts for the pipeline wizard."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import questionary
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


# ── Input collection ───────────────────────────────────────────────────────────

async def ask_input_method() -> str:
    return await questionary.select(
        "How would you like to provide URLs?",
        choices=["Single URL", "Multiple URLs", "Text file", "Sitemap URL"],
        **_q(),
    ).ask_async()


async def ask_single_url() -> str:
    url = await questionary.text(
        "Enter URL:",
        validate=lambda v: _valid_url(v) or "Enter a valid http(s) URL",
        **_q(),
    ).ask_async()
    return url.strip()


async def ask_multiple_urls() -> list[str]:
    raw = await questionary.text(
        "Enter URLs (one per line, blank line to finish):",
        multiline=True,
        **_q(),
    ).ask_async()
    return [u.strip() for u in raw.splitlines() if u.strip() and _valid_url(u.strip())]


async def ask_file_path() -> Path:
    path = await questionary.path(
        "Path to URL file:",
        validate=lambda v: Path(v).exists() or "File not found",
        **_q(),
    ).ask_async()
    return Path(path)


async def ask_goal() -> str:
    return await questionary.text(
        "Describe the goal of this dataset (e.g. 'customer support Q&A for a SaaS product'):",
        validate=lambda v: len(v.strip()) > 10 or "Please be more descriptive",
        **_q(),
    ).ask_async()


async def ask_format() -> str:
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


async def ask_custom_system_prompt() -> str:
    return await questionary.text(
        "Enter your custom system prompt for the LLM generator:",
        multiline=True,
        **_q(),
    ).ask_async()


async def ask_n_per_chunk() -> int:
    val = await questionary.text(
        "Samples to generate per content chunk:",
        default="3",
        validate=lambda v: v.isdigit() and 1 <= int(v) <= 10 or "Enter a number 1–10",
        **_q(),
    ).ask_async()
    return int(val)


async def ask_session_name() -> str:
    return await questionary.text(
        "Session name (for reference):",
        default="my-dataset",
        **_q(),
    ).ask_async()


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
        choices=["openai", "anthropic", "groq", "together", "ollama"],
        **_q(),
    ).ask_async()


async def ask_model(choices: list[str]) -> str:
    return await questionary.select("Model:", choices=choices, **_q()).ask_async()


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
