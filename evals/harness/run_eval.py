"""CLI: score one or more models' answers against evals/holdout/test.jsonl.

Usage:
    python -m evals.harness.run_eval \\
        --holdout evals/holdout/test.jsonl \\
        --answers outputs/base_answers.jsonl outputs/crea_answers.jsonl outputs/hosted_answers.jsonl \\
        --judge-model claude-3-5-sonnet-20241022 \\
        --out evals/results/2026-09-22

Each --answers file must be JSONL of ModelAnswer records (item_id, model_name, answer),
one file per model being compared, matching evals/holdout/test.jsonl item_ids.

Writes <out>.json (machine-readable) and <out>.md (human-readable) either way. A single judge
call failing (rate limit, malformed JSON, etc.) does not abort the whole run -- it's recorded as
a per-item failure in both files, and scoring continues for the rest.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path

from evals.harness.judge import score_answer
from evals.harness.schema import HoldoutItem, ModelAnswer


def load_jsonl(path: Path, model: type):
    with path.open(encoding="utf-8") as f:
        return [model.model_validate_json(line) for line in f if line.strip()]


def to_markdown(summary: dict) -> str:
    lines = [
        f"# Eval run — {summary['run_at']}",
        "",
        f"- Judge model: `{summary['judge_model']}`",
        f"- Holdout size: {summary['holdout_size']}",
        "",
        "| Model | n scored | n failed | groundedness | usefulness | safety |",
        "|---|---|---|---|---|---|",
    ]
    for model_name, stats in summary["models"].items():
        lines.append(
            f"| {model_name} | {stats['n_scored']} | {stats['n_failed']} | "
            f"{stats['mean_groundedness']} | {stats['mean_usefulness']} | {stats['mean_safety']} |"
        )
    for model_name, stats in summary["models"].items():
        if stats["failures"]:
            lines.append(f"\n## Failures — {model_name}\n")
            for f in stats["failures"]:
                lines.append(f"- item {f['item_id']}: `{f['error']}`")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--answers", type=Path, nargs="+", required=True)
    parser.add_argument("--judge-model", default="claude-3-5-sonnet-20241022")
    parser.add_argument("--out", type=Path, required=True, help="output path stem, no extension")
    args = parser.parse_args()

    holdout = {item.id: item for item in load_jsonl(args.holdout, HoldoutItem)}

    results_by_model: dict[str, list[dict]] = {}
    failures_by_model: dict[str, list[dict]] = {}
    for answers_path in args.answers:
        answers = load_jsonl(answers_path, ModelAnswer)
        model_name = answers[0].model_name if answers else answers_path.stem
        scores: list[dict] = []
        failures: list[dict] = []
        for answer in answers:
            item = holdout.get(answer.item_id)
            if item is None:
                failures.append({"item_id": answer.item_id, "error": "item_id not in holdout set"})
                continue
            try:
                score = score_answer(item, answer, judge_model=args.judge_model)
                scores.append(score.model_dump())
            except Exception as e:
                failures.append({"item_id": answer.item_id, "error": f"{type(e).__name__}: {e}"})
        results_by_model[model_name] = scores
        failures_by_model[model_name] = failures

    summary = {
        "run_at": datetime.now(UTC).isoformat(),
        "judge_model": args.judge_model,
        "holdout_size": len(holdout),
        "models": {
            model_name: {
                "n_scored": len(scores),
                "n_failed": len(failures_by_model[model_name]),
                "mean_groundedness": round(statistics.mean(s["groundedness"] for s in scores), 2)
                if scores
                else None,
                "mean_usefulness": round(statistics.mean(s["usefulness"] for s in scores), 2) if scores else None,
                "mean_safety": round(statistics.mean(s["safety"] for s in scores), 2) if scores else None,
                "scores": scores,
                "failures": failures_by_model[model_name],
            }
            for model_name, scores in results_by_model.items()
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    args.out.with_suffix(".md").write_text(to_markdown(summary), encoding="utf-8")
    print(f"Wrote {args.out}.json and {args.out}.md")
    for model_name, stats in summary["models"].items():
        print(
            f"{model_name}: groundedness={stats['mean_groundedness']}, "
            f"usefulness={stats['mean_usefulness']}, safety={stats['mean_safety']} "
            f"(n={stats['n_scored']}, failed={stats['n_failed']})"
        )


if __name__ == "__main__":
    main()
