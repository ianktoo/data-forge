# Issues log

Running index of gaps and follow-up work discovered during review sessions,
each filed as a GitHub issue at discovery time rather than left as a note in
a report. This file is the index; the issue is the source of truth for
status, discussion and resolution.

Convention going forward:
1. When a review (manual or agent-assisted) surfaces a real gap, file it as a
   GitHub issue immediately — title, clear problem statement, why it matters,
   suggested fix or investigation, and a label (`bug`, `gap:data`,
   `gap:pipeline`, `gap:ai`, `tech-debt`, `documentation`).
2. Add one row below linking to it. Do not duplicate the issue's content
   here — link out.
3. A dated report under `docs/reviews/` may accompany a review session with
   the full analysis; link both the report and the issues it produced.

## Index

| Date | Source | Issue | Summary |
|---|---|---|---|
| 2026-09-22 | [Gap analysis](reviews/2026-09-22-gap-analysis.md) | [#8](https://github.com/ianktoo/data-forge/issues/8) | No cost/token-budget cap on LLM generation or judge calls |
| 2026-09-22 | [Gap analysis](reviews/2026-09-22-gap-analysis.md) | [#9](https://github.com/ianktoo/data-forge/issues/9) | Dedup is exact-match on a 200-char prefix — near-duplicate paraphrases pass through |
| 2026-09-22 | [Gap analysis](reviews/2026-09-22-gap-analysis.md) | [#10](https://github.com/ianktoo/data-forge/issues/10) | No dataset profiling/statistics tooling, only raw sample viewing |
| 2026-09-22 | [Gap analysis](reviews/2026-09-22-gap-analysis.md) | [#11](https://github.com/ianktoo/data-forge/issues/11) | HuggingFace/Kaggle exporters and non-default LLM providers untested |
| 2026-09-22 | [Gap analysis](reviews/2026-09-22-gap-analysis.md) | [#12](https://github.com/ianktoo/data-forge/issues/12) | Google/Gemini provider implemented but undocumented (fixed in same PR) |
