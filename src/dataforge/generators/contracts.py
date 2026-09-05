"""The message contract — the boundary between raw LLM JSON and DataForge storage.

Generators receive whatever shape the configured LLM feels like returning.
Nothing downstream (quality scoring, export, ...) should ever see that raw
shape directly — it must pass through ``GeneratedMessage`` first, which
normalizes content to a string and validates the role, or raises
``GenerationParseError`` so the failure is diagnosable at its source instead
of surfacing as an unrelated ``AttributeError`` stages later.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError, field_validator

_VALID_ROLES = {"system", "user", "assistant"}


class GenerationParseError(Exception):
    """A generated item could not be normalized into a valid message contract."""


class GeneratedMessage(BaseModel):
    role: str
    content: str

    @field_validator("content", mode="before")
    @classmethod
    def _coerce_content(cls, v: object) -> str:
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        if isinstance(v, (dict, list)):
            # The model returned a nested structure where plain text was expected —
            # flatten it rather than losing the sample outright.
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in _VALID_ROLES:
            raise ValueError(f"invalid role {v!r} (expected one of {sorted(_VALID_ROLES)})")
        return v


def to_message_dict(role: object, content: object) -> dict[str, str]:
    """Validate a single (role, content) pair and return it as a plain dict.

    Raises ``GenerationParseError`` on an unusable role; content is always
    coerced to a string rather than rejected, since malformed content is
    recoverable (stringified) while a malformed role is not.
    """
    try:
        msg = GeneratedMessage(role=role, content=content)
    except ValidationError as exc:
        raise GenerationParseError(str(exc)) from exc
    return msg.model_dump()


def to_message_list(raw_messages: object) -> list[dict[str, str]]:
    """Validate a ``conversation``-format ``messages`` list from an LLM response."""
    if not isinstance(raw_messages, list):
        raise GenerationParseError(
            f"expected a list of messages, got {type(raw_messages).__name__}"
        )
    result = []
    for m in raw_messages:
        role = m.get("role", "") if isinstance(m, dict) else ""
        content = m.get("content", "") if isinstance(m, dict) else m
        result.append(to_message_dict(role, content))
    return result
