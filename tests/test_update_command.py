"""Regression tests for `dataforge update` reporting.

1. It must not crash when `uv` isn't on PATH. Most pip-installed users (README's
   own documented `pip install llm-web-crawler` path) won't have the `uv` binary
   available. subprocess.run raises FileNotFoundError for a missing executable,
   previously uncaught, so `update` crashed instead of falling back to pip.
2. On Windows, `uv tool upgrade` installs the new version and then fails to
   overwrite the running dataforge.exe launcher (os error 32), exiting 1. That
   used to be reported as "Update failed" (after a pip fallback that cannot
   work in a uv tool environment), and a second run then passed.
3. A pinned uv install upgrades nothing; that used to read "Already up to date".
4. A real failure must exit non-zero.
5. By default `update` and `uninstall` close DataForge first and hand the job to
   a helper that waits for it to exit, so nothing is replaced while in use.
6. Agents are refused outright: a new release can change what an agent relies
   on, so only a person, at a terminal, after reading the release notes, may
   update or uninstall.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from dataforge.cli import app as cli_app
from dataforge.cli import self_manage

runner = CliRunner()

UV_LOCKED_LAUNCHER = (
    "Updated llm-web-crawler v2.4.1 -> v2.4.2\n"
    " - llm-web-crawler==2.4.1\n"
    " + llm-web-crawler==2.4.2\n"
    "error: Failed to upgrade llm-web-crawler\n"
    "  Caused by: Failed to install entrypoint\n"
    "  Caused by: failed to copy file from ...\\Scripts\\dataforge.exe to "
    "...\\bin\\dataforge.exe: The process cannot access the file because it is "
    "being used by another process. (os error 32)\n"
)


def _fake_run(calls, uv=None, pip=None):
    """uv / pip: (returncode, stderr) or an exception instance to raise."""
    def run(cmd, capture_output=True, text=True):
        calls.append(cmd)
        result = uv if cmd[0] == "uv" else pip
        if isinstance(result, Exception):
            raise result
        code, err = result
        return subprocess.CompletedProcess(cmd, code, stdout="", stderr=err)
    return run


def test_update_falls_back_to_pip_when_uv_missing(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(
        calls,
        uv=FileNotFoundError("uv not found"),
        pip=(0, "Successfully installed llm-web-crawler-9.9.9\n"),
    ))

    assert cli_app._run_update()  # must not raise

    assert any(c[0] == "uv" for c in calls)
    assert any("pip" in c for c in calls)


def test_update_reports_success_when_uv_cannot_replace_running_launcher(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(
        calls, uv=(1, UV_LOCKED_LAUNCHER), pip=(1, "No module named pip\n"),
    ))

    assert cli_app._run_update()  # must not raise

    out = capsys.readouterr().out
    assert "2.4.2" in out
    assert "Update failed" not in out
    assert "uv tool install --force llm-web-crawler" in out
    assert not any("pip" in c for c in calls)  # the upgrade worked; no fallback


def test_update_warns_when_uv_install_is_pinned(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(
        calls,
        uv=(0, "Nothing to upgrade\n\nhint: `llm-web-crawler` is pinned to `2.4.1` "
               "(installed with an exact version pin); reinstall with "
               "`uv tool install llm-web-crawler@latest` to upgrade to a new version.\n"),
    ))

    assert cli_app._run_update()

    out = capsys.readouterr().out
    assert "Already up to date" not in out
    assert "pinned" in out
    assert "llm-web-crawler@latest" in out


class _Answer:
    def __init__(self, value):
        self.value = value

    async def ask_async(self):
        return self.value


@pytest.fixture
def person(monkeypatch):
    """A person at a real terminal, answering prompts from a dict keyed by
    prompt text (a prompt missing from it fails the test)."""
    import questionary

    answers: dict[str, object] = {}
    asked: list[str] = []

    def prompt(message, *a, **kw):
        asked.append(message)
        return _Answer(answers[message])

    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(cli_app, "_has_terminal", lambda: True)
    monkeypatch.setattr(questionary, "select", prompt)
    monkeypatch.setattr(questionary, "confirm", prompt)
    return SimpleNamespace(answers=answers, asked=asked)


@pytest.fixture
def handoffs(monkeypatch):
    """Record hand_off calls instead of spawning or exec-ing anything."""
    calls = []

    def fake_hand_off(cmd, **kw):
        calls.append((cmd, kw))

    monkeypatch.setattr(self_manage, "hand_off", fake_hand_off)
    monkeypatch.setattr(self_manage, "other_instances", lambda: [])
    monkeypatch.setattr(self_manage, "detect_install", lambda: "uv-tool")
    monkeypatch.setattr(self_manage, "latest_version", lambda timeout=5.0: "99.0.0")
    return calls


# ── agents are refused ───────────────────────────────────────────────────────

@pytest.mark.parametrize("args", [["update"], ["update", "--in-place"],
                                  ["uninstall"], ["uninstall", "--delete-data"]])
def test_refused_without_a_terminal(monkeypatch, handoffs, args):
    """CliRunner has no terminal, like an agent's or a script's shell."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))

    result = runner.invoke(cli_app.app, args)

    assert result.exit_code == 2
    assert "refused" in result.output
    assert "release notes" in result.output
    assert handoffs == [] and ran == []


