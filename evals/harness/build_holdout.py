"""Build evals/holdout/test.jsonl from a promoted export's test split.

This is the ONE held-out mechanism -- data-forge's own page-grouped split
(`export.split.group_by: page`, see examples/ready-gov.yaml) already guarantees no source page
contributes to both train and test, so nothing here re-derives or re-checks that; it just
reshapes the already-correct split into the schema evals/harness/judge.py needs, adding the
chunk text (source_excerpt) that the export doesn't carry directly -- pulled from the same run's
SQLite DB by chunk_id, since groundedness scoring needs the actual passage, not just its URL.

Usage:
    python -m evals.harness.build_holdout \\
        --test-export dataset/gold/dataset_test.jsonl \\
        --db output/dataforge.db \\
        --out evals/holdout/test.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlmodel import Session, create_engine, select

from dataforge.storage.models import ProcessedChunk


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-export", type=Path, required=True, help="dataset_test.jsonl from export.split")
    parser.add_argument("--db", type=Path, required=True, help="the run's dataforge.db (has chunk text)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    engine = create_engine(f"sqlite:///{args.db}")
    written = 0
    skipped = 0

    with Session(engine) as db, args.test_export.open(encoding="utf-8") as fin:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as fout:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                messages = record.get("messages", [])
                if len(messages) < 2:
                    skipped += 1
                    continue

                chunk = db.exec(
                    select(ProcessedChunk).where(ProcessedChunk.id == record.get("chunk_id"))
                ).first()
                if chunk is None:
                    skipped += 1
                    continue

                holdout_item = {
                    "id": record["id"],
                    "question": messages[0]["content"],
                    "reference_answer": messages[1]["content"],
                    "source_url": record.get("source_url", ""),
                    "source_excerpt": chunk.content,
                }
                fout.write(json.dumps(holdout_item) + "\n")
                written += 1

    print(f"Wrote {written} holdout items to {args.out} ({skipped} skipped: missing chunk or malformed record)")


if __name__ == "__main__":
    main()
