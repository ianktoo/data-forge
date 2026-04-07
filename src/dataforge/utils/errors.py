"""Structured error types and Rich-formatted user-facing error display."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console(stderr=True)


# ── Exception hierarchy ────────────────────────────────────────────────────────

class DataForgeError(Exception):
    """Base for all DataForge errors."""


class MissingCredentialError(DataForgeError):
    """A required API key or credential is absent."""
    def __init__(self, credential: str, provider: str = "") -> None:
        self.credential = credential
        self.provider   = provider
        super().__init__(f"Missing credential: {credential}")


class StageSkippedError(DataForgeError):
    """A pipeline stage was intentionally skipped due to missing prerequisites."""
    def __init__(self, stage: str, reason: str) -> None:
        self.stage  = stage
        self.reason = reason
        super().__init__(f"Stage '{stage}' skipped: {reason}")


class LLMConnectionError(DataForgeError):
    """Cannot reach the configured LLM endpoint."""


class RateLimitError(DataForgeError):
    """Upstream rate limit hit after all retries exhausted."""


class NoContentError(DataForgeError):
    """No usable content was extracted from the provided URLs."""


# ── Guidance catalogue ────────────────────────────────────────────────────────
# Maps credential/error key → (title, body lines, hint lines)

_GUIDANCE: dict[str, tuple[str, list[str], list[str]]] = {
    "OPENAI_API_KEY": (
        "OpenAI API key not set",
        ["The generation stage requires an OpenAI API key.",
         "Without it you can still run: discovery → collection → processing."],
        ["1. Get a key at https://platform.openai.com/api-keys",
         "2. Add it to your .env file:  OPENAI_API_KEY=sk-...",
         "3. Or switch to a local model:  dataforge config  (choose ollama)"],
    ),
    "ANTHROPIC_API_KEY": (
        "Anthropic API key not set",
        ["The generation stage requires an Anthropic API key."],
        ["1. Get a key at https://console.anthropic.com",
         "2. Add to .env:  ANTHROPIC_API_KEY=sk-ant-...",
         "3. Or switch provider:  dataforge config"],
    ),
    "GROQ_API_KEY": (
        "Groq API key not set",
        ["The generation stage requires a Groq API key."],
        ["1. Get a free key at https://console.groq.com",
         "2. Add to .env:  GROQ_API_KEY=gsk_...",
         "3. Or switch provider:  dataforge config"],
    ),
    "TOGETHER_API_KEY": (
        "Together AI key not set",
        ["The generation stage requires a Together AI API key."],
        ["1. Get a key at https://api.together.xyz",
         "2. Add to .env:  TOGETHER_API_KEY=...",
         "3. Or switch provider:  dataforge config"],
    ),
    "OLLAMA_UNREACHABLE": (
        "Ollama is not running",
        ["DataForge is configured to use Ollama (local), but cannot connect.",
         f"Expected at: http://localhost:11434"],
        ["1. Install Ollama: https://ollama.com",
         "2. Start it:  ollama serve",
         "3. Pull a model:  ollama pull llama3.2",
         "4. Or switch to a cloud provider:  dataforge config"],
    ),
    "HUGGINGFACE_TOKEN": (
        "HuggingFace token not set",
        ["Uploading to HuggingFace Hub requires a write token.",
         "Local export will still work."],
        ["1. Get a token at https://huggingface.co/settings/tokens",
         "2. Add to .env:  HUGGINGFACE_TOKEN=hf_...",
         "3. Re-run export:  dataforge export <session-id>"],
    ),
    "KAGGLE": (
        "Kaggle credentials not set",
        ["Uploading to Kaggle requires KAGGLE_USERNAME and KAGGLE_KEY.",
         "Local export will still work."],
        ["1. Get your credentials at https://www.kaggle.com/settings → API",
         "2. Add to .env:",
         "     KAGGLE_USERNAME=your-username",
         "     KAGGLE_KEY=xxxx",
         "3. Re-run export:  dataforge export <session-id>"],
    ),
    "NO_CONTENT": (
        "No usable content extracted",
        ["All scraped pages were either empty, blocked, or below the minimum",
         "word threshold (50 words). Nothing to process."],
        ["• Check that the URLs are publicly accessible (no login wall)",
         "• Try a different URL or sitemap",
         "• Lower the threshold: set DATAFORGE_MIN_WORDS=20 in .env"],
    ),
    "LLM_CONNECTION": (
        "Cannot connect to LLM provider",
        ["DataForge could not reach the configured LLM endpoint.",
         "Your internet connection or the provider service may be down."],
        ["• Run:  dataforge test-llm  to diagnose",
         "• Check your API key is valid and not expired",
         "• Try a different provider:  dataforge config"],
    ),
    "NO_URLS": (
        "No URLs to process",
        ["The discovery stage found no URLs to scrape.",
         "This can happen if the sitemap is empty or the URL is invalid."],
        ["• Verify the URL is correct and publicly reachable",
         "• Try providing URLs directly (text file or manual entry)",
         "• Run:  dataforge explore <url>  to debug discovery"],
    ),
    "ENV_NOT_FOUND": (
        ".env file not found",
        ["No .env file was found in the current directory.",
         "API keys and settings are loaded from this file."],
        ["1. Copy the template:  cp .env.example .env  (Unix)",
         "                  or:  copy .env.example .env  (Windows)",
         "2. Fill in at minimum one LLM provider key",
         "3. Re-run DataForge"],
    ),
}

_STAGE_NEEDS: dict[str, list[str]] = {
    "discovery":  [],
    "collection": [],
    "processing": [],
    "generation": ["llm_key"],
    "quality":    [],
    "export":     [],   # checked per-target at export time
}


# ── Display helpers ────────────────────────────────────────────────────────────

def show_error(key: str, extra: str = "") -> None:
    """Print a rich error panel for the given guidance key."""
    title, body_lines, hint_lines = _GUIDANCE.get(key, (
        f"Error: {key}",
        [extra or "An unexpected error occurred."],
        ["Check the logs in ./logs/ for details"],
    ))

    text = Text()
    for line in body_lines:
        text.append(line + "\n", style="white")
    if extra:
        text.append(f"\nDetail: {extra}\n", style="dim")

    text.append("\nWhat to do:\n", style="bold yellow")
    for line in hint_lines:
        text.append(f"  {line}\n", style="cyan")

    console.print(Panel(text, title=f"[bold red]✗ {title}[/]",
                        border_style="red", box=box.ROUNDED, padding=(0, 1)))


def show_warning(message: str, hint: str = "") -> None:
    text = Text(message, style="white")
    if hint:
        text.append(f"\n{hint}", style="dim cyan")
    console.print(Panel(text, title="[bold yellow]⚠ Warning[/]",
                        border_style="yellow", box=box.SIMPLE, padding=(0, 1)))


def show_skipped(stage: str, reason: str, what_works: list[str] | None = None) -> None:
    text = Text(f"Stage '{stage}' was skipped.\n", style="white")
    text.append(f"Reason: {reason}\n", style="dim")
    if what_works:
        text.append("\nYou can still:\n", style="bold green")
        for item in what_works:
            text.append(f"  • {item}\n", style="green")
    console.print(Panel(text, title="[bold yellow]⟳ Stage Skipped[/]",
                        border_style="yellow", box=box.ROUNDED, padding=(0, 1)))
