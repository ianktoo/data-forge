# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [2.4.1] - 2026-09-22

Fixes from the first pipeline run driven through the MCP server
(`docs/reviews/2026-09-22-mcp-run-report.md`). No breaking changes.

### Fixed
- **`run` used a different database from `sessions`, `stats` and `view`.** `run`
  skipped the CLI bootstrap, so it ignored the `.dataforge` project file and wrote
  to `./dataforge.db` while the reporting commands read the project file's
  database. The MCP tools `session_stats`, `view_samples` and `list_sessions`
  therefore could not find sessions that `start_run` created. `run` now
  bootstraps like every other command; an explicit `DATAFORGE_DB_PATH` or
  `DATAFORGE_OUTPUT_DIR` still takes precedence.
- **`view --json --stage` opened an interactive pager.** It now prints the rows as
  JSON (`session_id`, `stage`, `total`, `rows`), so the MCP `view_samples` tool
  works. Without a terminal, human mode prints the first page instead of paging.
  Sample rows include `id`, `chunk_id` and `rejection_reason`.
- **Errors under `--json` were plain text with exit code 0.** They are now
  `{"error": "..."}` with a non-zero exit, and the MCP server passes the CLI's
  message to the agent instead of a bare "Error executing tool".
- **`run_status` returned coloured DEBUG logs.** `run` now configures logging
  (INFO), the console logger honours `NO_COLOR`, and `run_status` strips colour
  codes and DEBUG lines and adds a `progress` field.
- **False "No LLM provider key detected" warning** when the key is in `.env`.
- The dry-run plan now shows the spending cap (`Budget:`) and the database path.

### Documentation
- Agent guide and README: Claude Code loads a newly added MCP server only in a
  new session (`/exit`, then `claude --continue` in the terminal); register the
  executable's full path when `dataforge` is not on PATH.
- `examples/ready-gov-core-hazards.yaml`: a focused Ready.gov recipe (floods,
  severe weather, wildfires, hurricanes, power outages, alerts, plan, kit,
  National Preparedness Month). `examples/ready-gov.yaml` now has a spending cap.
- `evals/pipeline/sites.yaml`: Ready.gov's own terms page recorded, with the
  owner's academic-use decision.
- `docs/TECHNICAL.tex`: the agent-integration section records the first real
  MCP-driven run and the inspection-tool failures it exposed. The title page
  now carries the report's Zenodo DOI, 10.5281/zenodo.22906072, under the
  author's name (linked, and in the PDF metadata), and is labelled
  "Technical Note, 2026".

## [2.4.0] - 2026-09-22

Agent integration (agent guide, local MCP server), a pipeline benchmark,
and crawl-politeness fixes the benchmark uncovered. No breaking changes.

### Added
- `dataforge agent-guide`: prints a guide for AI agents driving DataForge from
  a shell (non-interactive commands, env-var configuration, recipe workflow,
  exit codes, responsible-use rules). The guide ships inside the package, so it
  is available after `pip install` without the repository.
- `dataforge mcp`: runs DataForge as a local MCP server over stdio (optional
  extra: `pip install "llm-web-crawler[mcp]"`, requires `mcp>=2.2,<3`). Tools:
  `explore_site`, `validate_recipe`, `start_run` / `run_status` (background
  runs), `list_sessions`, `session_stats`, `view_samples`; the agent guide is
  served as the server's instructions and as the `dataforge://guide` resource.
  `start_run` refuses recipes with no `max_cost_usd` / `max_llm_calls` cap
  unless the caller opts in, and always refuses `source.ignore_robots: true`.
- `run_summary.json` in each session folder after `dataforge run`: exit code,
  wall time and time per stage, models, approved count, LLM calls/cost, budget
  and skipped calls, errors. Previously these were only printed.
