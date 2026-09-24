"""Pasted paths and URLs are accepted with the quotes terminals add around them."""
from __future__ import annotations

import pytest

from dataforge.cli.prompts import _path_exists, _split_urls, clean_input, read_url_file


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"C:\\my files\\urls.txt"', "C:\\my files\\urls.txt"),   # Windows "Copy as path"
        ("& 'C:\\my files\\urls.txt'", "C:\\my files\\urls.txt"),  # PowerShell drag-and-drop
        ("'/home/me/urls.txt'", "/home/me/urls.txt"),
        ("  https://example.com  ", "https://example.com"),
        ("<https://example.com>", "https://example.com"),
        ("\u201chttps://example.com\u201d", "https://example.com"),   # curly quotes
        ("'\"https://example.com\"'", "https://example.com"),     # nested
        ("'unbalanced", "'unbalanced"),                           # left alone
        ("", ""),
    ],
)
def test_clean_input(raw, expected):
    assert clean_input(raw) == expected


def test_split_urls_accepts_quotes_commas_and_lines():
    raw = '"https://a.com", https://b.com\n  \'https://c.com\'  https://d.com'
    assert _split_urls(raw) == ["https://a.com", "https://b.com", "https://c.com", "https://d.com"]


def test_path_exists_with_quotes(tmp_path):
    f = tmp_path / "my urls.txt"
    f.write_text("x", encoding="utf-8")
    assert _path_exists(f'"{f}"')
    assert _path_exists(f"& '{f}'")
    assert not _path_exists('"' + str(tmp_path / "missing.txt") + '"')


def test_read_url_file_strips_quotes_and_comments(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text('# comment\n"https://a.com"\nhttps://b.com, https://c.com\nnot-a-url\n', encoding="utf-8")
    assert read_url_file(f) == ["https://a.com", "https://b.com", "https://c.com"]
