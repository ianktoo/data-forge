"""Adapter test for the generation message contract.

Verifies that malformed LLM output (nested objects where plain text was
expected, or an invalid role) is normalized or rejected at the generation
boundary, instead of reaching quality scoring / storage in a shape that
crashes downstream (see: 'dict' object has no attribute 'split').
"""
from __future__ import annotations

import pytest

from dataforge.generators.contracts import (
    GenerationParseError,
    to_message_dict,
    to_message_list,
)
from dataforge.generators.synthetic import _items_to_samples, _to_messages


def test_to_message_dict_coerces_non_string_content():
    msg = to_message_dict("user", {"text": "nested"})
    assert msg["role"] == "user"
    assert isinstance(msg["content"], str)
    assert "nested" in msg["content"]


def test_to_message_dict_rejects_invalid_role():
    with pytest.raises(GenerationParseError):
        to_message_dict("narrator", "hello")


def test_to_message_list_rejects_non_list():
    with pytest.raises(GenerationParseError):
        to_message_list({"role": "user", "content": "not a list"})


def test_to_messages_qa_coerces_dict_content():
    # A model that returned a structured object instead of a plain string.
    item = {"question": {"text": "What is X?"}, "answer": "X is Y."}
    msgs = _to_messages(item, "qa")
    assert isinstance(msgs[0]["content"], str)
    assert msgs[1]["content"] == "X is Y."


def test_to_messages_conversation_rejects_bad_role():
    item = {"messages": [{"role": "bot", "content": "hi"}]}
    with pytest.raises(GenerationParseError):
        _to_messages(item, "conversation")


def test_items_to_samples_skips_malformed_item_keeps_valid_ones():
    items = [
        {"messages": [{"role": "bot", "content": "hi"}]},  # invalid role -> skipped
        {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]},
    ]
    samples = _items_to_samples(items, chunk_id=1, format="conversation",
                                 system_prompt="sys", raw_response="raw")
    assert len(samples) == 1
    assert samples[0].messages[0]["content"] == "hi"
