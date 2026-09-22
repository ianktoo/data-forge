"""Quality judging: source-reference detection, LLM judge parsing, fail-closed."""
from __future__ import annotations

import json
import uuid

import pytest
from sqlmodel import select

from dataforge.agents.base import PipelineContext
from dataforge.agents.quality import QualityAgent
from dataforge.generators.judge import Verdict, _parse_verdicts, find_source_reference
from dataforge.storage import DataFormat, ProcessedChunk, SyntheticSample, open_session

# -- Source-reference detection ----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # The exact failure seen in the live Ready.gov run.
        "The document emphasizes that homeowners insurance does not cover flood damage.",
        "According to the passage, you should evacuate early.",
        "As mentioned above, keep a battery-powered radio.",
        "This page lists the items for an emergency kit.",
        "The PDF titled 'Your Homeowners Insurance' explains coverage.",
        "The text describes three kinds of alerts.",
        "As stated in the guide, store water for three days.",
        # Missed by the first version; found by probing the live judge.
        "The guidance says storm surge is historically the leading cause.",
        "What does the guidance say is the leading cause of hurricane deaths?",
        "The information recommends keeping a three-day supply of water.",
        "What does the text recommend for pets?",
    ],
)
def test_source_references_are_caught(text):
    assert find_source_reference([{"role": "assistant", "content": text}])


@pytest.mark.parametrize(
    "text",
    [
        "Homeowners insurance does not cover flood damage.",
        "According to FEMA, you should store one gallon of water per person per day.",
        "Check the content of your emergency kit every six months.",
        "Stay away from the site of the fire until officials say it is safe.",
        "Text messages often get through when phone calls cannot.",
        "Turn Around, Don't Drown: never drive through flood water.",
        "A guide dog should have its own supplies in your kit.",
        "Follow the guidance of local officials during an evacuation.",
        "What does the National Weather Service recommend during a tornado watch?",
        "Share information with neighbors who may need help.",
        "Keep this information in a waterproof container.",
        "The section of road may flood quickly.",
    ],
)
def test_ordinary_language_is_not_flagged(text):
    assert find_source_reference([{"role": "assistant", "content": text}]) == ""


def test_reference_in_the_question_is_also_caught():
    msgs = [
        {"role": "user", "content": "What does the article say about tornado shelters?"},
        {"role": "assistant", "content": "Go to a basement or an interior room."},
    ]
    assert find_source_reference(msgs)


# -- Verdict parsing ---------------------------------------------------------


def test_parse_valid_verdicts():
    raw = '[{"score": 5, "grounded": true, "standalone": true, "reason": "good"},' \
          ' {"score": 2, "grounded": false, "standalone": true, "reason": "unsupported"}]'
    v = _parse_verdicts(raw, 2)
    assert v is not None and [x.score for x in v] == [5, 2]
    assert v[1].grounded is False


def test_parse_tolerates_markdown_fences():
    raw = '```json\n[{"score": 4, "grounded": true, "standalone": true, "reason": "ok"}]\n```'
    assert _parse_verdicts(raw, 1) is not None


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        '[{"score": 4, "grounded": true, "standalone": true}]',   # wrong count (expect 2)
        '[4, 5]',                                                  # not objects
        '[{"score": "high"}, {"score": 3}]',                       # bad score
    ],
)
def test_parse_rejects_malformed_output(raw):
    assert _parse_verdicts(raw, 2) is None


def test_score_is_clamped_and_booleans_must_be_true():
    v = _parse_verdicts('[{"score": 9, "grounded": "yes", "standalone": true}]', 1)
    assert v is not None
    assert v[0].score == 5
    assert v[0].grounded is False   # only a real JSON true counts


def test_verdict_rejection_reasons():
    assert Verdict(5, False, True, "").rejection_reason(4) == "judge: not grounded in source"
    assert Verdict(5, True, False, "").rejection_reason(4) == "judge: not standalone (refers to the source)"
    assert Verdict(3, True, True, "").rejection_reason(4) == "judge: score 3 < 4"
    assert Verdict(4, True, True, "").passes(4)


# -- QualityAgent integration (offline fake LLM) -----------------------------


class _Usage:
    total_calls = 0
    prompt_tokens = 0
    completion_tokens = 0
    cost_usd = 0.0
    errors = 0


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


def _fake_llm_factory(reply):
    """reply(user_prompt, n_examples) -> str"""

    class _LLM:
        usage = _Usage()
        calls = 0

        def __init__(self, **kwargs):
            pass

        async def complete(self, messages, **kwargs):
            type(self).calls += 1
            user = messages[-1]["content"]
            n = user.count("--- Example ")
            return _Resp(reply(user, n))

    return _LLM


