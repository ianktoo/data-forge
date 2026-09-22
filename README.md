# DataForge

**Turn any website into a fine-tuning dataset — in one command.**

[![PyPI](https://img.shields.io/pypi/v/llm-web-crawler)](https://pypi.org/project/llm-web-crawler/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

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

![DataForge pipeline architecture](docs/architecture.svg)

## Why DataForge

| | |
|---|---|
| **Configuration as code** | A YAML recipe captures every decision. Commit it, review it in a PR, re-run it in CI — the same input produces the same dataset. |
| **Unattended by default** | `dataforge run` needs no prompts. Cron it, or drive it interactively with `dataforge` when exploring a new site. |
| **Streaming pipeline** | Generation starts on the first page instead of waiting for the last one, so the LLM and the crawler work at the same time. |
| **Resumable, not restartable** | Every page, chunk and sample is checkpointed to SQLite. Interrupt a 5,000-page crawl and resume exactly where it stopped — nothing is re-fetched or re-billed. |
| **Any model** | OpenAI, Anthropic, Groq, Together, or fully local via Ollama — so sensitive content never has to leave your machine. |
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

Prefer to explore a site interactively first? `dataforge` launches the
guided wizard with a per-stage review. Full install options (uv, pip,
standalone binaries, local models via Ollama), every CLI command, all
environment variables and the complete recipe schema are in the docs:

- **[Installation](docs/INSTALLATION.md)** — uv / pip / source / standalone binaries / Ollama
- **[Configuration](docs/CONFIGURATION.md)** — environment variables and full recipe reference
- **[Architecture](docs/ARCHITECTURE.md)** — how discovery, streaming, quality and export fit together
- **[Development](docs/DEVELOPMENT.md)** — project layout, testing, releasing
- **[Ethics & data residency](docs/ETHICS.md)** — responsible use, PII, copyright, rate limits
- **[Third-party libraries](docs/THIRD_PARTY.md)** — full dependency and license list
- **[Technical writeup](docs/TECHNICAL.tex)** — design rationale and references

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
Ian Too. DataForge (2026). https://github.com/ianktoo/data-forge
```

```bibtex
@software{dataforge2026,
  author  = {Ian Too},
  title   = {DataForge: LLM Data Pipeline},
  year    = {2026},
  url     = {https://github.com/ianktoo/data-forge},
  license = {MIT}
}
```
