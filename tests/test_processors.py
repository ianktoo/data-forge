"""Tests for processors — cleaner, chunker, formatter."""
from __future__ import annotations

from dataforge.processors.chunker import chunk, token_count
from dataforge.processors.cleaner import clean, is_content_rich, word_count
from dataforge.processors.formatter import format_records

SAMPLE_TEXT = """
Fine-tuning large language models requires high-quality data.

The process involves collecting, cleaning, and structuring text into
instruction-response pairs that guide the model's behaviour.

Synthetic data generation is an increasingly popular technique.
By prompting a capable model to produce training examples grounded in
real content, practitioners can scale dataset creation.

Key considerations include diversity of topics, balance between
question types, and rigorous quality filtering.
"""


def test_clean_collapses_whitespace():
    text = "Hello\n\n\n\nWorld"
    result = clean(text)
    assert "\n\n\n" not in result


def test_clean_strips_boilerplate():
    text = "Real content.\nSubscribe to our newsletter for weekly updates.\nMore content."
    result = clean(text)
    assert "Subscribe" not in result


def test_word_count():
    assert word_count("hello world foo") == 3


def test_is_content_rich_true():
    assert is_content_rich(SAMPLE_TEXT, min_words=20)


def test_is_content_rich_false():
    assert not is_content_rich("Too short.", min_words=50)


def test_chunk_produces_chunks():
    chunks = chunk(SAMPLE_TEXT, size=64, overlap=8)
    assert len(chunks) >= 1
    assert all(isinstance(c, str) for c in chunks)


def test_chunk_respects_size():
    chunks = chunk(SAMPLE_TEXT, size=30, overlap=5)
    for c in chunks:
        assert token_count(c) <= 50  # some slack for overlap


def test_token_count():
    assert token_count("hello world") == 2


def test_format_records():
    chunks = chunk(SAMPLE_TEXT, size=128, overlap=16)
    records = format_records(
        chunks,
        page_id=1,
        url="https://example.com",
        title="Test",
        author="",
        date="",
        session_id="sess-1",
        token_counts=[token_count(c) for c in chunks],
    )
    assert len(records) == len(chunks)
    assert records[0].source_url == "https://example.com"
    assert records[0].metadata["chunk_index"] == 0
