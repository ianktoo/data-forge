"""Human audit of the LLM judge: blind labelling sheet, then agreement scores.

    uv run python -m evals.pipeline.audit sample [--per-class 30]   # writes audit_<ts>.csv + key
    # label the `acceptable` column y/n in a spreadsheet, save as CSV
    uv run python -m evals.pipeline.audit score evals/results/audit_<ts>.csv

`sample` draws up to N approved and N rejected samples per site (seed 42) from
the latest benchmark run. The CSV is blind: it shows the source passage, the
question and the answer, never the judge's verdict, which goes to a separate
audit_<ts>_key.json so it cannot bias the label.

Label `acceptable` = y when the answer is correct according to the passage,
fully supported by it, and makes sense without seeing the passage (does not say
"the document" or "the text"); otherwise n. Leave it blank to skip a row.

Because sampling is stratified (equal approved and rejected per site), report
the per-stratum rates, not a pooled accuracy: judge precision (approved rows
labelled y) and rejection precision (rejected rows labelled n). Cohen's kappa
is reported for the audited sample only.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

from sqlmodel import select

from dataforge.storage import open_session
from dataforge.storage.models import ProcessedChunk, SyntheticSample

RESULTS = Path(__file__).parent.parent / "results"


def _qa(messages_json: str) -> tuple[str, str]:
    msgs = json.loads(messages_json)
    q = next((m["content"] for m in msgs if m.get("role") == "user"), "")
    a = next((m["content"] for m in msgs if m.get("role") == "assistant"), "")
    return str(q), str(a)


def sample(per_class: int) -> int:
    metrics = sorted(RESULTS.glob("pipeline_metrics_*.json"))
    if not metrics:
        print("no pipeline_metrics_*.json; run aggregate first", file=sys.stderr)
        return 1
    data = json.loads(metrics[-1].read_text(encoding="utf-8"))
    rng = random.Random(42)
    rows, key = [], {}
    for entry in data["sites"]:
        m = entry.get("metrics")
        if not m:
            continue
        with open_session(Path(entry["run"]["db_path"])) as db:
            samples = db.exec(
                select(SyntheticSample).where(SyntheticSample.session_id == m["session_id"])
            ).all()
            chunks = {c.id: c.content for c in db.exec(
                select(ProcessedChunk).where(ProcessedChunk.session_id == m["session_id"])
            ).all()}
        for approved in (True, False):
            pool = [s for s in samples if s.approved == approved]
            for s in rng.sample(pool, min(per_class, len(pool))):
                q, a = _qa(s.messages_json)
                rid = f"{entry['id']}-{s.id}"
                rows.append({"row_id": rid, "site": entry["id"], "source_passage": chunks.get(s.chunk_id, ""),
                             "question": q, "answer": a, "acceptable": "", "notes": ""})
                key[rid] = {"approved": s.approved, "rejection_reason": s.rejection_reason,
                            "quality_score": s.quality_score}
    rng.shuffle(rows)  # interleave approved and rejected so order leaks nothing

    stamp = metrics[-1].stem.removeprefix("pipeline_metrics_")
    csv_path = RESULTS / f"audit_{stamp}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM: opens cleanly in Excel
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["row_id"])
        w.writeheader()
        w.writerows(rows)
    (RESULTS / f"audit_{stamp}_key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    print(f"wrote {len(rows)} rows to {csv_path} (key: audit_{stamp}_key.json)")
    return 0


def _kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    n = len(pairs)
    if not n:
        return None
    po = sum(j == h for j, h in pairs) / n
    pj, ph = sum(j for j, _ in pairs) / n, sum(h for _, h in pairs) / n
    pe = pj * ph + (1 - pj) * (1 - ph)
    return None if pe == 1 else round((po - pe) / (1 - pe), 3)


def _rates(pairs: list[tuple[bool, bool]]) -> dict:
    appr = [h for j, h in pairs if j]
    rej = [h for j, h in pairs if not j]
    return {
        "labelled": len(pairs),
        "approved_labelled": len(appr),
        "judge_precision": round(sum(appr) / len(appr), 3) if appr else None,
        "rejected_labelled": len(rej),
        "rejection_precision": round(sum(not h for h in rej) / len(rej), 3) if rej else None,
        "cohens_kappa": _kappa(pairs),
    }


def score(csv_path: Path) -> int:
    key_path = csv_path.with_name(csv_path.stem + "_key.json")
    key = json.loads(key_path.read_text(encoding="utf-8"))
    by_site: dict[str, list[tuple[bool, bool]]] = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            label = row["acceptable"].strip().lower()
            if label not in ("y", "n"):
                continue
            by_site.setdefault(row["site"], []).append((key[row["row_id"]]["approved"], label == "y"))
    report = {site: _rates(p) for site, p in sorted(by_site.items())}
    report["all"] = _rates([p for ps in by_site.values() for p in ps])
    out = csv_path.with_name(csv_path.stem + "_scores.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"wrote {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--per-class", type=int, default=30)
    sc = sub.add_parser("score")
    sc.add_argument("csv", type=Path)
    args = ap.parse_args()
    return sample(args.per_class) if args.cmd == "sample" else score(args.csv)


if __name__ == "__main__":
    sys.exit(main())
