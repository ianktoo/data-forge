# DataForge — LLM Data Collection Pipeline
## Architecture Plan

---

## Vision

DataForge is a guided, interactive CLI that takes a user from raw website URLs to
upload-ready synthetic fine-tuning datasets. Every stage is pausable, exportable,
and resumable. Quality and correctness take precedence over speed.

---

## Tech Stack

| Layer          | Library                                      |
|----------------|----------------------------------------------|
| Runtime        | Python 3.11+, uv                             |
| CLI framework  | Typer + Rich + Questionary                   |
| HTTP           | HTTPX (async) + Tenacity (retries)           |
| HTML parsing   | BeautifulSoup4 + lxml + Markdownify          |
| LLM            | LiteLLM (OpenAI / Anthropic / Groq / Ollama) |
| Persistence    | SQLModel + SQLite                            |
| Data format    | Pydantic v2 + Datasets (HF)                  |
| Logging        | Loguru                                       |
| Export         | HuggingFace Hub + Kaggle API                 |
| System         | PSUtil                                       |
| Tests          | Pytest + pytest-asyncio + pytest-cov         |
| Linting        | Ruff                                         |

---

## Repository Layout

```
website-explorer/
├── PLAN.md                    ← this file
├── pyproject.toml             ← uv project config
├── .python-version            ← 3.11
├── .env.example               ← API key template
├── run.sh / run.bat / run.ps1 ← one-command start
├── scripts/
│   ├── setup.sh               ← uv env bootstrap (Unix)
│   ├── setup.bat              ← uv env bootstrap (Windows CMD)
│   └── setup.ps1              ← uv env bootstrap (PowerShell)
├── src/
│   └── dataforge/
│       ├── main.py            ← entry point
│       ├── cli/
│       │   ├── app.py         ← Typer app + top-level commands
│       │   ├── ui.py          ← Rich panels, tables, progress
│       │   └── prompts.py     ← Questionary interactive prompts
│       ├── agents/
│       │   ├── base.py        ← BaseAgent ABC + PipelineContext
│       │   ├── orchestrator.py← Pipeline state machine
│       │   ├── explorer.py    ← URL discovery (sitemap / robots)
│       │   ├── scraper.py     ← Rate-limited web collection
│       │   ├── processor.py   ← Clean / chunk / structure
│       │   ├── generator.py   ← LLM synthetic data creation
│       │   ├── quality.py     ← Score / deduplicate / filter
│       │   └── exporter.py    ← Package + upload datasets
│       ├── collectors/
│       │   ├── http.py        ← Async HTTPX client wrapper
│       │   ├── sitemap.py     ← Sitemap XML parser
│       │   └── extractor.py   ← HTML → clean text / markdown
│       ├── processors/
│       │   ├── cleaner.py     ← Boilerplate removal, normalise
│       │   ├── chunker.py     ← Token-aware text splitting
│       │   └── formatter.py   ← Structure into dataset records
│       ├── generators/
│       │   ├── llm.py         ← LiteLLM wrapper + retry logic
│       │   ├── templates.py   ← Prompt templates per format
│       │   └── synthetic.py   ← Orchestrate generation loop
│       ├── exporters/
│       │   ├── local.py       ← JSONL / Parquet / CSV writer
│       │   ├── huggingface.py ← HF Hub dataset push
│       │   └── kaggle_exp.py  ← Kaggle dataset upload
│       ├── config/
│       │   ├── settings.py    ← Pydantic-settings app config
│       │   └── providers.py   ← LLM provider registry
│       ├── storage/
│       │   ├── models.py      ← SQLModel table definitions
│       │   └── database.py    ← Session-scoped DB engine
│       └── utils/
│           ├── logger.py      ← Loguru setup (file + console)
│           ├── rate_limiter.py← Token-bucket rate limiter
│           └── system.py      ← CPU / RAM / disk helpers
├── tests/
│   ├── conftest.py
│   ├── test_collectors.py
│   ├── test_processors.py
│   └── test_generators.py
├── output/                    ← all generated artefacts
│   └── sessions/
│       └── {session_id}/
│           ├── raw/           ← scraped HTML / text files
│           ├── processed/     ← cleaned chunks (JSONL)
│           ├── synthetic/     ← generated samples (JSONL)
│           └── exports/       ← final packaged datasets
└── logs/
    ├── pipeline.log           ← structured JSON log
    └── debug.log              ← verbose debug log
```

---

## Database Schema

