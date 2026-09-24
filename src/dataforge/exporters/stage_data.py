"""Export what a session has produced so far, in general-purpose formats.

The training export (``ExporterAgent``) only knows about generated samples.
Each earlier stage also leaves useful data behind: the discovered URL list,
the scraped pages as Markdown, and the cleaned chunks. This module writes any
of those out as plain files anyone can open (Markdown, text, JSON, CSV), with
no LLM involved, so a session is useful even if it never reaches generation.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import select

from dataforge.storage import (
    DiscoveredURL,
    ProcessedChunk,
    ScrapedPage,
    SyntheticSample,
    open_session,
)

from .local import write_jsonl


@dataclass(frozen=True)
class DataKind:
    key: str
    label: str
    formats: tuple[str, ...]


# Ordered by pipeline stage. The first format of each kind is the default.
KINDS: dict[str, DataKind] = {
    "urls":    DataKind("urls",    "Discovered URLs",           ("txt", "csv", "json")),
    "pages":   DataKind("pages",   "Scraped pages",             ("md", "txt", "json", "jsonl")),
    "chunks":  DataKind("chunks",  "Text chunks",               ("jsonl", "json", "txt")),
    "samples": DataKind("samples", "Generated samples (all)",   ("jsonl", "json")),
}

FORMAT_LABELS = {
    "md":    "Markdown (one .md file per page)",
    "txt":   "Plain text",
    "json":  "JSON (one file)",
    "jsonl": "JSON Lines (one record per line)",
    "csv":   "CSV spreadsheet",
}


def available_data(db_path: Path, session_id: str) -> dict[str, int]:
    """Row counts per data kind for a session; kinds with no rows are omitted."""
    with open_session(db_path) as db:
        counts = {
            "urls":    len(db.exec(select(DiscoveredURL.id).where(DiscoveredURL.session_id == session_id)).all()),
            "pages":   len(db.exec(select(ScrapedPage.id).where(ScrapedPage.session_id == session_id)).all()),
            "chunks":  len(db.exec(select(ProcessedChunk.id).where(ProcessedChunk.session_id == session_id)).all()),
            "samples": len(db.exec(select(SyntheticSample.id).where(SyntheticSample.session_id == session_id)).all()),
        }
    return {k: n for k, n in counts.items() if n}


def export_stage_data(
    db_path: Path,
    session_id: str,
    kind: str,
    formats: list[str],
    out_dir: Path,
) -> dict[str, Path]:
    """Write one kind of session data in each requested format.

    Returns ``{format: path}``; for per-page Markdown/text the path is a folder.
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown data kind: {kind}")
    bad = [f for f in formats if f not in KINDS[kind].formats]
    if bad:
        raise ValueError(f"{kind} cannot be exported as: {', '.join(bad)}")

    records = _load(db_path, session_id, kind)
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
    writer = _WRITERS[kind]
    return {fmt: writer(records, fmt, out_dir) for fmt in formats}


# -- Loading -------------------------------------------------------------------

def _load(db_path: Path, session_id: str, kind: str) -> list[dict]:
    with open_session(db_path) as db:
        if kind == "urls":
            rows = db.exec(select(DiscoveredURL).where(DiscoveredURL.session_id == session_id)).all()
            return [
                {"url": r.url, "source": r.source, "selected": r.selected, "scraped": r.scraped}
                for r in rows
            ]
        if kind == "pages":
            rows = db.exec(select(ScrapedPage).where(ScrapedPage.session_id == session_id)).all()
            return [
                {
                    "id": r.id, "url": r.url, "title": r.title, "author": r.author,
                    "published_date": r.published_date, "word_count": r.word_count,
                    "markdown": _read(r.raw_path),
                }
                for r in rows
            ]
        if kind == "chunks":
            rows = db.exec(select(ProcessedChunk).where(ProcessedChunk.session_id == session_id)).all()
            return [
                {
                    "id": r.id, "page_id": r.page_id, "chunk_index": r.chunk_index,
                    "source_url": r.parsed_meta().get("source_url", ""),
                    "title": r.parsed_meta().get("title", ""),
                    "token_count": r.token_count, "content": r.content,
                }
                for r in rows
            ]
        rows = db.exec(select(SyntheticSample).where(SyntheticSample.session_id == session_id)).all()
        return [
            {
                "id": r.id, "chunk_id": r.chunk_id, "format": r.format,
                "messages": r.messages(), "quality_score": r.quality_score,
                "approved": r.approved,
            }
            for r in rows
        ]


