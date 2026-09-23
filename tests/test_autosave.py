"""Adapter test: Settings.autosave gates the orchestrator's checkpoint write."""
from __future__ import annotations

import json

from dataforge.agents.base import PipelineContext
from dataforge.agents.orchestrator import Orchestrator
from dataforge.storage import open_session
from dataforge.storage.models import DataFormat, PipelineSession


def _make_ctx(tmp_settings):
    return PipelineContext(
        session_id="autosave-test",
        session_name="test",
        goal="goal",
        format=DataFormat.qa,
        seed_urls=["https://example.com"],
        settings=tmp_settings,
        discovered_urls=["https://example.com/a"],
    )


def test_checkpoint_writes_when_autosave_enabled(tmp_settings):
    tmp_settings.autosave = True
    ctx = _make_ctx(tmp_settings)
    orch = Orchestrator(ctx)
    orch._init_session()

    orch._checkpoint()

    with open_session(tmp_settings.db_path) as db:
        session = db.get(PipelineSession, ctx.session_id)
        assert json.loads(session.config_json)["discovered"] == 1


def test_checkpoint_skipped_when_autosave_disabled(tmp_settings):
    tmp_settings.autosave = False
    ctx = _make_ctx(tmp_settings)
    orch = Orchestrator(ctx)
    orch._init_session()

    orch._checkpoint()

    with open_session(tmp_settings.db_path) as db:
        session = db.get(PipelineSession, ctx.session_id)
        # _init_session records the output folder (#40); with autosave off the
        # checkpoint must add none of its counters.
        assert set(session.config()) == {"output_dir"}
