"""Discovery scope (just this page / follow links / whole site) and global API keys."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dataforge.agents import ExplorerAgent, PipelineContext
from dataforge.storage import DataFormat, URLSource


def _ctx(scope: str) -> PipelineContext:
    settings = MagicMock()
    settings.max_crawl_pages = 10
    settings.max_crawl_depth = 2
    return PipelineContext(
        session_id="s", session_name="s", goal="", format=DataFormat.qa,
        seed_urls=["https://example.com/guide/start"], settings=settings,
        discovery_scope=scope,
    )


# ── Discovery scope ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_page_scope_uses_the_seed_without_any_request():
    client = MagicMock()
    client.get_safe = AsyncMock()
    with patch("dataforge.agents.explorer.discover_sitemap_urls", new=AsyncMock()) as sitemaps, \
         patch("dataforge.agents.explorer.crawl", new=AsyncMock()) as crawl:
        urls, source = await ExplorerAgent(_ctx("page"))._explore_seed(
            client, "https://example.com/guide/start")
    assert urls == ["https://example.com/guide/start"]
    assert source == URLSource.manual
    sitemaps.assert_not_called()
    crawl.assert_not_called()
    client.get_safe.assert_not_called()


@pytest.mark.asyncio
async def test_links_scope_crawls_from_the_seed_and_skips_the_sitemap():
    found = ["https://example.com/guide/start", "https://example.com/guide/next"]
    with patch("dataforge.agents.explorer.discover_sitemap_urls", new=AsyncMock()) as sitemaps, \
         patch("dataforge.agents.explorer.crawl", new=AsyncMock(return_value=found)) as crawl:
        urls, source = await ExplorerAgent(_ctx("links"))._explore_seed(
            MagicMock(), "https://example.com/guide/start")
    assert urls == found
    assert source == URLSource.crawl
    sitemaps.assert_not_called()
    assert crawl.call_args.args[1] == "https://example.com/guide/start"


@pytest.mark.asyncio
async def test_site_scope_still_reads_the_sitemap():
    with patch("dataforge.agents.explorer.discover_sitemap_urls",
               new=AsyncMock(return_value=["https://example.com/sitemap.xml"])), \
         patch("dataforge.agents.explorer.parse_sitemaps",
               new=AsyncMock(return_value=["https://example.com/guide/start/a"])):
        urls, source = await ExplorerAgent(_ctx("site"))._explore_seed(
            MagicMock(), "https://example.com/guide/start")
    assert urls == ["https://example.com/guide/start/a"]
    assert source == URLSource.sitemap


# ── Globally saved API keys ───────────────────────────────────────────────────

@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
                "TOGETHER_API_KEY", "DATAFORGE_LLM_PROVIDER", "DATAFORGE_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _saved(keys: dict[str, str], prefs: dict | None = None):
    """Patch the global config: *keys* as stored in the OS keychain."""
    from dataforge.cli import prefs as user_prefs
    return (
        patch.object(user_prefs, "get_api_key", side_effect=lambda k: keys.get(k, "")),
        patch.object(user_prefs, "load", return_value=prefs or {}),
    )


def test_keychain_key_is_loaded_even_when_a_project_env_exists(clean_env):
    """The bug: a key saved globally lives in the keychain, and .env existing
    (which `dataforge config` always creates) skipped loading it."""
    from dataforge.cli.preflight import apply_saved_config

    (clean_env / ".env").write_text("DATAFORGE_LLM_PROVIDER=anthropic\n")
    a, b = _saved({"ANTHROPIC_API_KEY": "sk-ant-global"})
    with a, b:
        apply_saved_config(clean_env / ".env")
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-global"


def test_env_file_key_wins_over_global_and_is_exported(clean_env):
    from dataforge.cli.preflight import apply_saved_config

    (clean_env / ".env").write_text("OPENAI_API_KEY=sk-project\n")
    a, b = _saved({"OPENAI_API_KEY": "sk-global"})
    with a, b:
        apply_saved_config(clean_env / ".env")
    assert os.environ["OPENAI_API_KEY"] == "sk-project"


def test_process_env_wins_over_everything(clean_env, monkeypatch):
    from dataforge.cli.preflight import apply_saved_config

    monkeypatch.setenv("OPENAI_API_KEY", "sk-shell")
    a, b = _saved({"OPENAI_API_KEY": "sk-global"})
    with a, b:
        apply_saved_config(clean_env / ".env")
    assert os.environ["OPENAI_API_KEY"] == "sk-shell"


def test_saved_provider_does_not_override_project_env(clean_env):
    from dataforge.cli.preflight import apply_saved_config

    (clean_env / ".env").write_text("DATAFORGE_LLM_PROVIDER=groq\n")
    a, b = _saved({}, {"llm_provider": "anthropic", "llm_model": "claude-x"})
    with a, b:
        apply_saved_config(clean_env / ".env")
    assert "DATAFORGE_LLM_PROVIDER" not in os.environ
    assert os.environ["DATAFORGE_LLM_MODEL"] == "claude-x"


def test_credentials_check_uses_the_saved_key_instead_of_prompting(clean_env):
    from dataforge.cli import preflight

    settings = MagicMock(llm_provider="anthropic", anthropic_api_key="")
    a, b = _saved({"ANTHROPIC_API_KEY": "sk-ant-global"})
    with a, b, patch.object(preflight, "get_settings", return_value=settings), \
         patch.object(preflight.getpass, "getpass") as ask:
        ok, missing = preflight.check_llm_credentials()
    assert ok and missing is None
    ask.assert_not_called()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-global"
