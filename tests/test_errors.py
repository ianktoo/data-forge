"""Smoke tests for user-facing error display."""
from __future__ import annotations

from dataforge.utils.errors import _GUIDANCE, show_error


def test_quality_has_a_guidance_entry():
    """Regression: the quality stage used to fall through to a bare
    'Error: quality' panel with no actionable guidance."""
    assert "quality" in _GUIDANCE
    title, body_lines, hint_lines = _GUIDANCE["quality"]
    assert body_lines
    assert hint_lines


def test_show_error_unknown_key_does_not_raise(capsys):
    # No guidance entry exists for this key — must fall back gracefully,
    # not raise, even when the log file doesn't exist yet.
    show_error("some-unrecognized-key", extra="boom", stage="quality")
