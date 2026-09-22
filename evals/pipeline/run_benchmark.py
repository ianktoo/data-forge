"""Run the pipeline benchmark: one small, capped recipe per site in sites.yaml.

    uv run python -m evals.pipeline.run_benchmark --preflight-only   # checks only, spends nothing
    uv run python -m evals.pipeline.run_benchmark                    # every reviewed site
    uv run python -m evals.pipeline.run_benchmark --site uscis       # one site

For each site, in order:
  1. Refuse unless a person has recorded a terms review in sites.yaml.
  2. Preflight: fetch robots.txt and the seed URL with DataForge's own
     User-Agent; record status, Crawl-delay and a robots.txt snapshot. A 403/429,
     a CAPTCHA, or a redirect to another host counts as a block: the site is
     skipped, never worked around.
  3. Run `dataforge run <recipe>` in a subprocess with its own database, so the
     sites never share state.
Writes evals/results/pipeline_run_<timestamp>.json, which aggregate.py reads.
Run from the repository root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

from dataforge.collectors.http import USER_AGENT, ssl_context

HERE = Path(__file__).parent
RESULTS = HERE.parent / "results"
BENCH_OUT = Path("output/benchmark")


def load_sites() -> dict:
    return yaml.safe_load((HERE / "sites.yaml").read_text(encoding="utf-8"))


# Wording of bot-challenge / access-request pages, matched against visible text
# only: a commented-out reCAPTCHA <script> on a normal page (KRA) is not a block.
_CHALLENGE = re.compile(
    r"captcha|request access|unblock|verify you are (a )?human|just a moment|access denied",
    re.I,
)


def _site(host: str) -> str:
    """example.com and www.example.com are the same site."""
    return host.lower().removeprefix("www.")


def _visible_text(html: str) -> str:
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    html = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", html)
    return re.sub(r"<[^>]+>", " ", html)[:50000]


def preflight(seed: str, snapshot_dir: Path) -> dict:
    """robots.txt + seed fetch, exactly as DataForge would identify itself."""
    parsed = urlparse(seed)
    base = f"{parsed.scheme}://{parsed.netloc}"
    headers = {"User-Agent": USER_AGENT}
    out: dict = {"checked_at": datetime.now(UTC).isoformat(), "user_agent": USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=30, verify=ssl_context()) as client:
        robots = client.get(f"{base}/robots.txt")
        out["robots_status"] = robots.status_code
        if robots.status_code == 200:
            text = robots.text
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            (snapshot_dir / "robots.txt").write_text(text, encoding="utf-8")
            out["robots_sha256"] = hashlib.sha256(text.encode()).hexdigest()
            out["crawl_delay"] = re.findall(r"(?im)^\s*crawl-delay:\s*(\S+)", text)
        seed_resp = client.get(seed)
        out["seed_status"] = seed_resp.status_code
        out["seed_final_url"] = str(seed_resp.url)

    final_host = urlparse(out["seed_final_url"]).netloc
    reasons = []
    if out["robots_status"] in (401, 403, 429):
        reasons.append(f"robots.txt returned {out['robots_status']}")
    if out["seed_status"] in (401, 403, 429):
        reasons.append(f"seed returned {out['seed_status']}")
    if final_host and _site(final_host) != _site(parsed.netloc):
        reasons.append(f"seed redirected to another host ({final_host})")
    if _CHALLENGE.search(_visible_text(seed_resp.text)):
        reasons.append("seed page looks like a bot challenge")
    out["blocked"] = bool(reasons)
    out["block_reasons"] = reasons
    return out


def seed_of(recipe_path: Path) -> str:
    recipe = yaml.safe_load(recipe_path.read_text(encoding="utf-8"))
    return recipe["source"]["urls"][0]


def run_site(site: dict) -> dict:
    out_dir = BENCH_OUT / site["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "dataforge.db"
    env = {**os.environ, "DATAFORGE_DB_PATH": str(db_path), "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
    log_path = out_dir / f"run_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.log"
    recipe = HERE / site["recipe"]
    t0 = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        code = subprocess.call(
            [sys.executable, "-m", "dataforge.main", "--quiet", "--no-color", "run", str(recipe)],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
        )
    summaries = sorted(out_dir.glob("sessions/*/run_summary.json"), key=lambda p: p.stat().st_mtime)
    return {
        "exit_code": code,
        "wall_seconds": round(time.monotonic() - t0, 1),
        "db_path": str(db_path),
        "log": str(log_path),
        "run_summary": str(summaries[-1]) if summaries else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--site", action="append", help="site id (repeatable); default: all")
    ap.add_argument("--preflight-only", action="store_true", help="check access, run nothing")
    args = ap.parse_args()

    config = load_sites()
    sites = [s for s in config["sites"] if not args.site or s["id"] in args.site]
    if args.site and len(sites) != len(args.site):
        print(f"unknown site id in {args.site}", file=sys.stderr)
        return 2

    results = []
    for site in sites:
        entry: dict = {"id": site["id"], "topic": site["topic"], "recipe": site["recipe"]}
        review = site.get("terms_review") or {}
        entry["terms_reviewed"] = bool(review.get("reviewed_on"))
        entry["preflight"] = preflight(seed_of(HERE / site["recipe"]), BENCH_OUT / site["id"])
        pf = entry["preflight"]
        print(f"[{site['id']}] robots={pf['robots_status']} seed={pf['seed_status']} "
              f"crawl-delay={pf.get('crawl_delay') or '-'} blocked={pf['blocked']} "
              f"terms_reviewed={entry['terms_reviewed']}")

        if pf["blocked"]:
            entry["skipped"] = "blocked: " + "; ".join(pf["block_reasons"])
        elif not entry["terms_reviewed"]:
            entry["skipped"] = "no terms review recorded in sites.yaml"
        elif args.preflight_only:
            entry["skipped"] = "preflight only"
        else:
            print(f"[{site['id']}] running {site['recipe']} ...", flush=True)
            entry["run"] = run_site(site)
            print(f"[{site['id']}] exit {entry['run']['exit_code']} "
                  f"in {entry['run']['wall_seconds']}s", flush=True)
        if "skipped" in entry:
            print(f"[{site['id']}] skipped: {entry['skipped']}")
        results.append(entry)

    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"pipeline_run_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps({"sites": results}, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
