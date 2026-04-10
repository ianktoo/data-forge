"""User preferences — persisted to ~/.config/dataforge/prefs.json.

Stores cross-project settings (provider, model, tip indices) that survive
across different working directories, unlike the project-local .env file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


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


def next_tip_index(stage: str, total: int) -> int:
    """Return the next tip index for a stage and advance the stored counter."""
    prefs = load()
    indices: dict[str, int] = prefs.get("tip_indices", {})
    current = indices.get(stage, 0)
    indices[stage] = (current + 1) % total
    prefs["tip_indices"] = indices
    save(prefs)
    return current
