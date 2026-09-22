"""HTTP error handling: which statuses retry, which don't, and Crawl-delay."""
from __future__ import annotations

import httpx
import pytest
import respx

from dataforge.collectors.http import (
    HTTPClient,
    RetryableHTTPError,
    _parse_retry_after,
    _robots_cache,
)
from dataforge.utils import RateLimiter

ROBOTS_OPEN = "User-agent: *\nAllow: /\n"


@pytest.fixture(autouse=True)
def _clear_caches():
    _robots_cache.clear()
    yield
    _robots_cache.clear()


@pytest.fixture
def limiter():
    return RateLimiter(default_rps=1000.0)   # no real throttling in tests


# -- Retry-After parsing -----------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, 0.0),
        ("", 0.0),
        ("30", 30.0),
        ("  12  ", 12.0),
        ("-5", 0.0),          # clamped, never negative
        ("garbage", 0.0),
    ],
)
def test_parse_retry_after(value, expected):
    assert _parse_retry_after(value) == expected


def test_parse_retry_after_accepts_http_date():
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    future = datetime.now(UTC) + timedelta(seconds=60)
    assert 30 < _parse_retry_after(format_datetime(future)) <= 61


# -- Status handling ---------------------------------------------------------


@respx.mock
async def test_permanent_404_is_not_retried(limiter):
    respx.get("https://x.test/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_OPEN)
    )
    route = respx.get("https://x.test/gone").mock(return_value=httpx.Response(404))

    async with HTTPClient(limiter) as c:
        assert await c.get_safe("https://x.test/gone") is None

    # Exactly one attempt: retrying a 404 only wastes crawl budget.
    assert route.call_count == 1


@respx.mock
async def test_403_is_not_retried(limiter):
    respx.get("https://x.test/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_OPEN)
    )
    route = respx.get("https://x.test/forbidden").mock(return_value=httpx.Response(403))

    async with HTTPClient(limiter) as c:
        assert await c.get_safe("https://x.test/forbidden") is None
    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_transient_statuses_are_retried_then_give_up(limiter, status):
    respx.get("https://x.test/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_OPEN)
    )
    route = respx.get("https://x.test/flaky").mock(return_value=httpx.Response(status))

    async with HTTPClient(limiter) as c:
        assert await c.get_safe("https://x.test/flaky") is None

    # stop_after_attempt(3)
    assert route.call_count == 3


@respx.mock
async def test_transient_status_then_success_recovers(limiter):
    respx.get("https://x.test/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_OPEN)
    )
    responses = [
        httpx.Response(503),
        httpx.Response(200, html="<html><body><p>recovered</p></body></html>"),
    ]
    route = respx.get("https://x.test/page").mock(side_effect=responses)

    async with HTTPClient(limiter) as c:
        resp = await c.get("https://x.test/page")

    assert resp.status_code == 200
    assert route.call_count == 2


@respx.mock
async def test_retryable_error_carries_its_status(limiter):
    respx.get("https://x.test/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_OPEN)
    )
    respx.get("https://x.test/busy").mock(return_value=httpx.Response(429))

    async with HTTPClient(limiter) as c:
        with pytest.raises(RetryableHTTPError) as exc:
            await c.get("https://x.test/busy")
    assert exc.value.status == 429


