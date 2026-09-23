"""Smoke-test an installed `dataforge` the way a user would. Spends nothing.

    python scripts/smoke_test.py <dataforge executable> [--version X.Y.Z] [--mcp] [--e2e]

Runs the executable as a black box (a pip/uv install, or a standalone binary)
and checks that the everyday commands work, that `update`/`uninstall` refuse a
non-interactive caller, and optionally:

  --mcp  the MCP server answers `initialize` and lists its tools
  --e2e  two real pipeline runs, fully offline: a local test site (one with a
         sitemap, one crawled by following links) and a fake OpenAI-compatible
         LLM server, through crawl, chunking, generation, the LLM judge and
         export (JSONL, Parquet, CSV, train/validation/test splits)

Standard library only, so it runs on any Python without installing anything.
Exits 1 if any check fails.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

MCP_TOOLS = {"explore_site", "validate_recipe", "start_run", "run_status",
             "list_sessions", "session_stats", "view_samples"}

PAGE = """<html><head><title>{title}</title></head><body>
<nav>{nav}</nav><article><h1>{title}</h1>
<p>{title}: floods are among the most common and costly natural disasters. Know
your flood risk before a storm arrives and sign up for your community's warning
system so that official alerts reach you quickly.</p>
<p>Assemble an emergency kit with water, non-perishable food, medication, a
flashlight and a battery powered radio. Store copies of important documents in
a waterproof container and agree an evacuation route to higher ground.</p>
<p>Never walk, swim or drive through flood waters. Six inches of moving water
can knock a person down and one foot can sweep away a vehicle. Wait for the
official all clear before returning home.</p>
</article></body></html>"""

# path -> (title, links). The sitemap lists every page; the crawl test starts
# at "/" and must follow links down to depth 2 (/guides/floods -> /guides/floods/kit).
# Links sit in <nav> only, as on most sites: the crawl must follow navigation (#53).
PAGES = {
    "/": ("Home", ["/hazards", "/guides/floods"]),
    "/hazards": ("Hazards Overview", ["/hazards/wildfires"]),
    "/hazards/wildfires": ("Wildfire Safety", []),
    "/guides/floods": ("Flood Guide", ["/guides/floods/kit"]),
    "/guides/floods/kit": ("Flood Kit Checklist", []),
}


class Site(BaseHTTPRequestHandler):
    sitemap = True

    def log_message(self, *a):
        pass

    def do_GET(self):
        host = f"http://{self.headers['Host']}"
        if self.path == "/robots.txt":
            return self._send("User-agent: *\nAllow: /\n", "text/plain")
        if self.path == "/sitemap.xml" and self.sitemap:
            urls = "".join(f"<url><loc>{host}{p}</loc></url>" for p in PAGES if p != "/")
            return self._send('<?xml version="1.0"?><urlset xmlns='
                              f'"http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                              "application/xml")
        if self.path in PAGES:
            title, links = PAGES[self.path]
            nav = " ".join(f'<a href="{host}{link}">{link}</a>' for link in links)
            return self._send(PAGE.format(title=title, nav=nav), "text/html")
        self.send_response(404)
        self.end_headers()

    def _send(self, body, ctype):
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class NoSitemapSite(Site):
    sitemap = False


class FakeLLM(BaseHTTPRequestHandler):
    """Just enough of the OpenAI chat API for DataForge's generator and judge."""
    counter = 0
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._json({"object": "list", "data": [{"id": "fake", "object": "model"}]})
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = "\n".join(str(m.get("content", "")) for m in body.get("messages", []))
        judged = re.search(r"Judge these (\d+) examples", prompt)
        if judged:
            content = json.dumps([{"score": 5, "grounded": True, "standalone": True,
                                   "reason": "accurate"}] * int(judged.group(1)))
        else:
            with FakeLLM.lock:
                FakeLLM.counter += 1
                n = FakeLLM.counter
            topics = ["an emergency kit", "flood warnings", "evacuation routes",
                      "moving flood water", "important documents", "returning home"]
            content = json.dumps([{
                "question": f"What does guidance {n}.{i} say about {topics[(n + i) % 6]}?",
                "answer": (f"Guidance {n}.{i}: for {topics[(n + i) % 6]}, prepare well before "
                           f"a storm. Keep water, food and medication ready (item {n * 7 + i}), "
                           "sign up for official alerts, and never enter moving flood water."),
            } for i in range(2)])
        self._json({
            "id": f"chatcmpl-{FakeLLM.counter}", "object": "chat.completion",
            "created": 0, "model": body.get("model", "fake"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        })

    def _json(self, obj):
        raw = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


class Smoke:
    def __init__(self, exe: str):
        self.exe = exe
        self.work = tempfile.mkdtemp(prefix="dataforge-smoke-")
        self.env = {**os.environ, "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"}
        for k in list(self.env):
            if k.startswith("DATAFORGE_") or k == "CLAUDECODE":
                del self.env[k]
        self.results: list[bool] = []

    def run(self, *args, env=None, timeout=300):
        r = subprocess.run([self.exe, *args], cwd=self.work, env=env or self.env,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, r.stdout, r.stderr

    def check(self, name, ok, detail=""):
        self.results.append(bool(ok))
        line = f"{'PASS' if ok else 'FAIL'}  {name}"
        if not ok and detail:
            line += "\n      " + detail.strip().replace("\n", "\n      ")[-1500:]
        print(line, flush=True)

    # ── everyday commands ────────────────────────────────────────────────
    def basics(self, version):
        code, out, err = self.run("--version")
        self.check("--version", code == 0 and (not version or out.strip() == version),
                   f"exit {code}: {out}{err}")
        code, out, err = self.run("--help")
        self.check("--help", code == 0 and "uninstall" in out, f"exit {code}: {out}{err}")
        code, out, err = self.run("agent-guide")
        self.check("agent-guide", code == 0 and "guide for AI agents" in out, f"exit {code}: {out}{err}")
        code, out, err = self.run("init-recipe", "r.yaml")
        self.check("init-recipe", code == 0 and os.path.exists(os.path.join(self.work, "r.yaml")),
                   f"exit {code}: {out}{err}")
        code, out, err = self.run("run", "r.yaml", "--dry-run")
        self.check("run --dry-run", code == 0 and "Seeds:" in out, f"exit {code}: {out}{err}")
        code, out, err = self.run("providers")
        self.check("providers", code == 0, f"exit {code}: {out}{err}")
        code, out, err = self.run("info")
        self.check("info", code == 0, f"exit {code}: {out}{err}")
        code, out, err = self.run("--json", "sessions")
        self.check("--json sessions (stdout is pure JSON)", code == 0 and _is_json(out),
                   f"exit {code}: {out}{err}")
        code, out, err = self.run("no-such-command")
        self.check("unknown command fails", code != 0, f"exit {code}")
        # Agents and scripts must never update or uninstall (#47).
        for cmd in (["update"], ["uninstall", "--delete-data"]):
            code, out, err = self.run(*cmd)
            self.check(f"{cmd[0]} refused without a terminal",
                       code == 2 and "refused" in out + err, f"exit {code}: {out}{err}")

    # ── MCP ──────────────────────────────────────────────────────────────
    def mcp(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        p = subprocess.Popen([self.exe, "mcp"], cwd=self.work, env=self.env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8")
        tools: list[str] = []
        detail = ""
        try:
            assert p.stdin and p.stdout
            for m in msgs:
                p.stdin.write(json.dumps(m) + "\n")
                p.stdin.flush()
                if "id" in m:
                    line = p.stdout.readline()
                    if m["id"] == 2:
                        tools = [t["name"] for t in json.loads(line)["result"]["tools"]]
        except Exception as exc:
            detail = f"{exc!r}"
        finally:
            p.kill()
            _, err = p.communicate()
        self.check("mcp: initialize + tools/list", set(tools) == MCP_TOOLS,
                   f"{detail} tools={tools} stderr={err}")

    # ── offline end-to-end runs ──────────────────────────────────────────
    def e2e(self):
        llm, llm_url = serve(FakeLLM)
        env = {**self.env,
               "DATAFORGE_LLM_PROVIDER": "openai_compatible",
               "DATAFORGE_LOCAL_BASE_URL": f"{llm_url}/v1",
               "DATAFORGE_LLM_MODEL": "fake"}
        try:
            for name, handler, seed, want_pages in (
                ("sitemap", Site, "/sitemap.xml", 4),
                ("crawl (no sitemap, depth 2)", NoSitemapSite, "/", 5),
            ):
                site, url = serve(handler)
                try:
                    self._one_run(name, env, url + seed, want_pages)
                finally:
                    site.shutdown()
        finally:
            llm.shutdown()

    def _one_run(self, name, env, seed, want_pages):
        slug = re.sub(r"\W+", "-", name.split()[0])
        run_dir = os.path.join(self.work, f"e2e-{slug}")
        os.makedirs(run_dir)
        recipe = os.path.join(run_dir, "recipe.yaml")
        with open(recipe, "w", encoding="utf-8") as f:
            f.write(f"""version: 1
name: smoke-{slug}
stream: true
source:
  urls: ["{seed}"]
  language: ""
  skip_known: false
crawl:
  rate_limit: 100
  max_crawl_depth: 2
  max_crawl_pages: 20
generation:
  format: qa
  goal: flood preparedness
  n_per_chunk: 2
  chunk_size: 128
  chunk_overlap: 16
  max_cost_usd: 5
quality:
  threshold: 0.2
  llm_judge: true
export:
  targets: [local]
  split:
    train: 0.6
    validation: 0.2
    test: 0.2
    group_by: page
""")
        env = {**env, "DATAFORGE_DB_PATH": os.path.join(run_dir, "df.db"),
               "DATAFORGE_OUTPUT_DIR": os.path.join(run_dir, "out")}
        code, out, err = self.run("run", recipe, env=env, timeout=600)
        sid = re.search(r"Session ID:\s*(\w+)", out + err)
        self.check(f"e2e {name}: run exits 0", code == 0 and sid, f"exit {code}:\n{out}\n{err}")
        if not sid:
            return
        exports = glob.glob(os.path.join(run_dir, "out", "sessions", "*", "exports", "*"))
        files = {os.path.basename(p) for d in exports for p in glob.glob(os.path.join(d, "*"))}
        # With a split configured, only split files are written.
        need = {f"dataset_{p}.{ext}" for p in ("train", "validation", "test")
                for ext in ("jsonl", "parquet", "csv")}
        self.check(f"e2e {name}: JSONL, Parquet, CSV and split files", need <= files,
                   f"missing {sorted(need - files)}; have {sorted(files)}")
        rows = 0
        for d in exports:
            for part in ("train", "validation", "test"):
                path = os.path.join(d, f"dataset_{part}.jsonl")
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as f:
                        rows += sum(1 for line in f if json.loads(line).get("messages"))
        self.check(f"e2e {name}: dataset has samples", rows > 0, f"{rows} rows")
        code, out, err = self.run("--json", "stats", sid.group(1), env=env)
        stats = json.loads(out) if _is_json(out) else {}
        self.check(f"e2e {name}: stats JSON agrees with the export",
                   code == 0 and stats.get("approved") == rows > 0,
                   f"exit {code}: rows={rows} {out[:800]}{err[-800:]}")
        # Every page yields samples, so the pages behind them show what the
        # crawl reached. Read them from the split files' source_url.
        urls = set()
        for d in exports:
            for part in ("train", "validation", "test"):
                path = os.path.join(d, f"dataset_{part}.jsonl")
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as f:
                        urls |= {json.loads(line).get("source_url") for line in f}
        self.check(f"e2e {name}: samples from all {want_pages} pages",
                   len(urls - {None}) == want_pages, f"got {sorted(u for u in urls if u)}")

    def done(self):
        if os.environ.get("SMOKE_KEEP"):
            print(f"kept: {self.work}")
        else:
            shutil.rmtree(self.work, ignore_errors=True)
        print(f"{sum(self.results)}/{len(self.results)} checks passed", flush=True)
        return 0 if all(self.results) else 1


def _is_json(text):
    try:
        json.loads(text)
        return True
    except ValueError:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("exe", help="path to the dataforge executable (or just 'dataforge')")
    ap.add_argument("--version", default="", help="expected `dataforge --version` output")
    ap.add_argument("--mcp", action="store_true", help="also check the MCP server (needs the [mcp] extra)")
    ap.add_argument("--e2e", action="store_true", help="also run two offline pipeline runs")
    a = ap.parse_args()
    exe = shutil.which(a.exe) or a.exe
    s = Smoke(os.path.abspath(exe))
    s.basics(a.version)
    if a.mcp:
        s.mcp()
    if a.e2e:
        s.e2e()
    sys.exit(s.done())


if __name__ == "__main__":
    main()
