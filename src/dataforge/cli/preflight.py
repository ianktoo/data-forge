"""Pre-flight checks run before each pipeline stage.

Each check returns (ok: bool, skip_key: str | None).
If ok is False and skip_key is set, the orchestrator shows guidance and
either skips the stage or halts, depending on whether the stage is optional.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx

from dataforge.config import get_settings
from dataforge.utils.errors import (
    MissingCredentialError,
    show_error,
    show_skipped,
    show_warning,
)

# Stages that can be skipped when their prerequisite is missing
_SKIPPABLE = {"generation"}

# Which env var each provider needs
_PROVIDER_KEY_MAP = {
    "openai":    "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq":      "GROQ_API_KEY",
    "together":  "TOGETHER_API_KEY",
    "ollama":    None,   # local, no key needed
}


def check_env_file() -> bool:
    """Warn (not fatal) if .env doesn't exist."""
    if not Path(".env").exists():
        show_warning(
            ".env file not found — using default settings and environment variables.",
            "Run:  cp .env.example .env  then fill in your API keys.",
        )
    return True   # non-fatal


def check_llm_credentials() -> tuple[bool, str | None]:
    """Verify the configured LLM provider has the required key (or is reachable)."""
    s = get_settings()
    provider = s.llm_provider.lower()
    key_env  = _PROVIDER_KEY_MAP.get(provider)

    if provider == "ollama":
        return _check_ollama(s.ollama_base_url)

    if key_env and not os.getenv(key_env) and not getattr(s, key_env.lower(), ""):
        show_error(key_env)
        return False, key_env

    return True, None


def _check_ollama(base_url: str) -> tuple[bool, str | None]:
    try:
        resp = httpx.get(f"{base_url}/api/tags", timeout=3)
        if resp.status_code == 200:
            return True, None
    except Exception:
        pass
    show_error("OLLAMA_UNREACHABLE")
    return False, "OLLAMA_UNREACHABLE"


def check_huggingface_token() -> tuple[bool, str | None]:
    s = get_settings()
    if not s.huggingface_token:
        show_error("HUGGINGFACE_TOKEN")
        return False, "HUGGINGFACE_TOKEN"
    return True, None


def check_kaggle_credentials() -> tuple[bool, str | None]:
    s = get_settings()
    if not s.kaggle_username or not s.kaggle_key:
        show_error("KAGGLE")
        return False, "KAGGLE"
    return True, None


# ── Stage-level gate ───────────────────────────────────────────────────────────

class PreflightResult:
    def __init__(self, ok: bool, skip: bool = False, error_key: str = "") -> None:
        self.ok        = ok       # True = proceed
        self.skip      = skip     # True = skip stage gracefully
        self.error_key = error_key


def check_stage(stage: str) -> PreflightResult:
    """Run all checks relevant to the given stage name."""
    if stage == "generation":
        ok, key = check_llm_credentials()
        if not ok:
            show_skipped(
                stage="generation",
                reason="LLM credentials are not configured.",
                what_works=[
                    "Export the processed chunks now:  dataforge export <session-id>",
                    "Add your API key to .env, then resume:  dataforge resume <session-id>",
                    "Use a local model (no key needed):  dataforge config  → choose ollama",
                ],
            )
            return PreflightResult(ok=False, skip=True, error_key=key or "")

    return PreflightResult(ok=True)


def check_export_target(target: str) -> PreflightResult:
    if target == "huggingface":
        ok, key = check_huggingface_token()
        return PreflightResult(ok=ok, skip=not ok, error_key=key or "")
    if target == "kaggle":
        ok, key = check_kaggle_credentials()
        return PreflightResult(ok=ok, skip=not ok, error_key=key or "")
    return PreflightResult(ok=True)
