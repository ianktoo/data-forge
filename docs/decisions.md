# Decisions log — CREA dataset

Technical reasoning behind `dataset/` and `evals/`. Source for the paper's Data Collection and
Synthetic Dataset Generation sections once a real run exists.

## Regenerating instead of reusing the old dataset

The predecessor project's dataset (`ianktoo/crisis-data-v3-sft`, ~2,000 pairs) has no recorded
teacher model, generation prompt, chunking strategy, or filtering/dedup rules, and never had a
held-out split (`merge_all_splits_for_training: true` in its config). None of that is defensible
in a paper. Regenerating via `dataforge run examples/ready-gov.yaml` instead means every one of
those parameters gets recorded as it happens — see `dataset/README.md` for the promotion
checklist that enforces this (`SOURCES.md` + `GENERATION.md` per export).

## Known issue found 2026-09-22

The only dataset export that existed at the time (`output/sessions/cbad682a-.../exports/
20260905_220123/dataset.jsonl`) is **not** FEMA/Ready.gov content — it's Q&A about a summer camp.
The `examples/ready-gov.yaml` recipe had not actually been run end-to-end. Do not promote that
export to `dataset/gold/` if it's still sitting there — re-run the real recipe first.

## Held-out split strategy (revised 2026-09-22)

Originally designed as a hand-rolled `evals/holdout/held_out_urls.txt` list of excluded source
URLs. Dropped in favor of `examples/ready-gov.yaml`'s own `export.split` (`group_by: page`, fixed
`seed: 42`), which already does the same thing — every sample from one source page lands wholly
in train, validation, or test — natively, at export time, with a fixed seed for reproducibility.
Two mechanisms doing the same job risked either double-splitting or silently training on data
that should have been held out; there is now exactly one. `evals/harness/build_holdout.py` turns
the export's `dataset_test.jsonl` into `evals/holdout/test.jsonl`, adding the source chunk text
(looked up from the run's `dataforge.db` by `chunk_id`) that the export doesn't carry directly —
groundedness scoring needs the actual passage the answer should be checked against, not just its
URL.

## Evaluation harness

LLM-as-judge (`evals/harness/judge.py`), ported from the predecessor repo's
`src/training/ai_evaluation.py` and generalized to `litellm` (already a dependency here) instead
of separate per-provider SDKs. Scores groundedness / usefulness / safety, 0-2 each, matching the
paper's Evaluation Design section. Full three-way comparison strategy (base model vs. fine-tuned
CREA vs. hosted reference) is documented in `crea-finetune/STRATEGY.md`, since it spans both
repos.
