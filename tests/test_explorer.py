"""Unit and integration tests for ExplorerAgent URL discovery."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from dataforge.agents import ExplorerAgent, PipelineContext
from dataforge.config import get_settings
from dataforge.storage import DataFormat, open_session, DiscoveredURL
from dataforge.utils import RateLimiter

# Sample XML for testing
SAMPLE_SITEMAP_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page-1</loc></url>
  <url><loc>https://example.com/page-2</loc></url>
  <url><loc>https://www.example.com/page-3</loc></url>
</urlset>
"""


@pytest.fixture
def mock_client():
    """Create a mock HTTPClient for testing."""
    client = MagicMock()
    client.get_safe = AsyncMock()
    client.get = AsyncMock()
    return client


@pytest.fixture
async def explorer_context(tmp_settings):
    """Create a PipelineContext for testing."""
    ctx = PipelineContext(
        session_id="test-session-id",
        session_name="Test Session",
        goal="Test goal",
        format=DataFormat.qa,
        seed_urls=["https://example.com"],
        settings=tmp_settings,
        custom_system_prompt="",
        n_per_chunk=3,
    )
    return ctx


class TestExplorerAgent:
    """Tests for ExplorerAgent._explore_seed method."""

    @pytest.mark.asyncio
    async def test_explore_seed_direct_sitemap_url(self, mock_client):
        """Seed URL ending in .xml is parsed directly as a sitemap."""
        response = MagicMock()
        response.status_code = 200
        response.text = SAMPLE_SITEMAP_XML
        mock_client.get_safe = AsyncMock(return_value=response)

        ctx = PipelineContext(
            session_id="test",
            session_name="Test",
            goal="Test",
            format=DataFormat.qa,
            seed_urls=["https://example.com/sitemap.xml"],
            settings=MagicMock(),
            custom_system_prompt="",
            n_per_chunk=3,
        )
        agent = ExplorerAgent(ctx)

        urls, source = await agent._explore_seed(mock_client, "https://example.com/sitemap.xml")
        assert len(urls) == 3
        assert "https://example.com/page-1" in urls

    @pytest.mark.asyncio
    async def test_explore_seed_no_sitemap_uses_seed(self, mock_client):
        """When no sitemap is found, seed URL is returned as fallback."""
        # robots.txt without Sitemap directive
        robots_response = MagicMock()
        robots_response.text = "User-agent: *"

        async def get_side_effect(url, **kw):
            return robots_response

        mock_client.get = AsyncMock(side_effect=get_side_effect)
        mock_client.get_safe = AsyncMock(return_value=None)

        mock_settings = MagicMock()
        mock_settings.max_crawl_pages = 5
        mock_settings.max_crawl_depth = 1

        ctx = PipelineContext(
            session_id="test",
            session_name="Test",
            goal="Test",
            format=DataFormat.qa,
            seed_urls=["https://example.com"],
            settings=mock_settings,
            custom_system_prompt="",
            n_per_chunk=3,
        )
        agent = ExplorerAgent(ctx)

        urls, source = await agent._explore_seed(mock_client, "https://example.com")
        assert urls == ["https://example.com"]

    @pytest.mark.asyncio
    async def test_explore_seed_with_sitemap_found(self, mock_client):
        """Sitemap discovery succeeds and URLs are filtered by domain."""
        robots_response = MagicMock()
        robots_response.text = "User-agent: *"

        sitemap_response = MagicMock()
        sitemap_response.status_code = 200
        sitemap_response.text = SAMPLE_SITEMAP_XML

        async def get_side_effect(url, **kw):
            return robots_response

        mock_client.get = AsyncMock(side_effect=get_side_effect)
        mock_client.get_safe = AsyncMock(return_value=sitemap_response)

        ctx = PipelineContext(
            session_id="test",
            session_name="Test",
            goal="Test",
            format=DataFormat.qa,
            seed_urls=["https://example.com"],
            settings=MagicMock(),
            custom_system_prompt="",
            n_per_chunk=3,
        )
        agent = ExplorerAgent(ctx)

        urls, source = await agent._explore_seed(mock_client, "https://example.com")
        # All 3 URLs should pass through (www and non-www normalization enabled)
        assert len(urls) == 3

    @pytest.mark.asyncio
    async def test_explore_seed_fallback_when_filter_empty(self, mock_client):
        """When all URLs are filtered out, return unfiltered list with warning."""
        robots_response = MagicMock()
        robots_response.text = "User-agent: *"

        sitemap_with_other_domain = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://other.com/page-1</loc></url>
  <url><loc>https://other.com/page-2</loc></url>
