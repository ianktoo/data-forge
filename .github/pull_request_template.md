## What and why

<!-- The problem, its cause, and what this changes. Link issues: Closes #123 -->

## Checklist

- [ ] Bug fix: a regression test that fails without the fix
- [ ] CI is green on every OS (lint, tests, package smoke test)
- [ ] Touched packaging, entry points or the build: ran `python scripts/smoke_test.py dataforge --mcp --e2e` against an installed build
- [ ] Behaviour change: docs, `docs/TECHNICAL.tex` and CHANGELOG updated
- [ ] No test depends on file order, path separators or timing
