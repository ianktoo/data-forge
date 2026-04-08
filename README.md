# DataForge

LLM data collection and synthetic fine-tuning dataset pipeline.

## Installation

### Via pip (any platform)
```bash
pip install dataforge
```

### From source
```bash
git clone https://github.com/yourusername/website-explorer.git
cd website-explorer
pip install -e .
```

### Standalone executables
Download pre-built executables for your platform from [Releases](https://github.com/yourusername/website-explorer/releases):
- **Linux**: `dataforge-linux-x64`
- **Windows**: `dataforge-windows-x64.exe`
- **macOS**: `dataforge-macos-x64`

## Usage

```bash
dataforge
```

## Development

Install development dependencies:
```bash
pip install -e ".[dev]"
```

Run tests:
```bash
pytest
```

Run linting:
```bash
ruff check src/ tests/
```

## Publishing Releases

1. Update version in `pyproject.toml`
2. Commit changes
3. Create a tag: `git tag v0.1.0`
4. Push tag: `git push origin v0.1.0`

This will trigger:
- Automated builds for Windows, macOS, and Linux
- Publishing to PyPI
- Creation of a GitHub Release with executables
