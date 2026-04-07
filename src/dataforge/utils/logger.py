"""Loguru setup — structured JSON to file, human-readable to stderr."""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

_configured = False


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()

    # Human-readable stderr (only WARNING+ by default, overridden by level)
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[agent]}</cyan> — {message}",
        filter=lambda r: r["extra"].get("agent", True),  # always show
        colorize=True,
    )

    # Structured JSON pipeline log
    logger.add(
        log_dir / "pipeline.log",
        level="INFO",
        serialize=True,
        rotation="50 MB",
        retention="30 days",
        encoding="utf-8",
    )

    # Verbose debug log
    logger.add(
        log_dir / "debug.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} — {message}",
        rotation="100 MB",
        retention="7 days",
        encoding="utf-8",
    )

    _configured = True


def get_logger(agent: str = "core"):
    return logger.bind(agent=agent)
