"""Local file exporter — JSONL, Parquet, CSV, and Unsloth-compatible formats."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from dataforge.utils import get_logger

log = get_logger("export.local")


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o750)
    return path


def write_jsonl(records: list[dict], path: Path) -> int:
    _ensure(path.parent)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log.info(f"Wrote {len(records)} records → {path}")
    return len(records)


def write_parquet(records: list[dict], path: Path) -> int:
    _ensure(path.parent)
    if not records:
        return 0
    table = pa.Table.from_pylist(records)
    pq.write_table(table, path, compression="snappy")
    log.info(f"Wrote {len(records)} records → {path}")
    return len(records)


def write_csv(records: list[dict], path: Path) -> int:
    _ensure(path.parent)
    if not records:
        return 0
    _ensure(path.parent)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
    log.info(f"Wrote {len(records)} records → {path}")
    return len(records)


def to_unsloth_format(samples: list[dict], system_prompt: str = "") -> list[dict]:
    """Convert any sample list to Unsloth / ShareGPT format."""
    out = []
    for s in samples:
        msgs = s.get("messages", [])
        conversations = []
        if system_prompt:
            conversations.append({"from": "system", "value": system_prompt})
        for m in msgs:
            role = m.get("role", "user")
            mapping = {"user": "human", "assistant": "gpt", "system": "system"}
            conversations.append({"from": mapping.get(role, role), "value": m.get("content", "")})
        out.append({"conversations": conversations})
    return out


def export_all_formats(
    records: list[dict],
    export_dir: Path,
    name: str = "dataset",
    include_unsloth: bool = True,
    system_prompt: str = "",
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    paths["jsonl"]   = export_dir / f"{name}.jsonl"
    paths["parquet"] = export_dir / f"{name}.parquet"
    paths["csv"]     = export_dir / f"{name}.csv"

    write_jsonl(records, paths["jsonl"])
    write_parquet(records, paths["parquet"])
    write_csv(records, paths["csv"])

    if include_unsloth:
        unsloth = to_unsloth_format(records, system_prompt)
        paths["unsloth"] = export_dir / f"{name}_unsloth.jsonl"
        write_jsonl(unsloth, paths["unsloth"])

    return paths
