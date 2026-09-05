"""Regression test: `dataforge update` must not crash when `uv` isn't on PATH.

Most pip-installed users (README's own documented `pip install llm-web-crawler`
path) won't have the `uv` binary available. subprocess.run raises
FileNotFoundError for a missing executable — previously uncaught, so `update`
crashed outright instead of falling back to the pip upgrade path.
"""
from __future__ import annotations

import subprocess

from dataforge.cli import app as cli_app


def test_update_falls_back_to_pip_when_uv_missing(monkeypatch, capsys):
    calls = []

    def fake_run(cmd, capture_output=True, text=True):
        calls.append(cmd)
        if cmd[0] == "uv":
            raise FileNotFoundError("uv not found")
        # simulate a successful pip upgrade
        return subprocess.CompletedProcess(
            cmd, 0, stdout="Successfully installed llm-web-crawler-9.9.9\n", stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    cli_app.update()  # must not raise

    assert any(c[0] == "uv" for c in calls)
    assert any("pip" in c for c in calls)
