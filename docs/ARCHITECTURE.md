# Architecture

A run is six stages driven by one recipe (YAML) or an interactive wizard.
Discovery runs first; Collection, Processing and Generation then form a
producer/consumer chain that runs either strictly sequentially (**batch
mode**) or as concurrent worker pools (**streaming mode**, `stream: true`,
the recipe default). Quality and Export always run
as batch stages because deduplication and splitting need to see the whole
sample set at once.

| Stage | Consumes | Produces | Execution | Key mechanism |
|---|---|---|---|---|
| 1. Discovery | Seed URLs | Candidate URLs | Batch | Sitemap + `robots.txt` parsing, bounded best-first crawl fallback, Playwright retry for JS-rendered pages |
| 2. Collection | URLs | Pages (Markdown) | Batch or stream | Per-domain rate limit, transient vs. permanent status handling, `Crawl-delay` |
| 3. Processing | Pages | Token-bounded chunks | Batch or stream | Boilerplate stripping, overlapping `tiktoken`-sized chunks |
| 4. Generation | Chunks | Candidate samples | Batch or stream | LiteLLM provider portability, `n_per_chunk` samples, shared cost/call budget |
| 5. Quality | All samples | Approved samples | Batch | Heuristic + source-reference filters, exact/near-dup removal, fail-closed LLM judge |
| 6. Export | Approved samples | JSONL / Parquet / CSV | Batch | Per-record lineage, group-aware train/validation/test split |

```
Discovery → Collection → Processing → Generation → Quality → Export
```

```
Discovery → ┌─────────── Streaming ───────────┐ → Quality → Export
            │ scrape ⇄ chunk ⇄ generate       │
            └─────────────────────────────────┘
```

Both modes are pausable and resumable, and sessions are interchangeable
between them: a run paused in batch mode can be resumed with streaming
enabled and vice versa.

## Stages

### 1. Discovery
Finds candidate URLs before a single page is fetched:
- Parses `sitemap.xml` (including sitemap indexes) and every `Sitemap:`
  line in `robots.txt`, merged and deduplicated.
- Falls back to a crawl from the seed when no usable sitemap exists (see
  below).
- Detects JavaScript-rendered pages (few links, rich body) and retries with
  Playwright if installed.
- Runs in parallel across multiple seed URLs.
- Deduplicates by a canonical URL key: `/a` and `/a/`, `www.` and the bare
  host, `http` and `https`, default ports, query parameter order and
  tracking parameters count as one page. The URL is kept as the site wrote
  it; only the comparison is normalised.
- Interactive mode adds a checklist review step (substring / glob / regex
  filter, per-URL toggle, persisted across resume); recipes replace this
  with `source.include` / `source.exclude` / `source.max_urls`.

#### The fallback crawl

Used when a site has no usable sitemap, or when the seed is deeper than the
root and the sitemap lists nothing under it (`collectors/crawler.py`).

- **Bounded:** `crawl.max_crawl_depth` (default 3; the seed is depth 0) and
  `crawl.max_crawl_pages` (default 50) limit what is kept, and a hard cap on
  total requests (four times the page budget) limits what is fetched. Same
  domain only; `robots.txt`, rate limits and `Crawl-delay` as everywhere.
- **Follows every link on a page**, navigation, header and footer included.
  Content extraction still ignores navigation, so it never reaches the
  dataset.
- **Best-first frontier:** a binary heap keyed by
  `(depth, is_trap, filtered_out, not_in_content, path_segments, order)`.
  - Depth comes first, so coverage stays level by level and
    `max_crawl_depth` means what it says.
  - Within a depth:
    1. real pages before likely crawl traps (pagination, date archives,
       calendars, search, sort and filter URLs, login, print, feeds); traps
       are demoted, never forbidden
    2. pages the recipe wants before hubs it does not
    3. main-content links before navigation
    4. shallower paths first
  - The final counter keeps ties in page order, so a crawl is deterministic.
