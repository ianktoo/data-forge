"""Regression test: engines must be cached per db_path, not globally.

A single global engine silently kept serving the *first* db_path ever
requested in the process, even when a caller later asked for a different
path — e.g. a second Settings instance pointed at a different session DB
would transparently read/write the first one instead.
"""
from __future__ import annotations

from dataforge.storage.database import _get_engine, init_db, open_session
from dataforge.storage.models import PipelineSession, SessionStatus


def test_different_db_paths_get_different_engines(tmp_path):
    path_a = tmp_path / "a" / "db.sqlite"
    path_b = tmp_path / "b" / "db.sqlite"
    init_db(path_a)
    init_db(path_b)

    assert _get_engine(path_a) is not _get_engine(path_b)

    with open_session(path_a) as db:
        db.add(PipelineSession(id="only-in-a", name="a", status=SessionStatus.active, seed_urls="[]"))
        db.commit()

    with open_session(path_b) as db:
        assert db.get(PipelineSession, "only-in-a") is None
