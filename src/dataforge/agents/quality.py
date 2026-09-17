"""QualityAgent — score, deduplicate, and filter synthetic samples.

Filtering runs in layers, cheapest first, and a sample must pass every layer
that is enabled:

1. Heuristic score — answer/question length and refusal detection, compared
   against ``quality_threshold``. Free.
2. Source-reference check — rejects samples that talk about "the document" or
   "the passage" instead of the subject. Free, always on.
3. Deduplication — exact-prefix fingerprint across the whole session. Free.
4. LLM judge (``quality_llm_judge``) — sees each sample next to the chunk it was
   generated from and rejects anything ungrounded, source-dependent, or scored
   below ``quality_min_judge_score``. Costs one call per chunk.

When the judge is on, its 1-5 score becomes the sample's ``quality_score``
(normalised to 0-1); otherwise the heuristic score is kept. This agent is the
only place ``approved`` is decided, so nothing downstream can overwrite it.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from collections import Counter, defaultdict

from sqlmodel import select

from dataforge.generators.judge import find_source_reference, judge_chunk
from dataforge.storage import ProcessedChunk, SyntheticSample, open_session
from dataforge.utils.errors import LLMConnectionError, MissingCredentialError, show_warning

from .base import BaseAgent, PipelineContext

_MIN_ANSWER_WORDS = 10
_MIN_QUESTION_WORDS = 5

_REFUSAL_RE = re.compile(
    r"(I cannot|I'm unable|I can't|As an AI|I am an AI|I'm not able to)",
    re.I,
)


class QualityAgent(BaseAgent):
    name = "quality"

    async def run(self) -> PipelineContext:
        with open_session(self.ctx.settings.db_path) as db:
            samples = db.exec(
                select(SyntheticSample)
                .where(SyntheticSample.session_id == self.ctx.session_id)
            ).all()

        self.log.info(f"Evaluating {len(samples)} samples")
        seen_hashes: set[str] = set()
        # id -> (score, approved, rejection reason)
        results: dict[int, tuple[float, bool, str]] = {}
        candidates: list[SyntheticSample] = []

        for sample in samples:
            if sample.id is None:
                continue
            msgs = sample.messages()
            score = self._score(msgs, sample.format)
            fingerprint = self._fingerprint(msgs)

            if fingerprint in seen_hashes:
                results[sample.id] = (0.0, False, "duplicate")
                continue
            seen_hashes.add(fingerprint)

            ref = find_source_reference(msgs)
            if ref:
                results[sample.id] = (0.0, False, "refers to the source")
                continue

            if score < self.ctx.quality_threshold:
                results[sample.id] = (score, False, "below heuristic threshold")
                continue

            results[sample.id] = (score, True, "")
            candidates.append(sample)

        if self.ctx.quality_llm_judge and candidates:
            await self._apply_judge(candidates, results)

        approved_ids = self._persist(results)
        self.ctx.approved_sample_ids = approved_ids
        self._report(results, len(samples))
        return self.ctx

    # -- LLM judge -----------------------------------------------------------

    async def _apply_judge(
        self,
        candidates: list[SyntheticSample],
        results: dict[int, tuple[float, bool, str]],
    ) -> None:
        from dataforge.cli.preflight import check_llm_credentials
        from dataforge.generators import LLMClient

        ok, _ = check_llm_credentials()
        if not ok:
            # Do not silently weaken the filter the recipe asked for: reject
            # everything unjudged and say why, so a resume re-judges it.
            show_warning(
                "LLM judge is enabled but no LLM credentials are configured.",
                "Samples were not approved. Add your API key and resume: "
                f"dataforge resume {self.ctx.session_id[:8]}",
            )
            for s in candidates:
                if s.id is not None:
                    results[s.id] = (results[s.id][0], False, "not judged (no credentials)")
            return

        by_chunk: dict[int, list[SyntheticSample]] = defaultdict(list)
        for s in candidates:
            by_chunk[s.chunk_id].append(s)

        with open_session(self.ctx.settings.db_path) as db:
            sources = {
                c.id: c.content
                for c in db.exec(
                    select(ProcessedChunk).where(ProcessedChunk.id.in_(list(by_chunk)))  # type: ignore[union-attr]
                ).all()
                if c.id is not None
            }

        llm = LLMClient(model_override=self.ctx.quality_model or self.ctx.generation_model)
        min_score = self.ctx.quality_min_judge_score
        sem = asyncio.Semaphore(max(1, self.ctx.settings.stream_generate_workers))
        fatal: list[str] = []

        async def _one(chunk_id: int, group: list[SyntheticSample]) -> None:
            source = sources.get(chunk_id)
            if source is None:
                for s in group:
                    if s.id is not None:
                        results[s.id] = (0.0, False, "source chunk missing")
                return
            if fatal:
                for s in group:
                    if s.id is not None:
                        results[s.id] = (results[s.id][0], False, "not judged (LLM error)")
                return
            async with sem:
                try:
                    verdicts = await judge_chunk(llm, source, [s.messages() for s in group])
                except (MissingCredentialError, LLMConnectionError) as exc:
                    fatal.append(str(exc))
                    verdicts = [None] * len(group)
            for s, v in zip(group, verdicts):
                if s.id is None:
                    continue
                if v is None:
                    results[s.id] = (0.0, False, "judge could not score")
                    continue
                normalised = (v.score - 1) / 4.0
                if v.passes(min_score):
                    results[s.id] = (normalised, True, "")
                else:
                    results[s.id] = (normalised, False, v.rejection_reason(min_score))

        self.log.info(
            f"LLM judge: {len(candidates)} samples across {len(by_chunk)} chunks "
            f"(min score {min_score}/5)"
        )
        await asyncio.gather(*[_one(cid, grp) for cid, grp in by_chunk.items()])

        if fatal:
            show_warning(
                f"LLM judge stopped early: {fatal[0]}",
                "Unjudged samples were not approved. Fix the issue and resume: "
                f"dataforge resume {self.ctx.session_id[:8]}",
            )

        usage = self.ctx.llm_usage or {}
        self.ctx.llm_usage = {
            "total_calls":       usage.get("total_calls", 0) + llm.usage.total_calls,
            "prompt_tokens":     usage.get("prompt_tokens", 0) + llm.usage.prompt_tokens,
            "completion_tokens": usage.get("completion_tokens", 0) + llm.usage.completion_tokens,
            "cost_usd":          usage.get("cost_usd", 0.0) + llm.usage.cost_usd,
            "errors":            usage.get("errors", 0) + llm.usage.errors,
        }
        self.log.info(
            f"LLM judge cost: {llm.usage.total_calls} calls, ${llm.usage.cost_usd:.4f}"
        )

    # -- Persistence and reporting -------------------------------------------

    def _persist(self, results: dict[int, tuple[float, bool, str]]) -> list[int]:
        approved_ids: list[int] = []
        with open_session(self.ctx.settings.db_path) as db:
            for sample_id, (score, approved, _reason) in results.items():
                s = db.get(SyntheticSample, sample_id)
                if s:
                    s.quality_score = score
                    s.approved = approved
                    db.add(s)
                    if approved:
                        approved_ids.append(sample_id)
            db.commit()
        return approved_ids

    def _report(self, results: dict[int, tuple[float, bool, str]], total: int) -> None:
        approved = sum(1 for _, ok, _ in results.values() if ok)
        rate = approved / max(total, 1) * 100
        self.log.info(f"Quality pass: {approved}/{total} approved ({rate:.0f}%)")
        reasons = Counter(r for _, ok, r in results.values() if not ok)
        for reason, count in reasons.most_common():
            self.log.info(f"  rejected {count:>4}  {reason}")

    # -- Heuristics ----------------------------------------------------------

    def _score(self, messages: list[dict], format: str) -> float:
        if not messages:
            return 0.0
        scores = []
        for msg in messages:
            content = msg.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            words = len(content.split())
            role = msg.get("role", "")
            if role == "user":
                scores.append(min(1.0, words / _MIN_QUESTION_WORDS))
            elif role == "assistant":
                scores.append(min(1.0, words / _MIN_ANSWER_WORDS))
                if _REFUSAL_RE.search(content):
                    scores[-1] *= 0.1
        return sum(scores) / max(len(scores), 1)

    def _fingerprint(self, messages: list[dict]) -> str:
        text = " ".join(str(m.get("content", "")) for m in messages)[:200]
        return hashlib.md5(text.encode()).hexdigest()
