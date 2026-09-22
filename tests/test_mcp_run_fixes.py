"""Regression tests for the problems found in the first MCP-driven run
(docs/reviews/2026-09-22-mcp-run-report.md)."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from dataforge.cli import app as cli_app

# -- 1. run and the reporting commands must use the same database ------------


def _project_file(tmp_path):
    db = tmp_path / "proj" / "dataforge.db"
    out = tmp_path / "proj" / "output"
    (tmp_path / ".dataforge").write_text(
        json.dumps({"version": "1", "db_path": str(db), "output_dir": str(out), "sessions": []}),
        encoding="utf-8",
    )
    return db, out


def test_project_file_sets_db_and_output(tmp_path, monkeypatch):
    db, out = _project_file(tmp_path)
    monkeypatch.delenv("DATAFORGE_DB_PATH", raising=False)
    monkeypatch.delenv("DATAFORGE_OUTPUT_DIR", raising=False)
    s = SimpleNamespace(db_path=tmp_path / "default.db", output_dir=tmp_path / "default")
    cli_app._apply_project_file(s, tmp_path)
    assert s.db_path == db and s.output_dir == out


def test_explicit_env_wins_over_project_file(tmp_path, monkeypatch):
    """The benchmark gives each site its own DB through DATAFORGE_DB_PATH."""
    _project_file(tmp_path)
    monkeypatch.setenv("DATAFORGE_DB_PATH", str(tmp_path / "site.db"))
    monkeypatch.setenv("DATAFORGE_OUTPUT_DIR", str(tmp_path / "site-out"))
    s = SimpleNamespace(db_path=tmp_path / "site.db", output_dir=tmp_path / "site-out")
    cli_app._apply_project_file(s, tmp_path)
    assert s.db_path == tmp_path / "site.db" and s.output_dir == tmp_path / "site-out"


def test_run_command_bootstraps_like_every_other_command(monkeypatch):
    """Regression: `run` skipped _bootstrap, so it ignored the project file
    (wrong DB) and never configured logging (DEBUG, in colour)."""
    from typer.testing import CliRunner

    from dataforge.cli import headless

    calls = []
    monkeypatch.setattr(cli_app, "_bootstrap", lambda: calls.append("bootstrap"))

    async def fake_run(recipe, dry_run=False):
        calls.append("run")
        return 0

    monkeypatch.setattr(headless, "run_recipe", fake_run)
    result = CliRunner().invoke(cli_app.app, ["run", "recipe.yaml", "--dry-run"])
    assert result.exit_code == 0
    assert calls == ["bootstrap", "run"]


# -- 2. errors must be machine-readable under --json --------------------------


def test_missing_session_is_a_json_error(tmp_path, monkeypatch, capsys):
    from dataforge.storage import init_db

    db = tmp_path / "x.db"
    init_db(db)
    monkeypatch.setattr(cli_app, "_JSON_OUTPUT", True)
    assert cli_app._resolve_session(db, "nope") is None
    out = json.loads(capsys.readouterr().out)
    assert "not found" in out["error"]


def _completed(stdout, code=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


def test_mcp_tool_returns_the_cli_reason_not_a_bare_failure(monkeypatch):
    pytest.importorskip("mcp")
    from dataforge import mcp_server

    # Old CLI behaviour: plain text on stdout, exit 0.
    monkeypatch.setattr(mcp_server, "_run_cli", lambda *a, **k: _completed("\x1b[31m✗\x1b[0m Session 'x' not found\n"))
    assert mcp_server._json_cli("view", "x") == {"error": "✗ Session 'x' not found"}

    # New CLI behaviour: JSON error, exit 1.
    monkeypatch.setattr(mcp_server, "_run_cli", lambda *a, **k: _completed('{"error": "Session \'x\' not found"}', 1))
    assert mcp_server._json_cli("view", "x") == {"error": "Session 'x' not found"}

    # Success passes through.
    monkeypatch.setattr(mcp_server, "_run_cli", lambda *a, **k: _completed('[{"id": "abc"}]'))
    assert mcp_server._json_cli("sessions") == [{"id": "abc"}]


# -- 3. run_status is readable ------------------------------------------------


def test_run_status_log_is_plain_and_reports_progress(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from dataforge import mcp_server

    log = tmp_path / "abc.log"
    log.write_text(
        "· Session ID: daf0b1dc\n"
        "\x1b[32m15:06:45\x1b[0m | \x1b[34mDEBUG   \x1b[0m | http - GET https://x.test/ -> 200\n"
        "·   scraped 11/14  chunks 29  samples 12\n"
        "\x1b[32m15:07:43\x1b[0m | \x1b[1mINFO    \x1b[0m | quality - Quality pass: 125/126 approved\n"
        "·   scraped 14/14  chunks 42  samples 66\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_server, "get_settings", lambda: SimpleNamespace(output_dir=tmp_path.parent))
    (tmp_path.parent / "mcp-runs").mkdir(exist_ok=True)
    log.replace(tmp_path.parent / "mcp-runs" / "abc.log")
    status = mcp_server.run_status("abc")
    assert status["session_id"] == "daf0b1dc"
    assert status["progress"] == "scraped 14/14  chunks 42  samples 66"
    assert "\x1b[" not in status["log_tail"] and "DEBUG" not in status["log_tail"]
    assert "Quality pass" in status["log_tail"]


# -- 4. a key in .env is a key ------------------------------------------------


def test_key_in_env_file_is_not_reported_missing(tmp_path, monkeypatch):
    from dataforge.cli import preflight

    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(preflight, "get_settings",
                        lambda: SimpleNamespace(llm_provider="openai", openai_api_key="sk-test"))
    warnings = []
    monkeypatch.setattr(preflight, "show_warning", lambda *a, **k: warnings.append(a))
    preflight.check_env_file()
    assert not any("No LLM provider key" in str(w) for w in warnings)


# -- 5. the dry-run plan states the budget ------------------------------------


@pytest.mark.parametrize(
    "gen,expected",
    [
        ({"max_cost_usd": 1.0}, "at most $1.00"),
        ({"max_cost_usd": 2.0, "max_llm_calls": 500}, "at most $2.00 and 500 LLM calls"),
        ({}, "NO CAP"),
    ],
)
def test_plan_shows_budget(gen, expected):
    from dataforge.cli.headless import _budget_text

    recipe = SimpleNamespace(generation=SimpleNamespace(
        max_cost_usd=gen.get("max_cost_usd"), max_llm_calls=gen.get("max_llm_calls")))
    assert _budget_text(recipe).startswith(expected)


# -- 7. view --json --stage returns JSON, never the interactive pager --------


@pytest.mark.parametrize("stage", ["discovery", "collection", "processing", "generation", "quality"])
def test_view_stage_json_is_machine_readable(tmp_path, monkeypatch, capsys, stage):
    """Regression: with --stage, view always opened the interactive n/p pager,
    which crashes without a terminal, and printed a human header first; so the
    MCP view_samples tool could never return anything."""
    import asyncio

    from dataforge.storage import PipelineSession, SyntheticSample, init_db, open_session

    db = tmp_path / "v.db"
    init_db(db)
    with open_session(db) as s:
        s.add(PipelineSession(id="sess-123", name="t"))
        s.add(SyntheticSample(session_id="sess-123", chunk_id=1, format="qa", approved=True,
                              messages_json='[{"role":"user","content":"q"},{"role":"assistant","content":"a"}]'))
        s.commit()
    monkeypatch.setattr(cli_app, "get_settings", lambda: SimpleNamespace(db_path=db))
    monkeypatch.setattr(cli_app, "_JSON_OUTPUT", True)
    asyncio.run(cli_app._view_session("sess", stage, limit=5))
    out = json.loads(capsys.readouterr().out)
    assert out["session_id"] == "sess-123" and out["stage"] == stage
    if stage in ("generation", "quality"):
        assert out["total"] == 1 and out["rows"][0]["messages"][1]["content"] == "a"
