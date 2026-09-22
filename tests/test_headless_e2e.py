"""End-to-end headless run: recipe file -> live local HTTP server -> exported dataset.

Exercises the real `dataforge run <recipe.yaml>` path — discovery, URL filtering,
the streaming pipeline, quality scoring and local export — against a throwaway
HTTP server. Only the LLM is faked.
"""
from __future__ import annotations

import json
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from dataforge.cli.headless import EXIT_NO_URLS, EXIT_OK, run_recipe

PAGE = """<html><head><title>{title}</title></head><body><article>
<h1>{title}</h1>
<p>Floods are among the most common and costly natural disasters in the United
States. Know your flood risk before a storm arrives and sign up for your
community's warning system so that official alerts reach you quickly.</p>
<p>Assemble an emergency kit with water, non-perishable food, medication, a
flashlight and a battery powered radio. Store copies of important documents in
a waterproof container and agree an evacuation route to higher ground.</p>
<p>Never walk, swim or drive through flood waters. Six inches of moving water
can knock a person down and one foot can sweep away a vehicle. Turn around,
do not drown, and wait for the official all clear before returning home.</p>
</article></body></html>"""

PAGES = {
    "/hazard/floods": "Flood Safety",
    "/hazard/wildfires": "Wildfire Safety",
    "/plan/family": "Family Emergency Plan",
    "/kit/basics": "Build A Kit",
    "/press-release/2026": "Press Release",      # excluded by the recipe
    "/es/hazard/floods": "Inundaciones",         # excluded by the recipe
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence the default stderr logging
        pass

    def do_GET(self):
        if self.path == "/robots.txt":
            return self._send("User-agent: *\nAllow: /\n", "text/plain")
        if self.path == "/sitemap.xml":
            urls = "".join(
                f"<url><loc>http://{self.headers['Host']}{p}</loc></url>" for p in PAGES
            )
            return self._send(
                f'<?xml version="1.0"?><urlset '
                f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                "application/xml",
            )
        if self.path in PAGES:
            return self._send(PAGE.format(title=PAGES[self.path]), "text/html")
        self.send_response(404)
        self.end_headers()

    def _send(self, body: str, ctype: str):
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def site():
    server = HTTPServer(("127.0.0.1", 0), partial(_Handler))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def fake_llm(monkeypatch):
    """Deterministic, offline generation."""

    class _Usage:
        total_calls = 0
        prompt_tokens = 0
        completion_tokens = 0
        cost_usd = 0.0
        errors = 0

    class _LLM:
        usage = _Usage()

    async def gen(llm, record, **kw):
        from dataforge.generators.synthetic import GeneratedSample

        return [
            GeneratedSample(
                chunk_id=record.chunk_id,
                format="qa",
                system_prompt="sys",
                # Distinct per chunk: the quality stage fingerprints samples and
                # zeroes duplicates, so identical text here would be filtered out
                # and the test would be asserting on dedup, not on the run.
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Question {record.chunk_id}: what should I do to "
                            f"prepare for the hazard described in section "
                            f"{record.chunk_id}?"
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": (
                            f"For section {record.chunk_id}: know your risk, sign "
                            f"up for community alerts, build an emergency kit, and "
                            f"agree an evacuation route to higher ground well "
                            f"before any storm arrives."
                        ),
                    },
                ],
                raw_response="[]",
            )
        ]

    monkeypatch.setattr("dataforge.agents.streaming.LLMClient", lambda **k: _LLM())
    monkeypatch.setattr("dataforge.agents.streaming.generate_from_chunk", gen)
    monkeypatch.setattr(
        "dataforge.agents.streaming.StreamingAgent._generation_available",
        lambda self: True,
    )


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    from dataforge.config.settings import Settings

    s = Settings(
        output_dir=tmp_path / "out",
        db_path=tmp_path / "e2e.db",
        rate_limit=1000.0,
        chunk_size=128,
        chunk_overlap=16,
        stream_generate_workers=2,
    )
    monkeypatch.setattr("dataforge.cli.headless.get_settings", lambda: s)
    return s


def _recipe(tmp_path, site: str, **over) -> str:
    body = {
        "version": 1,
        "name": "e2e-preparedness",
        "stream": True,
        "source": {
            "urls": [f"{site}/sitemap.xml"],
            "include": ["/hazard", "/plan", "/kit"],
            "exclude": ["/es/", "/press-release"],
        },
        "generation": {"format": "qa", "goal": "flood preparedness", "n_per_chunk": 1},
        # Judge off: these fixtures fake generation offline, and the judge would
        # otherwise call the real LLM API. It has its own offline tests.
        "quality": {"threshold": 0.3, "llm_judge": False},
        "export": {"targets": ["local"]},
        **over,
    }
    import yaml

    p = tmp_path / "recipe.yaml"
    p.write_text(yaml.safe_dump(body), encoding="utf-8")
    return str(p)


