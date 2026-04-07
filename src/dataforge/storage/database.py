"""SQLite engine and session factory via SQLModel."""
from __future__ import annotations

from pathlib import Path
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine

from .models import (  # noqa: F401 — ensure models are registered
    DiscoveredURL,
    ExportRecord,
    PipelineSession,
    ProcessedChunk,
    ScrapedPage,
    SyntheticSample,
)

_engine = None


def _get_engine(db_path: Path):
    global _engine
    if _engine is None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{db_path}"
        _engine = create_engine(url, connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(_engine)
    return _engine


def init_db(db_path: Path) -> None:
    _get_engine(db_path)


def get_session(db_path: Path) -> Generator[Session, None, None]:
    engine = _get_engine(db_path)
    with Session(engine) as session:
        yield session


def open_session(db_path: Path) -> Session:
    return Session(_get_engine(db_path))
