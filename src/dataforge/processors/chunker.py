"""Token-aware text splitting with configurable overlap."""
from __future__ import annotations

import re

import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")


def token_count(text: str) -> int:
    return len(_enc.encode(text))


def chunk(text: str, size: int = 512, overlap: int = 64) -> list[str]:
    """Split text into overlapping token-aware chunks."""
    # First split on paragraph boundaries to preserve semantics
    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        ptokens = token_count(para)

        if ptokens > size:
            # Paragraph itself exceeds chunk size — split by sentence
            for sent in _split_sentences(para):
                stokens = token_count(sent)
                if current_tokens + stokens > size and current:
                    chunks.append("\n\n".join(current))
                    # overlap: keep last N tokens worth of sentences
                    current, current_tokens = _trim_to_overlap(current, overlap)
                current.append(sent)
                current_tokens += stokens
        else:
            if current_tokens + ptokens > size and current:
                chunks.append("\n\n".join(current))
                current, current_tokens = _trim_to_overlap(current, overlap)
            current.append(para)
            current_tokens += ptokens

    if current:
        chunks.append("\n\n".join(current))

    return [c for c in chunks if c.strip()]


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _trim_to_overlap(parts: list[str], overlap_tokens: int) -> tuple[list[str], int]:
    kept: list[str] = []
    total = 0
    for part in reversed(parts):
        t = token_count(part)
        if total + t > overlap_tokens:
            break
        kept.insert(0, part)
        total += t
    return kept, total
