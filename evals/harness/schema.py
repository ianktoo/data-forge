"""Pydantic models shared by the eval harness."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HoldoutItem(BaseModel):
    """One held-out Q&A pair, same shape as dataset/gold/dataset.jsonl."""

    id: int
    question: str
    reference_answer: str
    source_url: str
    source_excerpt: str


class ModelAnswer(BaseModel):
    """A candidate model's answer to one holdout item."""

    item_id: int
    model_name: str
    answer: str


class JudgeScore(BaseModel):
    """LLM-as-judge output for one (holdout item, model answer) pair."""

    item_id: int
    model_name: str
    groundedness: int = Field(ge=0, le=2)
    usefulness: int = Field(ge=0, le=2)
    safety: int = Field(ge=0, le=2)
    rationale: str
