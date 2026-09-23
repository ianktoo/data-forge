"""Regression tests for #22 (OpenAI-compatible local servers), #40 (session
output folder) and #41 (progress on small runs)."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from dataforge.config.providers import endpoint_kwargs, litellm_model

# -- #22 OpenAI-compatible local servers ----------------------------------------


class _FakeOpenAIServer(BaseHTTPRequestHandler):
    """Speaks just enough of the OpenAI API: GET /v1/models, POST /v1/chat/completions."""

    seen: list = []

    def log_message(self, *a):
        pass

    def _json(self, body, status=200):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/v1/models":
            return self._json({"object": "list", "data": [{"id": "local-model", "object": "model"}]})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append({"path": self.path, "model": body.get("model"),
                                "auth": self.headers.get("Authorization")})
        if self.path != "/v1/chat/completions":
            return self._json({"error": "not found"}, 404)
        self._json({
            "id": "c1", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hello from the local server"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        })


@pytest.fixture
def local_server():
    _FakeOpenAIServer.seen = []
    server = HTTPServer(("127.0.0.1", 0), _FakeOpenAIServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
    server.server_close()


def test_model_ids_route_to_the_openai_format():
    assert litellm_model("openai_compatible", "qwen2.5-7b-instruct") == "openai/qwen2.5-7b-instruct"
    assert litellm_model("openai_compatible", "openai/already") == "openai/already"


def test_missing_base_url_is_a_clear_error():
    with pytest.raises(ValueError, match="DATAFORGE_LOCAL_BASE_URL"):
        endpoint_kwargs("openai_compatible", SimpleNamespace(local_base_url="", local_api_key=""))


def test_ollama_base_url_is_passed_through():
    """OLLAMA_BASE_URL used to be read only by the preflight check, never by the LLM call."""
    s = SimpleNamespace(ollama_base_url="http://gpu-box:11434")
    assert endpoint_kwargs("ollama", s) == {"api_base": "http://gpu-box:11434"}
    assert endpoint_kwargs("openai", s) == {}


def test_llm_client_completes_against_a_local_openai_compatible_server(local_server, monkeypatch):
    """End to end through litellm: the request really reaches the local server."""
    from dataforge.config.settings import Settings
    from dataforge.generators import llm as llm_mod

    s = Settings(llm_provider="openai_compatible", llm_model="local-model",
                 local_base_url=local_server, local_api_key="secret-123")
    monkeypatch.setattr(llm_mod, "get_settings", lambda: s)
    client = llm_mod.LLMClient()
    resp = asyncio.run(client.complete([{"role": "user", "content": "hi"}]))
    assert resp.content == "hello from the local server"
    assert _FakeOpenAIServer.seen[-1]["model"] == "local-model"
    assert _FakeOpenAIServer.seen[-1]["auth"] == "Bearer secret-123"


def test_preflight_reaches_the_local_server(local_server):
    from dataforge.cli.preflight import _check_openai_compatible

    assert _check_openai_compatible(local_server) == (True, None)


def test_preflight_reports_an_unreachable_server(monkeypatch):
    from dataforge.cli import preflight

    monkeypatch.setattr(preflight, "show_error", lambda *a, **k: None)
    ok, key = preflight._check_openai_compatible("http://127.0.0.1:9/v1")
    assert not ok and key == "LOCAL_ENDPOINT_UNREACHABLE"


def test_provider_is_listed_and_needs_no_key():
    from dataforge.config import PROVIDER_INFO

    info = PROVIDER_INFO["openai_compatible"]
    assert info.requires_key is False and info.models == []


# -- #40 a session remembers its output folder ----------------------------------


def test_session_dir_uses_the_recorded_output_folder(tmp_path):
    from dataforge.storage import PipelineSession

    recorded = PipelineSession(id="s1", name="n", config_json=json.dumps({"output_dir": str(tmp_path / "rec")}))
    legacy = PipelineSession(id="s2", name="n")
    assert recorded.session_dir(tmp_path / "current") == tmp_path / "rec" / "sessions" / "s1"
    assert legacy.session_dir(tmp_path / "current") == tmp_path / "current" / "sessions" / "s2"


def test_checkpoint_keeps_the_recorded_output_folder(tmp_path):
    """_checkpoint used to rebuild config_json from counters only, which would
    have erased output_dir at the first checkpoint."""
    from dataforge.agents import Orchestrator, PipelineContext
    from dataforge.config.settings import Settings
    from dataforge.storage import DataFormat, PipelineSession, init_db, open_session

    s = Settings(output_dir=tmp_path / "custom-out", db_path=tmp_path / "t.db")
    init_db(s.db_path)
    ctx = PipelineContext(session_id="sess-40", session_name="n", goal="", format=DataFormat.qa,
                          seed_urls=[], settings=s, custom_system_prompt="", n_per_chunk=1)
    orch = Orchestrator(ctx)
    orch._init_session()
    ctx.discovered_urls = ["https://x.test/a"]
    orch._checkpoint()
    with open_session(s.db_path) as db:
        cfg = db.get(PipelineSession, "sess-40").config()
    assert cfg["output_dir"] == str((tmp_path / "custom-out").resolve())
    assert cfg["discovered"] == 1


def test_stats_find_split_counts_through_the_export_record(tmp_path):
    """A session exported outside the folder stats looks in (older sessions
    have no recorded output_dir) is found through its ExportRecord."""
    from dataforge.storage import ExportRecord, PipelineSession, init_db, open_session
    from dataforge.storage.stats import compute_session_stats

    db = tmp_path / "t.db"
    init_db(db)
    export_dir = tmp_path / "elsewhere" / "exports" / "20260922_000000"
    export_dir.mkdir(parents=True)
    (export_dir / "dataset_train.jsonl").write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    (export_dir / "dataset_validation.jsonl").write_text('{"a":3}\n', encoding="utf-8")
    with open_session(db) as s:
        s.add(PipelineSession(id="s40", name="n"))
        s.add(ExportRecord(session_id="s40", destination="local", path_or_url=str(export_dir), format="jsonl"))
        s.commit()
    stats = compute_session_stats(db, "s40", tmp_path / "wrong-place" / "sessions" / "s40")
    assert stats.split_counts == {"train": 2, "validation": 1}


# -- #41 progress on small runs ------------------------------------------------


def test_small_run_prints_progress(monkeypatch):
    """With fewer than 25 events the counter line never printed."""
    from dataforge.cli import headless

    lines = []
    monkeypatch.setattr(headless.ui, "info", lambda msg: lines.append(msg))
    cb = headless._stream_progress()
    for scraped in (1, 1, 2, 3, 3, 4):
        asyncio.run(cb({"scraped": scraped, "urls_total": 4, "chunks": scraped * 2,
                        "samples": scraped * 3, "gen_skipped": 0}))
    assert lines, "no progress line on a 6-event run"
    assert "scraped 4/4" in lines[-1]          # always reports the last page


def test_run_status_progress_falls_back_to_stage_lines(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from dataforge import mcp_server

    (tmp_path / "mcp-runs").mkdir()
    (tmp_path / "mcp-runs" / "r1.log").write_text(
        "· Session ID: abc\n"
        "✓ Stage 'streaming' complete, 6 pages, 8 chunks, 24 samples\n"
        "✓ Stage 'quality' complete, 21 approved\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_server, "get_settings", lambda: SimpleNamespace(output_dir=tmp_path))
    status = mcp_server.run_status("r1")
    assert status["progress"].startswith("Stage 'quality' complete")
