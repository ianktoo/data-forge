"""A missing or invalid API key must stop generation once, not once per chunk."""
from __future__ import annotations

import litellm
import pytest

from dataforge.generators.llm import LLMClient
from dataforge.generators.synthetic import generate_batch, generate_from_chunk
from dataforge.processors.formatter import DataRecord
from dataforge.utils.errors import MissingCredentialError


def _record(i: int) -> DataRecord:
    return DataRecord(
        chunk_id=i, source_url="https://x", title="t",
        content="Floods are dangerous.", token_count=5,
    )


@pytest.fixture
def auth_failure(monkeypatch):
    calls = {"n": 0}

    async def _fail(**kwargs):
        calls["n"] += 1
        raise Exception("AuthenticationError: Incorrect API key provided")

    monkeypatch.setattr(litellm, "acompletion", _fail)
    return calls


async def test_generate_from_chunk_propagates_missing_credential(auth_failure):
    with pytest.raises(MissingCredentialError):
        await generate_from_chunk(LLMClient(), _record(1), format="qa", goal="g", n_per_chunk=1)


async def test_auth_failure_is_not_retried(auth_failure):
    with pytest.raises(MissingCredentialError):
        await LLMClient().complete([{"role": "user", "content": "hi"}])
    assert auth_failure["n"] == 1


async def test_generate_batch_stops_on_first_auth_failure(auth_failure):
    records = [_record(i) for i in range(50)]
    with pytest.raises(MissingCredentialError):
        async for _ in generate_batch(
            LLMClient(), records, format="qa", goal="g", n_per_chunk=1, concurrency=3
        ):
            pass
    # Only the calls already in flight may run; the rest are cancelled.
    assert auth_failure["n"] <= 3
