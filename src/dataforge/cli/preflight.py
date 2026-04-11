"""Pre-flight checks run before each pipeline stage.

Each check returns (ok: bool, skip_key: str | None).
If ok is False and skip_key is set, the orchestrator shows guidance and
either skips the stage or halts, depending on whether the stage is optional.
"""
from __future__ import annotations

import getpass
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
    "google":    "GEMINI_API_KEY",
    "groq":      "GROQ_API_KEY",
    "together":  "TOGETHER_API_KEY",
    "ollama":    None,   # local, no key needed
}


def check_env_file() -> bool:
    """Warn (not fatal) if .env doesn't exist or no provider key is configured."""
    from dataforge.cli import prefs as user_prefs

    env_path = Path(".env").resolve()
    env_exists = env_path.exists()
    if not env_exists:
        show_warning(
            f".env file not found at {env_path}",
            f"Run:  dataforge config  to save settings globally to {user_prefs._prefs_path()}",
        )
        # Apply saved prefs as env var defaults so pip-installed users keep their config
        saved_provider = user_prefs.get("llm_provider")
        saved_model = user_prefs.get("llm_model")
        if saved_provider and not os.getenv("DATAFORGE_LLM_PROVIDER"):
            os.environ["DATAFORGE_LLM_PROVIDER"] = saved_provider
        if saved_model and not os.getenv("DATAFORGE_LLM_MODEL"):
            os.environ["DATAFORGE_LLM_MODEL"] = saved_model
        # Apply globally-saved API keys so pip-installed users work without .env
        for key_env, value in user_prefs.load().get("api_keys", {}).items():
            if value and not os.getenv(key_env):
                os.environ[key_env] = value

    # Check whether the active provider is satisfied (keyless like Ollama, or has a key set)
    s = get_settings()
    active_key_env = _PROVIDER_KEY_MAP.get(s.llm_provider.lower())
    needs_key = active_key_env is not None
    has_key = bool(needs_key and os.getenv(active_key_env))
    if needs_key and not has_key:
        show_warning(
            "No LLM provider key detected — you will be prompted for one when needed.",
            "To avoid this each session, run:  dataforge config  to save your key permanently.",
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
        # Offer a live prompt before failing
        try:
            value = getpass.getpass(f"  {key_env} not set — paste your key now (hidden, session-only): ")
        except (KeyboardInterrupt, EOFError):
            value = ""
        if value.strip():
            os.environ[key_env] = value.strip()
            return True, None
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
