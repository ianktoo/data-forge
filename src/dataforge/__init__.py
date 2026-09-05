"""DataForge — LLM data collection and synthetic fine-tuning pipeline."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("llm-web-crawler")
except PackageNotFoundError:  # running from source without install
    __version__ = "0.0.0.dev0"