@respx.mock
async def test_retry_after_header_is_honoured(limiter, monkeypatch):
    """A Retry-After must actually delay the next attempt."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("dataforge.collectors.http.asyncio.sleep", fake_sleep)

    respx.get("https://x.test/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_OPEN)
    )
    respx.get("https://x.test/busy").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "7"})
    )

    async with HTTPClient(limiter) as c:
        await c.get_safe("https://x.test/busy")

    assert 7.0 in slept, f"Retry-After was ignored; slept={slept}"


@respx.mock
async def test_absurd_retry_after_is_capped(limiter, monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("dataforge.collectors.http.asyncio.sleep", fake_sleep)

    respx.get("https://x.test/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_OPEN)
    )
    respx.get("https://x.test/busy").mock(
        return_value=httpx.Response(503, headers={"Retry-After": "86400"})
    )

    async with HTTPClient(limiter) as c:
        await c.get_safe("https://x.test/busy")

    assert slept and max(slept) <= 120.0, f"one URL could stall the run: {slept}"


# -- Crawl-delay -------------------------------------------------------------


@respx.mock
async def test_crawl_delay_slows_the_limiter(limiter):
    """FEMA declares Crawl-delay: 15 — it must actually be applied."""
    respx.get("https://slow.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nCrawl-delay: 15\nAllow: /\n")
    )
    respx.get("https://slow.test/page").mock(
        return_value=httpx.Response(200, html="<html><body><p>hi</p></body></html>")
    )

    async with HTTPClient(limiter) as c:
        await c.get("https://slow.test/page")

    bucket = limiter._buckets["slow.test"]
    assert bucket.rate == pytest.approx(1 / 15), (
        f"Crawl-delay ignored: bucket still at {bucket.rate} req/s"
    )


@respx.mock
async def test_crawl_delay_never_speeds_a_slow_limiter_up(limiter):
    """A permissive Crawl-delay must not override a stricter configured rate."""
    slow = RateLimiter(default_rps=0.1)        # user asked for 1 req / 10s
    respx.get("https://fast.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nCrawl-delay: 1\nAllow: /\n")
    )
    respx.get("https://fast.test/page").mock(
        return_value=httpx.Response(200, html="<html><body><p>hi</p></body></html>")
    )

    async with HTTPClient(slow) as c:
        await c.get("https://fast.test/page")

    assert slow._buckets["fast.test"].rate == pytest.approx(0.1)


@respx.mock
async def test_no_crawl_delay_leaves_the_default_rate(limiter):
    respx.get("https://plain.test/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_OPEN)
    )
    respx.get("https://plain.test/page").mock(
        return_value=httpx.Response(200, html="<html><body><p>hi</p></body></html>")
    )

    async with HTTPClient(limiter) as c:
        await c.get("https://plain.test/page")

    assert limiter._buckets["plain.test"].rate == pytest.approx(1000.0)


@respx.mock
async def test_crawl_delay_reaches_every_stage_limiter():
    """Regression: discovery and scraping build separate limiters. The delay was
    tracked process-wide, so only the first limiter (discovery) ever got it and
    the scrape stage crawled USCIS (Crawl-delay: 10) at the default rate."""
    respx.get("https://slow.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nCrawl-delay: 10\nAllow: /\n")
    )
    respx.get("https://slow.test/page").mock(
        return_value=httpx.Response(200, html="<html><body><p>hi</p></body></html>")
    )
    discovery, scraping = RateLimiter(default_rps=1000.0), RateLimiter(default_rps=1000.0)
    for lim in (discovery, scraping):
        async with HTTPClient(lim) as c:
            await c.get("https://slow.test/page")
    assert discovery._buckets["slow.test"].rate == pytest.approx(0.1)
    assert scraping._buckets["slow.test"].rate == pytest.approx(0.1)


# -- Rate limiter timing -----------------------------------------------------


async def test_sustained_rate_matches_the_configured_rate():
    """Regression: time spent sleeping was credited again as refill time, so
    every other request went through free and crawls ran at ~2x the rate."""
    import time

    rps = 10.0
    lim = RateLimiter(default_rps=rps)
    for _ in range(int(rps) + 1):      # drain the initial burst
        await lim.wait("https://x.test/")
    t0 = time.monotonic()
    n = 15
    for _ in range(n):
        await lim.wait("https://x.test/")
    elapsed = time.monotonic() - t0
    # Ideal is n/rps = 1.5s. The old bug gave about half that; allow for coarse
    # (~16 ms) timers on Windows.
    assert elapsed >= 0.7 * n / rps, f"{n} requests in {elapsed:.3f}s at {rps} rps"


def test_crawl_delay_bucket_bursts_one_request_only():
    """A Crawl-delay (rate < 1 req/s) allows one request, then one per delay."""
    lim = RateLimiter(default_rps=2.0)
    lim.set_domain_limit("slow.test", 0.1)          # Crawl-delay: 10
    bucket = lim._buckets["slow.test"]
    assert bucket.capacity == 1.0
    assert lim._buckets["fast.test"].capacity == 2.0   # >= 1 rps: one second's worth


async def test_jitter_never_shortens_the_gap_below_the_interval(monkeypatch):
    """Regression: jitter slept after a request was allowed was then counted as
    refill for the next one, so single gaps fell to ~85% of a Crawl-delay.
    Alternating maximum and zero jitter forces that case every time."""
    import itertools
    import time

    from dataforge.utils import rate_limiter

    jitters = itertools.cycle([0.15, 0.0])
    monkeypatch.setattr(rate_limiter.random, "uniform", lambda a, b: next(jitters))
    lim = RateLimiter(default_rps=1000.0)
    lim.set_domain_limit("slow.test", 5.0)            # 0.2s interval, burst of 5
    stamps = []
    for _ in range(11):
        await lim.wait("https://slow.test/")
        stamps.append(time.monotonic())
    gaps = [b - a for a, b in zip(stamps[5:], stamps[6:])]   # after the burst
    assert min(gaps) >= 0.2 * 0.95, [round(g, 3) for g in gaps]


def test_pipeline_context_shares_one_rate_limiter(tmp_settings):
    """Every stage must draw from the same limiter, or each new stage's first
    request ignores the Crawl-delay the previous stage was honouring."""
    from dataforge.agents import PipelineContext
    from dataforge.storage import DataFormat

    ctx = PipelineContext(
        session_id="s", session_name="n", goal="", format=DataFormat.qa, seed_urls=[],
        settings=tmp_settings, custom_system_prompt="", n_per_chunk=1,
    )
    assert ctx.get_rate_limiter() is ctx.get_rate_limiter()
