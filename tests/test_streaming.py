"""Streaming pipeline tests — overlap, idempotent resume, and failure isolation."""
from __future__ import annotations

import asyncio

import pytest

from dataforge.agents.base import PipelineContext
from dataforge.agents.streaming import StreamingAgent
from dataforge.config.settings import Settings
from dataforge.storage import (
    DataFormat,
    DiscoveredURL,
    ProcessedChunk,
    ScrapedPage,
    SyntheticSample,
    init_db,
    open_session,
)

_PAGE_HTML = """
<html><head><title>Flood Safety {n}</title></head><body><article>
<h1>Flood Safety {n}</h1>
<p>Floods are among the most common and costly natural disasters in the United
States. Know your flood risk before a storm arrives, and sign up for your
community's warning system so alerts reach you quickly and reliably.</p>
<p>Build an emergency kit containing water, non-perishable food, medication, a
flashlight and a battery powered radio. Keep copies of important documents in a
waterproof container, and plan an evacuation route to higher ground in advance.</p>
<p>Never walk, swim or drive through flood waters. Just six inches of moving
water can knock a person down, and one foot of water can sweep a vehicle away.
Turn around, do not drown, and wait for official all-clear guidance.</p>
</article></body></html>
"""


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings(
        output_dir=tmp_path / "out",
        db_path=tmp_path / "t.db",
        rate_limit=1000.0,      # no throttling in tests
        chunk_size=128,
        chunk_overlap=16,
        stream_generate_workers=2,
        stream_queue_size=8,
    )
    s.output_dir.mkdir(parents=True, exist_ok=True)
    init_db(s.db_path)
    return s


def _ctx(settings: Settings, urls: list[str]) -> PipelineContext:
    ctx = PipelineContext(
        session_id="sess-stream",
        session_name="stream test",
        goal="flood preparedness",
        format=DataFormat.qa,
        seed_urls=urls,
        settings=settings,
        n_per_chunk=2,
    )
    ctx.selected_urls = urls
    with open_session(settings.db_path) as db:
        for u in urls:
            db.add(DiscoveredURL(session_id=ctx.session_id, url=u, source="manual"))
        db.commit()
    return ctx


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.status_code = 200


class _FakeClient:
    """Stands in for HTTPClient: serves canned HTML, records call order."""

    def __init__(self, urls: list[str], delay: float = 0.0) -> None:
        self._urls = urls
        self._delay = delay
        self.fetched: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_safe(self, url: str):
        if self._delay:
            await asyncio.sleep(self._delay)
        self.fetched.append(url)
        if "broken" in url:
            return None
        n = self._urls.index(url) if url in self._urls else 0
        return _FakeResponse(_PAGE_HTML.format(n=n))


@pytest.fixture
def patched(monkeypatch):
    """Patch the network client and LLM so the pipeline runs offline."""

    def _install(urls, *, delay: float = 0.0, gen=None, gen_enabled: bool = True):
        client = _FakeClient(urls, delay)
        monkeypatch.setattr(
            "dataforge.agents.streaming.HTTPClient", lambda *a, **k: client
        )
        monkeypatch.setattr(
            StreamingAgent, "_generation_available", lambda self: gen_enabled
        )
        monkeypatch.setattr(
            "dataforge.agents.streaming.LLMClient", lambda **k: _FakeLLM()
        )
        if gen is not None:
            monkeypatch.setattr("dataforge.agents.streaming.generate_from_chunk", gen)
        return client

    return _install


class _FakeUsage:
    total_calls = 0
    prompt_tokens = 0
    completion_tokens = 0
    cost_usd = 0.0
    errors = 0


class _FakeLLM:
    usage = _FakeUsage()


def _sample(chunk_id: int):
    from dataforge.generators.synthetic import GeneratedSample

    return GeneratedSample(
        chunk_id=chunk_id,
        format="qa",
        system_prompt="sys",
        messages=[
            {"role": "user", "content": "What should I do before a flood arrives?"},
            {
                "role": "assistant",
                "content": (
                    "Know your flood risk, sign up for community alerts, build an "
                    "emergency kit and plan an evacuation route to higher ground."
                ),
            },
        ],
        raw_response="[]",
    )


# -- Tests -------------------------------------------------------------------


async def test_stream_produces_samples_end_to_end(settings, patched):
    urls = [f"https://ready.gov/hazard/flood-{i}" for i in range(6)]

    async def gen(llm, record, **kw):
        return [_sample(record.chunk_id)]

    patched(urls, gen=gen)
    agent = StreamingAgent(_ctx(settings, urls))
    ctx = await agent.run()

    assert agent.counters.scraped == 6
    assert agent.counters.chunks > 0
    assert agent.counters.samples == agent.counters.chunks
    assert len(ctx.synthetic_sample_ids) == agent.counters.samples

    with open_session(settings.db_path) as db:
        from sqlmodel import select

        assert len(db.exec(select(ScrapedPage)).all()) == 6
        assert len(db.exec(select(SyntheticSample)).all()) == agent.counters.samples


