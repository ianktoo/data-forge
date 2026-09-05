"""Base agent class and shared PipelineContext."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from dataforge.config import Settings
from dataforge.storage.models import DataFormat, PipelineStage
from dataforge.utils import get_logger


@dataclass
class PipelineContext:
    """Shared mutable state passed between agents."""
    session_id: str
    session_name: str
    goal: str
    format: DataFormat
    seed_urls: list[str]
    settings: Settings

    # Populated by agents in sequence
    discovered_urls: list[str] = field(default_factory=list)
    selected_urls: list[str] = field(default_factory=list)
    scraped_page_ids: list[int] = field(default_factory=list)
    processed_chunk_ids: list[int] = field(default_factory=list)
    synthetic_sample_ids: list[int] = field(default_factory=list)
    approved_sample_ids: list[int] = field(default_factory=list)
    export_records: list[dict] = field(default_factory=list)

    # Custom system prompt override
    custom_system_prompt: str = ""
    n_per_chunk: int = 3
    ignore_robots: bool = False
    skip_known: bool = False  # skip URLs already scraped in prior sessions

    # Quality control
    quality_threshold: float = 0.5   # min score to approve a sample

    # Per-session model overrides (empty string = use settings.llm_model)
    generation_model: str = ""
    quality_model: str = ""

    # Pause signal — any agent checks this and stops early when True
    pause_requested: bool = False

    # Lifecycle
    current_stage: PipelineStage = PipelineStage.discovery
    errors: list[str] = field(default_factory=list)

    # LLM usage accumulated across all stages (total_calls, tokens, cost)
    llm_usage: dict = field(default_factory=dict)

    def session_dir(self) -> Path:
        return self.settings.session_dir(self.session_id)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        get_logger("context").warning(msg)


# Checkpoint callback type: called after each agent completes
CheckpointFn = Callable[[PipelineContext], Awaitable[None]]


class BaseAgent(ABC):
    name: str = "base"

    def __init__(self, context: PipelineContext) -> None:
        self.ctx = context
        self.log = get_logger(self.name)

    @abstractmethod
    async def run(self) -> PipelineContext:
        """Execute agent logic. Must update and return self.ctx."""
        ...

    def _stage_dir(self, stage: str) -> Path:
        d = self.ctx.session_dir() / stage
        d.mkdir(parents=True, exist_ok=True, mode=0o750)
        return d
