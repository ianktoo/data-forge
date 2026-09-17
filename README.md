# DataForge

**Turn any website into a fine-tuning dataset — in one command.**

[![PyPI](https://img.shields.io/pypi/v/llm-web-crawler)](https://pypi.org/project/llm-web-crawler/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Fine-tuning needs data, and good domain data is trapped in documentation sites,
knowledge bases and public archives. Getting it out usually means writing a
throwaway scraper, a chunker, a prompt loop and an exporter — then doing it
again for the next domain.

DataForge is that pipeline, already built:

```
sitemap ──▶ crawl ──▶ clean + chunk ──▶ LLM generates Q&A ──▶ score ──▶ JSONL / Parquet / HF Hub
```

```bash
pip install llm-web-crawler
dataforge init-recipe fema.yaml   # write a starter config
dataforge run fema.yaml           # crawl, generate, score, export — unattended
```

You get a versioned, deduplicated, quality-scored dataset in ChatML JSONL,
Parquet and CSV, ready for Unsloth, Axolotl, TRL or HuggingFace `datasets`.

### Why DataForge

| | |
|---|---|
| **Configuration as code** | A YAML recipe captures every decision. Commit it, review it in a PR, re-run it in CI — the same input produces the same dataset. |
| **Unattended by default** | `dataforge run` needs no prompts. Cron it, or drive it interactively with `dataforge` when exploring a new site. |
| **Streaming pipeline** | Generation starts on the first page instead of waiting for the last one, so the LLM and the crawler work at the same time. |
| **Resumable, not restartable** | Every page, chunk and sample is checkpointed to SQLite. Interrupt a 5,000-page crawl and resume exactly where it stopped — nothing is re-fetched or re-billed. |
| **Any model** | OpenAI, Anthropic, Groq, Together, or fully local via Ollama — so sensitive content never has to leave your machine. |
| **Polite by construction** | `robots.txt` honoured, per-domain rate limiting, URL sanitisation, and PII/copyright guidance built into the docs. |

---

## A worked example

Building a U.S. disaster-preparedness dataset from FEMA and Ready.gov. The
full recipe ships with the project at
[`examples/fema-ready.yaml`](examples/fema-ready.yaml):

```yaml
version: 1
name: fema-ready-preparedness
stream: true                     # overlap crawling and generation

source:
  urls:
    - https://www.ready.gov/sitemap.xml
    - https://www.fema.gov/sitemap.xml
  include: ['/hazard', '/plan', '/kit', '/disaster']
  exclude: ['/es/', '/press-release', '.pdf']
  max_urls: 250

generation:
  format: qa
  goal: >
    Accurate Q&A about U.S. disaster preparedness, grounded strictly in
    official FEMA and Ready.gov guidance.
  n_per_chunk: 3

quality:
  threshold: 0.5

export:
  targets: [local]
```

```bash
dataforge run examples/fema-ready.yaml --dry-run   # validate + preview the plan
dataforge run examples/fema-ready.yaml             # execute
```

Two sitemaps become a few hundred curated pages, a few thousand chunks, and a
scored, deduplicated Q&A dataset — without answering a single prompt.

---

## Installation

### uv (recommended)
```bash
uv tool install llm-web-crawler
dataforge
```

Update:
```bash
uv tool upgrade llm-web-crawler
```

Uninstall:
```bash
uv tool uninstall llm-web-crawler
```

### pip
```bash
pip install llm-web-crawler
dataforge
```

Update:
```bash
pip install --upgrade llm-web-crawler
```

Uninstall:
```bash
pip uninstall llm-web-crawler
```

### From source
```bash
git clone https://github.com/ianktoo/data-forge.git
cd data-forge
uv sync
uv run dataforge
```

### Standalone executables (no Python required)
Download pre-built binaries for your platform from [GitHub Releases](https://github.com/ianktoo/data-forge/releases):

| Platform | File |
|---|---|
| Windows | `dataforge-windows-x64.exe` |
| macOS | `dataforge-macos-x64` |
| Linux | `dataforge-linux-x64` |

---

## Quick start

DataForge has two modes. Use the wizard to explore a new site, then freeze
what worked into a recipe and run it unattended from then on.

**Unattended — configuration as code**

```bash
dataforge init-recipe my.yaml       # write an annotated starter recipe
dataforge run my.yaml --dry-run     # validate and print the plan, run nothing
dataforge run my.yaml               # execute end-to-end, no prompts
```

**Interactive — guided wizard**

```bash
dataforge                  # full interactive pipeline with per-stage review
dataforge explore <url>    # preview URL discovery without running the pipeline
```

**Everything else**

```bash
dataforge config           # set your LLM provider and API key
dataforge sessions         # list past sessions
dataforge resume <id>      # resume a paused session
dataforge view <id> --stage generation   # inspect what was produced
dataforge export <id>      # re-export an existing session
dataforge update           # update to the latest version
```

---

## Features

### Recipes — configuration as code
Every answer the wizard would ask for lives in one YAML file, so a dataset build
is reproducible, reviewable and automatable.

```bash
dataforge init-recipe my.yaml     # annotated starter file
dataforge run my.yaml --dry-run   # validate schema + print the resolved plan
dataforge run my.yaml             # execute
```

- **Validated on load** — a typo, an out-of-range threshold or a missing
  `hf_repo_id` is reported with its exact field path *before* the crawl starts,
  not three hours in.
- **URL scoping without a wizard** — `include` / `exclude` substring filters and
  `max_urls` replace the interactive review step. Keep one locale, drop press
  releases, cap cost.
- **Split the URL list out** — point `source.url_file` at a plain text file
  (resolved relative to the recipe) and commit the two together.
- **Layered config** — a recipe only overrides the keys it declares; everything
  else still comes from `.env` and your global settings.
- **CI-friendly exit codes** — `0` ok, `2` invalid recipe, `3` no URLs survived
  the filters, `4` paused, `5` finished with zero approved samples.

### Leak-free train / validation / test splits
A synthetic dataset built from scraped pages is not independently distributed:
`n_per_chunk` samples come from one chunk, and several chunks come from one
page, so every sample tracing back to a page is a paraphrase of the same source
text.

**Splitting that randomly inflates your eval score.** Near-duplicates of the
same passage land on both sides, and the model answers eval questions from text
it memorised during training.

```yaml
export:
  split:
    train: 0.8
    validation: 0.1
    test: 0.1
    group_by: page     # every sample from one page stays in one split
    seed: 42
```

This writes `dataset_train.*`, `dataset_validation.*` and `dataset_test.*` in
each format, assigning whole groups so no source page spans two splits — and
verifies that property before writing, rather than assuming it.

Every export carries `page_id`, `chunk_id`, `chunk_index` and `source_url`
alongside the messages, **whether or not you split here**. That lineage is what
makes a correct split possible later; without it, a downstream `train_test_split`
has no way to avoid leaking.

### Resilient collection
Real sites fail in specific ways, and DataForge distinguishes them rather than
treating every non-200 the same:

- **Transient statuses retry** — `429`, `500`, `502`, `503` and `504` are retried
  up to three times with exponential backoff, because the server is saying
  "later", not "no".
- **`Retry-After` is honoured** when present (delta-seconds or HTTP-date),
  capped at 120s so one URL cannot stall a run.
- **Permanent statuses do not retry** — a `404` or `403` is taken at its word,
  which keeps the crawl budget for pages that exist.
- **`robots.txt` `Crawl-delay` is enforced.** If a site asks for one request
  every 15 seconds (FEMA.gov does), the limiter drops to that rate automatically.
  It only ever slows down — a permissive `Crawl-delay` never overrides a stricter
  `rate_limit` you configured.
- **Failures are isolated and resumable.** A page that fails is logged, counted
  and skipped; the rest of the crawl continues. Because a URL is only marked
  scraped after a successful fetch, `dataforge resume` retries exactly the ones
  that failed.
- **Non-HTML resources are filtered** from crawl candidates before a request is
  ever made (PDFs, images, archives).

### Streaming pipeline
By default the stages run strictly one after another, which means nothing is
generated until the last URL has been fetched — on a large site the LLM idles
through the entire crawl. Set `stream: true` (the default in a recipe) and
collection, processing and generation run as concurrent worker pools:

```
urls ─▶ [scrape pool] ─▶ pages ─▶ [chunk pool] ─▶ chunks ─▶ [LLM pool] ─▶ samples
```

- A page that finishes downloading is chunked immediately, and its chunks enter
  generation while the crawler is still working.
- **Bounded queues** apply backpressure, so a fast crawler cannot exhaust memory
  ahead of a slower LLM.
- **Independent pool sizes** — scraping is rate-limit bound, generation is
  cost/token bound. Tune them separately via `DATAFORGE_STREAM_GENERATE_WORKERS`.
- **Failure isolation** — a dead URL, an unparsable page or one bad LLM response
  is logged and skipped; the pools keep running.
- **Graceful degradation** — if generation fails fatally (missing key, provider
  down), scraping and chunking still finish, so nothing is lost and a resume
  only has to generate.
- **Idempotent resume** — resuming replays only what is genuinely unfinished:
  URLs never fetched, pages never chunked, chunks never generated. No page is
  downloaded twice and no LLM call is paid for twice.

Quality scoring and export stay batch stages, because deduplication has to see
the whole sample set.

### URL Discovery
- Automatically finds and parses XML sitemaps (including sitemap indexes)
- Checks `robots.txt` for `Sitemap:` directives
- **BFS crawler fallback** — if no sitemap is found, crawls the site up to a configurable depth and page limit
- **SPA support** — detects JavaScript-rendered pages (few links, rich body) and retries with Playwright if installed
- Parallel discovery across multiple seed URLs
- **Skip already-scraped URLs** — when re-running on the same domain, optionally exclude pages processed in previous sessions (great for incremental crawls)

### Interactive URL Review
After discovery, an interactive checklist lets you curate exactly which URLs proceed to collection — without re-running discovery.

- **Filter** the list before review using:
  - Plain substring: `blog` matches any URL containing "blog"
  - Glob path: `/blog/*` matches `/blog/post-1`, `/blog/post-2`, …
  - Regex: `re:\.html$` matches any URL ending in `.html`
- **Per-URL selection** via a scrollable checkbox list
- **Bulk operations** — select all, deselect all, then fine-tune individually
- **Persist across resume** — your selection is saved to the database; pausing and resuming a session restores the same URL subset
- Works cross-platform (Windows, macOS, Linux) — no curses or platform-specific terminal APIs

#### Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Space` | Toggle URL selection |
| `a` | Select all visible URLs |
| `n` | Deselect all |
| `↑` / `↓` | Navigate the list |
| `Enter` | Confirm selection and proceed |
| `Ctrl-C` | Cancel and return to the filter step |

### Zero-trust input handling
- All user-supplied URLs are sanitised before entering the pipeline
- Strips control characters, URL fragments, and tracking parameters (`utm_*`, `fbclid`, `gclid`, etc.)
- Auto-corrects bare domains (adds `https://`) and percent-encodes unsafe path characters
- Non-HTML resources (images, PDFs, JS, CSS) are filtered from crawl candidates

### Collection
- Async HTTPX client with retry + exponential backoff
- Per-domain rate limiting and `robots.txt` compliance
- Pages saved as Markdown in the session directory

### Processing
- Token-aware chunking with configurable size and overlap
- Boilerplate removal (nav, footer, cookie notices, etc.)
- Output as JSONL and Parquet

### Generation
- Synthetic Q&A, instruction, and conversation samples via LiteLLM
- Supports OpenAI, Anthropic, Groq, Together AI, and local Ollama
- Custom system prompt support

### Quality
- LLM-based quality scoring (1–5)
- Configurable approval threshold

### Export
- HuggingFace Hub (public or private datasets)
- Kaggle datasets
- Local JSONL / Parquet / CSV

### CLI experience
- Ghost-text inline autocomplete with Tab completion (powered by `prompt_toolkit`)
- Typo correction for unknown commands with fuzzy closest-match suggestions
- Contextual rotating tips at each pipeline stage
- `dataforge config` prompts for API keys securely via `getpass` and saves to `.env`
- Startup hint when no provider key is detected, with guidance to run `dataforge config`
- User preferences persisted to `~/.config/dataforge/prefs.json` (cross-project)

---

## Configuration

DataForge reads settings from environment variables or a `.env` file in the working directory.
Run `dataforge config` to set your provider and API key interactively.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | OpenAI key |
| `ANTHROPIC_API_KEY` | — | Anthropic key |
| `GROQ_API_KEY` | — | Groq key |
| `TOGETHER_API_KEY` | — | Together AI key |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint (no key needed) |
| `DATAFORGE_LLM_PROVIDER` | `openai` | Active provider |
| `DATAFORGE_LLM_MODEL` | `gpt-4o-mini` | Model name |
| `DATAFORGE_RATE_LIMIT` | `2.0` | Requests/sec per domain |
| `DATAFORGE_MAX_PAGES` | `500` | Max pages scraped per session |
| `DATAFORGE_MAX_CRAWL_PAGES` | `50` | Max pages found by BFS crawler |
| `DATAFORGE_MAX_CRAWL_DEPTH` | `3` | Max link depth for BFS crawler |
| `DATAFORGE_CHUNK_SIZE` | `512` | Tokens per chunk |
| `DATAFORGE_CHUNK_OVERLAP` | `64` | Token overlap between chunks |
| `DATAFORGE_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `DATAFORGE_OUTPUT_DIR` | `./output` | Session output directory (logs also stored here in `logs/`) |
| `DATAFORGE_DB_PATH` | `./dataforge.db` | SQLite database path |
| `DATAFORGE_AUTOSAVE` | `true` | Checkpoint progress to the session DB after every stage |
| `DATAFORGE_STREAM_PIPELINE` | `false` | Fuse collection+processing+generation into one concurrent stage (a recipe's `stream:` key overrides this) |
| `DATAFORGE_STREAM_GENERATE_WORKERS` | `3` | Concurrent LLM generation workers in streaming mode |
| `DATAFORGE_STREAM_QUEUE_SIZE` | `100` | Items buffered between streaming stages (backpressure) |
| `HUGGINGFACE_TOKEN` | — | HuggingFace Hub write token |
| `KAGGLE_USERNAME` | — | Kaggle username |
| `KAGGLE_KEY` | — | Kaggle API key |

### Recipe reference

Only `name` and one of `source.urls` / `source.url_file` are required. Every
other key falls back to your `.env` and global settings, so a working recipe
can be four lines long.

| Key | Default | Description |
|---|---|---|
| `version` | `1` | Recipe schema version |
| `name` | *required* | Session name |
| `stream` | `true` | Overlap collection/processing/generation |
| `output_dir` | *(global)* | Where exports and logs are written |
| `source.urls` | `[]` | Seed or sitemap URLs |
| `source.url_file` | `""` | Text file of URLs, one per line, `#` for comments — resolved relative to the recipe |
| `source.language` | `""` | Keep only one language. `en` keeps root-level pages and drops locale-prefixed ones (`/es/…`, `/fr/…`); `es` inverts it. Empty = keep all |
| `source.include` | `[]` | Keep only URLs matching any pattern — plain substring, or regex with an `re:` prefix |
| `source.exclude` | `[]` | Drop URLs matching any pattern (same syntax). Applied after `include` |
| `source.max_urls` | `0` | Cap URLs after filtering (`0` = no cap) |
| `source.ignore_robots` | `false` | Disable `robots.txt` enforcement (only with permission) |
| `source.skip_known` | `false` | Skip URLs already scraped in an earlier session |
| `crawl.rate_limit` | *(global)* | Requests/second/domain |
| `crawl.max_pages` | *(global)* | Max pages scraped this session |
| `crawl.max_crawl_pages` | *(global)* | Max pages found by the BFS fallback crawler |
| `crawl.max_crawl_depth` | *(global)* | Max link depth for the BFS crawler |
| `generation.format` | `qa` | `qa` / `instruction` / `conversation` / `custom` |
| `generation.goal` | `""` | Plain-language description of the dataset's purpose |
| `generation.n_per_chunk` | `3` | Samples generated per chunk (1–20) |
| `generation.model` | *(global)* | Override the LLM used for generation |
| `generation.system_prompt` | `""` | Custom system prompt (required when `format: custom`) |
| `generation.chunk_size` | *(global)* | Tokens per chunk |
| `generation.chunk_overlap` | *(global)* | Token overlap between chunks |
| `quality.threshold` | `0.5` | Minimum score (0.0–1.0) for a sample to be approved |
| `quality.model` | *(global)* | Override the LLM used for quality review |
| `export.targets` | `[local]` | Any of `local`, `huggingface`, `kaggle` |
| `export.approved_only` | `true` | Export only samples that passed the threshold |
| `export.hf_repo_id` | `""` | Required when targeting `huggingface` |
| `export.hf_private` | `true` | Create the HF dataset as private |
| `export.kaggle_slug` | `""` | Required when targeting `kaggle` |
| `export.kaggle_title` | `""` | Kaggle dataset title |
| `export.split` | *(none)* | Omit for one dataset file; set to emit train/validation/test |
| `export.split.train` / `.validation` / `.test` | `0.8` / `0.1` / `0.1` | Target shares — must sum to 1.0 |
| `export.split.group_by` | `page` | `page` / `url` / `chunk` / `none` — what must not span splits |
| `export.split.seed` | `42` | Same seed gives the same split |

Unknown keys are rejected with the offending path, so a typo like
`generation.formt` fails immediately rather than being silently ignored.

---

### Using Ollama (fully local, no API key)

```bash
ollama serve
ollama pull llama3.2
dataforge config   # choose ollama / llama3.2
dataforge
```

---

## Upgrading

See [CHANGELOG.md](CHANGELOG.md) for the full list of changes. The one worth
reading before you upgrade:

### ⚠️ Your `.env` settings now actually take effect

Previous versions had a bug where every `DATAFORGE_*` environment variable
(model, output directory, rate limit, chunk size, log level, etc.) was
silently ignored — the app always ran on its hardcoded defaults no matter
what your `.env` file said. That's now fixed.

**If you never noticed a difference, you have nothing to do.** But if you had
a `.env` file with `DATAFORGE_LLM_MODEL`, `DATAFORGE_OUTPUT_DIR`,
`DATAFORGE_RATE_LIMIT`, or similar settings that seemed to have no effect —
they will now actually apply. Double-check your `.env` before your next run,
in particular:

- `DATAFORGE_LLM_MODEL` / `DATAFORGE_LLM_PROVIDER` — if these were left over
  from an old experiment, the pipeline will now genuinely use them instead of
  the built-in default (`openai` / `gpt-4o-mini`).
- `DATAFORGE_OUTPUT_DIR` / `DATAFORGE_DB_PATH` — if set to something other
  than `./output` / `./dataforge.db`, sessions will now be created there
  instead of the defaults you may have gotten used to.

Run `dataforge info` after upgrading to see exactly which provider, model,
and paths are active.

### New in this release

- **`dataforge run <recipe.yaml>`** — run a whole pipeline from a YAML file
  with no prompts. `dataforge init-recipe` writes an annotated starter, and
  `--dry-run` validates it without spending a request.
- **Streaming pipeline** (`stream: true`) — collection, processing and
  generation run concurrently instead of one after another, so generation
  begins on the first page rather than the last.
- `dataforge test-llm` — pick any configured provider/model and ask it a
  random test question, without running the full pipeline.
- A between-stage **"Adjust settings"** menu option to change the generation
  model, quality model, or output directory mid-session.
- `DATAFORGE_AUTOSAVE` (default `true`) to control the after-every-stage
  checkpoint explicitly.

---

## Pipeline stages

**Batch mode** (default for the interactive wizard) — each stage completes
before the next begins, with a review checkpoint between them:

```
Discovery → Collection → Processing → Generation → Quality → Export
```

**Streaming mode** (`stream: true`, the default in a recipe) — the three
middle stages are fused into one concurrent phase:

```
Discovery → ┌─────────── Streaming ───────────┐ → Quality → Export
            │ scrape ⇄ chunk ⇄ generate       │
            └─────────────────────────────────┘
```

Both modes are pausable and resumable. Session state is checkpointed to SQLite,
and every page, chunk and sample is persisted as it is produced — so `Ctrl-C`
mid-run costs you nothing but the item in flight.

Sessions are interchangeable between modes: a run paused in batch mode can be
resumed with streaming enabled and vice versa.

---

## Development

```bash
git clone https://github.com/ianktoo/data-forge.git
cd data-forge
uv sync --extra dev
uv run pytest
uv run ruff check src/ tests/
uv run mypy src/
```

---

## Releasing

```bash
# Bump version
uv version --bump patch   # or minor / major

# Commit, tag, push — CI handles the rest
git add pyproject.toml uv.lock
git commit -m "Bump version to $(uv version --short)"
git tag v$(uv version --short)
git push origin master --tags
```

GitHub Actions will:
1. Build cross-platform executables (Windows, macOS, Linux) via PyInstaller
2. Attach them to a GitHub Release
3. Publish the package to PyPI via `uv publish` using Trusted Publishers

---

## Project structure

```
data-forge/
├── src/dataforge/
│   ├── agents/          # pipeline stage agents + streaming pipeline
│   ├── cli/             # typer app, prompts, UI, recipes, headless runner
│   ├── collectors/      # HTTP client, sitemap parser, BFS crawler, HTML extractor
│   ├── config/          # pydantic-settings, provider registry
│   ├── exporters/       # local, HuggingFace, Kaggle
│   ├── generators/      # LiteLLM wrapper, synthetic sample generation
│   ├── processors/      # chunker, cleaner, formatter
│   ├── storage/         # SQLModel models, database session
│   └── utils/           # logger, rate limiter, URL sanitiser, errors
├── examples/            # ready-to-run recipe files
├── tests/
├── .github/workflows/
│   ├── build-executables.yml
│   └── publish-pypi.yml
├── pyproject.toml
└── uv.lock
```

---

## Ethical Use & Data Residency

DataForge was developed for **educational and research purposes** — specifically to explore how publicly available web content can be transformed into fine-tuning datasets for LLMs.

**Please use this tool responsibly:**

- **Respect `robots.txt` and Terms of Service.** DataForge honours `robots.txt` directives by default. Before scraping any site, verify you have permission to do so under that site's terms.
- **Do not collect personal data.** Avoid targeting pages that contain personally identifiable information (PII), protected health information, or other sensitive data. You are responsible for ensuring your dataset complies with applicable privacy laws (GDPR, CCPA, etc.).
- **Data residency.** When using cloud-hosted LLM providers (OpenAI, Anthropic, Google, Groq, Together AI, etc.), scraped content is transmitted to those providers for generation and scoring. If your source material is subject to data residency requirements, use a **local model via Ollama** so data never leaves your machine.
- **Respect copyright.** Publicly accessible does not mean freely reusable. Ensure your intended use of the collected content is consistent with the source site's copyright and licensing terms.
- **Rate limiting.** The default rate limit is 2 requests/second per domain. Do not lower this value to the point where it disrupts the availability of target sites.

This tool is provided as-is for learning purposes. The author assumes no liability for misuse.

---

## Third-Party Libraries

DataForge is built on the following open-source libraries. We thank their authors and contributors.

### Runtime dependencies

| Library | Purpose | License |
|---|---|---|
| [typer](https://pypi.org/project/typer/) | CLI framework | MIT |
| [rich](https://pypi.org/project/rich/) | Terminal rendering — panels, tables, progress bars | MIT |
| [questionary](https://pypi.org/project/questionary/) | Interactive terminal prompts | MIT |
| [prompt-toolkit](https://pypi.org/project/prompt-toolkit/) | Advanced terminal input with autocomplete | BSD-3-Clause |
| [httpx](https://pypi.org/project/httpx/) | Async HTTP/1.1 and HTTP/2 client | BSD-3-Clause |
| [beautifulsoup4](https://pypi.org/project/beautifulsoup4/) | HTML parsing | MIT |
| [lxml](https://pypi.org/project/lxml/) | XML/HTML parser backend | BSD-3-Clause |
| [litellm](https://pypi.org/project/litellm/) | Unified API for 100+ LLM providers | MIT |
| [sqlmodel](https://pypi.org/project/sqlmodel/) | SQLite ORM built on SQLAlchemy + Pydantic | MIT |
| [pydantic](https://pypi.org/project/pydantic/) | Data validation and settings management | MIT |
| [pydantic-settings](https://pypi.org/project/pydantic-settings/) | Environment variable and .env config loading | MIT |
| [huggingface-hub](https://pypi.org/project/huggingface-hub/) | HuggingFace Hub dataset upload | Apache-2.0 |
| [datasets](https://pypi.org/project/datasets/) | HuggingFace Datasets library | Apache-2.0 |
| [kaggle](https://pypi.org/project/kaggle/) | Kaggle API client for dataset publishing | Apache-2.0 |
| [loguru](https://pypi.org/project/loguru/) | Structured logging with rotation | MIT |
| [python-dotenv](https://pypi.org/project/python-dotenv/) | .env file loader | BSD-3-Clause |
| [tenacity](https://pypi.org/project/tenacity/) | Retry logic with exponential backoff | Apache-2.0 |
| [xmltodict](https://pypi.org/project/xmltodict/) | XML → Python dict parser | MIT |
| [markdownify](https://pypi.org/project/markdownify/) | HTML → Markdown converter | MIT |
| [tiktoken](https://pypi.org/project/tiktoken/) | OpenAI tokeniser for chunk sizing | MIT |
| [psutil](https://pypi.org/project/psutil/) | System metrics — CPU, RAM, disk | BSD-3-Clause |
| [aiofiles](https://pypi.org/project/aiofiles/) | Async file I/O | Apache-2.0 |
| [pyarrow](https://pypi.org/project/pyarrow/) | Apache Arrow / Parquet format support | Apache-2.0 |
| [jinja2](https://pypi.org/project/jinja2/) | Prompt template engine | BSD-3-Clause |
| [pyyaml](https://pypi.org/project/PyYAML/) | Recipe file parsing | MIT |
| [keyring](https://pypi.org/project/keyring/) | OS keychain integration for API key storage | MIT |

### Dev and build dependencies

| Library | Purpose | License |
|---|---|---|
| [pytest](https://pypi.org/project/pytest/) | Test framework | MIT |
| [pytest-asyncio](https://pypi.org/project/pytest-asyncio/) | Async test support | Apache-2.0 |
| [pytest-cov](https://pypi.org/project/pytest-cov/) | Test coverage reporting | MIT |
| [ruff](https://pypi.org/project/ruff/) | Linter and code formatter | MIT |
| [mypy](https://pypi.org/project/mypy/) | Static type checker | MIT |
| [respx](https://pypi.org/project/respx/) | HTTPX request mocking for tests | BSD-3-Clause |
| [pip-audit](https://pypi.org/project/pip-audit/) | Dependency vulnerability scanning | Apache-2.0 |
| [pyinstaller](https://pypi.org/project/pyinstaller/) | Standalone executable packaging | GPL-2.0 with bootloader exception |

---

## License

MIT — see [LICENSE](LICENSE) for details.

If you use DataForge-generated datasets in a publication or project, attribution is appreciated but not required:

```
Ian Too. DataForge (2026). https://github.com/ianktoo/data-forge
```

Or in BibTeX:

```bibtex
@software{dataforge2026,
  author  = {Ian Too},
  title   = {DataForge: LLM Data Pipeline},
  year    = {2026},
  url     = {https://github.com/ianktoo/data-forge},
  license = {MIT}
}
```
