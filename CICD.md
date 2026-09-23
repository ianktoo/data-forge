# CI/CD Documentation

This project uses GitHub Actions for testing, building and publishing.

## Workflows

### 0. Tests (`test.yml`)

Runs on every push to `master`, every pull request, and on demand.

- **lint**: `ruff check src tests scripts`
- **test**: `pytest -m "not integration"` on ubuntu, windows and macos, with
  Python 3.11 (the only supported version)
- **package**: builds the wheel, installs it with pip into a clean venv, and
  runs `scripts/smoke_test.py --mcp --e2e` against the installed `dataforge`

`scripts/smoke_test.py` treats `dataforge` as a black box and spends nothing:
everyday commands, pure-JSON `--json` output, `update`/`uninstall` refusing a
non-interactive caller, the MCP handshake, and two offline pipeline runs (a
local site with a sitemap and one crawled by links, plus a fake
OpenAI-compatible LLM server). Run it yourself against any install:

```bash
python scripts/smoke_test.py dataforge --mcp --e2e
```

### Release smoke test (`release-smoke.yml`)

After each PyPI publish (and on demand, with an optional version), installs
the released package with pip and with `uv tool` on every OS with Python
3.11, and runs the smoke test against it.

### 1. Build Executables (`build-executables.yml`)

Triggered when a version tag is pushed (e.g., `git tag v0.1.3`). Also runs on
pull requests that change the build or the smoke test, and on demand; those
runs build and smoke-test the binaries but do not create a release.

**Steps:**
1. Checks out the code
2. Installs Python 3.11
3. Installs the package and PyInstaller
4. Builds standalone executables using PyInstaller, bundling the data files
   and plugins PyInstaller cannot find by itself (`agent_guide.md`, litellm's
   model price table, tiktoken's encoding plugin)
5. Smoke-tests each binary (`scripts/smoke_test.py --mcp --e2e`); a failing
   binary is never uploaded
6. Uploads artifacts to GitHub

**Outputs:**
- `dataforge-windows-x64.exe` (Windows)
- `dataforge-macos-arm64` (macOS, Apple Silicon: `macos-latest` runners are arm64)
- `dataforge-linux-x64` (Linux)

Each job copies PyInstaller's `dist/dataforge[.exe]` to its platform name
before uploading. The release step uploads files by their own name, so without
that copy the Linux and macOS builds (both `dataforge`) overwrote each other
(#51).

These are attached to the GitHub Release.

### 2. Publish to PyPI (`publish-pypi.yml`)

Triggered when a version tag is pushed.

**Steps:**
1. Checks out the code
2. Installs Python 3.11
3. Builds the distribution package (wheel)
4. Publishes to PyPI using OIDC (no API tokens)

**Security:**
- Uses OIDC trusted publisher authentication
- No secrets stored in the repository
- Only runs in the `pypi` GitHub environment

**Configuration:**
- PyPI Project: `llm-web-crawler`
- Trusted publisher set up on PyPI side
- Environment: `pypi` (restricts who can publish)

## Setup (One-time)

The workflows are pre-configured, but OIDC required initial setup:

1. **PyPI Trusted Publisher** (already done)
   - Go to https://pypi.org/manage/project/llm-web-crawler/publishing/
   - Trusted publisher is configured with:
     - Owner: `ianktoo`
     - Repository: `data-forge`
     - Workflow: `publish-pypi.yml`
     - Environment: `pypi`

2. **GitHub Environment** (already created)
   - Repository Settings → Environments → `pypi`
   - This restricts publishing access

## Releasing

### Manual release (as a maintainer)

```bash
# 1. Update version in pyproject.toml
# 2. Commit changes
git add .
git commit -m "Bump version to X.Y.Z"
git push

# 3. Create and push tag
git tag vX.Y.Z
git push origin vX.Y.Z
```

### What happens next

1. GitHub Actions starts both workflows automatically
2. **Build Executables** creates binaries (~2-3 minutes)
3. **Publish to PyPI** publishes the package (~1-2 minutes)
4. Check progress: https://github.com/ianktoo/data-forge/actions

### Verify the release

```bash
# Should be available on PyPI after ~5 minutes
pip install llm-web-crawler

# Check GitHub Releases
# https://github.com/ianktoo/data-forge/releases
```

## Troubleshooting

### Build fails
- Check Python compatibility in `.python-version` or `pyproject.toml`
- Ensure `src/dataforge/main.py` is the correct entry point

### PyPI publish fails
- Verify OIDC trusted publisher is set up on PyPI
- Check that `publish-pypi.yml` has `environment: pypi`
- Ensure GitHub environment `pypi` exists

### Executables won't run
- PyInstaller may need additional hidden imports
- Check: `pyinstaller --hidden-import=module_name`
- Update `build-executables.yml` if needed

## Monitoring

Check workflow status:
- **Actions tab**: https://github.com/ianktoo/data-forge/actions
- **Each workflow** shows logs, errors, and artifacts

## Adding new workflows

To add more CI (tests, linting, etc.):
1. Create `.github/workflows/test.yml`
2. Use actions like `actions/checkout`, `actions/setup-python`
3. Run your test/lint commands
4. No secrets needed for public repos

Example:
```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -e ".[dev]"
      - run: pytest
```
