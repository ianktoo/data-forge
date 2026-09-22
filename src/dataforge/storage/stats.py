"""Read-only dataset statistics for a session — the `dataforge stats` command.

Everything here reads data the pipeline already computes (quality scores,
rejection reasons, message content, and — when a split export exists — the
exported split files). No new scoring or filtering logic; this is purely a
reporting layer over what QualityAgent and ExporterAgent already produced.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import select

from .database import open_session
from .models import SyntheticSample

_HISTOGRAM_BUCKETS = 5  # score histogram: 5 buckets over [0, 1]


@dataclass
class LengthStats:
    count: int = 0
    min: int = 0
    max: int = 0
    mean: float = 0.0
    median: float = 0.0


@dataclass
class ScoreStats:
    count: int = 0
    min: float = 0.0
    max: float = 0.0
    mean: float = 0.0
    median: float = 0.0
    # histogram[i] = count of scores in bucket i of _HISTOGRAM_BUCKETS
    histogram: list[int] = field(default_factory=list)


@dataclass
class SessionStats:
    total_samples: int = 0
    approved: int = 0
    rejected: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    question_length: LengthStats = field(default_factory=LengthStats)
    answer_length: LengthStats = field(default_factory=LengthStats)
    score: ScoreStats = field(default_factory=ScoreStats)
    # Realized split counts from the most recent split export on disk, if
    # any (e.g. {"train": 812, "validation": 101, "test": 99}). None if this
    # session was never exported with a split.
    split_counts: dict[str, int] | None = None


def _word_lengths(messages: list[dict], role: str) -> list[int]:
    lengths = []
    for m in messages:
        if m.get("role") != role:
            continue
        content = m.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        lengths.append(len(content.split()))
    return lengths


def _length_stats(values: list[int]) -> LengthStats:
    if not values:
        return LengthStats()
    return LengthStats(
        count=len(values),
        min=min(values),
        max=max(values),
        mean=statistics.fmean(values),
        median=statistics.median(values),
    )


def _score_stats(values: list[float]) -> ScoreStats:
    if not values:
        return ScoreStats(histogram=[0] * _HISTOGRAM_BUCKETS)
    histogram = [0] * _HISTOGRAM_BUCKETS
    for v in values:
        bucket = min(int(v * _HISTOGRAM_BUCKETS), _HISTOGRAM_BUCKETS - 1)
        histogram[bucket] += 1
    return ScoreStats(
        count=len(values),
        min=min(values),
        max=max(values),
        mean=statistics.fmean(values),
        median=statistics.median(values),
        histogram=histogram,
    )


def _latest_split_counts(session_dir: Path) -> dict[str, int] | None:
    """Count lines in the most recent dataset_{train,validation,test}.jsonl
    export, if one exists. Returns None if this session was never exported
    with a split (or has no exports at all).
    """
    exports_dir = session_dir / "exports"
    if not exports_dir.is_dir():
        return None
    run_dirs = sorted((d for d in exports_dir.iterdir() if d.is_dir()), reverse=True)
    for run_dir in run_dirs:
        counts: dict[str, int] = {}
        for split_name in ("train", "validation", "test"):
            f = run_dir / f"dataset_{split_name}.jsonl"
            if f.exists():
                with f.open(encoding="utf-8") as fh:
                    counts[split_name] = sum(1 for line in fh if line.strip())
        if counts:
            return counts
    return None


def compute_session_stats(db_path: Path, session_id: str, session_dir: Path) -> SessionStats:
    with open_session(db_path) as db:
        samples = db.exec(
            select(SyntheticSample).where(SyntheticSample.session_id == session_id)
        ).all()

    q_lengths: list[int] = []
    a_lengths: list[int] = []
    scores: list[float] = []
    reasons: dict[str, int] = {}
    approved = 0

    for s in samples:
        try:
            messages = json.loads(s.messages_json)
        except json.JSONDecodeError:
            messages = []
        q_lengths.extend(_word_lengths(messages, "user"))
        a_lengths.extend(_word_lengths(messages, "assistant"))
        scores.append(s.quality_score)
        if s.approved:
            approved += 1
        elif s.rejection_reason:
            reasons[s.rejection_reason] = reasons.get(s.rejection_reason, 0) + 1

    return SessionStats(
        total_samples=len(samples),
        approved=approved,
        rejected=len(samples) - approved,
        rejection_reasons=reasons,
        question_length=_length_stats(q_lengths),
        answer_length=_length_stats(a_lengths),
        score=_score_stats(scores),
        split_counts=_latest_split_counts(session_dir),
    )
