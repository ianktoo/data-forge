"""HTML → clean text / Markdown extractor."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from markdownify import markdownify

_NOISE_TAGS = [
    "script", "style", "nav", "header", "footer", "aside",
    "form", "noscript", "iframe", "svg", "button", "input",
    "select", "textarea", "advertisement", "cookie-notice",
]

_META_AUTHORS = ["author", "article:author", "og:article:author", "twitter:creator"]
_META_DATES   = ["article:published_time", "og:article:published_time",
                 "date", "pubdate", "DC.date"]


@dataclass
class PageContent:
    url: str
    title: str
    author: str
    published_date: str
    text: str        # clean plain text
    markdown: str    # markdown version
    word_count: int
    links: list[str]


def extract(html: str, url: str) -> PageContent:
    soup = BeautifulSoup(html, "lxml")

    # ── Metadata ──────────────────────────────────────────────────────────────
    title = _meta_or_tag(soup, "title", ["og:title", "twitter:title"])
    author = _meta_first(soup, _META_AUTHORS)
    date   = _meta_first(soup, _META_DATES)

    # ── Remove noise tags ─────────────────────────────────────────────────────
    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()

    # ── Main content heuristic: largest text block ────────────────────────────
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id=re.compile(r"content|main|article|post", re.I))
        or soup.find(class_=re.compile(r"content|main|article|post|entry", re.I))
        or soup.find("body")
        or soup
    )

    # ── Links (absolute) ──────────────────────────────────────────────────────
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    links = [
        urljoin(base, a["href"])
        for a in main.find_all("a", href=True)
        if not a["href"].startswith(("#", "mailto:", "tel:"))
    ]

    # ── Text & Markdown ───────────────────────────────────────────────────────
    raw_md  = markdownify(str(main), heading_style="ATX", strip=["a", "img"])
    text    = _normalise(main.get_text(separator="\n"))
    cleaned = _normalise(raw_md)

    return PageContent(
        url=url,
        title=title,
        author=author,
        published_date=date,
        text=text,
        markdown=cleaned,
        word_count=len(text.split()),
        links=list(dict.fromkeys(links)),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _meta_first(soup: BeautifulSoup, names: list[str]) -> str:
    for name in names:
        tag = soup.find("meta", {"name": name}) or soup.find("meta", {"property": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _meta_or_tag(soup: BeautifulSoup, tag_name: str, meta_names: list[str]) -> str:
    val = _meta_first(soup, meta_names)
    if val:
        return val
    tag = soup.find(tag_name)
    return tag.get_text(strip=True) if tag else ""


def _normalise(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()
