# DataForge

**Turn any website into a fine-tuning dataset — in one command.**

[![PyPI](https://img.shields.io/pypi/v/llm-web-crawler)](https://pypi.org/project/llm-web-crawler/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Current release: 2.4.2.** AI agents can drive DataForge through a local MCP
server or the built-in `dataforge agent-guide`, robots.txt `Crawl-delay` is
enforced in every stage, local models run through Ollama or any
OpenAI-compatible server (LM Studio, vLLM, llama.cpp), and split files are
shuffled with source lineage kept in every export format. See the
[changelog](CHANGELOG.md#242---2026-09-22).

Fine-tuning needs data, and good domain data is trapped in documentation
sites, knowledge bases and public archives. Getting it out usually means
writing a throwaway scraper, a chunker, a prompt loop and an exporter — then
doing it all again for the next domain.

DataForge is that pipeline, already built, tuned, and checkpointed end to
end:

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

```
Discovery → Collection → Processing → Generation → Quality → Export
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for what each stage does.

## What you'll need

Before you run anything, here's the honest list of what's required vs.
optional:

| | Required? | Notes |
|---|---|---|
| **Python 3.11+** | ✅ Required | Or skip it entirely with a [standalone binary](docs/INSTALLATION.md#standalone-executables-no-python-required) — no interpreter needed. |
| **An LLM to generate and score samples** | ✅ Required (one of the below) | This is what actually writes the Q&A pairs. |
| — A hosted provider API key (OpenAI, Anthropic, Google, Groq, or Together) | One of these, or the local option below | `dataforge config` walks you through it; stored in `.env`. Content leaves your machine for generation. |
| — A local model, no key | *or* fully local | **Ollama:** `ollama serve && ollama pull llama3.2`, then `dataforge config` → `ollama`. **Any OpenAI-compatible server** (LM Studio, vLLM, llama.cpp, Lemonade): set `DATAFORGE_LLM_PROVIDER=openai_compatible`, `DATAFORGE_LOCAL_BASE_URL=http://localhost:1234/v1` and `DATAFORGE_LLM_MODEL` to a model ID the server lists. Nothing leaves your machine. |
| **A HuggingFace account** | ❌ Optional | Only if you set `export.targets: [huggingface]` to publish the finished dataset to the Hub. Needs `HUGGINGFACE_TOKEN`. |
| **A Kaggle account** | ❌ Optional | Only if you set `export.targets: [kaggle]`. Needs `KAGGLE_USERNAME` / `KAGGLE_KEY`. |
| **Nothing else** | — | Local JSONL/Parquet/CSV export (the default) needs no account at all — the whole pipeline runs against just an LLM provider. |

In short: **one LLM (hosted key, or a local model through Ollama or any
OpenAI-compatible server) is the
only hard requirement.** Everything else — which provider, which export
target, whether you need a HuggingFace or Kaggle account — is a choice you
make in the recipe, not a prerequisite to get started. Full setup for each
option is in [docs/INSTALLATION.md](docs/INSTALLATION.md) and
[docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Why DataForge

| | |
|---|---|
| **Configuration as code** | A YAML recipe captures every decision. Commit it, review it in a PR, re-run it in CI — the same input produces the same dataset. |
| **Unattended by default** | `dataforge run` needs no prompts. Cron it, or drive it interactively with `dataforge` when exploring a new site. |
| **Streaming pipeline** | Generation starts on the first page instead of waiting for the last one, so the LLM and the crawler work at the same time. |
| **Resumable, not restartable** | Every page, chunk and sample is checkpointed to SQLite. Interrupt a 5,000-page crawl and resume exactly where it stopped — nothing is re-fetched or re-billed. |
| **Any model** | OpenAI, Anthropic, Google, Groq, Together, or fully local via Ollama or any OpenAI-compatible server (LM Studio, vLLM, llama.cpp), so sensitive content never has to leave your machine. |
| **Leak-free by design** | Train/validation/test splits are page-aware, not sample-aware, so paraphrases of the same source never land on both sides of a split. |
| **Polite by construction** | `robots.txt` honoured, per-domain rate limiting, URL sanitisation, and PII/copyright guidance built in. |

## A worked example

Building a U.S. disaster-preparedness dataset from Ready.gov. The full
recipe ships with the project at
[`examples/ready-gov.yaml`](examples/ready-gov.yaml):

```yaml
version: 1
name: ready-gov-preparedness
stream: true

source:
  urls:
    - https://www.ready.gov/sitemap.xml
  language: en
  exclude: ['/node/', '/press-release', 're:toolkit']
  max_urls: 150

generation:
  format: qa
  goal: >
    Accurate Q&A about U.S. disaster preparedness, grounded strictly in
    official Ready.gov and FEMA guidance.
  n_per_chunk: 3

quality:
  threshold: 0.5

export:
  targets: [local]
  split:
    train: 0.8
    validation: 0.1
    test: 0.1
    group_by: page
```

```bash
dataforge run examples/ready-gov.yaml --dry-run   # validate + preview the plan
dataforge run examples/ready-gov.yaml             # execute
```

One sitemap becomes a few hundred curated pages, a few thousand chunks, and
a scored, deduplicated, leak-free Q&A dataset — without answering a single
prompt.

## Get started

```bash
pip install llm-web-crawler
dataforge init-recipe my.yaml       # write an annotated starter recipe
dataforge run my.yaml --dry-run     # validate and print the plan, run nothing
dataforge run my.yaml               # execute end-to-end, no prompts
```

To update or remove DataForge, run `dataforge update` or `dataforge uninstall`
yourself, in your own terminal. Both close DataForge first, so nothing is
replaced while in use; `uninstall` asks whether to keep your data. Read the
[release notes](https://github.com/ianktoo/data-forge/releases) before
updating: a new version can change recipes, outputs or commands. See
[Updating and uninstalling](docs/INSTALLATION.md#updating-and-uninstalling-from-dataforge).

Prefer to explore a site interactively first? `dataforge` launches the
guided wizard with a per-stage review. Full install options (uv, pip,
standalone binaries, local models via Ollama), every CLI command, all
environment variables and the complete recipe schema are in the docs:

- **[Installation](docs/INSTALLATION.md)** — uv / pip / source / standalone binaries / Ollama, plus [known issues and workarounds](docs/INSTALLATION.md#known-issues-and-workarounds)
- **[Configuration](docs/CONFIGURATION.md)** — environment variables and full recipe reference
- **[Architecture](docs/ARCHITECTURE.md)** — how discovery, streaming, quality and export fit together
- **[Development](docs/DEVELOPMENT.md)** — project layout, testing, releasing
- **[Ethics & data residency](docs/ETHICS.md)** — responsible use, PII, copyright, rate limits
- **[Third-party libraries](docs/THIRD_PARTY.md)** — full dependency and license list
- **[Technical writeup](docs/TECHNICAL.tex)** — design rationale and references

## Using DataForge from an AI agent

Claude Code, Codex, Cursor and other coding agents can drive DataForge through
its non-interactive commands. Point the agent at the built-in guide:

```bash
dataforge agent-guide
```

It lists which commands are safe to run without a terminal, how to configure a
provider through environment variables, the recipe workflow and exit codes, and
the responsible-use rules an agent must follow before crawling a site.

For clients that support the [Model Context Protocol](https://modelcontextprotocol.io),
DataForge also runs as a local MCP server. Nothing is hosted; the client starts
it on your machine:

```bash
pip install "llm-web-crawler[mcp]"
claude mcp add dataforge -- dataforge mcp   # run from your project directory
```

Claude Code picks up a newly added server only in a new session: exit with
`/exit`, then run `claude --continue` in your terminal to reopen the same
conversation with the DataForge tools loaded.

The agent then gets typed tools (explore a site, validate a recipe, start and
monitor a run, read session stats and samples) instead of parsing terminal
output. Runs started this way must have a spending cap, and a recipe that
disables `robots.txt` is refused.

Agents may never update, upgrade or uninstall DataForge. `dataforge update` and
`dataforge uninstall` refuse to run without a terminal or inside an agent
session, and the agent guide tells agents to leave it to you, after you have
read the release notes.

## What makes it different

Most scrapers stop at "here's the text." DataForge is built for the part
that actually determines whether a fine-tune works:

- **It knows a scraped dataset isn't i.i.d.** Several Q&A pairs come from
  one chunk, several chunks from one page — so a naive random split leaks
  paraphrases across train/test and quietly inflates your eval score.
  DataForge splits by page and verifies the property before writing.
- **It treats web failure modes as data, not noise.** `429` isn't `404`,
  `Retry-After` isn't ignored, and `robots.txt` `Crawl-delay` is honoured
  automatically — so a run against a real, imperfect site finishes instead
  of half-failing silently.
- **It doesn't make you choose between fast and resumable.** Streaming mode
  overlaps crawling and generation for throughput; every stage still
  checkpoints to SQLite, so a killed process loses only the item in flight.
- **The quality bar is layered, not a single score.** Length heuristics,
  a source-reference filter, deduplication, and an LLM judge that fails
  closed — with a rejection breakdown so you can see what's actually being
  filtered out.

## License

MIT — see [LICENSE](LICENSE) for details.

If you use DataForge-generated datasets in a publication or project,
attribution is appreciated but not required:

```
Too, I. K. (2026). DataForge: A Streaming, Leak-Aware Pipeline for Synthetic
LLM Fine-Tuning Datasets (Technical Note). Zenodo.
https://doi.org/10.5281/zenodo.22906071
```

```bibtex
@techreport{too2026dataforge,
  author      = {Too, Ian K.},
  title       = {{DataForge}: A Streaming, Leak-Aware Pipeline for Synthetic
                 {LLM} Fine-Tuning Datasets},
  type        = {Technical Note},
  institution = {Zenodo},
  year        = {2026},
  doi         = {10.5281/zenodo.22906071},
  url         = {https://doi.org/10.5281/zenodo.22906071}
}
```

GitHub's "Cite this repository" button reads the same details from
[`CITATION.cff`](CITATION.cff).

That DOI covers every version of the technical note and always resolves to
the latest. To cite one exact version, use its own DOI: v1.1.0 is
[10.5281/zenodo.22908858](https://doi.org/10.5281/zenodo.22908858) and v1.0.0
is [10.5281/zenodo.22906072](https://doi.org/10.5281/zenodo.22906072).
