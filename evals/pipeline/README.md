# evals/pipeline: pipeline benchmark

Measures what DataForge does on real websites: how much it fetches, how many
samples it generates and approves, why it rejects the rest, what it costs, how
long it takes, and whether the train/validation/test split leaks pages. This is
separate from `evals/harness/`, which scores a *model* trained on the dataset.

## Sites

`sites.yaml` lists the three benchmark sites and three that were considered and
excluded, with the reason: FAA (unclear terms), eCFR (blocks automated access,
asks for its API) and CDC (access depends on the client; terms not reviewed). Each run is capped at 20 URLs and
`max_cost_usd: 1.00` (`recipes/`).

**A person must review each site's terms before it can be crawled.** Fill in
`terms_review` (`reviewed_by`, `reviewed_on`, `outcome`) for a site in
`sites.yaml`; `run_benchmark.py` refuses any site where `reviewed_on` is empty.
Passing robots.txt is not permission: eCFR's robots.txt allows the Part 107
page, but the site redirects automated requests to a bot block and asks for
its API to be used instead.

## Steps

Run from the repository root, with an LLM provider configured (`.env` or
environment variables, see `docs/CONFIGURATION.md`).

```bash
# 1. Access check only: robots.txt, Crawl-delay, bot-block detection. Spends nothing.
uv run python -m evals.pipeline.run_benchmark --preflight-only

# 2. Run every reviewed site (or --site uscis). About $1 per site at most.
uv run python -m evals.pipeline.run_benchmark

# 3. Metrics: evals/results/pipeline_metrics_<ts>.{json,md} and pipeline_tables_<ts>.tex
uv run python -m evals.pipeline.aggregate

# 4. Human audit of the LLM judge: blind sheet, label `acceptable` y/n, then score.
uv run python -m evals.pipeline.audit sample --per-class 30
uv run python -m evals.pipeline.audit score evals/results/audit_<ts>.csv
```

Each site gets its own database and output folder under `output/benchmark/<site>/`,
and every run writes `run_summary.json` (timing per stage, LLM calls, cost,
budget, errors) into its session folder.

## What each number means

| Metric | Source |
|---|---|
| URLs selected / fetched / failed | `discovered_url` rows (selected after filters; fetched = scraped) |
| Pages, chunks | `scraped_page`, `processed_chunk` rows |
| Samples, approved, rejection reasons | `synthetic_sample` rows (judge scores below threshold grouped) |
| Cost, LLM calls, wall/stage seconds | `run_summary.json` |
| Leak-free | No `page_id` appears in more than one split file of the export |
| Judge precision | Share of audited *approved* samples a human labelled acceptable |
| Rejection precision | Share of audited *rejected* samples a human labelled unacceptable |

The audit samples equal numbers of approved and rejected samples per site, so
report the two precisions separately; a pooled accuracy would reflect the
sampling ratio, not the pipeline.

## Limitations

- 20 URLs per site is enough to show where each site's samples are lost and
  what they cost, not for tight confidence intervals.
- `source.max_urls` keeps the first N URLs in sitemap order, not a random sample.
- Results depend on the generation and judge models, which `run_summary.json`
  records; rerunning with a different model is a different experiment.
- One labeller gives agreement with that person, not inter-annotator agreement.
