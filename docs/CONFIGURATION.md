# Configuration

DataForge reads settings from environment variables or a `.env` file in the working directory.
Run `dataforge config` to set your provider and API key interactively.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | OpenAI key |
| `ANTHROPIC_API_KEY` | — | Anthropic key |
| `GEMINI_API_KEY` | — | Google AI (Gemini) key |
| `GROQ_API_KEY` | — | Groq key |
| `TOGETHER_API_KEY` | — | Together AI key |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint (no key needed); passed to every Ollama call |
| `DATAFORGE_LOCAL_BASE_URL` | — | Base URL of an OpenAI-compatible local server, including `/v1` (LM Studio `http://localhost:1234/v1`, vLLM `http://localhost:8000/v1`, llama.cpp `http://localhost:8080/v1`). Required for provider `openai_compatible`. |
| `DATAFORGE_LOCAL_API_KEY` | — | Optional key for an OpenAI-compatible server that requires one |
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

## Recipe reference

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
| `generation.max_cost_usd` | *(unlimited)* | Stop dispatching new generation/judge LLM calls once this run's total estimated spend reaches this amount |
| `generation.max_llm_calls` | *(unlimited)* | Stop dispatching new generation/judge LLM calls once this run has made this many calls in total |
| `quality.threshold` | `0.5` | Minimum score (0.0–1.0) for a sample to be approved |
| `quality.model` | *(generation model)* | Override the LLM used by the judge |
| `quality.llm_judge` | `true` | Check each sample against its source chunk with an LLM; fails closed |
| `quality.min_judge_score` | `4` | Judge score (1–5) required to keep a sample |
| `quality.near_dup_threshold` | `0.85` | Token-overlap (Jaccard) similarity above which two samples from the same chunk are rejected as near-duplicate paraphrases |
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

CI-friendly exit codes for `dataforge run`: `0` ok, `2` invalid recipe, `3` no
URLs survived the filters, `4` paused, `5` finished with zero approved samples.

## Upgrading

See [../CHANGELOG.md](../CHANGELOG.md) for the full list of changes.

### `.env` settings take effect (fixed in 2.x)

Versions before the fix ignored every `DATAFORGE_*` environment variable
(model, output directory, rate limit, chunk size, log level, etc.) and always
ran on hardcoded defaults. If you have a `.env` with `DATAFORGE_LLM_MODEL`,
`DATAFORGE_OUTPUT_DIR`, `DATAFORGE_RATE_LIMIT`, or similar left over from an
older install, double-check it — those values now genuinely apply. Run
`dataforge info` after upgrading to see exactly which provider, model, and
paths are active.
