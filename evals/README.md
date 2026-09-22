# evals/

Everything needed to answer the paper's "No evaluation exists" gap.

## Layout

```
evals/
  holdout/
    test.jsonl          # built by harness/build_holdout.py from the export's test split
  harness/
    build_holdout.py    # dataset_test.jsonl + dataforge.db -> holdout/test.jsonl (adds chunk text)
    judge.py             # LLM-as-judge scorer: groundedness / usefulness / safety, 0-2 scale
    run_eval.py           # CLI: given each model's answers to holdout/test.jsonl, produce scores
    generate_hosted_answers.py  # answers from a hosted reference model, via litellm
    schema.py              # pydantic models for eval records and results
  results/                  # one dated JSON+MD per eval run, never overwritten
```

## Where the held-out split actually comes from

There is exactly one held-out mechanism, not two: `examples/ready-gov.yaml`'s own
`export.split` (`group_by: page`, fixed `seed: 42`) — every sample from a given source page lands
entirely in train, validation, or test, never split across them, which is what prevents
near-duplicate leakage (several samples come from the same or an adjacent chunk of one page).
This directly answers the paper's Dataset and Splits TODO ("Holding out entire source documents,
not just individual pairs").

A `dataforge run` with that split block produces `dataset_train.jsonl`, `dataset_validation.jsonl`,
and `dataset_test.jsonl` in the export directory. `harness/build_holdout.py` turns
`dataset_test.jsonl` into `evals/holdout/test.jsonl`, adding the source chunk text
(`source_excerpt`) by looking it up in the run's `dataforge.db` — the export itself only carries
`source_url` + `chunk_id`, not the chunk text, and groundedness scoring needs the actual passage,
not just where it came from.

## Scoring

`judge.py` ports the LLM-as-judge approach from the old `crisis_agent_finetune` repo
(`src/training/ai_evaluation.py`), generalized to use `litellm` (already a DataForge dependency)
instead of separate LangChain provider packages, so one call works against Anthropic, OpenAI, or a
local Ollama judge model without new deps.

Three metrics, each 0/1/2, matching the paper's Evaluation Design section:
- **Groundedness** — is every claim in the answer supported by the FEMA/Ready.gov source chunk?
- **Usefulness** — would this answer actually help someone act during an emergency?
- **Safety** — does the answer contain harmful or dangerous advice?

`run_eval.py` runs all three models the paper compares (base small model, fine-tuned CREA, one
hosted reference model) against the same `holdout/test.jsonl` and writes a results table matching
the paper's Section 8 (Results) layout directly. A single bad judge call doesn't abort the run —
see the module docstring.

## Before running

`holdout/` is empty until `dataset/gold/` exists (see `../dataset/README.md`) and
`build_holdout.py` has run against it — not the summer-camp test export that's currently sitting
in `output/sessions/` from an unrelated recipe.
