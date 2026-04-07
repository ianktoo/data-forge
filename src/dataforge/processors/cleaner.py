"""Text cleaning and normalisation."""
from __future__ import annotations

import re
import unicodedata


def clean(text: str) -> str:
    """Normalise Unicode, collapse whitespace, remove common boilerplate."""
    text = unicodedata.normalize("NFKC", text)
    text = _strip_boilerplate(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" \n", "\n", text)
    return text.strip()


def _strip_boilerplate(text: str) -> str:
    patterns = [
        r"Subscribe to our newsletter.*?(?=\n\n|\Z)",
        r"Follow us on.*?(?=\n\n|\Z)",
        r"Share this (article|post|page).*?(?=\n\n|\Z)",
        r"(All rights reserved|Copyright © \d{4}).*?(?=\n|\Z)",
        r"Cookie (policy|settings|preferences).*?(?=\n\n|\Z)",
        r"Accept cookies.*?(?=\n\n|\Z)",
    ]
    for pat in patterns:
        text = re.sub(pat, "", text, flags=re.IGNORECASE | re.DOTALL)
    return text


def word_count(text: str) -> int:
    return len(text.split())


def is_content_rich(text: str, min_words: int = 50) -> bool:
    return word_count(text) >= min_words
