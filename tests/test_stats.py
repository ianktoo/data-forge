"""dataforge stats — dataset profiling/statistics tooling (issue #10).

Exercises compute_session_stats() directly against seeded DB rows (same
lightweight pattern as test_judge.py/test_dedup.py) plus a synthetic export
directory for the realized-split check, rather than a full e2e pipeline run —
the statistics layer is pure read-over-already-persisted-data, so it doesn't
need a live crawl to exercise meaningfully.
"""
from __future__ import annotations

import json
import uuid

from dataforge.agents.base import PipelineContext
from dataforge.agents.quality import QualityAgent
from dataforge.storage import DataFormat, ProcessedChunk, SyntheticSample, open_session
from dataforge.storage.stats import compute_session_stats


def _qa(q, a):
    return [{"role": "user", "content": q}, {"role": "assistant", "content": a}]


def _seed(settings, samples: list[list[dict]]):
    sid = str(uuid.uuid4())
    with open_session(settings.db_path) as db:
        chunk = ProcessedChunk(
            session_id=sid, page_id=1, content="Floods are dangerous.",
            token_count=10, chunk_index=0, metadata_json="{}",
        )
        db.add(chunk)
        db.commit()
        db.refresh(chunk)
        for msgs in samples:
            db.add(SyntheticSample(
                session_id=sid, chunk_id=chunk.id, format="qa", system_prompt="",
                messages_json=json.dumps(msgs), quality_score=0.0, approved=False,
            ))
        db.commit()
    return sid


async def test_stats_reports_approval_rate_and_rejection_breakdown(tmp_settings):
    good = _qa("Why should you never drive through flood water?",
               "Moving flood water can sweep your vehicle away in seconds.")
    weak = _qa("Floods?", "They happen.")
    sid = _seed(tmp_settings, [good, weak])
    ctx = PipelineContext(
        session_id=sid, session_name="t", goal="g", format=DataFormat.qa,
        seed_urls=["https://x"], settings=tmp_settings, quality_threshold=0.5,
        quality_llm_judge=False,
    )
    await QualityAgent(ctx).run()

    result = compute_session_stats(
        tmp_settings.db_path, sid, tmp_settings.session_dir(sid)
    )
    assert result.total_samples == 2
    assert result.approved == 1
    assert result.rejected == 1
    assert result.rejection_reasons == {"below heuristic threshold": 1}


async def test_stats_rejection_reason_is_persisted_and_queryable(tmp_settings):
    """The rejection reason must survive to the DB row, not just the
    in-memory quality-stage log — that's the actual gap being fixed."""
    a = _qa("Why should you never drive through flood water?",
            "Moving flood water can sweep your vehicle away.")
    b = _qa("Why should you never drive through flood water?",
            "Moving flood water can sweep your vehicle away.")
    sid = _seed(tmp_settings, [a, b])
    ctx = PipelineContext(
        session_id=sid, session_name="t", goal="g", format=DataFormat.qa,
        seed_urls=["https://x"], settings=tmp_settings, quality_threshold=0.1,
        quality_llm_judge=False,
    )
    await QualityAgent(ctx).run()

    from sqlmodel import select
    with open_session(tmp_settings.db_path) as db:
        rows = db.exec(select(SyntheticSample).where(SyntheticSample.session_id == sid)).all()
    reasons = sorted(r.rejection_reason for r in rows)
    assert reasons == ["", "exact duplicate"]


async def test_stats_length_and_score_distribution(tmp_settings):
    a = _qa("What should be in an emergency kit?",
            "Pack water, non-perishable food, a flashlight, and a first aid kit.")
    b = _qa("How long should stored water last per person?",
            "Store at least one gallon of water per person per day for three days total.")
    sid = _seed(tmp_settings, [a, b])
    ctx = PipelineContext(
        session_id=sid, session_name="t", goal="g", format=DataFormat.qa,
        seed_urls=["https://x"], settings=tmp_settings, quality_threshold=0.1,
        quality_llm_judge=False,
    )
    await QualityAgent(ctx).run()

    result = compute_session_stats(
        tmp_settings.db_path, sid, tmp_settings.session_dir(sid)
    )
    assert result.answer_length.count == 2
    assert result.answer_length.min > 0
    assert result.score.count == 2
    assert sum(result.score.histogram) == 2


def test_stats_reads_realized_split_from_latest_export(tmp_settings):
    sid = str(uuid.uuid4())
    export_dir = tmp_settings.session_dir(sid) / "exports" / "20260101_000000"
    export_dir.mkdir(parents=True)
    (export_dir / "dataset_train.jsonl").write_text(
        "\n".join(json.dumps({"messages": []}) for _ in range(8)) + "\n", encoding="utf-8"
    )
    (export_dir / "dataset_validation.jsonl").write_text(
        json.dumps({"messages": []}) + "\n", encoding="utf-8"
    )
    (export_dir / "dataset_test.jsonl").write_text(
        json.dumps({"messages": []}) + "\n", encoding="utf-8"
    )

    result = compute_session_stats(tmp_settings.db_path, sid, tmp_settings.session_dir(sid))
    assert result.split_counts == {"train": 8, "validation": 1, "test": 1}


def test_stats_split_counts_none_when_never_exported(tmp_settings):
    sid = str(uuid.uuid4())
    result = compute_session_stats(tmp_settings.db_path, sid, tmp_settings.session_dir(sid))
    assert result.split_counts is None
