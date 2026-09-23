"""SQLModel table definitions for all pipeline entities."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

# ── Enums ─────────────────────────────────────────────────────────────────────

class PipelineStage(StrEnum):
    discovery  = "discovery"
    collection = "collection"
    streaming  = "streaming"   # fused collection+processing+generation
    processing = "processing"
    generation = "generation"
    quality    = "quality"
    export     = "export"
    completed  = "completed"


class SessionStatus(StrEnum):
    active    = "active"
    paused    = "paused"
    completed = "completed"
    failed    = "failed"


class DataFormat(StrEnum):
    qa           = "qa"
    instruction  = "instruction"
    conversation = "conversation"
    custom       = "custom"


class URLSource(StrEnum):
    sitemap = "sitemap"
    manual  = "manual"
    file    = "file"
    crawl   = "crawl"


# ── Tables ────────────────────────────────────────────────────────────────────

class PipelineSession(SQLModel, table=True):
    __tablename__ = "pipeline_session"

    id: str          = Field(primary_key=True)
    name: str
    goal: str        = Field(default="")
    format: str      = Field(default=DataFormat.qa)
    stage: str       = Field(default=PipelineStage.discovery)
    status: str      = Field(default=SessionStatus.active)
    seed_urls: str   = Field(default="[]")   # JSON list
    config_json: str = Field(default="{}")   # JSON dict
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def seed_url_list(self) -> list[str]:
        return json.loads(self.seed_urls)

    def config(self) -> dict[str, Any]:
        return json.loads(self.config_json)

    def session_dir(self, fallback_output_dir: Path) -> Path:
        """Where this session's files live. A recipe can set its own
        output_dir, so the folder recorded when the run started wins over the
        current setting; sessions from before it was recorded use the fallback."""
        recorded = self.config().get("output_dir")
        return Path(recorded or fallback_output_dir) / "sessions" / self.id


class DiscoveredURL(SQLModel, table=True):
    __tablename__ = "discovered_url"
    # The scraper looks up (session_id, url) once per page; without this each
    # lookup scanned the whole session, O(N^2) per run (#59). The url index
    # serves the cross-session skip_known check. database.py also creates
    # both on databases made before they existed.
    __table_args__ = (
        Index("ix_discovered_url_session_url", "session_id", "url"),
        Index("ix_discovered_url_url", "url"),
    )

    id: int | None   = Field(default=None, primary_key=True)
    session_id: str     = Field(index=True)
    url: str
    source: str         = Field(default=URLSource.manual)
    selected: bool      = Field(default=True)
    scraped: bool       = Field(default=False)
    http_status: int | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ScrapedPage(SQLModel, table=True):
    __tablename__ = "scraped_page"

    id: int | None   = Field(default=None, primary_key=True)
    session_id: str     = Field(index=True)
    url_id: int
    url: str
    title: str          = Field(default="")
    author: str         = Field(default="")
    published_date: str = Field(default="")
    raw_path: str       = Field(default="")   # path to saved HTML/text
    word_count: int     = Field(default=0)
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProcessedChunk(SQLModel, table=True):
    __tablename__ = "processed_chunk"

    id: int | None   = Field(default=None, primary_key=True)
    session_id: str     = Field(index=True)
    page_id: int
    content: str
    token_count: int    = Field(default=0)
    chunk_index: int    = Field(default=0)
    metadata_json: str  = Field(default="{}")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def parsed_meta(self) -> dict[str, Any]:
        return json.loads(self.metadata_json)


class SyntheticSample(SQLModel, table=True):
    __tablename__ = "synthetic_sample"

    id: int | None       = Field(default=None, primary_key=True)
    session_id: str         = Field(index=True)
    chunk_id: int
    format: str
    system_prompt: str      = Field(default="")
    messages_json: str      = Field(default="[]")  # [{role, content}]
    quality_score: float    = Field(default=0.0)
    approved: bool          = Field(default=False)
    rejection_reason: str   = Field(default="")  # "" if approved
    created_at: datetime    = Field(default_factory=lambda: datetime.now(UTC))

    def messages(self) -> list[dict[str, str]]:
        return json.loads(self.messages_json)


class ExportRecord(SQLModel, table=True):
    __tablename__ = "export_record"

    id: int | None   = Field(default=None, primary_key=True)
    session_id: str     = Field(index=True)
    destination: str    # local | huggingface | kaggle
    path_or_url: str
    format: str
    sample_count: int   = Field(default=0)
    stage_snapshot: str = Field(default="")  # which stage data came from
    exported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