async def test_generation_overlaps_scraping(settings, patched, monkeypatch):
    """The point of the feature: generation starts before the crawl finishes.

    Modelled on a real crawl, where the URL list is far longer than the scrape
    pool and the crawl therefore has a long tail. With as many workers as URLs
    everything fetches at once and there is nothing to overlap with, so the
    pool is pinned small here deliberately.
    """
    monkeypatch.setattr("dataforge.agents.streaming.concurrency_ceiling", lambda: 2)

    urls = [f"https://ready.gov/hazard/flood-{i}" for i in range(20)]
    first_generation_at: list[int] = []
    client_ref = {}

    async def gen(llm, record, **kw):
        # Record how many URLs had been fetched when generation first ran.
        if not first_generation_at:
            first_generation_at.append(len(client_ref["c"].fetched))
        return [_sample(record.chunk_id)]

    client_ref["c"] = patched(urls, delay=0.01, gen=gen)
    agent = StreamingAgent(_ctx(settings, urls))
    await agent.run()

    assert first_generation_at, "generation never ran"
    assert first_generation_at[0] < len(urls), (
        f"generation only began after all {len(urls)} URLs were fetched "
        f"({first_generation_at[0]}) - stages are still serialised"
    )
    # Under the batch orchestrator this number would be exactly len(urls);
    # streaming should start generating within the first handful of pages.
    assert first_generation_at[0] <= len(urls) // 2, (
        f"generation started late ({first_generation_at[0]}/{len(urls)} fetched) "
        "- overlap is much weaker than expected"
    )
    assert agent.counters.samples == agent.counters.chunks


async def test_failed_page_does_not_kill_the_pool(settings, patched):
    urls = [
        "https://ready.gov/ok-1",
        "https://ready.gov/broken",   # _FakeClient returns None for this
        "https://ready.gov/ok-2",
    ]

    async def gen(llm, record, **kw):
        return [_sample(record.chunk_id)]

    patched(urls, gen=gen)
    agent = StreamingAgent(_ctx(settings, urls))
    await agent.run()

    assert agent.counters.scraped == 2
    assert agent.counters.scrape_failed == 1
    assert agent.counters.samples > 0


async def test_generation_error_drains_instead_of_deadlocking(settings, patched):
    """A fatal LLM error must not block the upstream pools on a full queue."""
    from dataforge.utils.errors import LLMConnectionError

    urls = [f"https://ready.gov/hazard/flood-{i}" for i in range(6)]

    async def gen(llm, record, **kw):
        raise LLMConnectionError("provider unreachable")

    patched(urls, gen=gen)
    agent = StreamingAgent(_ctx(settings, urls))

    ctx = await asyncio.wait_for(agent.run(), timeout=30)

    assert agent._gen_fatal
    assert agent.counters.samples == 0
    # Scraping and chunking still completed — that work is not lost.
    assert agent.counters.scraped == 6
    assert agent.counters.chunks > 0
    assert len(ctx.processed_chunk_ids) == agent.counters.chunks


async def test_no_llm_credentials_still_scrapes_and_chunks(settings, patched):
    urls = [f"https://ready.gov/hazard/flood-{i}" for i in range(4)]
    patched(urls, gen_enabled=False)
    agent = StreamingAgent(_ctx(settings, urls))
    await agent.run()

    assert agent.counters.scraped == 4
    assert agent.counters.chunks > 0
    assert agent.counters.samples == 0


async def test_resume_skips_completed_work(settings, patched):
    """Second run re-scrapes nothing and only generates the missing chunks."""
    urls = [f"https://ready.gov/hazard/flood-{i}" for i in range(4)]

    async def gen(llm, record, **kw):
        return [_sample(record.chunk_id)]

    # First pass: scrape + chunk only (no generation).
    patched(urls, gen_enabled=False)
    ctx = _ctx(settings, urls)
    first = StreamingAgent(ctx)
    await first.run()
    assert first.counters.chunks > 0
    assert first.counters.samples == 0

    with open_session(settings.db_path) as db:
        from sqlmodel import select

        chunk_total = len(db.exec(select(ProcessedChunk)).all())

    # Second pass on the same session, now with generation available.
    client = patched(urls, gen=gen, gen_enabled=True)
    resumed = StreamingAgent(_resume_ctx(settings, urls))
    await resumed.run()

    assert client.fetched == [], "resume re-fetched already-scraped URLs"
    assert resumed.counters.scraped == 0
    assert resumed.counters.samples == chunk_total

    with open_session(settings.db_path) as db:
        from sqlmodel import select

        # No duplicate pages or chunks were created.
        assert len(db.exec(select(ScrapedPage)).all()) == 4
        assert len(db.exec(select(ProcessedChunk)).all()) == chunk_total


def _resume_ctx(settings: Settings, urls: list[str]) -> PipelineContext:
    """Fresh context for the same session id — mirrors `dataforge resume`."""
    ctx = PipelineContext(
        session_id="sess-stream",
        session_name="stream test",
        goal="flood preparedness",
        format=DataFormat.qa,
        seed_urls=urls,
        settings=settings,
        n_per_chunk=2,
    )
    ctx.selected_urls = urls
    return ctx
