"""LLM spend/call budget cap — BudgetTracker unit tests and end-to-end skip behavior.

Covers issue #8: a run must stop dispatching new generation/judge LLM calls
once a configured cap is reached, without crashing, and without ever making
the underlying network call for a call that's already over budget.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlmodel import select

from dataforge.agents.base import PipelineContext
from dataforge.agents.quality import QualityAgent
from dataforge.generators.llm import BudgetTracker, LLMClient
from dataforge.generators.synthetic import generate_from_chunk
from dataforge.processors.formatter import DataRecord
from dataforge.storage import DataFormat, ProcessedChunk, SyntheticSample, open_session
from dataforge.utils.errors import BudgetExceededError

# -- BudgetTracker -------------------------------------------------------------


async def test_try_reserve_respects_max_calls():
    budget = BudgetTracker(max_calls=2)
    assert await budget.try_reserve() is True
    assert await budget.try_reserve() is True
    assert await budget.try_reserve() is False
    assert budget.skipped == 1
    assert budget.call_count == 2


async def test_try_reserve_respects_max_cost():
    budget = BudgetTracker(max_cost_usd=0.01)
    assert await budget.try_reserve() is True
    await budget.record_cost(0.02)
    assert await budget.try_reserve() is False
    assert budget.exhausted is True


async def test_unlimited_budget_never_exhausted():
    budget = BudgetTracker()
    for _ in range(50):
        assert await budget.try_reserve() is True
    assert budget.exhausted is False


# -- LLMClient wiring ------------------------------------------------------------


async def test_llm_client_raises_budget_exceeded_without_network_call():
    budget = BudgetTracker(max_calls=0)
    client = LLMClient(budget=budget)
    with pytest.raises(BudgetExceededError):
        await client.complete([{"role": "user", "content": "hi"}])
    assert budget.skipped == 1
    assert budget.call_count == 0


async def test_llm_client_without_budget_is_unaffected_by_cap_logic():
    client = LLMClient()
    assert client.budget is None


# -- Generation stage: skip, don't crash -----------------------------------------


async def test_generate_from_chunk_skips_cleanly_when_budget_exhausted():
    budget = BudgetTracker(max_calls=0)
    client = LLMClient(budget=budget)
    record = DataRecord(
        chunk_id=1, source_url="https://x", title="t",
        content="Floods are dangerous.", token_count=5,
    )
    samples = await generate_from_chunk(client, record, format="qa", goal="g", n_per_chunk=1)
    assert samples == []


# -- Quality/judge stage: shared budget across chunks ----------------------------


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
    class _LLM:
        usage = _Usage()
        calls = 0

        def __init__(self, **kwargs):
            self.budget = kwargs.get("budget")

        async def complete(self, messages, **kwargs):
            if self.budget is not None and not await self.budget.try_reserve():
                raise BudgetExceededError(self.budget.max_calls, self.budget.max_cost_usd)
            type(self).calls += 1
            user = messages[-1]["content"]
            n = user.count("--- Example ")
            return _Resp(reply(user, n))

    return _LLM


def _qa(q, a):
    return [{"role": "user", "content": q}, {"role": "assistant", "content": a}]


GOOD = _qa("Why should you never drive through flood water during a storm?",
           "Moving flood water can sweep a vehicle away, so turn around and find another route.")


def _seed_two_chunks(settings):
    sid = str(uuid.uuid4())
    with open_session(settings.db_path) as db:
        for i in range(2):
            chunk = ProcessedChunk(
                session_id=sid, page_id=1, content=f"Source passage {i}.",
                token_count=10, chunk_index=i, metadata_json="{}",
            )
            db.add(chunk)
            db.commit()
            db.refresh(chunk)
            db.add(SyntheticSample(
                session_id=sid, chunk_id=chunk.id, format="qa", system_prompt="",
                messages_json=json.dumps(GOOD), quality_score=0.0, approved=False,
            ))
        db.commit()
    return sid


@pytest.fixture
def creds_ok(monkeypatch):
    monkeypatch.setattr("dataforge.cli.preflight.check_llm_credentials", lambda: (True, None))


async def test_judge_stops_dispatching_once_call_cap_hit(tmp_settings, monkeypatch, creds_ok):
    """Two chunks need judging; a cap of 1 call must leave the second unjudged
    (fails closed) instead of crashing the quality stage."""
    def reply(user, n):
        return json.dumps([{"score": 5, "grounded": True, "standalone": True, "reason": "good"}] * n)

    llm_cls = _fake_llm_factory(reply)
    monkeypatch.setattr("dataforge.generators.LLMClient", llm_cls)
    sid = _seed_two_chunks(tmp_settings)
    ctx = PipelineContext(
        session_id=sid, session_name="t", goal="g", format=DataFormat.qa,
        seed_urls=["https://x"], settings=tmp_settings, quality_threshold=0.1,
        quality_llm_judge=True, quality_min_judge_score=4, max_llm_calls=1,
    )
    await QualityAgent(ctx).run()

    assert llm_cls.calls == 1, "only one chunk's judge call should have been dispatched"
    with open_session(tmp_settings.db_path) as db:
        rows = db.exec(
            select(SyntheticSample).where(SyntheticSample.session_id == sid)
        ).all()
    approved = sum(1 for r in rows if r.approved)
    rejected_unjudged = sum(1 for r in rows if not r.approved)
    assert approved == 1
    assert rejected_unjudged == 1, "the chunk over budget must fail closed, not crash the run"