@pytest.mark.parametrize("args", [["update"], ["uninstall"]])
def test_refused_inside_claude_code_even_with_a_terminal(monkeypatch, handoffs, person, args):
    monkeypatch.setenv("CLAUDECODE", "1")

    result = runner.invoke(cli_app.app, args)

    assert result.exit_code == 2
    assert "AI agent" in result.output
    assert handoffs == [] and person.asked == []


def test_yes_flag_is_gone(handoffs, person):
    """No flag may skip the prompts: that is how an agent would get through."""
    for cmd in ("update", "uninstall"):
        result = runner.invoke(cli_app.app, [cmd, "--yes"])
        assert result.exit_code != 0
        assert handoffs == []


# ── update, for a person ─────────────────────────────────────────────────────

UPDATE_Q = "How should DataForge update?"


def test_update_in_place_exits_nonzero_and_shows_reason_on_failure(monkeypatch, handoffs, person):
    person.answers[UPDATE_Q] = "in_place"
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(
        calls,
        uv=(2, "error: Failed to fetch: https://pypi.org/simple/llm-web-crawler/\n"),
        pip=(1, "No module named pip\n"),
    ))

    result = runner.invoke(cli_app.app, ["update", "--in-place"])

    assert result.exit_code == 1
    assert "Update failed" in result.output
    assert "Failed to fetch" in result.output
    assert "No module named pip" not in result.output  # expected noise in a uv env
    assert "dataforge update" in result.output  # told to check again


def test_update_does_not_close_when_already_latest(monkeypatch, handoffs, person):
    from importlib.metadata import version
    monkeypatch.setattr(self_manage, "latest_version", lambda timeout=5.0: version("llm-web-crawler"))

    result = runner.invoke(cli_app.app, ["update"])

    assert result.exit_code == 0
    assert "Already up to date" in result.output
    assert handoffs == [] and person.asked == []


def test_update_close_first_shows_release_notes_and_hands_off(handoffs, person):
    person.answers[UPDATE_Q] = "exit"

    result = runner.invoke(cli_app.app, ["update"])

    assert result.exit_code == 0
    assert "release notes" in result.output
    assert "major version change" in result.output  # 2.x -> 99.0.0
    assert "DataForge will close now" in result.output
    assert [c for c, _ in handoffs] == [["uv", "tool", "upgrade", "llm-web-crawler"]]


def test_update_cancel_does_nothing(handoffs, person):
    person.answers[UPDATE_Q] = "cancel"

    result = runner.invoke(cli_app.app, ["update"])

    assert result.exit_code == 0
    assert handoffs == []


def test_update_from_source_checkout_does_not_touch_the_install(monkeypatch, handoffs, person):
    monkeypatch.setattr(self_manage, "detect_install", lambda: "editable")

    result = runner.invoke(cli_app.app, ["update"])

    assert result.exit_code == 0
    assert "git pull" in result.output
    assert handoffs == []


def test_major_jump():
    assert self_manage.is_major_jump("3.0.0", "2.4.2")
    assert not self_manage.is_major_jump("2.5.0", "2.4.2")


