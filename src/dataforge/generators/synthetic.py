"""Orchestrate LLM generation over processed chunks."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

from dataforge.processors.formatter import DataRecord
from dataforge.utils import get_logger

from .contracts import GenerationParseError, to_message_dict, to_message_list
from .llm import LLMClient
from .templates import build_prompt

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
    from dataforge.config import get_settings, model_supports_thinking
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
    except Exception as exc:
        log.warning(f"Generation failed for chunk {record.chunk_id}: {exc}")
        return []
    return _items_to_samples(items, record.chunk_id, format, prompt.system, resp.content)


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
    return _items_to_samples(items, record.chunk_id, format, prompt.system, resp.content)


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

def _items_to_samples(
    items: list[dict],
    chunk_id: int,
    format: str,
    system_prompt: str,
    raw_response: str,
) -> list[GeneratedSample]:
    """Validate each parsed item through the message contract.

    A single malformed item (e.g. a nested object where plain text was
    expected) is skipped and logged rather than discarding every other valid
    sample produced for the same chunk.
    """
    samples: list[GeneratedSample] = []
    for item in items:
        try:
            messages = _to_messages(item, format)
        except GenerationParseError as exc:
            log.warning(f"Skipping malformed sample for chunk {chunk_id}: {exc}")
            continue
        samples.append(GeneratedSample(
            chunk_id=chunk_id,
            format=format,
            system_prompt=system_prompt,
            messages=messages,
            raw_response=raw_response,
        ))
    return samples


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
    """Build a validated message list from a raw parsed LLM item.

    Every path goes through ``contracts.to_message_dict``/``to_message_list``
    so a malformed field (e.g. a nested object where the model was asked for
    plain text) is normalized here, at the generation boundary, instead of
    surfacing as an unrelated crash in quality scoring or export.
    """
    if format == "qa":
        return [
            to_message_dict("user",      item.get("question", "")),
            to_message_dict("assistant", item.get("answer", "")),
        ]
    if format == "instruction":
        user_content = item.get("instruction", "")
        if not isinstance(user_content, str):
            user_content = str(user_content)
        if item.get("input"):
            user_content += f"\n\nInput: {item['input']}"
        return [
            to_message_dict("user",      user_content),
            to_message_dict("assistant", item.get("output", "")),
        ]
    if format == "conversation":
        return to_message_list(item.get("messages", []))
    return [to_message_dict("user", str(item))]
