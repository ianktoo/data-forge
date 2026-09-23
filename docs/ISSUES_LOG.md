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

| Date | Source | Issue | Summary | Resolved by |
|---|---|---|---|---|
| 2026-09-22 | [Gap analysis](reviews/2026-09-22-gap-analysis.md) | [#8](https://github.com/ianktoo/data-forge/issues/8) | No cost/token-budget cap on LLM generation or judge calls | [PR #14](https://github.com/ianktoo/data-forge/pull/14) |
| 2026-09-22 | [Gap analysis](reviews/2026-09-22-gap-analysis.md) | [#9](https://github.com/ianktoo/data-forge/issues/9) | Dedup is exact-match on a 200-char prefix — near-duplicate paraphrases pass through | [PR #15](https://github.com/ianktoo/data-forge/pull/15) |
| 2026-09-22 | [Gap analysis](reviews/2026-09-22-gap-analysis.md) | [#10](https://github.com/ianktoo/data-forge/issues/10) | No dataset profiling/statistics tooling, only raw sample viewing | [PR #16](https://github.com/ianktoo/data-forge/pull/16) |
| 2026-09-22 | [Gap analysis](reviews/2026-09-22-gap-analysis.md) | [#11](https://github.com/ianktoo/data-forge/issues/11) | HuggingFace/Kaggle exporters and non-default LLM providers untested | [PR #17](https://github.com/ianktoo/data-forge/pull/17) |
| 2026-09-22 | [Gap analysis](reviews/2026-09-22-gap-analysis.md) | [#12](https://github.com/ianktoo/data-forge/issues/12) | Google/Gemini provider implemented but undocumented (fixed in same PR) | fixed in filing PR |
| 2026-09-22 | [Technical paper](TECHNICAL.tex) review | [#19](https://github.com/ianktoo/data-forge/issues/19) | Unsloth/ShareGPT export drops source lineage (page_id, chunk_id, source_url) | [PR #42](https://github.com/ianktoo/data-forge/pull/42) |
| 2026-09-22 | [Technical paper](TECHNICAL.tex) review | [#20](https://github.com/ianktoo/data-forge/issues/20) | Split output rows not shuffled — samples cluster by source page, largest-first | [PR #42](https://github.com/ianktoo/data-forge/pull/42) |
| 2026-09-22 | README "What you'll need" review | [#22](https://github.com/ianktoo/data-forge/issues/22) | No generic OpenAI-compatible local endpoint support (LM Studio, Lemonade, vLLM) — only Ollama | [PR #42](https://github.com/ianktoo/data-forge/pull/42) |
| 2026-09-22 | [MCP run report](reviews/2026-09-22-mcp-run-report.md) | [#31](https://github.com/ianktoo/data-forge/issues/31) | `run` used a different database from sessions/stats/view, so MCP session tools could not find runs | [PR #38](https://github.com/ianktoo/data-forge/pull/38) |
| 2026-09-22 | [MCP run report](reviews/2026-09-22-mcp-run-report.md) | [#32](https://github.com/ianktoo/data-forge/issues/32) | `view --json --stage` opened an interactive pager, so `view_samples` never returned records | [PR #38](https://github.com/ianktoo/data-forge/pull/38) |
| 2026-09-22 | [MCP run report](reviews/2026-09-22-mcp-run-report.md) | [#33](https://github.com/ianktoo/data-forge/issues/33) | Errors under `--json` were plain text with exit 0; MCP tools failed with no reason | [PR #38](https://github.com/ianktoo/data-forge/pull/38) |
| 2026-09-22 | [MCP run report](reviews/2026-09-22-mcp-run-report.md) | [#34](https://github.com/ianktoo/data-forge/issues/34) | `run_status` returned coloured DEBUG logs | [PR #38](https://github.com/ianktoo/data-forge/pull/38) |
| 2026-09-22 | [MCP run report](reviews/2026-09-22-mcp-run-report.md) | [#35](https://github.com/ianktoo/data-forge/issues/35) | False "No LLM provider key detected" warning when the key is in `.env` | [PR #38](https://github.com/ianktoo/data-forge/pull/38) |
| 2026-09-22 | [MCP run report](reviews/2026-09-22-mcp-run-report.md) | [#36](https://github.com/ianktoo/data-forge/issues/36) | Dry-run plan did not show the spending cap | [PR #38](https://github.com/ianktoo/data-forge/pull/38) |
| 2026-09-22 | [MCP run report](reviews/2026-09-22-mcp-run-report.md) | [#37](https://github.com/ianktoo/data-forge/issues/37) | Docs: Claude Code loads a newly added MCP server only in a new session | [PR #38](https://github.com/ianktoo/data-forge/pull/38) |
| 2026-09-22 | 2.4.1 verification run through MCP | [#40](https://github.com/ianktoo/data-forge/issues/40) | `stats` / `session_stats` returned `split_counts: null` when a recipe set its own `output_dir` | [PR #42](https://github.com/ianktoo/data-forge/pull/42) |
| 2026-09-22 | 2.4.1 verification run through MCP | [#41](https://github.com/ianktoo/data-forge/issues/41) | `run_status` progress was null for small runs | [PR #42](https://github.com/ianktoo/data-forge/pull/42) |
| 2026-09-22 | Update bug report (Windows) | [#45](https://github.com/ianktoo/data-forge/issues/45) | `dataforge update` reported "Update failed" on Windows although the update worked (os error 32 on the running launcher) | [PR #49](https://github.com/ianktoo/data-forge/pull/49) |
| 2026-09-22 | Update bug report (Windows) | [#46](https://github.com/ianktoo/data-forge/issues/46) | `update` replaced DataForge while running; no `uninstall` command | [PR #49](https://github.com/ianktoo/data-forge/pull/49) |
| 2026-09-22 | Update bug report (Windows) | [#47](https://github.com/ianktoo/data-forge/issues/47) | AI agents could update or uninstall DataForge without anyone reading the release notes | [PR #49](https://github.com/ianktoo/data-forge/pull/49) |
| 2026-09-22 | Update bug report (Windows) | [#48](https://github.com/ianktoo/data-forge/issues/48) | Input sources beyond websites: PDFs, documents, audio | open |
| 2026-09-22 | 2.4.3 release check | [#51](https://github.com/ianktoo/data-forge/issues/51) | Release binaries overwrote each other: no macOS binary, ambiguous names | [PR #52](https://github.com/ianktoo/data-forge/pull/52) |
| 2026-09-22 | 2.4.3 stability check | [#53](https://github.com/ianktoo/data-forge/issues/53) | No-sitemap crawl only follows links in the main content, so it misses site navigation | [PR #58](https://github.com/ianktoo/data-forge/pull/58) |
| 2026-09-22 | 2.4.3 stability check | [#54](https://github.com/ianktoo/data-forge/issues/54) | Standalone binaries crash silently: missing data files (agent guide, litellm, tiktoken) | [PR #56](https://github.com/ianktoo/data-forge/pull/56) |
| 2026-09-22 | 2.4.3 stability check | [#55](https://github.com/ianktoo/data-forge/issues/55) | No CI tests: lint, tests and a package smoke test on every OS and Python | [PR #56](https://github.com/ianktoo/data-forge/pull/56) |
