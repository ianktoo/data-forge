"""MCP server guards: start_run refuses uncapped spend and ignore_robots."""
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from dataforge import mcp_server  # noqa: E402


def _recipe(tmp_path, extra: str = "") -> str:
    path = tmp_path / "r.yaml"
    path.write_text(
        "version: 1\nname: t\nsource:\n  urls: [https://example.gov/sitemap.xml]\n" + extra,
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture
def no_spawn(monkeypatch, tmp_path):
    """Point run logs at tmp_path and record Popen calls instead of running them."""
    calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append(args)

        def poll(self):
            return 0

    monkeypatch.setattr(mcp_server, "get_settings", lambda: SimpleNamespace(output_dir=tmp_path))
    monkeypatch.setattr(mcp_server.subprocess, "Popen", FakePopen)
    return calls


def test_refuses_uncapped_spend(tmp_path, no_spawn):
    result = mcp_server.start_run(_recipe(tmp_path))
    assert result["started"] is False
    assert "max_cost_usd" in result["error"]
    assert no_spawn == []


def test_uncapped_spend_allowed_when_explicit(tmp_path, no_spawn):
    result = mcp_server.start_run(_recipe(tmp_path), allow_uncapped_spend=True)
    assert result["started"] is True
    assert len(no_spawn) == 1


def test_refuses_ignore_robots_even_with_cap(tmp_path, no_spawn):
    recipe = _recipe(
        tmp_path,
        "  ignore_robots: true\ngeneration:\n  max_cost_usd: 1.0\n",
    )
    result = mcp_server.start_run(recipe, allow_uncapped_spend=True)
    assert result["started"] is False
    assert "ignore_robots" in result["error"]
    assert no_spawn == []


def test_capped_run_starts_and_reports_status(tmp_path, no_spawn):
    recipe = _recipe(tmp_path, "generation:\n  max_cost_usd: 1.0\n")
    started = mcp_server.start_run(recipe)
    assert started["started"] is True
    status = mcp_server.run_status(started["run_id"])
    assert status["state"] == "finished"
    assert status["exit_code"] == 0 and status["meaning"] == "ok"


def test_invalid_recipe_is_reported(tmp_path, no_spawn):
    path = tmp_path / "bad.yaml"
    path.write_text("version: 1\nname: t\nsource: {}\nbogus_key: 1\n", encoding="utf-8")
    result = mcp_server.start_run(str(path))
    assert result["started"] is False and "invalid recipe" in result["error"]


def test_unknown_run_id(tmp_path, no_spawn):
    assert "error" in mcp_server.run_status("nope")
