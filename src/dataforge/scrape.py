"""Quick scrape: fetch pages and keep their content and tables. No AI (#69).

The whole pipeline turns a site into a fine-tuning dataset. Often only the
first step is wanted: the text of a few pages, or the table on one of them.
:func:`scrape_urls` does just that, with the same politeness as every other
request DataForge makes (robots.txt, per-domain rate limit, ``Crawl-delay``)
and no LLM, API key, session or database.

Used by ``dataforge scrape`` and the MCP ``scrape_page`` tool. A folder it
wrote can later be the input of a dataset run (:func:`load_scrape_dir`,
:func:`import_into_session`), which then starts at processing.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dataforge.collectors.extractor import extract
from dataforge.collectors.tables import Table, extract_tables
from dataforge.utils.url_sanitiser import sanitise

# Non-AI checks (--check): a page with less text than this is flagged.
MIN_WORDS = 50
FORMATS = ("jsonl", "md", "csv")

# progress(index, total, url, page): called with page=None just before a URL
# is fetched, then again with its result, so a long list shows activity.
Progress = Callable[[int, int, str, "ScrapedPage | None"], None]


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
    progress: Progress | None = None,
) -> list[ScrapedPage]:
    """Fetch each URL once, in order, and extract its content (and tables)."""
    from dataforge.collectors import HTTPClient
    from dataforge.utils import RateLimiter

    results: list[ScrapedPage] = []
    seen_content: dict[str, str] = {}  # content hash -> first URL with it
    async with HTTPClient(RateLimiter(rate_limit)) as client:
        total = len(urls)
        for i, raw in enumerate(urls, 1):
            if progress:
                progress(i, total, raw, None)
            page = await _scrape_one(client, raw, tables, check, seen_content)
            results.append(page)
            if progress:
                progress(i, total, page.url, page)
    return results


async def _scrape_one(client, raw: str, tables: bool, check: bool,
                      seen_content: dict[str, str]) -> ScrapedPage:
    import httpx

    url = sanitise(raw)
    if not url:
        return ScrapedPage(url=raw, status="error: not a valid http(s) URL")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        resp = await client.get(url)
    except PermissionError:
        return ScrapedPage(url=url, status="blocked by robots.txt", fetched_at=now)
    except httpx.HTTPStatusError as exc:
        return ScrapedPage(url=url, status=f"http {exc.response.status_code}", fetched_at=now)
    except Exception as exc:
        return ScrapedPage(url=url, status=f"error: {type(exc).__name__}: {exc}", fetched_at=now)
    if "html" not in resp.headers.get("content-type", "html"):
        return ScrapedPage(url=url, status="not html", fetched_at=now)
    content = extract(resp.text, url)
    page = ScrapedPage(
        url=url, status="ok", title=content.title, markdown=content.markdown,
        text=content.text, word_count=content.word_count,
        tables=extract_tables(resp.text) if tables else [], fetched_at=now,
    )
    if check:
        page.warnings = _checks(page, seen_content)
    return page


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


# ── A scrape folder as dataset input ──────────────────────────────────────────

def _has_pages(d: Path) -> bool:
    return (d / "pages.jsonl").is_file() or any(d.glob("page_*.md"))


def resolve_scrape_dir(path: str | Path) -> Path:
    """The scrape folder ``path`` points at, absolute.

    Accepts the folder itself, a file inside it (``pages.jsonl``, a
    ``page_NNN.md``), or a folder of scrape runs such as ``scrape/``, where
    the newest run with pages is used. Raises ``ValueError`` with a message
    that names the full location looked at and what was expected there.
    """
    p = Path(path).expanduser()
    full = p.resolve()
    if not p.exists():
        return _fail(f"Not found: {full}")
    if p.is_file():
        if p.name == "pages.jsonl" or (p.name.startswith("page_") and p.suffix == ".md"):
            return full.parent
        return _fail(f"{full} is a file. Point to the scrape folder that holds pages.jsonl "
                     "or page_NNN.md files.")
    if _has_pages(p):
        return full
    runs = sorted((d for d in p.iterdir() if d.is_dir() and _has_pages(d)), reverse=True)
    if runs:
        return runs[0].resolve()
    return _fail(f"No scraped pages in {full}. Expected pages.jsonl or page_NNN.md files "
                 "in this folder or in one of its subfolders (a `dataforge scrape` run "
                 "writes them to scrape/<date-time>/ by default).")


def _fail(msg: str) -> Path:
    raise ValueError(msg)


def load_scrape_dir(folder: Path) -> list[ScrapedPage]:
    """Read back the pages a scrape saved in ``folder``. Only pages with text.

    Uses ``pages.jsonl`` when it is there; otherwise the ``page_NNN.md``
    files (a scrape saved as Markdown only), whose first line holds the URL.
    """
    folder = Path(folder)
    jsonl = folder / "pages.jsonl"
    pages: list[ScrapedPage] = []
    if jsonl.is_file():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("status") != "ok" or not (d.get("markdown") or d.get("text")):
                continue
            pages.append(ScrapedPage(
                url=d.get("url", ""), status="ok", title=d.get("title", ""),
                markdown=d.get("markdown") or d.get("text", ""), text=d.get("text", ""),
                word_count=d.get("word_count", 0), fetched_at=d.get("fetched_at", ""),
            ))
        return pages
    for md in sorted(folder.glob("page_*.md")):
        body = md.read_text(encoding="utf-8")
        first, _, rest = body.partition("\n")
        url = first.removeprefix("<!--").removesuffix("-->").strip() if first.startswith("<!--") else ""
        if not url:
            continue
        rest = rest.lstrip("\n")
        title = ""
        if rest.startswith("# "):
            title, _, rest = rest.partition("\n")
            title = title[2:].strip()
        text = rest.strip()
        if text:
            pages.append(ScrapedPage(url=url, status="ok", title=title, markdown=text,
                                     text=text, word_count=len(text.split())))
    return pages


def import_into_session(ctx, pages: list[ScrapedPage]) -> list[int]:
    """Store scraped pages as a session's collection, so the run starts at processing.

    Writes each page's Markdown where the scraper would (``sessions/<id>/raw``)
    and adds the URL and page rows the later stages and ``resume`` read.
    Returns the new page ids, which are also set on ``ctx``.
    """
    from dataforge.storage import DiscoveredURL, URLSource, open_session
    from dataforge.storage import ScrapedPage as PageRow

    raw_dir = ctx.session_dir() / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
    ids: list[int] = []
    with open_session(ctx.settings.db_path) as db:
        for i, page in enumerate(pages):
            raw_path = raw_dir / f"page_{i:05d}.md"
            raw_path.write_text(page.markdown or page.text, encoding="utf-8")
            url_row = DiscoveredURL(session_id=ctx.session_id, url=page.url,
                                    source=URLSource.file, selected=True, scraped=True,
                                    http_status=200)
            db.add(url_row)
            db.flush()
            row = PageRow(session_id=ctx.session_id, url_id=url_row.id or 0, url=page.url,
                          title=page.title, raw_path=str(raw_path), word_count=page.word_count)
            db.add(row)
            db.flush()
            ids.append(row.id)
        db.commit()
    urls = [p.url for p in pages]
    ctx.discovered_urls = list(urls)
    ctx.selected_urls = list(urls)
    ctx.scraped_page_ids = ids
    return ids
