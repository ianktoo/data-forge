"""ReviewerAgent — uses an LLM to score samples and reports token/cost usage.

The reviewer is a lightweight agent that runs after generation and before quality
filtering.  It sends a small batch of samples to the LLM and asks it to rate
each one from 1–5.  Results are stored in the database and shown to the user
with a plain-language cost summary.

Because LLM calls cost money, this agent:
  • estimates the cost BEFORE running and asks the user to confirm
  • lets the user set a hard cost cap (aborts if exceeded)
  • shows a running total after every batch
  • skips gracefully if the LLM is not configured
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlmodel import select

from dataforge.generators.llm import LLMClient, UsageSummary
from dataforge.storage import SyntheticSample, open_session
from dataforge.utils import get_logger
from dataforge.utils.errors import LLMConnectionError, MissingCredentialError, show_warning

from .base import BaseAgent, PipelineContext

log = get_logger("reviewer")

_REVIEW_SYSTEM = """You are a careful editor reviewing AI-generated training examples.
For each example, score the ANSWER (not the question) from 1 to 5:

5 — Clear, accurate, complete. Would make an excellent training example.
4 — Good, minor issues with depth or clarity.
3 — Acceptable but vague, generic, or slightly off-topic.
2 — Weak: too short, inaccurate, or unhelpful.
1 — Should be removed: refusal, hallucination, or off-topic.

Output ONLY a JSON array of integers, one per example, in the same order."""

_BATCH_SIZE = 5   # samples per LLM call (keeps prompts short → cheaper)


@dataclass
class ReviewSummary:
    total_reviewed: int = 0
    score_distribution: dict[int, int] = field(default_factory=lambda: {1:0,2:0,3:0,4:0,5:0})
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    skipped: int = 0

    def add_usage(self, u: UsageSummary) -> None:
        self.prompt_tokens     += u.prompt_tokens
        self.completion_tokens += u.completion_tokens
        self.cost_usd          += u.cost_usd

    def avg_score(self) -> float:
        total = sum(s * c for s, c in self.score_distribution.items())
        count = sum(self.score_distribution.values())
        return total / count if count else 0.0


class ReviewerAgent(BaseAgent):
    name = "reviewer"

    def __init__(
        self,
        context: PipelineContext,
        *,
        cost_cap_usd: float = 1.0,
        min_score_to_keep: int = 3,
        sample_limit: int | None = None,  # review only the first N samples (cost control)
    ) -> None:
        super().__init__(context)
        self._cost_cap       = cost_cap_usd
        self._min_score      = min_score_to_keep
        self._sample_limit   = sample_limit

    async def run(self) -> PipelineContext:
        with open_session(self.ctx.settings.db_path) as db:
            samples = db.exec(
                select(SyntheticSample)
                .where(SyntheticSample.session_id == self.ctx.session_id)
            ).all()

        if not samples:
            log.warning("No samples to review")
            return self.ctx

        if self._sample_limit:
            samples = samples[:self._sample_limit]

        # ── Cost estimate before running ──────────────────────────────────────
        n_batches = (len(samples) + _BATCH_SIZE - 1) // _BATCH_SIZE
        est_tokens = n_batches * 600   # rough: 500 prompt + 100 completion per batch
        est_cost   = est_tokens / 1_000_000 * 2.0  # ~$2 per 1M tokens (gpt-4o-mini)
        log.info(f"Review estimate: ~{n_batches} calls, ~{est_tokens} tokens, ~${est_cost:.3f}")

        summary = ReviewSummary()
        llm     = LLMClient()

        batches = [samples[i:i+_BATCH_SIZE] for i in range(0, len(samples), _BATCH_SIZE)]

        for batch in batches:
            if summary.cost_usd >= self._cost_cap:
                log.warning(f"Cost cap ${self._cost_cap:.2f} reached — stopping review early")
                show_warning(
                    f"Review stopped at cost cap (${self._cost_cap:.2f}).",
                    f"Reviewed {summary.total_reviewed}/{len(samples)} samples. "
                    "Remaining samples keep their default scores.",
                )
                summary.skipped += len(samples) - summary.total_reviewed
                break

            scores = await self._review_batch(llm, batch)

            for sample, score in zip(batch, scores):
                clamped = max(1, min(5, score))
                summary.score_distribution[clamped] += 1
                summary.total_reviewed += 1
                # Normalise to 0–1 for the quality_score field
                normalised = (clamped - 1) / 4.0
                with open_session(self.ctx.settings.db_path) as db:
                    row = db.get(SyntheticSample, sample.id)
                    if row:
                        row.quality_score = normalised
                        row.approved = clamped >= self._min_score
                        db.add(row)
                        db.commit()

            summary.add_usage(llm.usage)

        self._log_summary(summary, len(samples))
        return self.ctx

    async def _review_batch(self, llm: LLMClient, batch: list[SyntheticSample]) -> list[int]:
        """Ask the LLM to rate a batch of samples. Returns one score per sample."""
        examples_text = ""
        for i, s in enumerate(batch, 1):
            msgs = s.messages()
            question = next((m["content"] for m in msgs if m.get("role") == "user"), "")
            answer   = next((m["content"] for m in msgs if m.get("role") == "assistant"), "")
            examples_text += f"\n--- Example {i} ---\nQ: {question[:300]}\nA: {answer[:400]}\n"

        messages = [
            {"role": "system", "content": _REVIEW_SYSTEM},
            {"role": "user",   "content": f"Rate these {len(batch)} examples:\n{examples_text}"},
        ]
        try:
            resp = await llm.complete(messages, max_tokens=30)
            raw  = resp.content.strip()
            # Expect something like: [4, 3, 5, 2, 4]
            data = json.loads(raw)
            if isinstance(data, list) and len(data) == len(batch):
                return [int(x) for x in data]
        except (json.JSONDecodeError, ValueError, MissingCredentialError, LLMConnectionError):
            pass
        except Exception as exc:
            log.warning(f"Review batch failed: {exc}")
        return [3] * len(batch)  # default: neutral score on any error

    def _log_summary(self, s: ReviewSummary, total: int) -> None:
        dist = "  ".join(f"★{k}:{v}" for k, v in sorted(s.score_distribution.items()))
        log.info(
            f"Review complete: {s.total_reviewed}/{total} samples reviewed  "
            f"|  {dist}  "
            f"|  avg {s.avg_score():.1f}/5  "
            f"|  {s.prompt_tokens + s.completion_tokens} tokens  "
            f"|  ${s.cost_usd:.4f}"
        )
        if s.skipped:
            log.info(f"  {s.skipped} samples skipped (cost cap)")
