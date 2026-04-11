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

# Serialize thinking display so concurrent chunk workers don't interleave output
_thinking_lock = asyncio.Lock()


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
    from dataforge.config import model_supports_thinking, get_settings
    s = get_settings()
    if model_supports_thinking(s.llm_provider, s.llm_model):
        return await _generate_with_thinking(
            client, record,
            format=format, goal=goal,
            n_per_chunk=n_per_chunk, custom_system=custom_system,
        )

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


async def _generate_with_thinking(
    client: LLMClient,
    record: DataRecord,
    *,
    format: str,
    goal: str,
    n_per_chunk: int,
    custom_system: str,
) -> list[GeneratedSample]:
    """Generate samples with live thinking token display (for capable models)."""
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel

    prompt = build_prompt(record.content, format=format, goal=goal,
                          n=n_per_chunk, custom_system=custom_system)
    messages = [
        {"role": "system", "content": prompt.system},
        {"role": "user",   "content": prompt.user},
    ]

    thinking_buf: list[str] = []
    _console = Console()

    def _make_panel() -> Panel:
        text = "".join(thinking_buf)
        # Show last 400 chars so very long thinking doesn't swamp the terminal
        display = ("…" + text[-400:]) if len(text) > 400 else text
        return Panel(
            f"[dim italic]{display}[/]",
            title="[bold yellow]Thinking…[/]",
            border_style="dim yellow",
            padding=(0, 1),
        )

    async with _thinking_lock:
        live = Live(_make_panel(), console=_console, refresh_per_second=8,
                    transient=True)

        def on_thinking(chunk: str) -> None:
            thinking_buf.append(chunk)
            live.update(_make_panel())

        try:
            with live:
                resp = await client.complete_stream(
                    messages,
                    on_thinking=on_thinking,
                )
        except Exception as exc:
            log.warning(f"Thinking-stream generation failed for chunk {record.chunk_id}: {exc}")
            # Fall back to regular completion
            try:
                resp = await client.complete(messages)
            except Exception:
                return []

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
