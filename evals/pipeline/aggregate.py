"""Turn a benchmark run into per-site metrics, a Markdown table and LaTeX tables.

    uv run python -m evals.pipeline.aggregate                        # all pipeline_run_*.json
    uv run python -m evals.pipeline.aggregate evals/results/pipeline_run_<ts>.json ...

With several run files, each site's most recent completed run is used, so one
site can be re-run on its own (run_benchmark --site <id>) without redoing the rest.

Every number is computed from what the run left on disk (the site's database,
run_summary.json and the export files); nothing is re-scored. Writes
pipeline_metrics_<ts>.{json,md} and pipeline_tables_<ts>.tex next to the input.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

import yaml
from sqlmodel import select

from dataforge.storage import open_session
from dataforge.storage.models import (
    DiscoveredURL,
    PipelineSession,
    ProcessedChunk,
    ScrapedPage,
    SyntheticSample,
)

HERE = Path(__file__).parent
RESULTS = HERE.parent / "results"


def _reason_group(reason: str) -> str:
    # Current labels are "judge: score N < M"; runs before 2.4.0 wrote "judge score N < M".
    return "judge: score below threshold" if re.match(r"judge:? score \d+ <", reason) else reason


def _split_page_ids(export_dir: Path) -> dict[str, set]:
    pages: dict[str, set] = {}
    for split in ("train", "validation", "test"):
        f = export_dir / f"dataset_{split}.jsonl"
        if f.exists():
            pages[split] = {
                json.loads(line).get("page_id")
                for line in f.read_text(encoding="utf-8").splitlines() if line.strip()
            }
    return pages


def site_metrics(run: dict) -> dict:
    summary = json.loads(Path(run["run_summary"]).read_text(encoding="utf-8"))
    sid = summary["session_id"]
    with open_session(Path(run["db_path"])) as db:
        session = db.get(PipelineSession, sid)
        urls = db.exec(select(DiscoveredURL).where(DiscoveredURL.session_id == sid)).all()
        pages = db.exec(select(ScrapedPage).where(ScrapedPage.session_id == sid)).all()
        chunks = db.exec(select(ProcessedChunk).where(ProcessedChunk.session_id == sid)).all()
        samples = db.exec(select(SyntheticSample).where(SyntheticSample.session_id == sid)).all()

    selected = [u for u in urls if u.selected]
    approved = [s for s in samples if s.approved]
    reasons = Counter(_reason_group(s.rejection_reason) for s in samples if not s.approved)
    cost = summary["llm_usage"].get("cost_usd", 0.0) if summary.get("llm_usage") else 0.0

    session_dir = Path(run["run_summary"]).parent
    export_dirs = sorted((session_dir / "exports").glob("*/"), reverse=True)
    split_pages = _split_page_ids(export_dirs[0]) if export_dirs else {}
    names = list(split_pages)
    overlaps = {
        f"{a}&{b}": len(split_pages[a] & split_pages[b])
        for i, a in enumerate(names) for b in names[i + 1:]
    }

    return {
        "session_id": sid,
        "exit_code": summary["exit_code"],
        "model": summary["generation_model"],
        "judge_model": summary["quality_model"],
        "urls_discovered": len(urls),
        "urls_selected": len(selected),
        "urls_fetched": sum(u.scraped for u in selected),
        "urls_failed": sum(not u.scraped for u in selected),
        "discovery_source": dict(Counter(u.source for u in urls)),
        "pages": len(pages),
        "words_per_page_median": statistics.median([p.word_count for p in pages]) if pages else 0,
        "chunks": len(chunks),
        "samples_generated": len(samples),
        "samples_approved": len(approved),
        "approval_rate": round(len(approved) / len(samples), 4) if samples else None,
        "rejection_reasons": dict(reasons.most_common()),
        "quality_score_median": statistics.median([s.quality_score for s in samples]) if samples else None,
        "llm_calls": summary["llm_usage"].get("total_calls", 0) if summary.get("llm_usage") else 0,
        "cost_usd": round(cost, 4),
        "cost_per_approved_usd": round(cost / len(approved), 5) if approved else None,
        "budget_skipped_calls": summary["budget"]["skipped_calls"],
        "wall_seconds": summary["wall_seconds"],
        "stage_seconds": summary["stage_seconds"],
        "split_pages": {k: len(v) for k, v in split_pages.items()},
        "split_page_overlap": overlaps,
        "leak_free": all(v == 0 for v in overlaps.values()) if overlaps else None,
        "session_status": session.status if session else None,
    }


def _tex(s: object) -> str:
    return str(s).replace("_", r"\_").replace("&", r"\&").replace("%", r"\%").replace("$", r"\$")


def markdown(rows: list[dict]) -> str:
    head = ("| Site | URLs sel./fetched | Pages | Chunks | Samples | Approved | Approval | "
            "Cost | $/approved | Wall s | Leak-free |\n|" + "---|" * 11)
    lines = [head]
    for r in rows:
        m = r.get("metrics")
        if not m:
            lines.append(f"| {r['id']} | skipped: {r.get('skipped', 'no run')} |" + " |" * 9)
            continue
        cpa = m["cost_per_approved_usd"]
        cpa_text = "--" if cpa is None else f"${cpa:.4f}"
        lines.append(
            f"| {r['id']} | {m['urls_selected']}/{m['urls_fetched']} | {m['pages']} | {m['chunks']} | "
            f"{m['samples_generated']} | {m['samples_approved']} | "
            f"{(m['approval_rate'] or 0):.0%} | ${m['cost_usd']:.3f} | {cpa_text} | "
            f"{m['wall_seconds']:.0f} | {m['leak_free']} |"
        )
    return "\n".join(lines) + "\n"


def latex(rows: list[dict], sites_cfg: dict) -> str:
    out = [r"% Generated by evals/pipeline/aggregate.py -- do not edit by hand.", ""]
    out += [
        r"\begin{tabular}{@{}l r r r r r r r r@{}}", r"\toprule",
        r"Site & Pages & Chunks & Samples & Approved & Approval & Cost (USD) & USD/approved & Wall (s) \\",
        r"\midrule",
    ]
    for r in rows:
        m = r.get("metrics")
        if not m:
            out.append(rf"{_tex(r['id'])} & \multicolumn{{8}}{{l}}{{skipped: {_tex(r.get('skipped', 'no run'))}}} \\")
            continue
        cpa = "--" if m["cost_per_approved_usd"] is None else f"{m['cost_per_approved_usd']:.4f}"
        out.append(
            f"{_tex(r['id'])} & {m['pages']} & {m['chunks']} & {m['samples_generated']} & "
            f"{m['samples_approved']} & {(m['approval_rate'] or 0) * 100:.0f}\\% & "
            f"{m['cost_usd']:.3f} & {cpa} & {m['wall_seconds']:.0f} \\\\"
        )
    out += [r"\bottomrule", r"\end{tabular}", ""]

    out += [
        r"% Access and terms table", r"\begin{tabular}{@{}l p{3.2cm} p{8.2cm}@{}}", r"\toprule",
        r"Site & Topic & Access outcome \\", r"\midrule",
    ]
    for r in rows:
        pf = r["preflight"]
        delay = ", ".join(pf.get("crawl_delay") or []) or "none"
        note = f"robots.txt {pf['robots_status']}; Crawl-delay {delay}; terms reviewed: {'yes' if r['terms_reviewed'] else 'no'}"
        out.append(f"{_tex(r['id'])} & {_tex(r['topic'])} & {_tex(note)} \\\\")
    for ex in sites_cfg.get("excluded", []):
        out.append(f"{_tex(ex['id'])} & {_tex(ex['topic'])} & Excluded: {_tex(' '.join(ex['reason'].split()))} \\\\")
    out += [r"\bottomrule", r"\end{tabular}", ""]
    return "\n".join(out)


def _latest_per_site(run_files: list[Path]) -> dict:
    """Merge run files: for each site, the most recent entry (a completed run beats a skip)."""
    merged: dict[str, dict] = {}
    for f in sorted(run_files):
        for entry in json.loads(f.read_text(encoding="utf-8"))["sites"]:
            if "run" in entry or entry["id"] not in merged or "run" not in merged[entry["id"]]:
                merged[entry["id"]] = entry
    return {"sites": list(merged.values()), "run_files": [str(f) for f in sorted(run_files)]}


def main() -> int:
    run_files = [Path(a) for a in sys.argv[1:]] or sorted(RESULTS.glob("pipeline_run_*.json"))
    if not run_files:
        print("no pipeline_run_*.json in evals/results; run run_benchmark first", file=sys.stderr)
        return 1
    data = _latest_per_site(run_files)
    run_file = sorted(run_files)[-1]
    sites_cfg = yaml.safe_load((HERE / "sites.yaml").read_text(encoding="utf-8"))
    # A site moved to `excluded` after the run is reported there, not as a skipped row.
    excluded = {ex["id"] for ex in sites_cfg.get("excluded", [])}
    data["sites"] = [e for e in data["sites"] if e["id"] not in excluded]
    for entry in data["sites"]:
        if entry.get("run", {}).get("run_summary"):
            entry["metrics"] = site_metrics(entry["run"])

    stamp = run_file.stem.removeprefix("pipeline_run_")
    (RESULTS / f"pipeline_metrics_{stamp}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (RESULTS / f"pipeline_metrics_{stamp}.md").write_text(markdown(data["sites"]), encoding="utf-8")
    (RESULTS / f"pipeline_tables_{stamp}.tex").write_text(latex(data["sites"], sites_cfg), encoding="utf-8")
    print(markdown(data["sites"]))
    print(f"wrote pipeline_metrics_{stamp}.json/.md and pipeline_tables_{stamp}.tex in {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
