# Security Policy

## Supported versions

DataForge is developed on a single release line. Security fixes land in the
latest release on PyPI; older versions are not patched.

| Version | Supported |
|---|---|
| 2.7.x | Yes |
| < 2.7 | No |

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately through GitHub's
[Report a vulnerability](https://github.com/ianktoo/data-forge/security/advisories/new)
form, which opens a draft advisory visible only to the maintainers.

Include what you need to make the problem reproducible: the version, the
platform and Python version, the recipe or command involved, and what an
attacker gets out of it.

You can expect an acknowledgement within a week. If the report is confirmed, a
fix ships in the next release and the advisory is published with credit to the
reporter unless you ask otherwise.

## Scope

DataForge crawls sites you point it at, sends page content to an LLM provider
you configure, and writes datasets to disk. The areas most worth attention:

- **Credential handling.** Provider keys are read from the environment or a
  local `.env` and are never written into a dataset, a recipe or a log.
- **Content from crawled pages** reaches LLM prompts, so prompt injection from
  a crawled page is in scope where it can affect the host rather than only the
  generated samples.
- **The MCP server** (`dataforge mcp`) exposes tools to an AI client over
  stdio. Its spending cap and robots.txt refusals are safety boundaries, and a
  way to bypass either is in scope.
- **Recipe parsing**, which is YAML supplied by the user and loaded safely.

Out of scope: the quality of generated samples, cost overruns from a cap you
raised yourself, and the behavior of third-party LLM providers.

## What DataForge does by design

These are documented behavior, not vulnerabilities:

- `dataforge run` sends crawled page content to the LLM provider you configure.
  Choose a local model through Ollama or an OpenAI-compatible server if the
  content must not leave your machine.
- The crawler honors robots.txt. `source.ignore_robots` exists for sites you
  own; the MCP server refuses it outright.
