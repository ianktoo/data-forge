"""Generate the "hosted reference" side of the comparison table via litellm, so the third
row of the paper's Results table (Section 8) is produced the same way as the other two.

Usage:
    python -m evals.harness.generate_hosted_answers \\
        --holdout evals/holdout/test.jsonl --model claude-3-5-haiku-20241022 \\
        --out outputs/hosted_answers.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from litellm import completion

SYSTEM_PROMPT = (
    "You are an offline emergency-guidance assistant. Answer only using general knowledge of "
    "FEMA/Ready.gov-style disaster preparedness guidance. Be concise and actionable."
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout", required=True, type=Path)
    parser.add_argument("--model", required=True, help="litellm model string, e.g. claude-3-5-haiku-20241022")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with args.holdout.open(encoding="utf-8") as fin, args.out.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            response = completion(
                model=args.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": item["question"]},
                ],
                temperature=0.0,
                max_tokens=300,
            )
            answer = response.choices[0].message.content
            fout.write(json.dumps({"item_id": item["id"], "model_name": args.model, "answer": answer}) + "\n")
            n += 1
    print(f"Wrote {n} answers to {args.out}")


if __name__ == "__main__":
    main()