def _read(path: str) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


# -- Writers -------------------------------------------------------------------

def _write_json(records: list[dict], path: Path) -> Path:
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_urls(records: list[dict], fmt: str, out: Path) -> Path:
    if fmt == "txt":
        path = out / "urls.txt"
        path.write_text("".join(r["url"] + "\n" for r in records), encoding="utf-8")
        return path
    if fmt == "csv":
        path = out / "urls.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["url", "source", "selected", "scraped"])
            w.writeheader()
            w.writerows(records)
        return path
    return _write_json(records, out / "urls.json")


def _write_pages(records: list[dict], fmt: str, out: Path) -> Path:
    if fmt in ("md", "txt"):
        folder = out / ("pages_md" if fmt == "md" else "pages_txt")
        folder.mkdir(parents=True, exist_ok=True)
        used: set[str] = set()
        for i, r in enumerate(records, 1):
            name = _unique(_slug(r["title"] or r["url"]) or f"page-{i}", used)
            if fmt == "md":
                header = (
                    f"---\ntitle: {json.dumps(r['title'], ensure_ascii=False)}\nsource: {r['url']}\n"
                    f"words: {r['word_count']}\n---\n\n"
                )
                (folder / f"{name}.md").write_text(header + r["markdown"], encoding="utf-8")
            else:
                body = f"{r['title']}\n{r['url']}\n\n{_plain(r['markdown'])}\n"
                (folder / f"{name}.txt").write_text(body, encoding="utf-8")
        return folder
    if fmt == "jsonl":
        path = out / "pages.jsonl"
        write_jsonl(records, path)
        return path
    return _write_json(records, out / "pages.json")


def _write_chunks(records: list[dict], fmt: str, out: Path) -> Path:
    if fmt == "txt":
        path = out / "chunks.txt"
        path.write_text(
            "\n\n".join(f"### {r['source_url']} (chunk {r['chunk_index']})\n{r['content']}" for r in records),
            encoding="utf-8",
        )
        return path
    if fmt == "jsonl":
        path = out / "chunks.jsonl"
        write_jsonl(records, path)
        return path
    return _write_json(records, out / "chunks.json")


def _write_samples(records: list[dict], fmt: str, out: Path) -> Path:
    if fmt == "jsonl":
        path = out / "samples_all.jsonl"
        write_jsonl(records, path)
        return path
    return _write_json(records, out / "samples_all.json")


_WRITERS = {"urls": _write_urls, "pages": _write_pages, "chunks": _write_chunks, "samples": _write_samples}


# -- Helpers -------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_MARK_RE = re.compile(r"^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+)|[*_`]{1,3}", re.M)


def _slug(text: str, max_len: int = 60) -> str:
    text = re.sub(r"^https?://", "", text.lower())
    return _SLUG_RE.sub("-", text).strip("-")[:max_len].strip("-")


def _unique(name: str, used: set[str]) -> str:
    candidate, n = name, 2
    while candidate in used:
        candidate = f"{name}-{n}"
        n += 1
    used.add(candidate)
    return candidate


def _plain(markdown: str) -> str:
    """Markdown to readable plain text: keep link text, drop markup."""
    return _MD_MARK_RE.sub("", _MD_LINK_RE.sub(r"\1", markdown)).strip()
