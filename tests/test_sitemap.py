"""Unit tests for sitemap discovery and parsing logic."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from dataforge.collectors.sitemap import discover_sitemap_url, filter_urls, parse_sitemap

# Sample XML for testing
SAMPLE_SITEMAP_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page-1</loc></url>
  <url><loc>https://example.com/page-2</loc></url>
  <url><loc>https://www.example.com/page-3</loc></url>
</urlset>
"""

SAMPLE_SITEMAP_INDEX_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-posts.xml</loc></sitemap>
</sitemapindex>
"""

SAMPLE_SITEMAP_CHILD_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/post-1</loc></url>
  <url><loc>https://example.com/post-2</loc></url>
</urlset>
"""


class TestFilterUrls:
    """Tests for filter_urls domain and pattern filtering."""

    def test_filter_urls_www_vs_non_www(self):
        """Domain normalization: www.example.com URL matches example.com base domain."""
        urls = ["https://www.example.com/page", "https://other.com/page"]
        result = filter_urls(urls, pattern=None, base_domain="example.com")
        assert result == ["https://www.example.com/page"]

    def test_filter_urls_non_www_vs_www(self):
        """Domain normalization: example.com URL matches www.example.com base domain."""
        urls = ["https://example.com/page", "https://other.com/page"]
        result = filter_urls(urls, pattern=None, base_domain="www.example.com")
        assert result == ["https://example.com/page"]

    def test_filter_urls_mixed_domains(self):
        """Both www and non-www URLs of same base domain pass through."""
        urls = [
            "https://www.example.com/page-1",
            "https://example.com/page-2",
            "https://other.com/page-3",
        ]
        result = filter_urls(urls, pattern=None, base_domain="example.com")
        assert len(result) == 2
        assert "https://www.example.com/page-1" in result
        assert "https://example.com/page-2" in result
        assert "https://other.com/page-3" not in result

    def test_filter_urls_by_pattern_and_domain(self):
        """Combined pattern and domain filter."""
        urls = [
            "https://example.com/blog/post-1",
            "https://example.com/news/post-2",
            "https://other.com/blog/post-3",
        ]
        result = filter_urls(urls, pattern="/blog/", base_domain="example.com")
        assert result == ["https://example.com/blog/post-1"]

    def test_filter_urls_different_domain_excluded(self):
        """Unrelated domain is excluded regardless of www normalization."""
        urls = ["https://www.example.com/page", "https://different.com/page"]
        result = filter_urls(urls, pattern=None, base_domain="example.com")
        assert result == ["https://www.example.com/page"]

    def test_filter_urls_deduplicates(self):
        """Duplicate URLs are removed while preserving order."""
        urls = ["https://example.com/page", "https://example.com/page"]
        result = filter_urls(urls, pattern=None, base_domain="example.com")
        assert result == ["https://example.com/page"]
        assert len(result) == 1

    def test_filter_urls_pattern_only(self):
        """Pattern filter without domain restriction."""
        urls = ["https://example.com/blog/post", "https://example.com/about"]
        result = filter_urls(urls, pattern="/blog/", base_domain=None)
        assert result == ["https://example.com/blog/post"]

    def test_filter_urls_empty_input(self):
        """Empty URL list returns empty list."""
        result = filter_urls([], pattern=None, base_domain="example.com")
        assert result == []


