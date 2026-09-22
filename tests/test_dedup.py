"""Deduplication: full-content exact hash + per-chunk near-duplicate detection.

Covers issue #9: the old fingerprint hashed only the first 200 chars, which
both missed near-duplicate paraphrases (the actual output shape n_per_chunk>1
produces) and could false-positive on two different samples that happened to
share an opening sentence.
"""
from __future__ import annotations

import json
import uuid

from dataforge.agents.base import PipelineContext
from dataforge.agents.quality import QualityAgent
from dataforge.storage import DataFormat, ProcessedChunk, SyntheticSample, open_session


def _qa(q, a):
    return [{"role": "user", "content": q}, {"role": "assistant", "content": a}]


def _seed(settings, samples: list[list[dict]], same_chunk=True):
    sid = str(uuid.uuid4())
    with open_session(settings.db_path) as db:
        if same_chunk:
            chunk = ProcessedChunk(
                session_id=sid, page_id=1, content="Floods are dangerous.",
                token_count=10, chunk_index=0, metadata_json="{}",
            )
            db.add(chunk)
            db.commit()
            db.refresh(chunk)
            chunk_ids = [chunk.id] * len(samples)
        else:
            chunk_ids = []
            for i in range(len(samples)):
                chunk = ProcessedChunk(
                    session_id=sid, page_id=1, content=f"Passage {i}.",
                    token_count=10, chunk_index=i, metadata_json="{}",
                )
                db.add(chunk)
                db.commit()
                db.refresh(chunk)
                chunk_ids.append(chunk.id)

        for msgs, cid in zip(samples, chunk_ids):
            db.add(SyntheticSample(
                session_id=sid, chunk_id=cid, format="qa", system_prompt="",
                messages_json=json.dumps(msgs), quality_score=0.0, approved=False,
            ))
        db.commit()
    return sid


def _ctx(settings, sid, near_dup_threshold=0.85):
    return PipelineContext(
        session_id=sid, session_name="t", goal="g", format=DataFormat.qa,
        seed_urls=["https://x"], settings=settings, quality_threshold=0.1,
        quality_llm_judge=False, quality_near_dup_threshold=near_dup_threshold,
    )


async def test_exact_full_content_duplicate_is_rejected(tmp_settings):
    a = _qa("Why avoid flood water while driving?",
            "Moving flood water can sweep a car away in seconds, so never drive through it.")
    b = _qa("Why avoid flood water while driving?",
            "Moving flood water can sweep a car away in seconds, so never drive through it.")
    sid = _seed(tmp_settings, [a, b])
    ctx = _ctx(tmp_settings, sid)
    await QualityAgent(ctx).run()
    assert len(ctx.approved_sample_ids) == 1


async def test_same_opening_different_content_is_not_a_false_positive():
    """Two answers sharing an identical >200-char opening but differing after
    that point must NOT be treated as duplicates (the old prefix-hash bug)."""
    shared_opening = (
        "According to official emergency preparedness guidance published by "
        "federal and state agencies, every household should maintain a basic "
        "emergency supply kit that is regularly checked, restocked, and kept "
        "in an easily accessible location near the main exit. "
    )
    assert len(shared_opening) > 200
    a = _qa("What should a basic emergency kit contain?",
            shared_opening + "Include water, non-perishable food, and a flashlight.")
    b = _qa("How often should you check your kit?",
            shared_opening + "Check it every six months and replace expired items.")
    # (Exercised indirectly below via the full pipeline, since fingerprinting
    # is otherwise a private implementation detail.)
    from dataforge.agents.quality import QualityAgent as _QA
    agent = _QA.__new__(_QA)
    fp_a = _QA._fingerprint(agent, a)
    fp_b = _QA._fingerprint(agent, b)
    assert fp_a != fp_b, "full-content hash must not collide on a shared prefix"


async def test_near_duplicate_paraphrases_from_same_chunk_are_caught(tmp_settings):
    """The normal n_per_chunk>1 output shape: same fact, different wording."""
    a = _qa("Why should you never drive through flood water?",
            "Because moving flood water can sweep your vehicle away, so you should never drive through it.")
    b = _qa("What is the reason to avoid driving through flood water?",
            "You should never drive through flood water because moving water can sweep your vehicle away.")
    sid = _seed(tmp_settings, [a, b], same_chunk=True)
    ctx = _ctx(tmp_settings, sid, near_dup_threshold=0.5)
    await QualityAgent(ctx).run()
    assert len(ctx.approved_sample_ids) == 1, "near-paraphrase should be rejected, keeping one"


async def test_similar_wording_from_different_chunks_is_not_flagged(tmp_settings):
    """The near-dup check is scoped to one chunk's samples — two genuinely
    independent chunks producing similar phrasing by coincidence must not be
    cross-penalized."""
    a = _qa("Why should you never drive through flood water?",
            "Because moving flood water can sweep your vehicle away, so you should never drive through it.")
    b = _qa("What is the reason to avoid driving through flood water?",
            "You should never drive through flood water because moving water can sweep your vehicle away.")
    sid = _seed(tmp_settings, [a, b], same_chunk=False)
    ctx = _ctx(tmp_settings, sid, near_dup_threshold=0.5)
    await QualityAgent(ctx).run()
    assert len(ctx.approved_sample_ids) == 2


async def test_distinct_samples_from_same_chunk_both_pass(tmp_settings):
    a = _qa("What should be in an emergency kit?",
            "Pack water, non-perishable food, a flashlight, and a first aid kit.")
    b = _qa("How long should stored water last per person?",
            "Store at least one gallon of water per person per day for three days.")
    sid = _seed(tmp_settings, [a, b], same_chunk=True)
    ctx = _ctx(tmp_settings, sid)
    await QualityAgent(ctx).run()
    assert len(ctx.approved_sample_ids) == 2