```
PipelineSession          ← one row per run
  id TEXT PK
  name TEXT
  goal TEXT
  format TEXT            ← qa | instruction | conversation | custom
  stage TEXT             ← discovery | collection | processing | generation | quality | export
  status TEXT            ← active | paused | completed | failed
  config JSON
  created_at DATETIME
  updated_at DATETIME

DiscoveredURL
  id INTEGER PK
  session_id TEXT FK
  url TEXT
  source TEXT            ← sitemap | manual | file | crawl
  selected BOOLEAN
  scraped BOOLEAN
  http_status INTEGER
  discovered_at DATETIME

ScrapedPage
  id INTEGER PK
  session_id TEXT FK
  url TEXT
  title TEXT
  author TEXT
  published_date TEXT
  raw_path TEXT          ← path to raw HTML file
  word_count INTEGER
  scraped_at DATETIME

ProcessedChunk
  id INTEGER PK
  session_id TEXT FK
  page_id INTEGER FK
  content TEXT
  token_count INTEGER
  chunk_index INTEGER
  metadata JSON
  created_at DATETIME

SyntheticSample
  id INTEGER PK
  session_id TEXT FK
  chunk_id INTEGER FK
  format TEXT
  system_prompt TEXT
  messages JSON          ← [{role, content}, ...]
  quality_score REAL
  approved BOOLEAN
  created_at DATETIME

ExportRecord
  id INTEGER PK
  session_id TEXT FK
  destination TEXT       ← local | huggingface | kaggle
  path_or_url TEXT
  format TEXT
  sample_count INTEGER
  exported_at DATETIME
```

---

## Pipeline State Machine

```
INPUT → DISCOVERY → COLLECTION → PROCESSING → GENERATION → QUALITY → EXPORT
                 ↑                                                      |
                 └──────────── resume from any stage ──────────────────┘

At each stage boundary:
  • User is shown a summary (counts, samples, cost estimate)
  • User can: continue | export_now | pause_and_exit | skip_stage
```

---

## Agent Handover Protocol

Each agent receives a `PipelineContext` (dataclass), executes, updates context fields
it owns, and returns the mutated context. The orchestrator persists context to SQLite
after each agent completes, enabling crash recovery.

```
OrchestratorAgent
  └─ calls ExplorerAgent   → populates context.discovered_urls
  └─ calls ScraperAgent    → populates context.scraped_pages
  └─ calls ProcessorAgent  → populates context.processed_chunks
  └─ calls GeneratorAgent  → populates context.synthetic_samples
  └─ calls QualityAgent    → filters / scores context.synthetic_samples
  └─ calls ExporterAgent   → writes files, records ExportRecord
```

---

## CLI Commands

```bash
dataforge                        # interactive guided pipeline (default)
dataforge pipeline               # alias for interactive mode
dataforge explore <url>          # quick sitemap / URL discovery only
dataforge resume <session_id>    # resume a paused session
dataforge sessions               # list all sessions (table)
dataforge export <session_id>    # export data from any stage
dataforge config                 # interactive provider / prompt setup
dataforge providers              # list / add / remove LLM providers
dataforge info                   # system info + env check
dataforge test-llm               # send a test prompt to configured provider
```

---

## Data Formats

| Format        | Structure                                      | Use case                   |
|---------------|------------------------------------------------|----------------------------|
| qa            | `{question, answer, context}`                  | RAG / Q&A fine-tuning      |
| instruction   | `{instruction, input, output}`                 | Alpaca / instruction tuning|
| conversation  | `{messages: [{role, content}]}`                | Chat fine-tuning (ChatML)  |
| custom        | User-defined Jinja2 template                   | Any format                 |

All formats are written as JSONL and optionally Parquet. The HuggingFace export
produces a `DatasetDict` with `train` / `validation` splits.

---

## Rate Limiting Strategy

- Default: 2 requests/second, configurable per domain
- Respects `Crawl-delay` from `robots.txt`
- Exponential backoff on 429 / 503
- Per-domain token bucket (not global)
- Jitter added to all delays

---

## Security

- API keys stored only in `.env` (never committed)
- `.env` loaded via `python-dotenv`, not shell exports
- No outbound connections except to configured LLM endpoints and export targets
- Scraped content stored locally; no third-party cloud storage
- Output directories are created with restrictive permissions (0o750)

---

## Logging

- Every agent action emits a structured log event (JSON)
- Log level controlled by `DATAFORGE_LOG_LEVEL` env var
- `logs/pipeline.log` — INFO and above, structured JSON
- `logs/debug.log` — DEBUG and above, human-readable
- Each session has its own log context (`session_id` field in every record)

