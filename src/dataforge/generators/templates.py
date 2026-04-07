"""Jinja2 prompt templates for each dataset format."""
from __future__ import annotations

from dataclasses import dataclass

from jinja2 import Environment

_env = Environment(autoescape=False)

# ── System prompts ─────────────────────────────────────────────────────────────

_SYSTEM_QA = """You are a dataset curator. Given a passage of text, generate \
{{ n }} high-quality question-and-answer pairs that test understanding of the content.
Rules:
- Questions should be specific, not generic
- Answers must be fully supported by the passage
- Vary question types: factual, inferential, applied
- Do NOT ask questions about the URL, website, or author
Output JSON array: [{"question": "...", "answer": "..."}]"""

_SYSTEM_INSTRUCTION = """You are a dataset curator. Given a passage, generate \
{{ n }} instruction-following examples in Alpaca format.
Rules:
- Instructions should be actionable and specific
- Outputs must be grounded in the passage content
- Include an optional input field when context is needed
Output JSON array: [{"instruction": "...", "input": "...", "output": "..."}]"""

_SYSTEM_CONVERSATION = """You are a dataset curator. Given a passage, generate \
{{ n }} natural multi-turn conversations about the content.
Rules:
- 2–4 turns per conversation
- Use "user" and "assistant" roles
- Conversations should feel natural, not like an interview
- Ground all assistant responses in the passage
Output JSON array: [{"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}]"""

_USER_TMPL = """Passage:
---
{{ content }}
---
Generate {{ n }} {{ format }} examples from this passage.
Goal context: {{ goal }}"""

SYSTEM_TEMPLATES = {
    "qa":           _SYSTEM_QA,
    "instruction":  _SYSTEM_INSTRUCTION,
    "conversation": _SYSTEM_CONVERSATION,
}


@dataclass
class PromptPair:
    system: str
    user: str


def build_prompt(
    content: str,
    format: str,
    goal: str,
    n: int = 3,
    custom_system: str = "",
) -> PromptPair:
    system_tmpl = custom_system or SYSTEM_TEMPLATES.get(format, _SYSTEM_QA)
    system = _env.from_string(system_tmpl).render(n=n)
    user   = _env.from_string(_USER_TMPL).render(content=content, n=n,
                                                   format=format, goal=goal)
    return PromptPair(system=system, user=user)
