"""#59: the per-page (session_id, url) lookup must use an index, including on
databases created before the index existed, and skip_known must look up only
the newly discovered URLs."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from dataforge.agents.explorer import _LOOKUP_BATCH, ExplorerAgent
from dataforge.storage import DiscoveredURL, init_db, open_session

SCRAPER_LOOKUP = (
    "SELECT id FROM discovered_url WHERE session_id = ? AND url = ?"
)


def _plan(db: Path, sql: str, params: tuple) -> str:
    con = sqlite3.connect(db)
    try:
        return " ".join(str(r[-1]) for r in con.execute(f"EXPLAIN QUERY PLAN {sql}", params))
    finally:
        con.close()


def test_scraper_lookup_uses_the_composite_index(tmp_path):
    db = tmp_path / "new.db"
    init_db(db)
    plan = _plan(db, SCRAPER_LOOKUP, ("s", "https://example.org/"))
    assert "ix_discovered_url_session_url" in plan, plan


def test_existing_database_gets_the_indexes(tmp_path):
    """A database created by an older DataForge (table without the indexes)
    is upgraded in place when opened, without losing rows."""
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE discovered_url (
        id INTEGER PRIMARY KEY, session_id VARCHAR NOT NULL, url VARCHAR NOT NULL,
        source VARCHAR NOT NULL, selected BOOLEAN NOT NULL, scraped BOOLEAN NOT NULL,
        http_status INTEGER, discovered_at DATETIME NOT NULL)""")
    con.execute("CREATE INDEX ix_discovered_url_session_id ON discovered_url (session_id)")
    con.execute("INSERT INTO discovered_url VALUES (1,'s','https://example.org/','sitemap',1,0,NULL,'2026-01-01')")
    con.commit()
    con.close()

    init_db(db)

    names = {r[0] for r in sqlite3.connect(db).execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='discovered_url'")}
    assert {"ix_discovered_url_session_url", "ix_discovered_url_url"} <= names
    assert "ix_discovered_url_session_url" in _plan(db, SCRAPER_LOOKUP, ("s", "x"))
    assert sqlite3.connect(db).execute("SELECT count(*) FROM discovered_url").fetchone()[0] == 1


def _agent(db: Path, session_id: str) -> ExplorerAgent:
    ctx = SimpleNamespace(session_id=session_id, settings=SimpleNamespace(db_path=db))
    return ExplorerAgent(ctx)  # type: ignore[arg-type]


def test_skip_known_filters_prior_scrapes_across_batches(tmp_path):
    db = tmp_path / "e.db"
    init_db(db)
    n = _LOOKUP_BATCH * 2 + 7  # more than one batch
    urls = [f"https://example.org/p{i}" for i in range(n)]
    with open_session(db) as s:
        for i, u in enumerate(urls):
            # Every third URL was scraped in an earlier session; one URL was
            # scraped only in *this* session (must not be skipped); one was
            # discovered earlier but never scraped (must not be skipped).
            s.add(DiscoveredURL(session_id="old", url=u, scraped=(i % 3 == 0)))
        s.add(DiscoveredURL(session_id="now", url=urls[1], scraped=True))
        s.commit()

    kept = _agent(db, "now")._filter_already_scraped(urls)

    assert kept == [u for i, u in enumerate(urls) if i % 3 != 0]