- **Uses the recipe's filters while crawling.** `source.include`,
  `source.exclude` and `source.language` decide which pages count toward the
  page budget. A page that fails them is still visited when it can lead
  further (a hub such as the home page often links to what you want), but is
  not kept. A failing page at the depth limit is never fetched.
- **Each page is queued once:** URLs are marked as seen when queued, so the
  queue grows with the number of pages, not the number of links.
- **Each page is downloaded once:** the crawl keeps the HTML of the pages it
  keeps, and the collection stage uses it instead of fetching the page again.

| Structure | Used for | Cost |
|---|---|---|
| Binary heap (`heapq`) | Crawl frontier | O(log n) per push/pop |
| Hash set of canonical keys | "Seen this page?" in discovery and the crawl | O(1) |
| Dict, canonical key to HTML | Crawl downloads reused by collection | O(1); at most `max_crawl_pages` entries |
| SQLite index `(session_id, url)` | Per-page lookup while scraping | O(log n), was a scan of the session |
| SQLite index `url` | `skip_known` across sessions | O(log n) per URL, batched |
| Per-domain token bucket | Rate limiting and `Crawl-delay` | O(1) |

### 2. Collection (scrape pool)
- Async HTTPX client with retry + exponential backoff.
- **Transient statuses retry** — `429`, `500`, `502`, `503`, `504`, up to
  three times, because the server is saying "later," not "no."
- **`Retry-After` is honoured** (delta-seconds or HTTP-date), capped at
  120s so one URL cannot stall a run.
- **Permanent statuses do not retry** — a `404` or `403` is taken at its
  word.
- **`robots.txt` `Crawl-delay` is enforced** and only ever slows a crawl
  down; a permissive `Crawl-delay` never overrides a stricter configured
  `rate_limit`.
- Failures are logged, counted, and isolated — the rest of the crawl
  continues, and because a URL is only marked scraped after a successful
  fetch, `dataforge resume` retries exactly the ones that failed.
- Non-HTML resources (PDFs, images, archives) are filtered before a request
  is made.
- Pages are saved as Markdown in the session directory.

### 3. Processing (chunk pool)
- Boilerplate removal (nav, footer, cookie notices, etc.).
- Token-aware chunking with configurable size and overlap (`tiktoken`).

### 4. Generation (generate pool)
- Synthetic Q&A, instruction, and conversation samples via LiteLLM.
- Supports OpenAI, Anthropic, Groq, Together AI, and local Ollama.
- Custom system prompt support (`generation.format: custom`).

### 5. Quality (batch)
Layered filters, cheapest first:
1. **Heuristic pre-filter** — answer/question length and refusal detection
   against `quality.threshold`. Free, but on its own passes almost any real
   answer.
2. **Source-reference filter** — rejects samples that talk about "the
   document" or "the passage" instead of the subject. Free, always on.
3. **Deduplication** — exact full-content match across the whole session,
   plus a near-duplicate check (token-overlap/Jaccard similarity,
   `quality.near_dup_threshold`) scoped to samples from the same chunk,
   since `n_per_chunk` paraphrases are exactly what an exact-hash check
   cannot catch.
4. **LLM judge** (`quality.llm_judge`, on by default in recipes) — reads
   each sample next to the chunk it came from and rejects anything wrong,
   unsupported, or scoring below `min_judge_score` (1–5). Roughly doubles
   LLM cost.
5. **Fails closed** — a sample the judge cannot score is rejected, never
   approved.

The stage logs a rejection breakdown. Rejections by the free filters are
labelled plainly (`exact duplicate`, `near-duplicate`, `refers to the source`,
`below heuristic threshold`); rejections by the LLM judge are prefixed
`judge:` (`judge: not grounded in source`, `judge: not standalone (refers to
the source)`, `judge: score 3 < 4`). The judge's agreement with human
judgement has not been measured yet (see `evals/pipeline/audit.py`); LLM
judges are generally less consistent near a threshold, so set
`min_judge_score: 5` to trade recall for precision.

