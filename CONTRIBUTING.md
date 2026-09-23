# Contributing to LLM Web Crawler

## Development Setup

### Clone and install
```bash
git clone https://github.com/ianktoo/data-forge.git
cd data-forge
pip install -e ".[dev]"
```

### Running locally
```bash
# With editable install, the CLI is available:
dataforge

# Or run directly:
python -m dataforge
```

## Testing

```bash
# Run all tests (CI deselects live-network tests the same way)
pytest -m "not integration"

# Run with coverage
pytest --cov=src/dataforge

# Run specific test
pytest tests/test_file.py::test_function
```

## Keeping one fix from breaking another

Every change has to leave the rest of DataForge working. The rules:

1. **Every bug fix comes with a regression test** that fails without the fix
   and passes with it, next to the tests for that component.
2. **CI must be green before merging.** `.github/workflows/test.yml` runs on
   every pull request: ruff, the full test suite on Linux, Windows and macOS
   with Python 3.11 (the only supported version), and the built package installed and
   smoke-tested. A failure on a platform you did not touch is still your
   failure: behaviour can differ by OS (file ordering, paths, file locks).
3. **Releases are gated on the same suite.** Tagging `vX.Y.Z` runs it again
   before anything is published to PyPI or attached to a GitHub Release, and
   each standalone binary is smoke-tested before upload. After a PyPI publish,
   `release-smoke.yml` installs the release with pip and uv on every OS and
   tests it again.
4. **Smoke-test the installed command, not just the source.** Before a
   release, or after touching packaging, entry points or the build:
   ```bash
   python scripts/smoke_test.py dataforge --mcp --e2e
   ```
   It drives `dataforge` as a black box (everyday commands, MCP, and two
   offline pipeline runs against a local site and a fake LLM) and spends
   nothing.
5. **Tests must not depend on order or platform.** Do not rely on glob or
   directory listing order, on `/` vs `\`, or on timing. The Linux and
   macOS runners caught a test that passed on Windows only because of file
   ordering.
6. **When behaviour changes, update the docs and `docs/TECHNICAL.tex`** in the
   same pull request, and add a CHANGELOG entry.

## Code Quality

```bash
# Linting (CI runs exactly this)
ruff check src tests scripts

# Auto-fix
ruff check --fix src/ tests/

# Type checking
mypy src/
```

## Commit Guidelines

- Use clear, descriptive commit messages
- Reference issues when relevant: `Fixes #123`
- Keep commits focused on a single change

## Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes and commit them
4. Push to your fork: `git push origin feature/your-feature`
5. Open a Pull Request with a description of your changes

All PRs must:
- Have passing tests
- Pass linting checks
- Have meaningful commit messages

## Releasing

See [README.md](README.md#releasing-new-versions) for the release process.

Releases are automated via GitHub Actions:
- Executables are built for all platforms
- Package is published to PyPI
- GitHub Release is created with downloads
