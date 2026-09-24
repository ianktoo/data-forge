"""Every stage's data can be exported as plain files, with no samples required."""
from __future__ import annotations

import json

import pytest

from dataforge.exporters.stage_data import available_data, export_stage_data
from dataforge.storage import DiscoveredURL, ProcessedChunk, ScrapedPage, open_session

SID = "sess-1"


@pytest.fixture
def seeded(tmp_settings, tmp_path):
    raw = tmp_path / "page.md"
    raw.write_text("# Floods\n\nFloods are **dangerous**. See [the guide](https://x/guide).", encoding="utf-8")
    with open_session(tmp_settings.db_path) as db:
        db.add(DiscoveredURL(session_id=SID, url="https://x/floods"))
        db.add(DiscoveredURL(session_id=SID, url="https://x/fires", selected=False))
        db.add(ScrapedPage(session_id=SID, url_id=1, url="https://x/floods",
                           title="Floods", raw_path=str(raw), word_count=6))
        db.add(ScrapedPage(session_id=SID, url_id=2, url="https://x/floods-2",
                           title="Floods", raw_path=str(raw), word_count=6))
        db.add(ProcessedChunk(session_id=SID, page_id=1, content="Floods are dangerous.",
                              metadata_json=json.dumps({"source_url": "https://x/floods"})))
        db.commit()
    return tmp_settings


def test_available_data_counts_only_what_exists(seeded):
    assert available_data(seeded.db_path, SID) == {"urls": 2, "pages": 2, "chunks": 1}
    assert available_data(seeded.db_path, "other") == {}


def test_pages_as_markdown_one_file_each_with_front_matter(seeded, tmp_path):
    out = export_stage_data(seeded.db_path, SID, "pages", ["md"], tmp_path / "exp")
    files = sorted(out["md"].glob("*.md"))
    # Same title twice: names must not collide.
    assert [f.name for f in files] == ["floods-2.md", "floods.md"]
    text = files[1].read_text(encoding="utf-8")
    assert text.startswith('---\ntitle: "Floods"\nsource: https://x/floods')
    assert "Floods are **dangerous**" in text


def test_pages_as_plain_text_strip_markdown(seeded, tmp_path):
    out = export_stage_data(seeded.db_path, SID, "pages", ["txt"], tmp_path / "exp")
    body = (out["txt"] / "floods.txt").read_text(encoding="utf-8")
    assert "Floods are dangerous. See the guide." in body
    assert "**" not in body and "](" not in body


def test_pages_and_chunks_as_json(seeded, tmp_path):
    out = export_stage_data(seeded.db_path, SID, "pages", ["json", "jsonl"], tmp_path / "exp")
    pages = json.loads(out["json"].read_text(encoding="utf-8"))
    assert {p["url"] for p in pages} == {"https://x/floods", "https://x/floods-2"}
    assert len(out["jsonl"].read_text(encoding="utf-8").splitlines()) == 2

    out = export_stage_data(seeded.db_path, SID, "chunks", ["json"], tmp_path / "exp")
    chunks = json.loads(out["json"].read_text(encoding="utf-8"))
    assert chunks[0]["source_url"] == "https://x/floods"


def test_urls_as_text(seeded, tmp_path):
    out = export_stage_data(seeded.db_path, SID, "urls", ["txt", "csv"], tmp_path / "exp")
    assert out["txt"].read_text(encoding="utf-8").split() == ["https://x/floods", "https://x/fires"]
    assert out["csv"].read_text(encoding="utf-8").startswith("url,source,selected,scraped")


def test_rejects_format_that_does_not_fit_the_data(seeded, tmp_path):
    with pytest.raises(ValueError):
        export_stage_data(seeded.db_path, SID, "urls", ["md"], tmp_path / "exp")
