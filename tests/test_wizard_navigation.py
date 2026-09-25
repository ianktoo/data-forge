"""Back choices, the multi-URL box and wizard navigation."""
from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from dataforge.cli import app as cli_app
from dataforge.cli import prompts

DOWN, ENTER = "\x1b[B", "\r"


@contextlib.contextmanager
def keys(text: str):
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        inp.send_text(text)
        yield


# questionary turns a Choice with value=None into its title, so "Back" used to
# come back as the string "(b) Back to menu" instead of None.

async def test_input_method_back_returns_none():
    with keys(DOWN * 4 + ENTER):
        assert await prompts.ask_input_method() is None


async def test_input_method_offers_a_scrape_folder_in_the_wizard():
    with keys(DOWN * 4 + ENTER):
        assert await prompts.ask_input_method(allow_scrape_folder=True) == prompts.SCRAPE_FOLDER


async def test_discovery_scope_back_returns_none():
    with keys(DOWN * 3 + ENTER):
        assert await prompts.ask_discovery_scope(2) is None


async def test_adjust_settings_back_returns_none():
    with keys(DOWN * 3 + ENTER):
        assert await prompts.ask_adjust_settings_target() is None


async def test_multiple_urls_finish_on_an_empty_line():
    with keys("https://a.example/x" + ENTER + "https://b.example/y" + ENTER + ENTER):
        assert await prompts.ask_multiple_urls() == ["https://a.example/x", "https://b.example/y"]


async def test_multiple_urls_empty_enter_asks_again_instead_of_finishing():
    with keys(ENTER + "https://a.example" + ENTER + ENTER):
        assert await prompts.ask_multiple_urls() == ["https://a.example"]


async def test_backing_out_of_the_url_box_returns_to_the_method_list():
    method = AsyncMock(side_effect=["Single URL", None])
    with patch.object(prompts, "ask_input_method", method), \
            patch.object(prompts, "ask_single_url", AsyncMock(return_value=None)):
        assert await cli_app._collect_urls() is None
    assert method.await_count == 2


async def test_back_on_the_first_wizard_step_goes_home():
    with patch.object(cli_app, "_step_urls", AsyncMock(return_value="back")), \
            patch.object(cli_app.ui, "screen"):
        assert await cli_app._run_wizard({}) == "home"


async def test_url_step_uses_a_scrape_folder_without_asking_the_scope(tmp_path):
    from dataforge import scrape as qs

    (tmp_path / "pages.jsonl").write_text(
        '{"url": "https://a.example/p", "status": "ok", "title": "P", '
        '"markdown": "Some text here", "text": "Some text here", "word_count": 3}\n',
        encoding="utf-8")
    state: dict = {}
    scope = AsyncMock()
    with patch.object(prompts, "ask_input_method", AsyncMock(return_value=prompts.SCRAPE_FOLDER)), \
            patch.object(prompts, "ask_scrape_dir", AsyncMock(return_value=tmp_path)), \
            patch.object(prompts, "ask_discovery_scope", scope):
        assert await cli_app._step_urls(state) == "next"
    assert state["seed_urls"] == ["https://a.example/p"]
    assert state["discovery_scope"] == "page"
    assert [p.url for p in state["scrape_pages"]] == [p.url for p in qs.load_scrape_dir(tmp_path)]
    scope.assert_not_awaited()
