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
# Run all tests
pytest

# Run with coverage
pytest --cov=src/dataforge

# Run specific test
pytest tests/test_file.py::test_function
```

## Code Quality

```bash
# Linting
ruff check src/ tests/

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
