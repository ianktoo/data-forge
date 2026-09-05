# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed

- **Quality stage crash** (`'dict' object has no attribute 'split'`): a generated
  sample whose LLM response contained a nested object where plain text was
  expected (e.g. `{"question": {...}}`, or a `conversation`-format message with
  non-string content) silently corrupted stored samples and crashed the quality
  stage several steps later. LLM-generated messages are now validated and
  normalized (role checked, content coerced to a string) at the point they're
  generated, not left to explode downstream.
- **`.env` configuration was silently ignored**: `Settings` declared its config
  twice, and the second declaration replaced the first, dropping the
  `DATAFORGE_` environment variable prefix entirely. Every documented setting in
  `.env.example` (`DATAFORGE_LLM_MODEL`, `DATAFORGE_OUTPUT_DIR`,
  `DATAFORGE_RATE_LIMIT`, etc.) was a no-op — the app always used its hardcoded
  defaults regardless of `.env` contents. **This is now fixed and your `.env`
  settings will take effect** — see the upgrade guide in the README if you'd
  been relying on the previous (broken) behavior.
- **Quality-stage errors showed no guidance**: an unrecognized error key fell
  through to a bare `Error: quality` panel with only "check the logs" as
  advice. Added dedicated guidance, and the generic fallback now also prints
  the resolved log file path and its last few lines.
- **LLM connection errors were unreachable from the orchestrator's failure
  path**: the check that should have shown the "cannot connect to LLM
  provider" panel tested for the substring `"llm"` inside the stage name
  (`"discovery"`, `"quality"`, etc.) — a condition that can never be true.
  Now keyed on the actual exception type.
- **Interrupting the stage-action prompt could strand a session**: a
  `Ctrl+C` (or any error) while answering "continue / export / pause /
  adjust" between stages wasn't caught by the orchestrator, leaving the
  session `status=active` in the database with nothing actually running it.
- **`dataforge resume` couldn't find sessions stranded by the bug above**:
  auto-detect only looked for `status=paused` sessions, so a stuck `active`
  session was invisible to it ("session is still active") — you had to know
  to run `dataforge sessions` and pass the exact ID. Auto-detect now surfaces
  stale `active` sessions too, clearly labelled.
- **`dataforge update` crashed for pip-installed users without `uv` on
  PATH**: the `uv tool upgrade` attempt didn't guard against `uv` simply not
  being installed, so it raised an unhandled `FileNotFoundError` instead of
  falling back to the `pip install --upgrade` path.
- Stage names used internally were inconsistent (`"quality"` vs.
  `"PipelineStage.quality"`) depending on whether a pipeline run started
  fresh or was resumed, producing confusing log/error messages.
- A single global database engine was cached with no key, so once created
  for one `db_path`, any later request for a *different* `db_path` silently
  kept using the first one. Now cached per resolved path.

### Added

- `dataforge test-llm`: pick any configured provider/model and ask it a
  random test question — a quick way to sanity-check a model or API key
  without running the full pipeline.
- **Mid-session settings adjustment**: a new "Adjust settings" option in the
  between-stage menu lets you change the generation model, quality model, or
  output directory partway through a run, instead of only at initial setup.
  (Changing the output directory does not move the session database — your
  in-progress session's data stays where it is; only new exports/artifacts
  follow the new directory.)
- `DATAFORGE_AUTOSAVE` setting (default `true`): makes the existing
  after-every-stage checkpoint behavior an explicit, documented, and
  configurable switch.

### Changed

- `LLMClient` accepts an independent `provider_override`, and credential-error
  messages now name the actually-selected provider rather than always the
  globally-configured one.