# ── uninstall, for a person ──────────────────────────────────────────────────

KEEP_Q = "Keep your data?"
SURE_Q = "Permanently delete the files above once DataForge is uninstalled?"
GO_Q = "Uninstall DataForge now?"


def _project(tmp_path, monkeypatch):
    (tmp_path / "dataforge.db").write_text("")
    (tmp_path / "output").mkdir()
    (tmp_path / ".env").write_text("KEY=1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATAFORGE_DB_PATH", raising=False)
    monkeypatch.delenv("DATAFORGE_OUTPUT_DIR", raising=False)
    # Settings resolve paths when first built; rebuild them in the new CWD.
    from dataforge.config import settings
    monkeypatch.setattr(settings, "_settings", None)


def test_uninstall_keeping_data(tmp_path, monkeypatch, handoffs, person):
    _project(tmp_path, monkeypatch)
    person.answers.update({KEEP_Q: True, GO_Q: True})

    result = runner.invoke(cli_app.app, ["uninstall"])

    assert result.exit_code == 0
    assert person.asked == [KEEP_Q, GO_Q]
    (cmd, kw), = handoffs
    assert cmd == ["uv", "tool", "uninstall", "llm-web-crawler"]
    assert kw["delete"] == []


def test_uninstall_deleting_data_removes_only_project_data(tmp_path, monkeypatch, handoffs, person):
    _project(tmp_path, monkeypatch)
    person.answers.update({KEEP_Q: False, SURE_Q: True, GO_Q: True})

    result = runner.invoke(cli_app.app, ["uninstall"])

    assert result.exit_code == 0
    (_, kw), = handoffs
    assert {p.name for p in kw["delete"]} == {"dataforge.db", "output"}  # never .env


def test_uninstall_delete_data_flag_still_asks(tmp_path, monkeypatch, handoffs, person):
    _project(tmp_path, monkeypatch)
    person.answers.update({SURE_Q: False, GO_Q: True})

    result = runner.invoke(cli_app.app, ["uninstall", "--delete-data"])

    assert result.exit_code == 0
    assert person.asked == [SURE_Q, GO_Q]
    (_, kw), = handoffs
    assert kw["delete"] == []  # said no to "permanently delete"


def test_uninstall_final_no_does_nothing(tmp_path, monkeypatch, handoffs, person):
    _project(tmp_path, monkeypatch)
    person.answers.update({KEEP_Q: True, GO_Q: False})

    result = runner.invoke(cli_app.app, ["uninstall"])

    assert result.exit_code == 0
    assert handoffs == []


def test_data_paths_never_deletes_outside_the_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    elsewhere = tmp_path / "shared-output"
    elsewhere.mkdir()
    (project / "dataforge.db").write_text("")

    inside, outside = self_manage.data_paths(project, Path("dataforge.db"), elsewhere)

    assert inside == [(project / "dataforge.db").resolve()]
    assert outside == [elsewhere.resolve()]


# ── the helper that runs after DataForge exits ───────────────────────────────

def _run_helper(cmd, delete):
    spec = {"cmd": cmd, "start": "start", "ok": "all good", "fail": "failed",
            "delete": delete, "wait_pids": [], "pause": False}
    return subprocess.run([sys.executable, "-I", "-c", self_manage._HELPER, json.dumps(spec)],
                          capture_output=True, text=True)


def test_helper_runs_command_then_deletes_data(tmp_path):
    """Run the real helper script: it must work isolated (-I, stdlib only)."""
    doomed = tmp_path / "dataforge.db"
    doomed.write_text("")

    r = _run_helper([sys.executable, "-c", "print('installer ran')"], [str(doomed)])

    assert r.returncode == 0
    assert not doomed.exists()
    assert "installer ran" in r.stdout and "all good" in r.stdout


def test_helper_keeps_data_when_installer_fails(tmp_path):
    kept = tmp_path / "dataforge.db"
    kept.write_text("")

    r = _run_helper([sys.executable, "-c", "raise SystemExit(3)"], [str(kept)])

    assert r.returncode == 3
    assert kept.exists()
    assert "failed" in r.stdout
