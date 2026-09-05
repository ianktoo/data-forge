"""Tests for collectors — extractor and sitemap parser."""
from __future__ import annotations

from dataforge.collectors.extractor import extract
from dataforge.collectors.sitemap import filter_urls
from tests.conftest import SAMPLE_HTML


def test_extract_title():
    content = extract(SAMPLE_HTML, "https://example.com/article")
    assert "Train" in content.title or content.title == "Test Article"


def test_extract_author():
    content = extract(SAMPLE_HTML, "https://example.com/article")
    assert content.author == "Jane Doe"


def test_extract_text_not_empty():
    content = extract(SAMPLE_HTML, "https://example.com/article")
    assert len(content.text) > 100


def test_extract_removes_nav_footer():
    content = extract(SAMPLE_HTML, "https://example.com/article")
    assert "Navigation menu" not in content.text
    assert "Copyright 2024" not in content.text


def test_extract_word_count():
    content = extract(SAMPLE_HTML, "https://example.com/article")
    assert content.word_count > 30


def test_filter_urls_by_pattern():
    urls = [
        "https://example.com/blog/post-1",
        "https://example.com/about",
        "https://example.com/blog/post-2",
    ]
    filtered = filter_urls(urls, pattern="/blog/", base_domain=None)
    assert len(filtered) == 2
    assert all("/blog/" in u for u in filtered)


def test_filter_urls_by_domain():
    urls = [
        "https://example.com/page",
        "https://other.com/page",
    ]
    filtered = filter_urls(urls, pattern=None, base_domain="example.com")
    assert len(filtered) == 1
    assert filtered[0] == "https://example.com/page"


def test_filter_urls_deduplicates():
    urls = ["https://a.com/", "https://a.com/", "https://b.com/"]
    filtered = filter_urls(urls, pattern=None, base_domain=None)
    assert len(filtered) == 2


def test_filter_urls_glob():
    urls = [
        "https://example.com/blog/post-1",
        "https://example.com/products/widget",
    ]
    assert filter_urls(urls, "/blog/*", None) == ["https://example.com/blog/post-1"]


def test_filter_urls_regex():
    urls = [
        "https://example.com/blog/post-1",
        "https://example.com/products/widget",
    ]
    assert filter_urls(urls, "re:products", None) == ["https://example.com/products/widget"]


def test_filter_urls_substring_case_insensitive():
    urls = ["https://example.com/BLOG/post", "https://example.com/other"]
    assert filter_urls(urls, "blog", None) == ["https://example.com/BLOG/post"]
