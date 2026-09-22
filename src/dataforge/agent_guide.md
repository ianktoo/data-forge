# DataForge: guide for AI agents

You are driving DataForge (`dataforge`, PyPI package `llm-web-crawler`) from a shell.
DataForge crawls a website, chunks the text, has an LLM generate fine-tuning
samples (Q&A, instructions or conversations) from each chunk, filters them for
quality, and exports JSONL / Parquet / CSV with a leak-aware train/validation/test split.

Print this guide at any time with `dataforge agent-guide`.

## Prefer MCP when your client supports it

`dataforge mcp` runs a local MCP server over stdio (install with
`pip install "llm-web-crawler[mcp]"`). Nothing is hosted: your client starts the
process. Register it from the project directory, since the database, `.env` and
relative recipe paths resolve against that directory:

```
claude mcp add dataforge -- dataforge mcp
```

Tools: `explore_site`, `validate_recipe`, `start_run` + `run_status` (a run
takes minutes, so it runs in the background), `list_sessions`,
`session_stats`, `view_samples`. `start_run` refuses a recipe with no spending
cap unless you pass `allow_uncapped_spend`, and always refuses
`source.ignore_robots: true`. The rules below apply to MCP use too.

## Rule 1: only use the non-interactive commands

Several commands open interactive menus that will hang a non-TTY shell. Use:

| Command | What it does |
|---|---|
| `dataforge init-recipe <file.yaml>` | Write an annotated starter recipe (`--force` to overwrite) |
| `dataforge explore <url-or-sitemap>` | List the URLs a site exposes, before committing to a crawl |
| `dataforge run <file.yaml> --dry-run` | Validate a recipe and print the plan; spends nothing |
| `dataforge run <file.yaml>` | Run the whole pipeline with no prompts |
| `dataforge resume <session-id>` | Continue a paused/interrupted session (always pass the ID) |
| `dataforge --json sessions` | List sessions as JSON |
| `dataforge --json stats <session-id>` | Approval rate, rejection reasons, score/length stats, split counts |
| `dataforge --json view <session-id> -s <stage> -n 5` | Sample records from a stage: `discovery`, `collection`, `processing`, `generation`, `quality` |
| `dataforge providers` | List supported LLM providers and models |
| `dataforge info` | Show the active provider, model, output dir and database path |

`--json` and `--quiet` are global flags: put them **before** the subcommand
(`dataforge --json stats abc123`, not `dataforge stats abc123 --json`).

Do **not** run these (interactive): `dataforge` with no subcommand, `pipeline`,
`config`, `export`, `test-llm`, and `resume` without a session ID. To change
export targets, edit the recipe's `export:` block instead of calling `export`.

## Rule 2: configure through environment variables, not `dataforge config`

Settings come from environment variables or a `.env` file in the working
directory. Minimum: a provider, a model and that provider's key.

```
DATAFORGE_LLM_PROVIDER=openai          # openai | anthropic | google | groq | together | ollama
DATAFORGE_LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=...                     # or ANTHROPIC_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, TOGETHER_API_KEY
```

Ollama needs no key (`OLLAMA_BASE_URL`, default `http://localhost:11434`).
Never print, log or commit API keys. A recipe's `generation.model` /
`quality.model` override the default model for that run.

## Workflow

1. `dataforge explore https://example.gov/sitemap.xml`: see what exists.
2. `dataforge init-recipe site.yaml`, then edit it. Required: `name` and
   `source.urls`. Scope with `source.include` / `source.exclude` (substring,
   or `re:` prefix for a regex) and cap with `source.max_urls`.
3. Set a spending cap before any real run:
   ```yaml
   generation:
     max_cost_usd: 1.00     # hard cap on combined generation + judge spend
     max_llm_calls: 500     # optional call cap
   ```
4. `dataforge run site.yaml --dry-run`: fix anything it reports.
5. `dataforge run site.yaml`: the output prints `Session ID: <8 chars>`.
6. `dataforge --json stats <session-id>`: report the numbers to the user.

Exit codes from `run`: `0` ok, `2` invalid recipe, `3` no URLs left after
filters, `4` paused (resume it), `5` finished with zero approved samples
(loosen `quality.min_judge_score` or check the source content).

Outputs go to `<output_dir>/sessions/<session-id>/exports/<timestamp>/`:
`dataset.jsonl` (+ `.parquet`, `.csv`) and, when `export.split` is set,
`dataset_train.jsonl`, `dataset_validation.jsonl`, `dataset_test.jsonl`.
Keep `export.split.group_by: page` so samples from one page never land in
more than one split.

## Rule 3: responsible use comes before running anything

Passing robots.txt does not give you permission. Before crawling a site:

- **Ask the user to confirm** they have reviewed the site's terms of service or
  website policies, and that the content is public and not restricted.
- **Never set `source.ignore_robots: true`** unless the user says they have
  written permission from the site owner.
- **Stop if the site blocks automated access.** A `403` on `robots.txt`, a
  CAPTCHA, or a redirect to an "unblock"/"request access" page means the site
  does not want to be crawled. Do not change user agents, add delays, or
  otherwise work around it. If the site offers an official API or bulk
  download, tell the user and use that instead.
- **Keep the default politeness settings.** DataForge obeys `Crawl-delay`; do
  not raise `crawl.rate_limit` to speed up a crawl the site has asked to slow.
- **Do not crawl** logged-in areas, personal data, or pages the user has no
  right to reuse.
- Tell the user that generated samples are synthetic and can be wrong. For
  legal, medical or immigration topics they are educational, not advice.