---

## Unsloth Compatibility

Generated JSONL follows the ShareGPT / ChatML format that Unsloth expects:
```json
{"conversations": [{"from": "system", "value": "..."}, {"from": "human", "value": "..."}, {"from": "gpt", "value": "..."}]}
```
A conversion helper (`dataforge export --format unsloth`) rewrites any format
into this schema.

---

## Quick Start (for users)

```bash
# Unix / Mac
./run.sh

# Windows CMD
run.bat

# PowerShell
./run.ps1
```

The setup scripts:
1. Check uv is installed (and installs it if not)
2. Create the virtual environment (`uv sync`)
3. Copy `.env.example` → `.env` if not present
4. Launch `dataforge` interactive mode

---

## Installing via pip

If you installed DataForge globally with `pip install dataforge` and run it from any directory (not the project repo), there is no `.env` file to load from. Set your configuration using environment variables directly.

### Option 1 — export in your shell session

```bash
# Required: set at least one LLM provider key
export OPENAI_API_KEY=sk-...

# Optional: tune pipeline defaults
export DATAFORGE_LLM_PROVIDER=openai
export DATAFORGE_LLM_MODEL=gpt-4o-mini
export DATAFORGE_MAX_PAGES=500
export DATAFORGE_MAX_CRAWL_PAGES=50   # BFS crawler page cap (sitemap fallback)
export DATAFORGE_MAX_CRAWL_DEPTH=3    # BFS crawler max link depth
export DATAFORGE_OUTPUT_DIR=~/dataforge-output
export DATAFORGE_DB_PATH=~/dataforge.db

dataforge
```

On Windows (PowerShell):

```powershell
$env:OPENAI_API_KEY = "sk-..."
$env:DATAFORGE_LLM_PROVIDER = "openai"
$env:DATAFORGE_OUTPUT_DIR = "$HOME\dataforge-output"
dataforge
```

### Option 2 — place a `.env` file in your working directory

DataForge reads `.env` from the directory where you run the command. Create one anywhere:

```bash
cd ~/my-project
cp /path/to/dataforge/.env.example .env   # or create from scratch
# fill in your keys
dataforge
```

### Option 3 — place a `.env` file in your home directory

Alternatively, point the loader at a central file by setting the path explicitly:

```bash
export DATAFORGE_ENV_FILE=~/.config/dataforge/.env
dataforge
```

> **Note:** `DATAFORGE_ENV_FILE` is not currently supported by default — use Option 1 or 2 instead, or add this variable support in `config/settings.py` via `SettingsConfigDict(env_file=...)`.

### Full reference of environment variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | OpenAI key (required for openai provider) |
| `ANTHROPIC_API_KEY` | — | Anthropic key (required for anthropic provider) |
| `GROQ_API_KEY` | — | Groq key |
| `TOGETHER_API_KEY` | — | Together AI key |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama local endpoint |
| `DATAFORGE_LLM_PROVIDER` | `openai` | Active LLM provider |
| `DATAFORGE_LLM_MODEL` | `gpt-4o-mini` | Model name (passed to LiteLLM) |
| `DATAFORGE_LLM_TEMPERATURE` | `0.7` | Generation temperature |
| `DATAFORGE_LLM_MAX_TOKENS` | `2048` | Max tokens per LLM call |
| `DATAFORGE_RATE_LIMIT` | `2.0` | Requests/sec per domain |
| `DATAFORGE_MAX_PAGES` | `500` | Max pages scraped per session |
| `DATAFORGE_MAX_CRAWL_PAGES` | `50` | Max pages found by BFS crawler (sitemap fallback) |
| `DATAFORGE_MAX_CRAWL_DEPTH` | `3` | Max link depth for BFS crawler |
| `DATAFORGE_CHUNK_SIZE` | `512` | Tokens per chunk |
| `DATAFORGE_CHUNK_OVERLAP` | `64` | Token overlap between chunks |
| `DATAFORGE_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `DATAFORGE_OUTPUT_DIR` | `./output` | Base directory for session output |
| `DATAFORGE_DB_PATH` | `./dataforge.db` | SQLite database file path |
| `HUGGINGFACE_TOKEN` | — | HuggingFace Hub write token |
| `KAGGLE_USERNAME` | — | Kaggle username |
| `KAGGLE_KEY` | — | Kaggle API key |