- `evals/pipeline/`: pipeline benchmark over four sites (Ready.gov, USCIS,
  the Python tutorial and iantoo.space, the last with the owner's consent),
  each capped at 20 URLs and $1. KRA (terms prohibit data mining), FAA
  (unclear terms), eCFR (bot block, API only) and CDC are documented as
  excluded. Refuses a site until a person records a terms review in `sites.yaml`; a preflight records
  robots.txt, Crawl-delay and bot blocks and skips blocked sites. Aggregation
  into JSON/Markdown/LaTeX (including a split page-leak check) and a blind
  human audit of the LLM judge (judge precision, rejection precision, kappa).

### Fixed
- **robots.txt `Crawl-delay` was not enforced while scraping.** The "already
  applied" flag was process-wide, so a domain's delay reached only the first
  rate limiter created (discovery); the scrape and streaming stages build
  their own and crawled at the default rate. Found by the benchmark: USCIS
  declares `Crawl-delay: 10` and 20 pages were fetched in about 4 seconds.
  The flag is now tracked per limiter, and each run now shares one limiter
  across all stages, so the first request of a new stage no longer skips the
  delay. Random jitter now only ever lengthens a gap; before, single gaps fell
  to about 8.6 s against a 10 s `Crawl-delay`.
- **The rate limiter ran at about twice the configured rate.** The token
  bucket counted its own sleep as refill time, so every other request went
  through free, and it allowed a burst of 2x the rate. The burst is now one
  second's worth, or a single request when a `Crawl-delay` applies.
- **robots.txt failures now follow RFC 9309.** A 4xx means no rules; a 5xx,
  429 or unreachable server disallows the whole site. Before, a server-error
  page was parsed as if it were robots.txt, which in effect allowed everything.
  The robots.txt request itself now waits on the rate limiter.
- **The Playwright fallback bypassed politeness.** It launched a browser with
  no robots.txt check, no rate limit and a generic User-Agent. It now passes
  the same gate as every request (`HTTPClient.prepare`), sends DataForge's
  User-Agent, skips images/media/fonts, and is not used on a site that
  declares a `Crawl-delay`.
- **Judge and regex rejections shared a label.** Judge rejections are now
  prefixed `judge:` (`judge: not standalone (refers to the source)`,
  `judge: not grounded in source`, `judge: score 3 < 4`).
- **An empty split was silent.** With fewer page groups than splits, a split
  could receive nothing and simply not be written; it is now logged as a
  warning. The split's docstring states that assignment is largest-first
  greedy and the seed only breaks ties.
- **Relative links were resolved against the site root** instead of the page
  URL (or `<base href>`), sending the BFS crawler to wrong addresses on any
  site whose pages live below the root.
- Discovery: a seed deeper than the site root (e.g. `/3/tutorial/`) whose
  sitemap lists nothing under it now falls back to crawling from the seed.
  Before, it returned the sitemap's unrelated URLs and dropped the seed, so
  sites with a shallow sitemap (docs.python.org lists only version roots)
  could not be crawled at all.
- **TLS verification now uses the operating system's trust store**
  (`truststore`, as pip does). Sites that send an incomplete certificate chain
  (e.g. uonbi.ac.ke, jkuat.ac.ke, strathmore.edu) or chain to a root missing
  from certifi (health.go.ke) failed with `CERTIFICATE_VERIFY_FAILED` although
  browsers accept them. Verification is never disabled; self-signed and
  expired certificates are still rejected.
- The crawler's User-Agent reported `DataForge/0.1` and a URL that is not the
  project (`github.com/dataforge`); it now sends the real version and
  `https://github.com/ianktoo/data-forge`, so site operators can identify it.

### Changed
- `docs/TECHNICAL.tex`: new section "Empirical evaluation" with the benchmark
  results (setup, an access-and-terms table over the 14 sites considered,
  per-site results, the six defects the benchmark exposed, and what the
  results do not show), corrections where earlier sections described
  `Crawl-delay` enforcement as already holding, an updated abstract and
  conclusion, and an AI-assistance acknowledgment. Cites 17 U.S.C. 105 and
  Kenya's Copyright Act (ss. 2, 25, 31).
- `docs/TECHNICAL.tex`, after a critical review: design claims corrected to
  match the code (judge batched per chunk with a 1-5 score plus
  grounded/standalone checks; filter order; `cl100k_base` tokenizer; split is
  largest-first greedy, not random; cost cap can overshoot by in-flight calls;
  Retry-After cap; RFC 9309 behaviour); an unmeasured "the judge is
  empirically reliable" claim removed; rejection breakdown separated into
  regex (9) vs. judge (9) with the judge's score ceiling (89% scored 5); the
  near-duplicate check's non-firing explained with measured Jaccard values;
  held-out split sizes reported, including iantoo.space's empty test split;
  streaming-vs-batch and downstream effects stated as unmeasured; hard-coded
  section numbers replaced with references; jurisdiction point narrowed; six
  citations added (RFC 9309, Panickssery et al. 2024, Alberti et al. 2019,
  Puri et al. 2020, Nayak et al. 2024, distilabel).
- `docs/TECHNICAL.tex`: new section "Agent integration via the Model Context
  Protocol" (agent guide, the MCP server's tools and server-side guards, expected
  benefits stated as unmeasured, current gaps), citing the MCP announcement,
  the MCP specification (rev. 2026-07-28) and the Claude Code MCP docs.