class TestParseSitemap:
    """Tests for sitemap XML parsing."""

    @pytest.mark.asyncio
    async def test_parse_sitemap_plain_xml(self):
        """Parse plain <urlset> XML and extract URLs."""
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.text = SAMPLE_SITEMAP_XML
        client.get_safe = AsyncMock(return_value=response)

        urls = await parse_sitemap(client, "https://example.com/sitemap.xml")
        assert len(urls) == 3
        assert "https://example.com/page-1" in urls
        assert "https://www.example.com/page-3" in urls

    @pytest.mark.asyncio
    async def test_parse_sitemap_index_xml(self):
        """Parse <sitemapindex> with recursive child sitemap lookup."""
        client = MagicMock()

        # Index response
        index_response = MagicMock()
        index_response.status_code = 200
        index_response.text = SAMPLE_SITEMAP_INDEX_XML

        # Child sitemap response
        child_response = MagicMock()
        child_response.status_code = 200
        child_response.text = SAMPLE_SITEMAP_CHILD_XML

        # Configure get_safe to return different responses
        async def get_safe_side_effect(url):
            if "posts" in url:
                return child_response
            return index_response

        client.get_safe = AsyncMock(side_effect=get_safe_side_effect)

        urls = await parse_sitemap(client, "https://example.com/sitemap_index.xml")
        assert len(urls) == 2
        assert "https://example.com/post-1" in urls
        assert "https://example.com/post-2" in urls

    @pytest.mark.asyncio
    async def test_parse_sitemap_bad_xml(self):
        """Non-XML response returns empty list without raising."""
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.text = "<html><body>Not a sitemap</body></html>"
        client.get_safe = AsyncMock(return_value=response)

        urls = await parse_sitemap(client, "https://example.com/sitemap.xml")
        assert urls == []

    @pytest.mark.asyncio
    async def test_parse_sitemap_http_error(self):
        """HTTP errors (get_safe returns None) result in empty list."""
        client = MagicMock()
        client.get_safe = AsyncMock(return_value=None)

        urls = await parse_sitemap(client, "https://example.com/sitemap.xml")
        assert urls == []

    @pytest.mark.asyncio
    async def test_parse_sitemap_http_404(self):
        """HTTP 404 status returns empty list."""
        client = MagicMock()
        response = MagicMock()
        response.status_code = 404
        client.get_safe = AsyncMock(return_value=response)

        urls = await parse_sitemap(client, "https://example.com/sitemap.xml")
        assert urls == []

    @pytest.mark.asyncio
    async def test_parse_sitemap_prevents_infinite_loop(self):
        """Circular sitemap references don't cause infinite loop."""
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        # A self-referencing sitemapindex
        response.text = """\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap_index.xml</loc></sitemap>
</sitemapindex>
"""
        client.get_safe = AsyncMock(return_value=response)

        urls = await parse_sitemap(client, "https://example.com/sitemap_index.xml")
        # Should not hang; visited set prevents re-processing
        assert urls == []


class TestDiscoverSitemapUrl:
    """Tests for sitemap URL discovery."""

    @pytest.mark.asyncio
    async def test_discover_sitemap_url_from_robots_txt(self):
        """Sitemap URL found in robots.txt takes precedence."""
        client = MagicMock()

        # robots.txt response
        robots_response = MagicMock()
        robots_response.text = "User-agent: *\nSitemap: https://example.com/my-sitemap.xml"

        # sitemap.xml would fail, but shouldn't be called
        fallback_response = MagicMock()
        fallback_response.status_code = 404

        async def get_side_effect(url, **kw):
            if "robots.txt" in url:
                return robots_response
            return fallback_response

        client.get = AsyncMock(side_effect=get_side_effect)
        client.get_safe = AsyncMock(return_value=None)

        result = await discover_sitemap_url(client, "https://example.com")
        assert result == "https://example.com/my-sitemap.xml"

    @pytest.mark.asyncio
    async def test_discover_sitemap_url_fallback_path(self):
        """Fallback to hardcoded paths when robots.txt doesn't specify."""
        client = MagicMock()

        # robots.txt without Sitemap directive
        robots_response = MagicMock()
        robots_response.text = "User-agent: *\nDisallow: /admin"

        # /sitemap.xml found with valid content
        sitemap_response = MagicMock()
        sitemap_response.status_code = 200
        sitemap_response.text = SAMPLE_SITEMAP_XML

        async def get_side_effect(url, **kw):
            if "robots.txt" in url:
                return robots_response
            return None

        client.get = AsyncMock(side_effect=get_side_effect)
        client.get_safe = AsyncMock(return_value=sitemap_response)

        result = await discover_sitemap_url(client, "https://example.com")
        assert result == "https://example.com/sitemap.xml"

    @pytest.mark.asyncio
    async def test_discover_sitemap_url_returns_none(self):
        """All discovery methods fail returns None."""
        client = MagicMock()

        robots_response = MagicMock()
        robots_response.text = "User-agent: *"

        async def get_side_effect(url, **kw):
            return robots_response

        client.get = AsyncMock(side_effect=get_side_effect)
        client.get_safe = AsyncMock(return_value=None)

        result = await discover_sitemap_url(client, "https://example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_discover_sitemap_url_sitemapindex_body(self):
        """Correctly detects <sitemapindex> in response (operator precedence fix)."""
        client = MagicMock()

        robots_response = MagicMock()
        robots_response.text = "User-agent: *"

        sitemapindex_response = MagicMock()
        sitemapindex_response.status_code = 200
        sitemapindex_response.text = SAMPLE_SITEMAP_INDEX_XML

        async def get_side_effect(url, **kw):
            return robots_response

        client.get = AsyncMock(side_effect=get_side_effect)
        client.get_safe = AsyncMock(return_value=sitemapindex_response)

        result = await discover_sitemap_url(client, "https://example.com")
        assert result == "https://example.com/sitemap.xml"
