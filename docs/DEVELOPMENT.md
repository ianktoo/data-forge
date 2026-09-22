# Development

## Setup

```bash
git clone https://github.com/ianktoo/data-forge.git
cd data-forge
uv sync --extra dev
uv run pytest
uv run ruff check src/ tests/
uv run mypy src/
```

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

See [ARCHITECTURE.md](ARCHITECTURE.md) for how these pieces fit together at
runtime.

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

Both modes are pausable and resumable. Session state is checkpointed to
SQLite, and every page, chunk and sample is persisted as it is produced — so
`Ctrl-C` mid-run costs you nothing but the item in flight.

Sessions are interchangeable between modes: a run paused in batch mode can be
resumed with streaming enabled and vice versa.

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

See [CICD.md](../CICD.md) for the full CI/CD pipeline and
[CONTRIBUTING.md](../CONTRIBUTING.md) for contribution guidelines.
