# Installation

## uv (recommended)
```bash
uv tool install llm-web-crawler
dataforge
```

Update:
```bash
uv tool upgrade llm-web-crawler
```

Uninstall:
```bash
uv tool uninstall llm-web-crawler
```

## pip
```bash
pip install llm-web-crawler
dataforge
```

Update:
```bash
pip install --upgrade llm-web-crawler
```

Uninstall:
```bash
pip uninstall llm-web-crawler
```

## From source
```bash
git clone https://github.com/ianktoo/data-forge.git
cd data-forge
uv sync
uv run dataforge
```

## Standalone executables (no Python required)
Download pre-built binaries for your platform from [GitHub Releases](https://github.com/ianktoo/data-forge/releases):

| Platform | File |
|---|---|
| Windows | `dataforge-windows-x64.exe` |
| macOS | `dataforge-macos-x64` |
| Linux | `dataforge-linux-x64` |

## Using Ollama (fully local, no API key)

```bash
ollama serve
ollama pull llama3.2
dataforge config   # choose ollama / llama3.2
dataforge
```

See [CONFIGURATION.md](CONFIGURATION.md) for all provider and environment options.
