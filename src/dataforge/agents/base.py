"""Base agent class and shared PipelineContext."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from dataforge.config import Settings
from dataforge.storage.models import DataFormat, PipelineStage
from dataforge.utils import get_logger

if TYPE_CHECKING:
    from dataforge.generators import BudgetTracker


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
    # LLM judge: checks each sample against its source chunk for grounding and
    # for referring to the source. Off by default for the interactive wizard
    # (it costs extra calls); recipes turn it on by default.
    quality_llm_judge: bool = False
    quality_min_judge_score: int = 4   # 1-5; samples below this are rejected
    # Jaccard token-overlap threshold above which two samples from the same
    # chunk are rejected as near-duplicate paraphrases, not just exact-hash
    # duplicates.
    quality_near_dup_threshold: float = 0.85

    # Per-session model overrides (empty string = use settings.llm_model)
    generation_model: str = ""
    quality_model: str = ""

    # LLM spend/call cap, shared by the generation and quality-judge LLM
    # clients so a run's total cost (generation + judge) is bounded, not just
    # generation alone. None = unlimited.
    max_llm_calls: int | None = None
    max_cost_usd: float | None = None

    # Pause signal — any agent checks this and stops early when True
    pause_requested: bool = False

    # Lifecycle
    current_stage: PipelineStage = PipelineStage.discovery
    errors: list[str] = field(default_factory=list)

    # LLM usage accumulated across all stages (total_calls, tokens, cost)
    llm_usage: dict = field(default_factory=dict)

    # Lazily-created, shared across the generation and quality-judge LLM
    # clients (see get_budget()) so both draw down the same cap.
    _llm_budget: BudgetTracker | None = field(default=None, repr=False, compare=False)

    def session_dir(self) -> Path:
        return self.settings.session_dir(self.session_id)

    def get_budget(self) -> BudgetTracker:
        """Return the shared BudgetTracker for this run, creating it on first use."""
        from dataforge.generators import BudgetTracker as _BudgetTracker
        if self._llm_budget is None:
            self._llm_budget = _BudgetTracker(
                max_calls=self.max_llm_calls,
                max_cost_usd=self.max_cost_usd,
            )
        return self._llm_budget

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
