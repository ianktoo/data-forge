"""#69: `dataforge scrape` and the MCP scrape_page tool: pages and tables,
no AI, same politeness as every other request."""
from __future__ import annotations

import csv
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from typer.testing import CliRunner

from dataforge import scrape as qs
from dataforge.cli.app import app

runner = CliRunner()

LONG = " ".join(["Emergency kits need water, food, medicine and a radio."] * 12)
PAGES = {
    "/schools": f"""<html><head><title>Middle Schools</title></head><body><main>
        <p>{LONG}</p>
        <table><tr><td><strong>School</strong></td><td><strong>Phone</strong></td></tr>
        <tr><td>Ochoa</td><td>(510) 723-3130</td></tr>
        <tr><td>Harte</td><td>(510) 723-3100</td></tr></table></main></body></html>""",
    "/copy": f"<html><head><title>Copy</title></head><body><main><p>{LONG}</p></main></body></html>",
    "/short": "<html><head><title>Short</title></head><body><main><p>Hi.</p></main></body></html>",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/robots.txt":
            body, ctype = "User-agent: *\nDisallow: /private\n", "text/plain"
        elif self.path == "/file.pdf":
            body, ctype = "%PDF-1.4", "application/pdf"
        elif self.path in PAGES:
            body, ctype = PAGES[self.path], "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def site():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


async def test_scrape_urls_statuses_tables_and_checks(site):
    pages = await qs.scrape_urls(
        [f"{site}/schools", f"{site}/copy", f"{site}/short", f"{site}/private/x",
         f"{site}/nope", f"{site}/file.pdf", "not a url"],
        rate_limit=1000, check=True)
    assert [p.status for p in pages] == [
        "ok", "ok", "ok", "blocked by robots.txt", "http 404", "not html",
        "error: not a valid http(s) URL"]
    schools = pages[0]
    assert schools.title == "Middle Schools"
    (t,) = schools.tables
    assert t.headers == ["School", "Phone"]
    assert t.rows == [["Ochoa", "(510) 723-3130"], ["Harte", "(510) 723-3100"]]
    assert schools.warnings == []
    assert pages[1].warnings == []          # same prose, but /schools also has a table
    assert any("very little text" in w for w in pages[2].warnings)


async def test_duplicate_text_is_flagged(site):
    pages = await qs.scrape_urls([f"{site}/copy", f"{site}/copy?ref=x"], rate_limit=1000, check=True)
    assert pages[1].warnings == [f"same text as {site}/copy"]


async def test_no_tables_option(site):
    (page,) = await qs.scrape_urls([f"{site}/schools"], rate_limit=1000, tables=False)
    assert page.ok and page.tables == []


async def test_write_outputs(site, tmp_path):
    pages = await qs.scrape_urls([f"{site}/schools", f"{site}/nope"], rate_limit=1000)
    written = qs.write_outputs(pages, tmp_path)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["page_001.md", "page_001_table_1.csv", "page_001_table_1.json",
                     "pages.jsonl", "tables.jsonl"]
    lines = [json.loads(x) for x in (tmp_path / "pages.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["status"] for x in lines] == ["ok", "http 404"]
    with open(tmp_path / "page_001_table_1.csv", encoding="utf-8-sig", newline="") as f:
        assert list(csv.reader(f))[0] == ["School", "Phone"]
    tj = json.loads((tmp_path / "page_001_table_1.json").read_text(encoding="utf-8"))
    assert tj["source_url"] == f"{site}/schools" and tj["records"][0] == {"School": "Ochoa", "Phone": "(510) 723-3130"}
    assert written["tables"]


async def test_write_outputs_respects_formats(site, tmp_path):
    pages = await qs.scrape_urls([f"{site}/schools"], rate_limit=1000)
    qs.write_outputs(pages, tmp_path, ("csv",))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["page_001_table_1.csv", "page_001_table_1.json"]


def test_cli_json_output(site, tmp_path):
    out = tmp_path / "o"
    result = runner.invoke(app, ["--json", "scrape", f"{site}/schools", f"{site}/private/p", "-o", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [p["status"] for p in data["pages"]] == ["ok", "blocked by robots.txt"]
    assert "markdown" not in data["pages"][0]                    # summary stays small
    assert data["pages"][0]["tables"][0]["headers"] == ["School", "Phone"]
    assert (out / "page_001_table_1.csv").exists()


def test_cli_fails_when_nothing_was_scraped(site, tmp_path):
    result = runner.invoke(app, ["scrape", f"{site}/nope", "-o", str(tmp_path / "o")])
    assert result.exit_code == 1


def test_cli_rejects_unknown_format(tmp_path):
    result = runner.invoke(app, ["scrape", "https://example.org", "-f", "xlsx", "-o", str(tmp_path)])
    assert result.exit_code == 2


async def test_mcp_scrape_page(site):
    pytest.importorskip("mcp")
    from dataforge.mcp_server import scrape_page

    out = await scrape_page(f"{site}/schools", max_chars=40)
    assert out["status"] == "ok" and out["title"] == "Middle Schools"
    assert out["tables"][0]["rows"][0] == ["Ochoa", "(510) 723-3130"]
    assert len(out["markdown"]) == 40 and out["truncated"] is True
    assert "text" not in out


async def test_progress_reports_each_url_before_and_after(site):
    events = []
    await qs.scrape_urls([f"{site}/schools", f"{site}/nope"], rate_limit=1000,
                         progress=lambda i, n, url, page: events.append(
                             (i, n, page.status if page else None)))
    assert events == [(1, 2, None), (1, 2, "ok"), (2, 2, None), (2, 2, "http 404")]


# ── A scrape folder as dataset input ──────────────────────────────────────────

async def test_load_scrape_dir_reads_ok_pages_from_jsonl(site, tmp_path):
    pages = await qs.scrape_urls([f"{site}/schools", f"{site}/nope"], rate_limit=1000)
    qs.write_outputs(pages, tmp_path)
    (page,) = qs.load_scrape_dir(tmp_path)
    assert page.url == f"{site}/schools" and page.title == "Middle Schools"
    assert "Emergency kits" in page.markdown and page.word_count > 50


async def test_load_scrape_dir_falls_back_to_markdown_files(site, tmp_path):
    pages = await qs.scrape_urls([f"{site}/schools", f"{site}/copy"], rate_limit=1000)
    qs.write_outputs(pages, tmp_path, ("md",))
    loaded = qs.load_scrape_dir(tmp_path)
    assert [p.url for p in loaded] == [f"{site}/schools", f"{site}/copy"]
    assert loaded[0].title == "Middle Schools"
    assert not loaded[0].markdown.startswith("<!--") and "Emergency kits" in loaded[0].markdown


async def test_imported_pages_go_through_processing(site, tmp_path, tmp_settings):
    from sqlmodel import select

    from dataforge.agents import PipelineContext, ProcessorAgent
    from dataforge.storage import DataFormat, DiscoveredURL, open_session

    pages = await qs.scrape_urls([f"{site}/schools", f"{site}/copy"], rate_limit=1000)
    qs.write_outputs(pages, tmp_path / "scrape")
    ctx = PipelineContext(session_id="imp-1", session_name="imp", goal="", format=DataFormat.qa,
                          seed_urls=[], settings=tmp_settings)
    ids = qs.import_into_session(ctx, qs.load_scrape_dir(tmp_path / "scrape"))
    assert len(ids) == 2 and ctx.scraped_page_ids == ids
    assert ctx.selected_urls == [f"{site}/schools", f"{site}/copy"]
    with open_session(tmp_settings.db_path) as db:
        rows = db.exec(select(DiscoveredURL).where(DiscoveredURL.session_id == "imp-1")).all()
    assert all(r.scraped and r.selected for r in rows)

    ctx = await ProcessorAgent(ctx).run()
    assert ctx.processed_chunk_ids


def test_resolve_scrape_dir_accepts_folder_file_or_parent(tmp_path):
    old, new = tmp_path / "scrape" / "20260101-000000", tmp_path / "scrape" / "20260201-000000"
    for d in (old, new):
        d.mkdir(parents=True)
        (d / "pages.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "scrape" / "20260301-000000").mkdir()  # newer but empty: skipped
    assert qs.resolve_scrape_dir(old) == old.resolve()
    assert qs.resolve_scrape_dir(old / "pages.jsonl") == old.resolve()
    assert qs.resolve_scrape_dir(tmp_path / "scrape") == new.resolve()


def test_resolve_scrape_dir_errors_name_the_full_location(tmp_path):
    with pytest.raises(ValueError, match="Not found: .*missing"):
        qs.resolve_scrape_dir(tmp_path / "missing")
    with pytest.raises(ValueError, match=r"No scraped pages in .*pages\.jsonl or page_NNN\.md"):
        qs.resolve_scrape_dir(tmp_path)
    other = tmp_path / "notes.txt"
    other.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="is a file"):
        qs.resolve_scrape_dir(other)
    assert str(tmp_path.resolve()) in _err(tmp_path)


def _err(path) -> str:
    try:
        qs.resolve_scrape_dir(path)
    except ValueError as e:
        return str(e)
    return ""