### 6. Export (batch)
- JSONL / Parquet / CSV locally, or HuggingFace Hub / Kaggle.
- **Leak-free train/validation/test splits.** A synthetic dataset built from
  scraped pages is not independently distributed — several samples come
  from one chunk, and several chunks from one page, so every sample tracing
  back to a page is a paraphrase of the same source text. Splitting at the
  sample level lets near-duplicates land on both sides and inflates eval
  scores. `export.split.group_by: page` (default) assigns whole pages to
  one split and verifies that property before writing, rather than
  assuming it.
- Every export carries `page_id`, `chunk_id`, `chunk_index`, and
  `source_url` alongside the messages, whether or not you split; that
  lineage is what makes a correct split possible later. The Unsloth file
  (`*_unsloth.jsonl`, ShareGPT shape) has no room for it, so it gets a
  sidecar, `*_unsloth.meta.jsonl`, where line *i* describes row *i*.
- Rows are shuffled within each split (seeded from `export.split.seed`), so a
  split file does not run page by page. Pages are still assigned largest
  first, so which pages are held out depends on page size, not chance.

## Streaming design

By default the four collection→generation stages run strictly one after
another, so the LLM idles through the entire crawl. `stream: true` runs
them as concurrent worker pools instead:

```
urls ─▶ [scrape pool] ─▶ pages ─▶ [chunk pool] ─▶ chunks ─▶ [LLM pool] ─▶ samples
```

- A page that finishes downloading is chunked immediately, and its chunks
  enter generation while the crawler is still working.
- **Bounded queues** apply backpressure so a fast crawler cannot exhaust
  memory ahead of a slower LLM (`DATAFORGE_STREAM_QUEUE_SIZE`).
- **Independent pool sizes** — scraping is rate-limit bound, generation is
  cost/token bound. Tune generation concurrency via
  `DATAFORGE_STREAM_GENERATE_WORKERS`.
- **Failure isolation** — a dead URL, an unparsable page, or one bad LLM
  response is logged and skipped; the pools keep running.
- **Graceful degradation** — if generation fails fatally (missing key,
  provider down), scraping and chunking still finish, so nothing is lost
  and a resume only has to generate.
- **Idempotent resume** — resuming replays only what is genuinely
  unfinished: URLs never fetched, pages never chunked, chunks never
  generated. No page is downloaded twice and no LLM call is paid for
  twice.

## Persistence

Every page, chunk, and sample is checkpointed to SQLite (`storage/`,
SQLModel) as it is produced, with autosave after every stage
(`DATAFORGE_AUTOSAVE`). `Ctrl-C` mid-run costs only the item in flight.
`dataforge resume <id>` continues an interrupted session in either mode,
`dataforge view <id> --stage generation` inspects what was produced,
`dataforge export <id>` re-exports without re-running collection or
generation, and `dataforge stats <id>` reports dataset-level statistics —
approval rate, rejection-reason breakdown, quality-score distribution,
question/answer length, and realized train/validation/test proportions from
the most recent export — read entirely from data the pipeline already
computed, including for a past session.

## Module layout

```
src/dataforge/
├── agents/          # pipeline stage agents + streaming pipeline
├── cli/             # typer app, prompts, UI, recipes, headless runner
├── collectors/      # HTTP client, sitemap parser, best-first crawler, HTML extractor
├── config/          # pydantic-settings, provider registry
├── exporters/       # local, HuggingFace, Kaggle
├── generators/      # LiteLLM wrapper, synthetic sample generation
├── processors/      # chunker, cleaner, formatter
├── storage/         # SQLModel models, database session
└── utils/           # logger, rate limiter, URL sanitiser, errors
```

See [../docs/TECHNICAL.tex](TECHNICAL.tex) for the design rationale behind
these choices (leak-free splitting, streaming vs. batch, layered quality
filtering) with references to related work.
