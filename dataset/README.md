# dataset/

The golden FEMA / Ready.gov dataset, produced by `dataforge run examples/ready-gov.yaml`.

## Layout

```
dataset/
  gold/                     # latest accepted export, promoted from output/sessions/<id>/exports/
    dataset_train.jsonl      # 80% -- what crea-finetune actually trains on
    dataset_validation.jsonl # 10%
    dataset_test.jsonl        # 10% -- source for evals/holdout/test.jsonl, never trained on
    SOURCES.md     # every crawled URL, access date, page/doc count (fills paper Sec. "Data Collection")
    GENERATION.md  # teacher model, prompt, chunking, filtering/dedup rules used (fills paper Sec.
                    # "Synthetic Dataset Generation")
  archive/         # superseded exports, kept for reproducibility (not committed if large; see .gitignore)
```

The train/validation/test split is `examples/ready-gov.yaml`'s own `export.split`
(`group_by: page`, fixed `seed: 42`) — it's the one and only held-out mechanism, already run at
export time. Nothing downstream re-splits or re-derives it.

`output/sessions/<id>/` is DataForge's scratch/working area (crawl cache, checkpoints, raw exports).
Nothing there is guaranteed clean or FEMA-scoped — see the 2026-09-05 session, which turned out to
contain summer-camp content, not disaster guidance. Only promote a session's export into
`dataset/gold/` after checking `SOURCES.md` matches the intended recipe.

## Promoting a run

1. Run the real recipe: `dataforge run examples/ready-gov.yaml` (drop `--dry-run` once the plan
   looks right).
2. Verify the export actually came from `ready.gov`/`fema.gov` — spot check a few `messages[0].content`
   values in `dataset_train.jsonl`, they should be disaster-prep questions, not something else.
3. Copy `output/sessions/<id>/exports/<ts>/dataset_{train,validation,test}.jsonl` into
   `dataset/gold/`, and write `SOURCES.md` / `GENERATION.md` alongside them — these two files are
   what let the paper's Data Collection and Synthetic Dataset Generation sections be written from
   fact instead of memory next time.
4. Build the eval holdout set from the test split: `python -m evals.harness.build_holdout
   --test-export dataset/gold/dataset_test.jsonl --db output/dataforge.db --out
   evals/holdout/test.jsonl` — see `../evals/README.md`. Do this before anything gets fine-tuned
   on `dataset_train.jsonl`, not after.
