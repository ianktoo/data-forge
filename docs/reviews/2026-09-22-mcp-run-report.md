# MCP run report, 2026-09-22

First real pipeline run driven entirely through the `dataforge` MCP server from
Claude Code (DataForge 2.4.0). The server was registered with
`claude mcp add dataforge --scope local -- .venv/Scripts/dataforge.exe mcp`.
All findings below are fixed on branch `fix/mcp-run-issues` (release 2.4.1),
each with a regression test in `tests/test_mcp_run_fixes.py` that fails on the
2.4.0 code.

## The run

| | |
|---|---|
| Recipe | `examples/ready-gov-core-hazards.yaml` (14 Ready.gov pages: floods, severe weather, tornadoes, thunderstorms, wildfires, hurricanes, evacuation, power outages, alerts, plan, kit, water, food, 2026 National Preparedness Month) |
| Tools used | `explore_site`, `validate_recipe`, `start_run`, `run_status` (polled) |
| Session | `daf0b1dc-e1a5-49e8-825a-0b030837f5e0` |
| Result | exit 0; 14 pages, 42 chunks, 126 samples, **125 approved** (1 rejected: refers to the source) |
| Cost | 84 LLM calls, **$0.0167** of a $1.00 cap (judge: 42 calls, $0.0078) |
| Split | by page: train 98, validation 12, test 15 |
| Output | `output/sessions/daf0b1dc-.../exports/20260922_220743/` |

`explore_site`, `validate_recipe`, `start_run` and `run_status` worked. The
spending-cap guard was exercised earlier (`examples/ready-gov.yaml` had no cap
and was given one before use).

Note: this session was written to `./dataforge.db` by the 2.4.0 code. After the
fix, the reporting commands read the `.dataforge` project file's database
(`output/dataforge.db`), so to inspect this particular session use
`DATAFORGE_DB_PATH=./dataforge.db dataforge --json view daf0b1dc -s quality`.
New runs land in the project database and need no override.

## Findings

### 1. `run` and the reporting commands used different databases (major)

**Symptom.** `view_samples(session_id="daf0b1dc")` failed; the CLI behind it
said `Session 'daf0b1dc' not found`.

**Cause.** `dataforge run` never called `_bootstrap()`, so it ignored the
`.dataforge` project file and used `Settings.db_path` (`./dataforge.db`).
`view`, `stats` and `sessions` do call it, and it redirected them to the project
file's database (`output/dataforge.db`). The session existed in one database and
was looked up in the other. Any folder where the interactive wizard has created a
`.dataforge` file is affected, and the MCP server inherits it because its session
tools call the CLI.

**Fix.** `run` now bootstraps like every other command, through a shared
`_apply_project_file()`. An explicitly set `DATAFORGE_DB_PATH` or
`DATAFORGE_OUTPUT_DIR` still wins over the project file, which the benchmark
relies on to give each site its own database. The dry-run plan now prints the
database path, which would have made this visible at once.

### 2. CLI errors were exit code 0 and plain text, even with `--json` (major)

**Symptom.** The MCP client got a bare `Error executing tool view_samples`.

**Cause.** `_resolve_session()` printed a styled `✗ ... not found` line to stdout
and `view` returned normally (exit 0). Under `--json` that stdout is not JSON; the
MCP server's `_json_cli` only raised on a non-zero exit, so `json.loads` failed
inside the tool and the SDK reported the failure without a reason.

**Fix.** Under `--json`, errors are printed as `{"error": "..."}` and `view` exits
1 (as `stats` already did). The error names the database it searched. The MCP
server returns `{"error": <the CLI's own message>}` instead of raising, so an agent
sees why a tool failed.

### 3. `view --json --stage` opened an interactive pager (major, found while fixing 1)

**Symptom.** With the database fixed, `view --json <id> -s quality` printed a
human `· Session ...` line and then crashed (exit 2).