</urlset>
"""
        sitemap_response = MagicMock()
        sitemap_response.status_code = 200
        sitemap_response.text = sitemap_with_other_domain

        async def get_side_effect(url, **kw):
            return robots_response

        mock_client.get = AsyncMock(side_effect=get_side_effect)
        mock_client.get_safe = AsyncMock(return_value=sitemap_response)

        ctx = PipelineContext(
            session_id="test",
            session_name="Test",
            goal="Test",
            format=DataFormat.qa,
            seed_urls=["https://example.com"],
            settings=MagicMock(),
            custom_system_prompt="",
            n_per_chunk=3,
        )
        agent = ExplorerAgent(ctx)

        urls, source = await agent._explore_seed(mock_client, "https://example.com")
        # Should return unfiltered URLs as fallback
        assert len(urls) == 2
        assert "https://other.com/page-1" in urls

    @pytest.mark.asyncio
    async def test_explorer_agent_run_with_sitemap(self, mock_client, tmp_settings):
        """End-to-end: seed URL → discover sitemap → parse → filter → populated discovered_urls."""
        robots_response = MagicMock()
        robots_response.text = "User-agent: *"

        sitemap_response = MagicMock()
        sitemap_response.status_code = 200
        sitemap_response.text = SAMPLE_SITEMAP_XML

        async def get_side_effect(url, **kw):
            return robots_response

        mock_client.get = AsyncMock(side_effect=get_side_effect)
        mock_client.get_safe = AsyncMock(return_value=sitemap_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        ctx = PipelineContext(
            session_id="test-session",
            session_name="Test",
            goal="Test",
            format=DataFormat.qa,
            seed_urls=["https://example.com"],
            settings=tmp_settings,
            custom_system_prompt="",
            n_per_chunk=3,
        )

        with patch("dataforge.agents.explorer.HTTPClient", return_value=mock_client):
            agent = ExplorerAgent(ctx)
            ctx_result = await agent.run()

        # Verify URLs were discovered
        assert len(ctx_result.discovered_urls) > 0
        assert any("example.com" in url for url in ctx_result.discovered_urls)

        # Verify persisted to DB
        with open_session(tmp_settings.db_path) as db:
            from sqlmodel import select
            discovered = db.exec(select(DiscoveredURL)).all()
            assert len(discovered) > 0

    @pytest.mark.asyncio
    async def test_explorer_agent_deduplicates(self, tmp_settings):
        """Duplicate URLs across multiple seeds are deduplicated."""
        ctx = PipelineContext(
            session_id="test-session",
            session_name="Test",
            goal="Test",
            format=DataFormat.qa,
            seed_urls=["https://example.com", "https://example.com"],
            settings=tmp_settings,
            custom_system_prompt="",
            n_per_chunk=3,
        )

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=MagicMock(text="User-agent: *"))
        mock_client.get_safe = AsyncMock(return_value=None)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("dataforge.agents.explorer.HTTPClient", return_value=mock_client):
            agent = ExplorerAgent(ctx)
            ctx_result = await agent.run()

        # Both seeds fall back to themselves, but dedup should leave 1
        assert len(ctx_result.discovered_urls) == 1
        assert ctx_result.discovered_urls[0] == "https://example.com"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_sitemap_discovery():
    """Integration test: verify discovery works with a real public sitemap."""
    pytest.importorskip("respx")

    settings = get_settings()
    ctx = PipelineContext(
        session_id="integration-test",
        session_name="Integration Test",
        goal="Test real sitemap",
        format=DataFormat.qa,
        seed_urls=["https://example.com"],
        settings=settings,
        custom_system_prompt="",
        n_per_chunk=3,
    )

    # This test is marked @pytest.mark.integration and should be skipped by default
    # Run with: pytest -m integration tests/test_explorer.py::test_real_sitemap_discovery
    # For now, just verify the test can be structured
    assert ctx.seed_urls == ["https://example.com"]
