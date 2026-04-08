# LLM Web Crawler

A cross-platform CLI tool for collecting and processing web data for LLM fine-tuning datasets. Discovers URLs, fetches content, and prepares synthetic training data.

## Installation

### Via pip (recommended - all platforms)
```bash
pip install llm-web-crawler
```

Then run:
```bash
dataforge
```

### From source
```bash
git clone https://github.com/ianktoo/data-forge.git
cd data-forge
pip install -e .
dataforge
```

### Standalone executables (no Python required)
Download pre-built executables for your platform from [GitHub Releases](https://github.com/ianktoo/data-forge/releases):
- **Windows**: `dataforge-windows-x64.exe`
- **macOS**: `dataforge-macos-x64`
- **Linux**: `dataforge-linux-x64`

Just download and run directly.

## Features

- **URL Discovery**: Automatically discovers URLs from sitemaps and robots.txt
- **Parallel Processing**: Asynchronous collection of web content
- **LLM Integration**: Prepare data for fine-tuning with LiteLLM support
- **Multi-format Output**: Support for various output formats (JSON, Arrow, Parquet)
- **Cross-platform**: Runs on Windows, macOS, and Linux

## Usage

```bash
dataforge
```

The CLI provides an interactive interface for:
- Configuring data sources
- Setting collection parameters
- Monitoring progress
- Exporting datasets

## Development

### Setup
```bash
git clone https://github.com/ianktoo/data-forge.git
cd data-forge
pip install -e ".[dev]"
```

### Run tests
```bash
pytest
```

### Code quality
```bash
# Linting
ruff check src/ tests/

# Type checking
mypy src/
```

## Releasing New Versions

This project uses GitHub Actions for automated CI/CD:

### Release process
1. Update the version in `pyproject.toml`
2. Commit your changes:
   ```bash
   git add .
   git commit -m "Bump version to X.Y.Z"
   git push
   ```
3. Create and push a git tag:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

### What happens automatically
- **Build**: Cross-platform executables are built for Windows, macOS, and Linux
- **Release**: Executables are attached to a GitHub Release
- **Publish**: Package is published to PyPI as `llm-web-crawler`

Users can then:
- Install via `pip install llm-web-crawler`
- Or download standalone executables from [Releases](https://github.com/ianktoo/data-forge/releases)

## Project Structure

```
data-forge/
├── src/dataforge/           # Main package
│   ├── cli/                 # CLI interface (typer + questionary)
│   ├── collectors/          # Web content collectors
│   ├── processors/          # Data processors
│   └── main.py              # Entry point
├── tests/                   # Test suite
├── .github/workflows/       # CI/CD automation
│   ├── build-executables.yml  # PyInstaller builds
│   └── publish-pypi.yml       # PyPI publishing
└── pyproject.toml           # Project metadata & dependencies
```

## Dependencies

Core:
- `typer` - CLI framework
- `rich` - Terminal formatting
- `questionary` - Interactive prompts
- `httpx` - HTTP client
- `beautifulsoup4` - HTML parsing
- `litellm` - LLM API abstraction

Data:
- `sqlmodel` - Database ORM
- `pydantic` - Data validation
- `datasets` - Hugging Face datasets
- `pyarrow` - Arrow/Parquet support

## License

See LICENSE file for details.