def _seed(settings, samples: list[list[dict]], source="Floods are dangerous. Never drive through flood water."):
    sid = str(uuid.uuid4())
    with open_session(settings.db_path) as db:
        chunk = ProcessedChunk(
            session_id=sid, page_id=1, content=source, token_count=10,
            chunk_index=0, metadata_json="{}",
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


def _ctx(settings, sid, judge=True):
    return PipelineContext(
        session_id=sid, session_name="t", goal="g", format=DataFormat.qa,
        seed_urls=["https://x"], settings=settings, quality_threshold=0.1,
        quality_llm_judge=judge, quality_min_judge_score=4,
    )


def _qa(q, a):
    return [{"role": "user", "content": q}, {"role": "assistant", "content": a}]


GOOD = _qa("Why should you never drive through flood water during a storm?",
           "Moving flood water can sweep a vehicle away, so turn around and find another route.")
META = _qa("What does the guidance say about driving in floods overall?",
           "The document says never to drive through flood water because it is dangerous.")
WEAK = _qa("What are floods and why do they matter to people?",
           "Floods are events that can happen in many places and matter to many people.")


@pytest.fixture
def creds_ok(monkeypatch):
    monkeypatch.setattr("dataforge.cli.preflight.check_llm_credentials", lambda: (True, None))


async def test_judge_approves_good_and_rejects_weak(tmp_settings, monkeypatch, creds_ok):
    def reply(user, n):
        # Example order is GOOD then WEAK (META is removed before the judge).
        return json.dumps([
            {"score": 5, "grounded": True, "standalone": True, "reason": "good"},
            {"score": 2, "grounded": True, "standalone": True, "reason": "vague"},
        ][:n])

    llm_cls = _fake_llm_factory(reply)
    monkeypatch.setattr("dataforge.generators.LLMClient", llm_cls)
    sid = _seed(tmp_settings, [GOOD, META, WEAK])
    ctx = _ctx(tmp_settings, sid)
    await QualityAgent(ctx).run()

    with open_session(tmp_settings.db_path) as db:
        rows = {json.loads(r.messages_json)[1]["content"][:20]: r
                for r in db.exec(select(SyntheticSample).where(SyntheticSample.session_id == sid))}
    approved = [k for k, r in rows.items() if r.approved]
    assert approved == [GOOD[1]["content"][:20]]
    assert llm_cls.calls == 1, "samples from one chunk should share one judge call"


async def test_source_reference_is_rejected_without_calling_the_llm(tmp_settings, monkeypatch, creds_ok):
    llm_cls = _fake_llm_factory(lambda u, n: "[]")
    monkeypatch.setattr("dataforge.generators.LLMClient", llm_cls)
    sid = _seed(tmp_settings, [META])
    ctx = _ctx(tmp_settings, sid)
    await QualityAgent(ctx).run()
    assert ctx.approved_sample_ids == []
    assert llm_cls.calls == 0


async def test_source_reference_rejected_even_with_judge_off(tmp_settings):
    sid = _seed(tmp_settings, [META, GOOD])
    ctx = _ctx(tmp_settings, sid, judge=False)
    await QualityAgent(ctx).run()
    assert len(ctx.approved_sample_ids) == 1


async def test_unparsable_judge_output_fails_closed(tmp_settings, monkeypatch, creds_ok):
    """The old reviewer approved on error. The judge must not."""
    llm_cls = _fake_llm_factory(lambda u, n: "I think these look fine!")
    monkeypatch.setattr("dataforge.generators.LLMClient", llm_cls)
    sid = _seed(tmp_settings, [GOOD])
    ctx = _ctx(tmp_settings, sid)
    await QualityAgent(ctx).run()
    assert ctx.approved_sample_ids == []
    assert llm_cls.calls == 2, "should retry once before rejecting"


async def test_judge_api_error_fails_closed(tmp_settings, monkeypatch, creds_ok):
    class _Boom:
        usage = _Usage()

        def __init__(self, **kwargs):
            pass

        async def complete(self, messages, **kwargs):
            raise RuntimeError("500 from provider")

    monkeypatch.setattr("dataforge.generators.LLMClient", _Boom)
    sid = _seed(tmp_settings, [GOOD])
    ctx = _ctx(tmp_settings, sid)
    await QualityAgent(ctx).run()
    assert ctx.approved_sample_ids == []


async def test_judge_without_credentials_approves_nothing(tmp_settings, monkeypatch):
    monkeypatch.setattr("dataforge.cli.preflight.check_llm_credentials", lambda: (False, "OPENAI_API_KEY"))
    sid = _seed(tmp_settings, [GOOD])
    ctx = _ctx(tmp_settings, sid)
    await QualityAgent(ctx).run()
    assert ctx.approved_sample_ids == []


async def test_judge_score_becomes_quality_score(tmp_settings, monkeypatch, creds_ok):
    llm_cls = _fake_llm_factory(
        lambda u, n: json.dumps([{"score": 5, "grounded": True, "standalone": True, "reason": ""}] * n)
    )
    monkeypatch.setattr("dataforge.generators.LLMClient", llm_cls)
    sid = _seed(tmp_settings, [GOOD])
    ctx = _ctx(tmp_settings, sid)
    await QualityAgent(ctx).run()
    with open_session(tmp_settings.db_path) as db:
        row = db.exec(select(SyntheticSample).where(SyntheticSample.session_id == sid)).one()
    assert row.approved and row.quality_score == 1.0


def test_recipe_enables_judge_by_default(tmp_path):
    from dataforge.cli.recipe import load_recipe

    p = tmp_path / "r.yaml"
    p.write_text("name: x\nsource:\n  urls: [https://a.example]\n", encoding="utf-8")
    q = load_recipe(p).quality
    assert q.llm_judge is True and q.min_judge_score == 4


def test_generation_prompts_forbid_source_references():
    from dataforge.generators.templates import build_prompt

    for fmt in ("qa", "instruction", "conversation"):
        assert "Never refer to the source" in build_prompt("x", fmt, "g").system
