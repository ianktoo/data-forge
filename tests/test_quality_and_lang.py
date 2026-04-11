"""Tests for quality threshold and language-group detection."""
from __future__ import annotations

import json
import uuid

import pytest

from dataforge.agents.base import PipelineContext
from dataforge.agents.quality import QualityAgent
from dataforge.storage import DataFormat, SyntheticSample, init_db, open_session


# ── Language group detection ───────────────────────────────────────────────────

# Import the private helper directly from app module
from dataforge.cli.app import _detect_language_groups


def test_detect_language_groups_path_prefix():
    urls = [
        "https://example.com/en/about",
        "https://example.com/en/contact",
        "https://example.com/fr/about",
        "https://example.com/de/about",
    ]
    groups = _detect_language_groups(urls)
    assert groups == {"en": 2, "fr": 1, "de": 1}


def test_detect_language_groups_query_param():
    urls = [
        "https://example.com/page?lang=en",
        "https://example.com/page?lang=fr",
        "https://example.com/page?language=es",
    ]
    groups = _detect_language_groups(urls)
    assert groups.get("en") == 1
    assert groups.get("fr") == 1
    assert groups.get("es") == 1


def test_detect_language_groups_no_match():
    urls = [
        "https://example.com/about",
        "https://example.com/contact",
    ]
    groups = _detect_language_groups(urls)
    assert groups == {}


def test_detect_language_groups_mixed():
    urls = [
        "https://example.com/en/page",
        "https://example.com/fr/page",
        "https://example.com/no-locale",
    ]
    groups = _detect_language_groups(urls)
    assert "en" in groups
    assert "fr" in groups
    assert "no-locale" not in groups


# ── Quality threshold ──────────────────────────────────────────────────────────

def _make_sample(session_id: str, messages: list[dict]) -> dict:
    return dict(
        session_id=session_id,
        chunk_id=1,
        format="qa",
        system_prompt="",
        messages_json=json.dumps(messages),
        quality_score=0.0,
        approved=False,
    )


async def test_quality_threshold_default(tmp_settings):
    """Default threshold 0.5: short answers are rejected, long ones approved."""
    session_id = str(uuid.uuid4())

    with open_session(tmp_settings.db_path) as db:
        # Short answer — word count < 10 → score < 0.5
        db.add(SyntheticSample(**_make_sample(session_id, [
            {"role": "user", "content": "What?"},
            {"role": "assistant", "content": "Yes."},
        ])))
        # Long answer — word count >= 10 → score ≈ 1.0
        db.add(SyntheticSample(**_make_sample(session_id, [
            {"role": "user", "content": "Explain the concept of fine-tuning?"},
            {"role": "assistant", "content": " ".join(["word"] * 15)},
        ])))
        db.commit()

    ctx = PipelineContext(
        session_id=session_id,
        session_name="test",
        goal="test",
        format=DataFormat.qa,
        seed_urls=["https://example.com"],
        settings=tmp_settings,
        quality_threshold=0.5,
    )
    agent = QualityAgent(ctx)
    await agent.run()

    assert len(ctx.approved_sample_ids) == 1


async def test_quality_threshold_strict(tmp_settings):
    """Threshold 0.8: only the long answer qualifies."""
    session_id = str(uuid.uuid4())

    with open_session(tmp_settings.db_path) as db:
        # Medium answer — score around 0.5–0.7
        db.add(SyntheticSample(**_make_sample(session_id, [
            {"role": "user", "content": "What is X?"},
            {"role": "assistant", "content": " ".join(["word"] * 7)},
        ])))
        # Long answer — score ≈ 1.0
        db.add(SyntheticSample(**_make_sample(session_id, [
            {"role": "user", "content": "Explain fine-tuning in detail please?"},
            {"role": "assistant", "content": " ".join(["word"] * 20)},
        ])))
        db.commit()

    ctx = PipelineContext(
        session_id=session_id,
        session_name="test",
        goal="test",
        format=DataFormat.qa,
        seed_urls=["https://example.com"],
        settings=tmp_settings,
        quality_threshold=0.8,
    )
    agent = QualityAgent(ctx)
    await agent.run()

    # With threshold=0.8, the medium answer should be rejected
    assert len(ctx.approved_sample_ids) == 1


async def test_quality_threshold_lenient(tmp_settings):
    """Threshold 0.1: even a short answer is approved."""
    session_id = str(uuid.uuid4())

    with open_session(tmp_settings.db_path) as db:
        db.add(SyntheticSample(**_make_sample(session_id, [
            {"role": "user", "content": "What?"},
            {"role": "assistant", "content": "Yes it is."},
        ])))
        db.commit()

    ctx = PipelineContext(
        session_id=session_id,
        session_name="test",
        goal="test",
        format=DataFormat.qa,
        seed_urls=["https://example.com"],
        settings=tmp_settings,
        quality_threshold=0.1,
    )
    agent = QualityAgent(ctx)
    await agent.run()

    assert len(ctx.approved_sample_ids) == 1
