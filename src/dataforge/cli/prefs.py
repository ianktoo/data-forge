"""User preferences — persisted to ~/.config/dataforge/prefs.json.

Stores cross-project settings (provider, model, tip indices) that survive
across different working directories, unlike the project-local .env file.

API keys are stored in the OS keychain via the ``keyring`` library when
available (macOS Keychain, Windows Credential Manager, Linux Secret Service).
The prefs.json file is used as a fallback if keyring is unavailable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_KEYRING_SERVICE = "dataforge"

try:
    import keyring as _keyring  # type: ignore[import-untyped]
    _HAS_KEYRING = True
except ImportError:
    _keyring = None  # type: ignore[assignment]
    _HAS_KEYRING = False


def _prefs_path() -> Path:
    # Respect XDG_CONFIG_HOME on Linux/Mac; use APPDATA on Windows
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "dataforge" / "prefs.json"


def load() -> dict[str, Any]:
    path = _prefs_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save(prefs: dict[str, Any]) -> None:
    path = _prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def set(key: str, value: Any) -> None:  # noqa: A001
    prefs = load()
    prefs[key] = value
    save(prefs)


def get_api_key(key_env: str) -> str:
    """Return a saved API key — checks OS keychain first, falls back to prefs.json."""
    if _HAS_KEYRING:
        try:
            value = _keyring.get_password(_KEYRING_SERVICE, key_env)
            if value:
                return value
        except Exception:
            pass
    return load().get("api_keys", {}).get(key_env, "")


def set_api_key(key_env: str, value: str) -> None:
    """Persist an API key — stores in OS keychain when available, else prefs.json."""
    if _HAS_KEYRING:
        try:
            _keyring.set_password(_KEYRING_SERVICE, key_env, value)
            # Remove any plaintext copy that may exist from a previous version
            prefs = load()
            if "api_keys" in prefs and key_env in prefs["api_keys"]:
                del prefs["api_keys"][key_env]
                save(prefs)
            return
        except Exception:
            pass
    # Fallback: plaintext prefs.json
    prefs = load()
    prefs.setdefault("api_keys", {})[key_env] = value
    save(prefs)


def next_tip_index(stage: str, total: int) -> int:
    """Return the next tip index for a stage and advance the stored counter."""
    prefs = load()
    indices: dict[str, int] = prefs.get("tip_indices", {})
    current = indices.get(stage, 0)
    indices[stage] = (current + 1) % total
    prefs["tip_indices"] = indices
    save(prefs)
    return current