async def test_dry_run_validates_without_touching_the_network(tmp_path, site, isolated_settings):
    assert await run_recipe(_recipe(tmp_path, site), dry_run=True) == EXIT_OK
    # Nothing was created: a dry run must not start a session.
    assert not (isolated_settings.output_dir / "sessions").exists()


async def test_full_headless_run_produces_an_export(
    tmp_path, site, isolated_settings, fake_llm
):
    code = await run_recipe(_recipe(tmp_path, site))
    assert code == EXIT_OK

    from sqlmodel import select

    from dataforge.storage import ScrapedPage, SyntheticSample, open_session

    with open_session(isolated_settings.db_path) as db:
        pages = db.exec(select(ScrapedPage)).all()
        samples = db.exec(select(SyntheticSample)).all()

    # The two excluded URLs must never have been fetched.
    assert len(pages) == 4, [p.url for p in pages]
    assert not any("/es/" in p.url or "press-release" in p.url for p in pages)
    assert samples
    assert all(s.approved for s in samples), (
        f"{sum(not s.approved for s in samples)}/{len(samples)} samples rejected"
    )

    # rglob("*.jsonl") also matches dataset_unsloth.jsonl, which has a
    # different schema ({"conversations": [...]}, no "messages" key — see
    # issue #19) and whose glob ordering isn't guaranteed across platforms.
    # Pick the primary export explicitly rather than relying on order.
    exports = [
        p
        for p in isolated_settings.output_dir.rglob("*.jsonl")
        if not p.stem.endswith("_unsloth")
    ]
    assert exports, "no primary JSONL export was written"

    rows = [
        json.loads(line)
        for line in exports[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows and "messages" in rows[0]

    # run_summary.json persists what the end-of-run lines print.
    summaries = list(isolated_settings.output_dir.rglob("run_summary.json"))
    assert len(summaries) == 1
    summary = json.loads(summaries[0].read_text(encoding="utf-8"))
    assert summary["exit_code"] == EXIT_OK
    assert summary["approved_samples"] == len(samples)
    assert summary["wall_seconds"] >= 0
    assert "quality" in summary["stage_seconds"]


async def test_filters_matching_nothing_exit_cleanly(
    tmp_path, site, isolated_settings, fake_llm
):
    path = _recipe(
        tmp_path,
        site,
        source={"urls": [f"{site}/sitemap.xml"], "include": ["/nonexistent-section"]},
    )
    assert await run_recipe(path) == EXIT_NO_URLS


async def test_split_export_writes_train_val_test_without_leakage(
    tmp_path, site, isolated_settings, fake_llm
):
    """A recipe with export.split emits separate files, grouped by source page."""

    from dataforge.exporters.split import assert_no_group_leakage

    path = _recipe(
        tmp_path,
        site,
        export={
            "targets": ["local"],
            "approved_only": True,
            "split": {"train": 0.5, "validation": 0.25, "test": 0.25,
                      "group_by": "page", "seed": 1},
        },
    )
    assert await run_recipe(path) == EXIT_OK

    written = {p.name for p in isolated_settings.output_dir.rglob("dataset_*.jsonl")}
    assert any(n.startswith("dataset_train") for n in written), written

    # Reload what was actually written and prove no page spans two splits.
    splits = {}
    for p in isolated_settings.output_dir.rglob("dataset_*.jsonl"):
        if p.name.endswith("_unsloth.jsonl"):
            continue   # unsloth files carry no lineage by design
        name = p.stem.replace("dataset_", "")
        splits[name] = [
            json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()
        ]

    assert splits, "no split files were written"
    assert_no_group_leakage(splits, "page")

    # Lineage must survive to disk, or a later split is impossible.
    any_row = next(iter(next(iter(splits.values()))))
    for field in ("page_id", "chunk_id", "source_url"):
        assert field in any_row, f"{field} missing from export - lineage lost"
    assert any_row["source_url"].startswith("http")


async def test_default_export_still_carries_lineage(
    tmp_path, site, isolated_settings, fake_llm
):
    """Even without a split, lineage is exported so you can split later."""
    assert await run_recipe(_recipe(tmp_path, site)) == EXIT_OK

    main = [p for p in isolated_settings.output_dir.rglob("dataset.jsonl")]
    assert main, "no dataset.jsonl written"
    rows = [
        json.loads(line)
        for line in main[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    assert {"page_id", "chunk_id", "chunk_index", "source_url"} <= set(rows[0])
    assert len({r["page_id"] for r in rows}) > 1, "expected samples from several pages"
