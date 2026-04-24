"""QualityAgent — score, deduplicate, and filter synthetic samples."""
from __future__ import annotations

import hashlib
import re

from sqlmodel import select

from dataforge.storage import SyntheticSample, open_session

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
        updates: list[tuple[int, float, bool]] = []  # (id, score, approved)

        for sample in samples:
            msgs = sample.messages()
            score = self._score(msgs, sample.format)
            fingerprint = self._fingerprint(msgs)

            if fingerprint in seen_hashes:
                score = 0.0  # duplicate
            else:
                seen_hashes.add(fingerprint)

            approved = score >= self.ctx.quality_threshold
            if sample.id is not None:
                updates.append((sample.id, score, approved))

        # Batch all DB writes in a single session
        approved_ids: list[int] = []
        with open_session(self.ctx.settings.db_path) as db:
            for sample_id, score, approved in updates:
                s = db.get(SyntheticSample, sample_id)
                if s:
                    s.quality_score = score
                    s.approved = approved
                    db.add(s)
                    if approved:
                        approved_ids.append(sample_id)
            db.commit()

        self.ctx.approved_sample_ids = approved_ids
        rate = len(approved_ids) / max(len(samples), 1) * 100
        self.log.info(f"Quality pass: {len(approved_ids)}/{len(samples)} approved ({rate:.0f}%)")
        return self.ctx

    def _score(self, messages: list[dict], format: str) -> float:
        if not messages:
            return 0.0
        scores = []
        for msg in messages:
            content = msg.get("content", "")
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
        text = " ".join(m.get("content", "") for m in messages)[:200]
        return hashlib.md5(text.encode()).hexdigest()