**Cause.** In `--json` mode only the stage summary (no `--stage`) produced JSON.
Every `--stage` path went to `_paged_view`, an interactive n/p pager that waits for
keypresses and fails without a terminal. So `view_samples` could never have
returned records, although the agent guide, the MCP tool description and the paper
all describe it as a non-interactive JSON command.

**Fix.** Under `--json`, each stage prints `{"session_id", "stage", "total",
"rows"}` with the first `--limit` rows and no pager; the human header is skipped.
Sample rows now include `id`, `chunk_id` and `rejection_reason`. Without a
terminal (and without `--json`) the first page is printed instead of paging. An
unknown stage is a JSON error with exit 1.

### 4. `run_status.log_tail` was unreadable for an agent (minor)

**Symptom.** 40 lines of loguru DEBUG output with ANSI colour codes around every
field, despite `--no-color` and `NO_COLOR=1`; the useful lines were buried.

**Cause.** Because `run` skipped `_bootstrap()` (finding 1), logging was never
configured and loguru's default handler (DEBUG, coloured) was used. Separately,
`setup_logging` forced `colorize=True`.

**Fix.** `run` now configures logging (INFO by default). The console handler
colours only a real terminal and never when `NO_COLOR` is set. `run_status` also
strips colour codes and DEBUG lines (so older logs read cleanly), returns a
25-line tail, and adds a `progress` field with the latest
`scraped x/y chunks n samples m` line.

### 5. False "No LLM provider key detected" warning (minor)

**Symptom.** Every CLI command printed the warning while runs used the OpenAI key
successfully.

**Cause.** `cli/preflight.py` checked only `os.getenv("OPENAI_API_KEY")`. A key in
`.env` is loaded by pydantic-settings into `Settings.openai_api_key` without being
exported to the process environment.

**Fix.** The check also reads the settings field for the active provider.

### 6. The dry-run plan did not show the spending cap (minor)

`validate_recipe` and `run --dry-run` printed model, rate limit, judge and
filters, but not `max_cost_usd` / `max_llm_calls`, the one thing an agent must
confirm with the user before `start_run`.

**Fix.** A `Budget:` line (`at most $1.00`, `at most $2.00 and 500 LLM calls`, or
`NO CAP (spend is unbounded)`), plus the `Database:` line from finding 1.

### 7. Claude Code only loads a new MCP server in a new session (documentation)

**Symptom.** After `claude mcp add dataforge ...` the server showed
`✔ Connected` in `claude mcp list`, but its tools were not available in the open
session. Typing `claude --continue` into the chat did nothing, because it is a
terminal command.

**Cause.** Claude Code loads MCP servers when a session starts. This is Claude
Code behaviour, not a DataForge bug, but nothing in our docs said so.

**Fix.** The agent guide (served as the MCP server's instructions and by
`dataforge agent-guide`) and the README now say: after `claude mcp add`, exit with
`/exit` and run `claude --continue` in the terminal to reopen the conversation
with the tools loaded; check with `claude mcp list`; register the full path to the
executable when `dataforge` is not on PATH (for example, a virtual environment).

## Observations (not failures)

- **Judge ceiling again:** 125/126 approved (99%), same model generating and
  judging. Consistent with the benchmark; the human audit remains the real check.
- **Question style:** answers are specific and grounded (hurricane-season dates,
  WEA senders, the 2026 NPM theme "Americans Stand Ready"), but many questions are
  generic ("What are some..."). Worth a generation-prompt pass for more varied,
  scenario-style questions for a crisis-response model.
- **Terms:** Ready.gov has its own terms page (`/terms-and-conditions`) that the
  earlier review missed; recorded in `evals/pipeline/sites.yaml` with the owner's
  academic-use decision.

## Paper impact

`docs/TECHNICAL.tex`, Agent integration section: one paragraph added recording
that the first real MCP-driven run completed but its inspection tools failed
(findings 1 and 3), fixed in 2.4.1, and that like the politeness defects these
were visible only end to end.
