"""Tests for generators — templates and parsing logic."""
from __future__ import annotations

import json

import pytest

from dataforge.generators.templates import build_prompt
from dataforge.generators.synthetic import _parse_response, _to_messages


def test_build_prompt_qa():
    pair = build_prompt("Some content here.", format="qa", goal="test", n=2)
    assert "question" in pair.system.lower() or "qa" in pair.system.lower() or "pairs" in pair.system.lower()
    assert "Some content" in pair.user


def test_build_prompt_instruction():
    pair = build_prompt("Content.", format="instruction", goal="test", n=1)
    assert "instruction" in pair.system.lower()


def test_build_prompt_conversation():
    pair = build_prompt("Content.", format="conversation", goal="test", n=1)
    assert "conversation" in pair.system.lower()


def test_parse_response_plain_json():
    raw = json.dumps([{"question": "What?", "answer": "This."}])
    result = _parse_response(raw, "qa")
    assert len(result) == 1
    assert result[0]["question"] == "What?"


def test_parse_response_markdown_fence():
    raw = "```json\n[{\"question\": \"Why?\", \"answer\": \"Because.\"}]\n```"
    result = _parse_response(raw, "qa")
    assert len(result) == 1


def test_parse_response_bad_json():
    result = _parse_response("not json at all", "qa")
    assert result == []


def test_to_messages_qa():
    item = {"question": "What is X?", "answer": "X is Y."}
    msgs = _to_messages(item, "qa")
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


def test_to_messages_instruction():
    item = {"instruction": "Summarise.", "input": "Long text.", "output": "Short."}
    msgs = _to_messages(item, "instruction")
    assert "Summarise" in msgs[0]["content"]
    assert msgs[1]["content"] == "Short."


def test_to_messages_conversation():
    item = {"messages": [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]}
    msgs = _to_messages(item, "conversation")
    assert len(msgs) == 2
