"""Smoke test: mid-session settings adjustment.

Confirms _adjust_settings mutates the running PipelineContext/Settings the
way agents actually read them (generation_model / quality_model / output_dir),
and — critically — that changing the output directory does NOT move db_path,
since the running session's data lives in the existing database.
"""
from __future__ import annotations

import pytest

from dataforge.agents.base import PipelineContext
from dataforge.cli import app as cli_app
from dataforge.cli import prompts
from dataforge.storage.models import DataFormat


@pytest.fixture
def ctx(tmp_settings):
    return PipelineContext(
        session_id="test-session",
        session_name="test",
        goal="test goal",
        format=DataFormat.qa,
        seed_urls=["https://example.com"],
        settings=tmp_settings,
    )


@pytest.mark.asyncio
async def test_adjust_generation_model(ctx, monkeypatch):
    monkeypatch.setattr(prompts, "ask_adjust_settings_target", _const("generation_model"))
    monkeypatch.setattr(prompts, "ask_generation_model", _const("gpt-4-turbo"))

    await cli_app._adjust_settings(ctx)

    assert ctx.generation_model == "gpt-4-turbo"


@pytest.mark.asyncio
async def test_adjust_output_dir_leaves_db_path_untouched(ctx, monkeypatch, tmp_path):
    original_db_path = ctx.settings.db_path
    new_dir = tmp_path / "new_output"

    monkeypatch.setattr(prompts, "ask_adjust_settings_target", _const("output_dir"))
    monkeypatch.setattr(prompts, "ask_output_dir", _const(str(new_dir)))

    await cli_app._adjust_settings(ctx)

    assert ctx.settings.output_dir == new_dir.resolve()
    assert ctx.settings.db_path == original_db_path


def _const(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner
