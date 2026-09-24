"""Tests for the interactive URL review module and related helpers."""
from __future__ import annotations

import contextlib
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

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


# ── _URLReviewer unit tests ───────────────────────────────────────────────────

def make_reviewer(urls=None):
    from dataforge.cli.url_review import _URLReviewer
    default = [
        "https://example.com/blog/post-1",
        "https://example.com/blog/post-2",
        "https://example.com/products/widget",
        "https://example.com/about",
    ]
    return _URLReviewer(urls or default)


def test_reviewer_initial_state():
    r = make_reviewer()
    assert len(r._all) == 4
    assert r._selected == set(r._all)
    assert r._page == 0
    assert r._filter == ""


def test_reviewer_filter_narrows_view():
    r = make_reviewer()
    msg, done = r.handle("f /blog/")
    assert done is False
    assert len(r._view) == 2
    assert r._page == 0
    assert r._filter == "/blog/"


def test_reviewer_filter_clear():
    r = make_reviewer()
    r.handle("f /blog/")
    msg, done = r.handle("f")
    assert len(r._view) == 4
    assert r._filter == ""


def test_reviewer_deselect_row():
    r = make_reviewer()
    msg, done = r.handle("x 1")
    assert "example.com/blog/post-1" not in r._selected
    assert "Deselected" in msg


def test_reviewer_select_row():
    r = make_reviewer()
    r._selected.discard("https://example.com/blog/post-1")
    msg, done = r.handle("+ 1")
    assert "https://example.com/blog/post-1" in r._selected
    assert "Selected" in msg


def test_reviewer_deselect_range():
    r = make_reviewer()
    msg, done = r.handle("x 1-2")
    assert "https://example.com/blog/post-1" not in r._selected
    assert "https://example.com/blog/post-2" not in r._selected
    assert "Deselected 2" in msg


def test_reviewer_all_none():
    r = make_reviewer()
    r.handle("none")
    assert r._selected == set()
    r.handle("all")
    assert r._selected == set(r._all)


def test_reviewer_navigate_pages():
    urls = [f"https://example.com/page-{i}" for i in range(65)]
    r = make_reviewer(urls)
    assert r._page == 0
    msg, done = r.handle("n")
    assert r._page == 1
    msg, done = r.handle("n")
    assert r._page == 2
    msg, done = r.handle("n")  # already last page
    assert r._page == 2
    r.handle("p")
    assert r._page == 1
    r.handle("1")  # jump to page 1
    assert r._page == 0


def test_reviewer_done_returns_true():
    r = make_reviewer()
    msg, done = r.handle("done")
    assert done is True


def test_reviewer_quit_returns_none():
    r = make_reviewer()
    msg, done = r.handle("q")
    assert done is None


def test_reviewer_selected_urls_original_order():
    r = make_reviewer()
    r.handle("x 2")  # remove post-2
    result = r.selected_urls()
    assert result[0] == "https://example.com/blog/post-1"
    assert "https://example.com/blog/post-2" not in result


def test_reviewer_inspect_valid_row(capsys):
    r = make_reviewer()
    msg, done = r.handle("i 1")
    assert done is False


def test_reviewer_unknown_command():
    r = make_reviewer()
    msg, done = r.handle("zzz")
    assert "Unknown command" in msg


# ── run_url_review integration (keys sent through a pipe input) ─────────────

DOWN, UP, ENTER = "\x1b[B", "\x1b[A", "\r"


@contextlib.contextmanager
def keys(text: str):
    """Run the review screen against *text* as typed keys."""
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        inp.send_text(text)
        yield


@pytest.mark.asyncio
async def test_run_url_review_empty_input():
    from dataforge.cli.url_review import run_url_review
    result = await run_url_review([])
    assert result == []


@pytest.mark.asyncio
async def test_run_url_review_enter_and_confirm():
    """Enter + confirm returns every URL (all start ticked)."""
    from dataforge.cli.url_review import run_url_review

    urls = ["https://example.com/a", "https://example.com/b"]
    with keys(ENTER), patch("dataforge.cli.url_review.questionary") as mock_q:
        mock_q.confirm.return_value.ask_async = AsyncMock(return_value=True)
        result = await run_url_review(urls)
    assert result == urls


@pytest.mark.asyncio
async def test_run_url_review_space_unticks_row():
    """Space unticks the row under the cursor and moves down."""
    from dataforge.cli.url_review import run_url_review

    urls = ["https://example.com/a", "https://example.com/b", "https://example.com/c"]
    # untick a (cursor moves to b), skip b, untick c
    with keys(" " + DOWN + " " + ENTER), patch("dataforge.cli.url_review.questionary") as mock_q:
        mock_q.confirm.return_value.ask_async = AsyncMock(return_value=True)
        result = await run_url_review(urls)
    assert result == ["https://example.com/b"]


@pytest.mark.asyncio
async def test_run_url_review_filter_then_untick_all_shown():
    """/ filters as you type; a unticks everything shown."""
    from dataforge.cli.url_review import run_url_review

    urls = ["https://example.com/blog/a", "https://example.com/products/b"]
    with keys("/blog" + ENTER + "a" + ENTER), patch("dataforge.cli.url_review.questionary") as mock_q:
        mock_q.confirm.return_value.ask_async = AsyncMock(return_value=True)
        result = await run_url_review(urls)
    assert result == ["https://example.com/products/b"]


@pytest.mark.asyncio
async def test_run_url_review_typed_command():
    """: opens the command line for the typed commands (x 1-2)."""
    from dataforge.cli.url_review import run_url_review

    urls = ["https://example.com/a", "https://example.com/b", "https://example.com/c"]
    with keys(":x 1-2" + ENTER + ENTER), patch("dataforge.cli.url_review.questionary") as mock_q:
        mock_q.confirm.return_value.ask_async = AsyncMock(return_value=True)
        result = await run_url_review(urls)
    assert result == ["https://example.com/c"]


@pytest.mark.asyncio
async def test_run_url_review_q_returns_empty():
    from dataforge.cli.url_review import run_url_review
    with keys("q"):
        result = await run_url_review(["https://example.com/a"])
    assert result == []


@pytest.mark.asyncio
async def test_run_url_review_ctrl_c_cancels():
    from dataforge.cli.url_review import run_url_review
    with keys("\x03"):
        result = await run_url_review(["https://example.com/a"])
    assert result == []


@pytest.mark.asyncio
async def test_run_url_review_language_choice_ticks_one_language():
    """On a multilingual site, the language picked up front is the only one ticked."""
    from dataforge.cli.url_review import run_url_review

    urls = [
        "https://example.com/floods",
        "https://example.com/fires",
        "https://example.com/es/inundaciones",
        "https://example.com/fr/inondations",
    ]
    with keys(ENTER), patch("dataforge.cli.url_review.questionary") as mock_q:
        mock_q.select.return_value.ask_async = AsyncMock(return_value="")
        mock_q.confirm.return_value.ask_async = AsyncMock(return_value=True)
        result = await run_url_review(urls)
    assert result == ["https://example.com/floods", "https://example.com/fires"]


# ── keyboard helpers on _URLReviewer ──────────────────────────────────────────

def test_reviewer_turn_page_moves_cursor():
    r = make_reviewer([f"https://example.com/p{i}" for i in range(65)])
    r.turn_page(1)
    assert r._page == 1 and r._cursor == 30
    assert r.turn_page(5) == "Already on the last page."
    r.turn_page(-1)
    assert r._cursor == 0


def test_reviewer_cycle_language():
    r = make_reviewer([
        "https://example.com/a", "https://example.com/b", "https://example.com/es/a",
    ])
    r.cycle_language()          # most common first: no marker
    assert r._view == ["https://example.com/a", "https://example.com/b"]
    r.cycle_language()          # then es
    assert r._view == ["https://example.com/es/a"]
    r.cycle_language()          # back to all
    assert len(r._view) == 3


def test_url_language():
    from dataforge.cli.url_review import url_language
    assert url_language("https://example.com/es/terremotos") == "es"
    assert url_language("https://example.com/pt-br/x") == "pt-br"
    assert url_language("https://example.com/page?lang=fr") == "fr"
    assert url_language("https://example.com/us/news") == ""