- Replaced the architecture diagram (`docs/architecture.svg` / `.pdf`) with a
  stage table in `docs/TECHNICAL.tex` and `docs/ARCHITECTURE.md`. The README
  now shows a one-line pipeline summary that links to the architecture doc.

### Removed
- `docs/architecture.svg` and `docs/architecture.pdf`, which nothing
  references now that the stage table has replaced them.

## [2.3.3] - 2026-09-22

> `v2.3.2`'s tag and GitHub Release (with working cross-platform
> executables) were published successfully, but its "Publish to PyPI" CI
> job failed its pre-publish test gate on the Ubuntu runner before ever
> calling `uv publish` — PyPI has no `2.3.2` package. Cause: a test
> (`test_full_headless_run_produces_an_export`) picked the first
> `*.jsonl` match from a directory glob without filtering out
> `dataset_unsloth.jsonl` (different schema, no `messages` key — see
> issue #19); glob order isn't guaranteed across filesystems, and picked
> the wrong file on Linux where it hadn't locally on Windows. Fixed by
> filtering explicitly rather than relying on order. Re-cut as `2.3.3`
> rather than moving the already-public `v2.3.2` tag.

### Fixed

- Test reliability: `test_full_headless_run_produces_an_export` no longer
  depends on filesystem glob ordering to find the primary dataset export.

## [2.3.2] - 2026-09-22

Documentation, hardening, and traceability release. No breaking changes.

### Added

- **`dataforge stats <session_id>`** — dataset profiling command: approval
  rate, rejection-reason breakdown (now persisted, not just logged),
  quality-score distribution, question/answer length statistics, and
  realized train/validation/test split proportions.
- **LLM cost/call budget cap** — `generation.max_cost_usd` /
  `generation.max_llm_calls` recipe keys, enforced by a shared,
  concurrency-safe tracker across generation and the quality judge. Once
  hit, new calls are skipped cleanly rather than the run crashing or
  spending past the configured limit.
- **Near-duplicate detection** — deduplication now hashes full sample
  content (fixing false-positive collisions on shared openings) and adds a
  per-chunk token-set Jaccard near-duplicate check
  (`quality.near_dup_threshold`, default 0.85), catching paraphrase
  redundancy that an exact-hash check structurally cannot.
- `.gitattributes`, marking PDF/PNG/JPEG/DB files as binary so they aren't
  subject to CRLF translation on checkout.

### Changed

- **README rewritten** to lead with the pitch and a "What you'll need"
  section (Python + one LLM — hosted key or local via Ollama — is the only
  hard requirement; HuggingFace/Kaggle accounts are optional, only for
  those export targets). Technical detail (install, full env var and
  recipe reference, dev/release workflow, ethics, third-party licenses)
  moved to `docs/`.
- `docs/TECHNICAL.tex` — implementation technical writeup extended with:
  the fixes above, a "Retrieval augmentation as a complement, not a
  substitute" section on RAG vs. fine-tuning for hallucination reduction, a
  "Responsible use and misuse considerations" section, and non-text source
  ingestion (PDF/table/image/audio) named as the concrete next step. Five
  references added, each verified against its actual venue/arXiv ID.
  Architecture diagram embedded as `docs/architecture.pdf` (pre-rendered
  from `docs/architecture.svg`) so the paper compiles on hosted LaTeX
  toolchains (e.g. Papeeria) without `shell-escape`/Inkscape.
- Documented the `google` (Gemini) provider, which existed in
  `config/providers.py` but was missing from the README/docs.

### Fixed

- Test coverage added for `exporters/huggingface.py`, `exporters/kaggle_exp.py`,
  and the full `config/providers.py` registry — previously untested.

### Process

- Established a going-forward convention (`docs/ISSUES_LOG.md`): gaps found
  during review are filed as GitHub issues with problem/impact/fix, not
  left as prose in a report. This release's work is traceable through
  issues [#8](https://github.com/ianktoo/data-forge/issues/8)–[#12](https://github.com/ianktoo/data-forge/issues/12)
  (filed, fixed, and closed) and [#19](https://github.com/ianktoo/data-forge/issues/19),
  [#20](https://github.com/ianktoo/data-forge/issues/20),
  [#22](https://github.com/ianktoo/data-forge/issues/22) (filed, **open** —
  see below).

### Known open issues at this release

- **[#19](https://github.com/ianktoo/data-forge/issues/19)** — the
  Unsloth/ShareGPT export format drops source lineage (`page_id`,
  `chunk_id`, `source_url`); the plain JSONL/Parquet/CSV exports are
  unaffected.
- **[#20](https://github.com/ianktoo/data-forge/issues/20)** — rows within
  a split's output file are not shuffled; they cluster by source page,
  largest page first. Split *membership* (which page lands in which
  split) is correctly randomized — only row order within a split is not.
- **[#22](https://github.com/ianktoo/data-forge/issues/22)** — no generic
  OpenAI-compatible local endpoint support. "Fully local" today means
  Ollama specifically; LM Studio, Lemonade, vLLM, etc. aren't wired in.

## [2.3.1] - 2026-09-05

> This is the first release cut after reconciling `master` with the
> `feature/ux-overhaul` branch that `v2.2.0`/`v2.3.0` had actually shipped
> from (see below) — `master` itself had drifted behind those releases.
> Backfilled here for a complete history.

### Fixed

- **Quality stage crash** (`'dict' object has no attribute 'split'`): a generated
  sample whose LLM response contained a nested object where plain text was
  expected (e.g. `{"question": {...}}`, or a `conversation`-format message with
  non-string content) silently corrupted stored samples and crashed the quality
  stage several steps later. LLM-generated messages are now validated and
  normalized (role checked, content coerced to a string) at the point they're
  generated, not left to explode downstream.
- **`.env` configuration was silently ignored**: `Settings` declared its config
  twice, and the second declaration replaced the first, dropping the
  `DATAFORGE_` environment variable prefix entirely. Every documented setting in
  `.env.example` (`DATAFORGE_LLM_MODEL`, `DATAFORGE_OUTPUT_DIR`,
  `DATAFORGE_RATE_LIMIT`, etc.) was a no-op — the app always used its hardcoded
  defaults regardless of `.env` contents. **This is now fixed and your `.env`
  settings will take effect** — see the upgrade guide in the README if you'd
  been relying on the previous (broken) behavior.
- **Quality-stage errors showed no guidance**: an unrecognized error key fell
  through to a bare `Error: quality` panel with only "check the logs" as
  advice. Added dedicated guidance, and the generic fallback now also prints
  the resolved log file path and its last few lines.
- **LLM connection errors were unreachable from the orchestrator's failure
  path**: the check that should have shown the "cannot connect to LLM
  provider" panel tested for the substring `"llm"` inside the stage name
  (`"discovery"`, `"quality"`, etc.) — a condition that can never be true.
  Now keyed on the actual exception type.
- **Interrupting the stage-action prompt could strand a session**: a
  `Ctrl+C` (or any error) while answering "continue / export / pause /
  adjust" between stages wasn't caught by the orchestrator, leaving the
  session `status=active` in the database with nothing actually running it.
- **`dataforge resume` couldn't find sessions stranded by the bug above**:
  auto-detect only looked for `status=paused` sessions, so a stuck `active`
  session was invisible to it ("session is still active") — you had to know
  to run `dataforge sessions` and pass the exact ID. Auto-detect now surfaces
  stale `active` sessions too, clearly labelled.
- **`dataforge update` crashed for pip-installed users without `uv` on
  PATH**: the `uv tool upgrade` attempt didn't guard against `uv` simply not
  being installed, so it raised an unhandled `FileNotFoundError` instead of
  falling back to the `pip install --upgrade` path.
- Stage names used internally were inconsistent (`"quality"` vs.
  `"PipelineStage.quality"`) depending on whether a pipeline run started
  fresh or was resumed, producing confusing log/error messages.
- A single global database engine was cached with no key, so once created
  for one `db_path`, any later request for a *different* `db_path` silently
  kept using the first one. Now cached per resolved path.

### Added

- `dataforge test-llm`: pick any configured provider/model and ask it a
  random test question — a quick way to sanity-check a model or API key
  without running the full pipeline.
- **Mid-session settings adjustment**: a new "Adjust settings" option in the
  between-stage menu lets you change the generation model, quality model, or
  output directory partway through a run, instead of only at initial setup.
  (Changing the output directory does not move the session database — your
  in-progress session's data stays where it is; only new exports/artifacts
  follow the new directory.)
- `DATAFORGE_AUTOSAVE` setting (default `true`): makes the existing
  after-every-stage checkpoint behavior an explicit, documented, and
  configurable switch.

### Changed

- `LLMClient` accepts an independent `provider_override`, and credential-error
  messages now name the actually-selected provider rather than always the
  globally-configured one.

## [2.3.0] - 2026-04-24

### Changed

- `settings.py`: merged duplicate `model_config` into a single definition
  (independently re-discovered and re-fixed for `master` in `2.3.1` above,
  since this branch's version of the fix never reached `master` until now).
- `quality.py`: batch all DB writes for a run into one session instead of
  one commit per sample; expanded the refusal-phrase regex; compiled it once
  as `_REFUSAL_RE`.
- `rate_limiter.py`: guard against division by zero (`effective_rate = max(rate, 1e-6)`).
- `http.py`: LRU-capped robots.txt cache (256 entries); differentiate a
  robots-disallowed fetch (`PermissionError`) from a genuine fetch failure.

## [2.2.0] - 2026-04-24

### Added

- Main menu now uses arrow-key navigation (`questionary.select`) instead of
  typed commands.
- `dataforge clear`: deletes the `.dataforge` project file to start fresh in
  a directory (session data in the database is kept).
- Paginated browsing (`n`/`p`/`q`) for `dataforge view`'s discovered URLs,
  scraped pages, chunks, and samples.
- `ProcessorAgent` now processes pages concurrently (`asyncio.gather` +
  `asyncio.to_thread`, capped at `min(cpu_count, 8)` workers).

### Changed

- Session names default to a timestamp (`dataset-YYYY-MM-DD-HHMM`) instead
  of a UUID-style string.
- The URL step shows previously-entered URLs and asks to keep/edit them,
  instead of forcing re-entry every time.
- Heavy dependencies (`litellm`, `httpx`, `tiktoken`) and `PipelineContext`/
  `ExporterAgent` are now imported lazily at the call sites that need them,
  instead of eagerly at module load, for faster CLI startup.
- Batched per-page DB writes (collect → commit) instead of one commit per
  page; `return_exceptions=True` so one bad page can't abort the whole run.
- SQLite now runs in WAL journal mode with a 30s busy timeout, to handle
  concurrent writers without "database is locked" errors.
- ruff/mypy cleanup pass (unused imports, ambiguous names, `StrEnum`
  migration, `str | None` coercions).

