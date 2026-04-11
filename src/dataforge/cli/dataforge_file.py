"""Project-level .dataforge file — tracks sessions and absolute paths for a working directory."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

DATAFORGE_FILENAME = ".dataforge"


def find_project_file(cwd: Path | None = None) -> Path | None:
    """Walk up from *cwd* (defaults to CWD) looking for a .dataforge file."""
    search = (cwd or Path.cwd()).resolve()
    for directory in [search, *search.parents]:
        candidate = directory / DATAFORGE_FILENAME
        if candidate.is_file():
            return candidate
        # Stop at root
        if directory == directory.parent:
            break
    return None


def load_project(path: Path) -> dict[str, Any]:
    """Parse a .dataforge JSON file and return its data dict."""
    return json.loads(path.read_text(encoding="utf-8"))


def save_project(path: Path, data: dict[str, Any]) -> None:
    """Atomically write a .dataforge JSON file."""
    tmp = path.with_name(".dataforge.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def create_project(
    cwd: Path,
    db_path: Path,
    output_dir: Path,
    session_id: str,
    session_name: str,
) -> Path:
    """Create a new .dataforge file in *cwd*.  Returns the file path."""
    path = cwd / DATAFORGE_FILENAME
    data: dict[str, Any] = {
        "version": "1",
        "db_path": str(db_path.resolve()),
        "output_dir": str(output_dir.resolve()),
        "sessions": [
            {
                "id": session_id,
                "name": session_name,
                "created_at": datetime.utcnow().isoformat(),
            }
        ],
    }
    save_project(path, data)
    return path


def add_session(project_path: Path, session_id: str, session_name: str) -> None:
    """Append a new session entry to an existing .dataforge file."""
    data = load_project(project_path)
    sessions: list[dict] = data.get("sessions", [])
    if not any(s["id"] == session_id for s in sessions):
        sessions.append(
            {
                "id": session_id,
                "name": session_name,
                "created_at": datetime.utcnow().isoformat(),
            }
        )
    data["sessions"] = sessions
    save_project(project_path, data)


def get_project_sessions(project_path: Path) -> list[dict]:
    """Return all session entries recorded in the .dataforge file."""
    data = load_project(project_path)
    return data.get("sessions", [])
