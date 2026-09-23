"""Quick scrape: fetch pages and keep their content and tables. No AI (#69).

The whole pipeline turns a site into a fine-tuning dataset. Often only the
first step is wanted: the text of a few pages, or the table on one of them.
:func:`scrape_urls` does just that, with the same politeness as every other
request DataForge makes (robots.txt, per-domain rate limit, ``Crawl-delay``)
and no LLM, API key, session or database.

Used by ``dataforge scrape`` and the MCP ``scrape_page`` tool.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dataforge.collectors.extractor import extract
from dataforge.collectors.tables import Table, extract_tables
from dataforge.utils.url_sanitiser import sanitise

# Non-AI checks (--check): a page with less text than this is flagged.
MIN_WORDS = 50
FORMATS = ("jsonl", "md", "csv")


@dataclass
class ScrapedPage:
    url: str
    status: str                       # "ok", "blocked by robots.txt", "http 404", "not html", "error: ..."
    title: str = ""
    markdown: str = ""
    text: str = ""
    word_count: int = 0
    tables: list[Table] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fetched_at: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self, *, include_text: bool = True) -> dict:
        d = asdict(self)
        d["tables"] = [t.to_dict() for t in self.tables]
        if not include_text:
            d.pop("markdown")
            d.pop("text")
        return d


async def scrape_urls(
    urls: list[str],
    *,
    rate_limit: float,
    tables: bool = True,
    check: bool = False,
) -> list[ScrapedPage]:
    """Fetch each URL once, in order, and extract its content (and tables)."""
    import httpx

    from dataforge.collectors import HTTPClient
    from dataforge.utils import RateLimiter

    results: list[ScrapedPage] = []
    seen_content: dict[str, str] = {}  # content hash -> first URL with it
    async with HTTPClient(RateLimiter(rate_limit)) as client:
        for raw in urls:
            url = sanitise(raw)
            if not url:
                results.append(ScrapedPage(url=raw, status="error: not a valid http(s) URL"))
                continue
            now = datetime.now(UTC).isoformat(timespec="seconds")
            try:
                resp = await client.get(url)
            except PermissionError:
                results.append(ScrapedPage(url=url, status="blocked by robots.txt", fetched_at=now))
                continue
            except httpx.HTTPStatusError as exc:
                results.append(ScrapedPage(url=url, status=f"http {exc.response.status_code}",
                                           fetched_at=now))
                continue
            except Exception as exc:
                results.append(ScrapedPage(url=url, status=f"error: {type(exc).__name__}: {exc}",
                                           fetched_at=now))
                continue
            if "html" not in resp.headers.get("content-type", "html"):
                results.append(ScrapedPage(url=url, status="not html", fetched_at=now))
                continue
            content = extract(resp.text, url)
            page = ScrapedPage(
                url=url, status="ok", title=content.title, markdown=content.markdown,
                text=content.text, word_count=content.word_count,
                tables=extract_tables(resp.text) if tables else [], fetched_at=now,
            )
            if check:
                page.warnings = _checks(page, seen_content)
            results.append(page)
    return results


def _checks(page: ScrapedPage, seen: dict[str, str]) -> list[str]:
    """Cheap, rule-based quality checks. No LLM."""
    out = []
    if not page.text.strip():
        out.append("no text extracted (the page may be built by JavaScript)")
    elif page.word_count < MIN_WORDS:
        out.append(f"very little text ({page.word_count} words)")
    digest = hashlib.sha256(" ".join(page.text.split()).lower().encode()).hexdigest()
    if page.text.strip() and digest in seen:
        out.append(f"same text as {seen[digest]}")
    else:
        seen.setdefault(digest, page.url)
    return out


def write_outputs(pages: list[ScrapedPage], out_dir: Path,
                  formats: tuple[str, ...] = FORMATS) -> dict[str, list[str]]:
    """Write what was scraped. Returns the files written, by kind.

    - ``pages.jsonl``: one line per URL (status, title, text, markdown, tables)
    - ``page_NNN.md``: each page's Markdown
    - ``page_NNN_table_K.csv`` and ``.json``: each table
    - ``tables.jsonl``: every table, one per line, with its ``source_url``
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, list[str]] = {"pages": [], "markdown": [], "tables": []}
    if "jsonl" in formats:
        p = out_dir / "pages.jsonl"
        with p.open("w", encoding="utf-8") as f:
            for page in pages:
                f.write(json.dumps(page.to_dict(), ensure_ascii=False) + "\n")
        written["pages"].append(str(p))
    all_tables = []
    for n, page in enumerate(pages, 1):
        if not page.ok:
            continue
        if "md" in formats:
            p = out_dir / f"page_{n:03d}.md"
            p.write_text(f"<!-- {page.url} -->\n# {page.title}\n\n{page.markdown}\n", encoding="utf-8")
            written["markdown"].append(str(p))
        for k, table in enumerate(page.tables, 1):
            stem = f"page_{n:03d}_table_{k}"
            if "csv" in formats:
                p = out_dir / f"{stem}.csv"
                # utf-8-sig so spreadsheet apps (Excel) detect the encoding.
                p.write_text(table.to_csv(), encoding="utf-8-sig")
                written["tables"].append(str(p))
                j = out_dir / f"{stem}.json"
                j.write_text(json.dumps({"source_url": page.url, **table.to_dict(),
                                         "records": table.records()},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
                written["tables"].append(str(j))
            all_tables.append({"source_url": page.url, "table": k, **table.to_dict()})
    if all_tables and "jsonl" in formats:
        p = out_dir / "tables.jsonl"
        with p.open("w", encoding="utf-8") as f:
            for t in all_tables:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
        written["tables"].append(str(p))
    return written
