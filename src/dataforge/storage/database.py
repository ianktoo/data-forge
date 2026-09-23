"""SQLite engine and session factory via SQLModel."""
from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from .models import (  # noqa: F401 — ensure models are registered
    DiscoveredURL,
    ExportRecord,
    PipelineSession,
    ProcessedChunk,
    ScrapedPage,
    SyntheticSample,
)

_engines: dict[str, Engine] = {}


# Columns added after a table's first release. create_all() only creates
# missing *tables*, never adds columns to one that already exists, so an
# existing session DB from before a column was added needs this additive,
# idempotent ALTER TABLE — never a destructive migration.
_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("synthetic_sample", "rejection_reason", "TEXT NOT NULL DEFAULT ''"),
]


def _apply_additive_migrations(engine) -> None:  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        for table, column, ddl_type in _ADDITIVE_COLUMNS:
            existing = {
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
        conn.commit()


def _get_engine(db_path: Path):  # type: ignore[no-untyped-def]
    """Return the (cached) engine for this db_path.

    Cached per resolved path, not globally — a single global engine would
    silently keep serving the *first* db_path ever requested even after a
    caller asked for a different one (e.g. a session's own DB vs. a
    differently-configured one in the same process).
    """
    key = str(db_path.resolve())
    engine = _engines.get(key)
    if engine is None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{db_path}"
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
        SQLModel.metadata.create_all(engine)
        _apply_additive_migrations(engine)
        _engines[key] = engine
    return engine


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
