"""SQLite engine and session factory via SQLModel."""
from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

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


def persist_url_selection(db: Session, session_id: str, selected_urls: set[str]) -> None:
    """Update DiscoveredURL.selected in DB to match the user's chosen subset."""
    rows = db.exec(
        select(DiscoveredURL).where(DiscoveredURL.session_id == session_id)
    ).all()
    for row in rows:
        row.selected = row.url in selected_urls
        db.add(row)
    db.commit()
