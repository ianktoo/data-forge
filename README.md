# DataForge

An interactive CLI pipeline that turns websites into fine-tuning datasets for LLMs.
Discovers URLs, scrapes content, chunks it, generates synthetic Q&A / instruction / conversation
samples, scores them for quality, and exports to HuggingFace Hub, Kaggle, or local files.

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

```bash
dataforge          # interactive guided pipeline
dataforge explore <url>   # preview URL discovery without running the full pipeline
dataforge config   # set your LLM provider and API key
dataforge sessions # list past sessions
dataforge resume <id>     # resume a paused session
dataforge update   # update to the latest version
```

---

## Features

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
| `HUGGINGFACE_TOKEN` | — | HuggingFace Hub write token |
| `KAGGLE_USERNAME` | — | Kaggle username |
| `KAGGLE_KEY` | — | Kaggle API key |

### Using Ollama (fully local, no API key)

```bash
ollama serve
ollama pull llama3.2
dataforge config   # choose ollama / llama3.2
dataforge
```

---

## Pipeline stages

```
Discovery → Collection → Processing → Generation → Quality → Export
```

Each stage is pausable and resumable. The session state is persisted to SQLite after every stage.

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
uv version patch   # or minor / major

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
│   ├── agents/          # pipeline stage agents (explorer, scraper, processor, …)
│   ├── cli/             # typer app, prompts, UI, prefs, tips
│   ├── collectors/      # HTTP client, sitemap parser, BFS crawler, HTML extractor
│   ├── config/          # pydantic-settings, provider registry
│   ├── exporters/       # local, HuggingFace, Kaggle
│   ├── generators/      # LiteLLM wrapper, synthetic sample generation
│   ├── processors/      # chunker, cleaner, formatter
│   ├── storage/         # SQLModel models, database session
│   └── utils/           # logger, rate limiter, URL sanitiser, errors
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
