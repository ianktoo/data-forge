"""Tests for the interactive URL review module and related helpers."""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from dataforge.collectors.sitemap import filter_urls
from dataforge.storage.database import init_db, open_session, persist_url_selection
from dataforge.storage.models import DiscoveredURL, PipelineSession, URLSource

# ── filter_urls ───────────────────────────────────────────────────────────────

URLS = [
    "https://example.com/blog/post-1",
    "https://example.com/blog/post-2",
    "https://example.com/products/widget",
    "https://example.com/about",
    "https://other.com/page",
]


def test_filter_substring_case_insensitive():
    result = filter_urls(URLS, "BLOG", None)
    assert result == [
        "https://example.com/blog/post-1",
        "https://example.com/blog/post-2",
    ]


def test_filter_glob_path():
    result = filter_urls(URLS, "/blog/*", None)
    assert result == [
        "https://example.com/blog/post-1",
        "https://example.com/blog/post-2",
    ]


def test_filter_glob_wildcard_extension():
    urls = ["https://example.com/doc.pdf", "https://example.com/page.html", "https://example.com/image.png"]
    result = filter_urls(urls, "/*.pdf", None)
    assert result == ["https://example.com/doc.pdf"]


def test_filter_regex_basic():
    result = filter_urls(URLS, "re:blog", None)
    assert result == [
        "https://example.com/blog/post-1",
        "https://example.com/blog/post-2",
    ]


def test_filter_regex_anchored_path():
    result = filter_urls(URLS, r"re:/blog/post-\d+$", None)
    assert len(result) == 2


def test_filter_regex_case_insensitive():
    result = filter_urls(URLS, "re:PRODUCT", None)
    assert result == ["https://example.com/products/widget"]


def test_filter_regex_invalid_falls_back_to_literal():
    # An invalid regex after "re:" should not crash — falls back to substring on the raw text
    result = filter_urls(URLS, "re:[invalid", None)
    # "[invalid" as a substring matches nothing
    assert result == []


def test_filter_domain():
    result = filter_urls(URLS, None, "example.com")
    assert all("example.com" in u for u in result)
    assert len(result) == 4


def test_filter_combined_domain_and_pattern():
    result = filter_urls(URLS, "/blog/", "example.com")
    assert len(result) == 2


def test_filter_deduplicates():
    dupes = URLS + URLS
    result = filter_urls(dupes, None, None)
    assert result == URLS


def test_filter_no_pattern_no_domain():
    result = filter_urls(URLS, None, None)
    assert result == URLS


def test_filter_pattern_matches_nothing():
    result = filter_urls(URLS, "/nonexistent/", None)
    assert result == []


# ── persist_url_selection ─────────────────────────────────────────────────────

@pytest.fixture()
def db_with_session(tmp_path: Path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    session_id = str(uuid.uuid4())
    urls = [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    with open_session(db_path) as db:
        db.add(PipelineSession(id=session_id, name="test"))
        for url in urls:
            db.add(DiscoveredURL(session_id=session_id, url=url, source=URLSource.sitemap))
        db.commit()
    return db_path, session_id, urls


def test_persist_url_selection_partial(db_with_session):
    db_path, session_id, urls = db_with_session
    selected = {urls[0], urls[2]}  # keep a and c, drop b

    with open_session(db_path) as db:
        persist_url_selection(db, session_id, selected)

    with open_session(db_path) as db:
        from sqlmodel import select
        rows = db.exec(select(DiscoveredURL).where(DiscoveredURL.session_id == session_id)).all()
        by_url = {r.url: r.selected for r in rows}

    assert by_url[urls[0]] is True
    assert by_url[urls[1]] is False
    assert by_url[urls[2]] is True


def test_persist_url_selection_all(db_with_session):
    db_path, session_id, urls = db_with_session
    with open_session(db_path) as db:
        persist_url_selection(db, session_id, set(urls))

    with open_session(db_path) as db:
        from sqlmodel import select
        rows = db.exec(select(DiscoveredURL).where(DiscoveredURL.session_id == session_id)).all()
        assert all(r.selected for r in rows)


def test_persist_url_selection_none(db_with_session):
    db_path, session_id, urls = db_with_session
    with open_session(db_path) as db:
        persist_url_selection(db, session_id, set())

    with open_session(db_path) as db:
        from sqlmodel import select
        rows = db.exec(select(DiscoveredURL).where(DiscoveredURL.session_id == session_id)).all()
        assert not any(r.selected for r in rows)


# ── run_url_review (behaviour under mocked questionary) ───────────────────────

@pytest.mark.asyncio
async def test_run_url_review_returns_selection():
    """run_url_review should return whatever the checkbox yields."""
    from dataforge.cli.url_review import run_url_review

    urls = [
        "https://example.com/blog/a",
        "https://example.com/blog/b",
        "https://example.com/products/c",
    ]

    with (
        patch("dataforge.cli.url_review.questionary.text") as mock_text,
        patch("dataforge.cli.url_review.questionary.checkbox") as mock_checkbox,
        patch("dataforge.cli.url_review.questionary.confirm") as mock_confirm,
    ):
        # Filter step: no pattern
        mock_text.return_value.ask_async = AsyncMock(return_value="")
        # Checkbox: user deselects products URL
        mock_checkbox.return_value.ask_async = AsyncMock(return_value=urls[:2])
        # Confirm: yes
        mock_confirm.return_value.ask_async = AsyncMock(return_value=True)

        result = await run_url_review(urls)

    assert result == urls[:2]


@pytest.mark.asyncio
async def test_run_url_review_with_filter():
    """A filter pattern narrows the checkbox list."""
    from dataforge.cli.url_review import run_url_review

    urls = [
        "https://example.com/blog/a",
        "https://example.com/products/b",
    ]

    with (
        patch("dataforge.cli.url_review.questionary.text") as mock_text,
        patch("dataforge.cli.url_review.questionary.checkbox") as mock_checkbox,
        patch("dataforge.cli.url_review.questionary.confirm") as mock_confirm,
    ):
        mock_text.return_value.ask_async = AsyncMock(return_value="/blog/*")
        # Only the blog URL is shown in the checkbox; user confirms both
        mock_checkbox.return_value.ask_async = AsyncMock(return_value=[urls[0]])
        mock_confirm.return_value.ask_async = AsyncMock(return_value=True)

        result = await run_url_review(urls)

    assert result == [urls[0]]


@pytest.mark.asyncio
async def test_run_url_review_empty_input():
    from dataforge.cli.url_review import run_url_review
    result = await run_url_review([])
    assert result == []


@pytest.mark.asyncio
async def test_run_url_review_ctrl_c_retries():
    """Ctrl-C in checkbox (None return) should loop back and eventually succeed."""
    from dataforge.cli.url_review import run_url_review

    urls = ["https://example.com/a", "https://example.com/b"]
    call_count = 0

    async def checkbox_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None  # simulate Ctrl-C first time
        return urls  # succeed second time

    with (
        patch("dataforge.cli.url_review.questionary.text") as mock_text,
        patch("dataforge.cli.url_review.questionary.checkbox") as mock_checkbox,
        patch("dataforge.cli.url_review.questionary.confirm") as mock_confirm,
    ):
        mock_text.return_value.ask_async = AsyncMock(return_value="")
        mock_checkbox.return_value.ask_async = AsyncMock(side_effect=checkbox_side_effect)
        mock_confirm.return_value.ask_async = AsyncMock(return_value=True)

        result = await run_url_review(urls)

    assert result == urls
    assert call_count == 2
