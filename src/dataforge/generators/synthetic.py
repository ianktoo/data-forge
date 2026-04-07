"""Orchestrate LLM generation over processed chunks."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncIterator

from dataforge.processors.formatter import DataRecord
from dataforge.utils import get_logger

from .llm import LLMClient, LLMResponse
from .templates import PromptPair, build_prompt

log = get_logger("generator")


@dataclass
class GeneratedSample:
    chunk_id: int
    format: str
    system_prompt: str
    messages: list[dict]        # [{role, content}]
    raw_response: str
    quality_score: float = 0.0


async def generate_from_chunk(
    client: LLMClient,
    record: DataRecord,
    *,
    format: str,
    goal: str,
    n_per_chunk: int = 3,
    custom_system: str = "",
) -> list[GeneratedSample]:
    prompt = build_prompt(record.content, format=format, goal=goal,
                          n=n_per_chunk, custom_system=custom_system)
    messages = [
        {"role": "system", "content": prompt.system},
        {"role": "user",   "content": prompt.user},
    ]
    try:
        resp = await client.complete(messages)
        items = _parse_response(resp.content, format)
        return [
            GeneratedSample(
                chunk_id=record.chunk_id,
                format=format,
                system_prompt=prompt.system,
                messages=_to_messages(item, format),
                raw_response=resp.content,
            )
            for item in items
        ]
    except Exception as exc:
        log.warning(f"Generation failed for chunk {record.chunk_id}: {exc}")
        return []


async def generate_batch(
    client: LLMClient,
    records: list[DataRecord],
    *,
    format: str,
    goal: str,
    n_per_chunk: int = 3,
    custom_system: str = "",
    concurrency: int = 3,
) -> AsyncIterator[GeneratedSample]:
    sem = asyncio.Semaphore(concurrency)

    async def _worker(rec: DataRecord):
        async with sem:
            return await generate_from_chunk(
                client, rec,
                format=format, goal=goal,
                n_per_chunk=n_per_chunk, custom_system=custom_system,
            )

    tasks = [asyncio.create_task(_worker(r)) for r in records]
    for coro in asyncio.as_completed(tasks):
        samples = await coro
        for s in samples:
            yield s


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _parse_response(text: str, format: str) -> list[dict]:
    """Extract JSON array from LLM response, tolerating markdown fences."""
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        # Attempt to extract first JSON array
        import re
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
        log.warning(f"Could not parse LLM JSON for format={format}")
        return []


def _to_messages(item: dict, format: str) -> list[dict]:
    if format == "qa":
        return [
            {"role": "user",      "content": item.get("question", "")},
            {"role": "assistant", "content": item.get("answer", "")},
        ]
    if format == "instruction":
        user_content = item.get("instruction", "")
        if item.get("input"):
            user_content += f"\n\nInput: {item['input']}"
        return [
            {"role": "user",      "content": user_content},
            {"role": "assistant", "content": item.get("output", "")},
        ]
    if format == "conversation":
        return item.get("messages", [])
    return [{"role": "user", "content": str(item)}]
