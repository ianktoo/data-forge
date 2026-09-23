"""Smoke tests for crash-recovery: interrupted stage-hooks and stale-active resume.

Regression coverage for two related gaps:
1. A KeyboardInterrupt/exception raised inside the interactive stage-hook
   (continue/export/pause/adjust prompt) previously wasn't caught by the
   orchestrator, leaving the session status=active in the DB forever.
2. `dataforge resume` (no args) only auto-detected status=paused sessions,
   so a session stuck at status=active from #1 was invisible to it.
"""
from __future__ import annotations

import pytest

from dataforge.agents.base import PipelineContext
from dataforge.agents.orchestrator import Orchestrator
from dataforge.cli import app as cli_app
from dataforge.cli import dataforge_file
from dataforge.storage import open_session
from dataforge.storage.models import DataFormat, PipelineSession, PipelineStage, SessionStatus


def _make_ctx(tmp_settings, session_id="s1"):
    return PipelineContext(
        session_id=session_id,
        session_name="test",
        goal="goal",
        format=DataFormat.qa,
        seed_urls=["https://example.com"],
        settings=tmp_settings,
    )


@pytest.mark.asyncio
async def test_interrupt_in_stage_hook_marks_session_paused(tmp_settings):
    ctx = _make_ctx(tmp_settings)

    async def raising_hook(stage, context):
        raise KeyboardInterrupt

    orch = Orchestrator(ctx, stage_hook=raising_hook)
    # Bypass discovery/collection/etc. agents entirely: patch _build_agent so
    # the first stage "succeeds" trivially and we reach the hook call.
    async def _fake_run(agent_self):
        return agent_self.ctx
    orch._build_agent = lambda stage: type("A", (), {"run": _fake_run, "ctx": ctx})()

    await orch.run()

    with open_session(tmp_settings.db_path) as db:
        session = db.get(PipelineSession, ctx.session_id)
        assert session.status == SessionStatus.paused


@pytest.mark.asyncio
async def test_resume_autodetect_surfaces_stale_active_session(tmp_settings, tmp_path, monkeypatch):
    session_id = "stale-1"
    with open_session(tmp_settings.db_path) as db:
        db.add(PipelineSession(
            id=session_id,
            name="stale-session",
            stage=PipelineStage.collection.value,
            status=SessionStatus.active,  # crashed mid-run, never marked paused
            seed_urls="[]",
        ))
        db.commit()

    project_file = tmp_path / ".dataforge"
    dataforge_file.create_project(tmp_path, tmp_settings.db_path, tmp_settings.output_dir,
                                   session_id, "stale-session")

    monkeypatch.setattr(dataforge_file, "find_project_file", lambda cwd=None: project_file)

    called_with = {}

    async def fake_run_orchestrator(ctx, start_from=None):
        called_with["session_id"] = ctx.session_id
        called_with["start_from"] = start_from

    monkeypatch.setattr(cli_app, "_run_orchestrator", fake_run_orchestrator)
    monkeypatch.setattr(cli_app, "get_settings", lambda: tmp_settings)

    await cli_app._resume_session(None)

    assert called_with.get("session_id") == session_id
