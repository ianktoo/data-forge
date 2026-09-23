"""HTML tables as structured rows (#69).

A page's text keeps its tables as Markdown, which is fine for an LLM but not
for someone who wants the data itself (a list of schools, a price table).
:func:`extract_tables` turns every ``<table>`` into a header row plus data
rows, ready for CSV or JSON.

- Headers: the first row, when it is a header row: all ``<th>`` cells, or
  inside ``<thead>``, or (common on hand-made pages) every non-empty cell
  entirely bold (``<strong>``/``<b>``) while the next row is not. Otherwise
  there are no headers. Header names are made unique ("Phone", "Phone 2").
- ``colspan`` repeats a cell across the columns it spans; ``rowspan``
  carries it down into the rows below, so every row has every column.
- Nested tables are extracted on their own and do not add cells to the
  table around them.
- Tables used only for layout (a single row, or a single column with no
  header) are skipped.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

_WS = re.compile(r"\s+")
# Browsers cap colspan at 1000 and rowspan at 65534; a hostile page should
# not be able to make us allocate that much.
_MAX_SPAN = 100


@dataclass
class Table:
    caption: str
    headers: list[str]
    rows: list[list[str]] = field(default_factory=list)

    def records(self) -> list[dict[str, str]]:
        """Rows as dicts keyed by header (column_1, column_2, ... without headers)."""
        keys = self.headers or [f"column_{i + 1}" for i in range(self.width)]
        return [dict(zip(keys, row, strict=False)) for row in self.rows]

    @property
    def width(self) -> int:
        return max([len(self.headers), *(len(r) for r in self.rows)], default=0)

    def to_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        if self.headers:
            w.writerow(self.headers)
        w.writerows(self.rows)
        return buf.getvalue()

    def to_dict(self) -> dict:
        return {"caption": self.caption, "headers": self.headers, "rows": self.rows}


def _text(cell: Tag) -> str:
    # Nested tables are extracted separately; do not fold their text in here.
    for nested in cell.find_all("table"):
        nested.extract()
    return _WS.sub(" ", cell.get_text(" ", strip=True)).strip()


def _all_bold(cell: Tag) -> bool:
    text = _WS.sub("", cell.get_text(""))
    if not text:
        return True
    bold = "".join(b.get_text("") for b in cell.find_all(["strong", "b"])
                   if not b.find_parent(["strong", "b"]))
    return _WS.sub("", bold) == text


def _is_header_row(tr: Tag, cells: list[Tag]) -> bool:
    if not cells:
        return False
    if all(c.name == "th" for c in cells) or tr.find_parent("thead") is not None:
        return True
    return any(_WS.sub("", c.get_text("")) for c in cells) and all(_all_bold(c) for c in cells)


def _span(cell: Tag, attr: str) -> int:
    try:
        return max(1, min(int(str(cell.get(attr, "1")).strip() or 1), _MAX_SPAN))
    except ValueError:
        return 1


def _own_rows(table: Tag) -> list[Tag]:
    """<tr> elements of this table, not of tables nested inside it."""
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def _grid(rows: list[Tag]) -> tuple[list[list[str]], list[bool]]:
    """Expand colspan/rowspan into a rectangular grid of cell text, and note
    which rows are all header cells."""
    grid: list[list[str]] = []
    header_row: list[bool] = []
    carry: dict[int, tuple[str, int]] = {}  # column -> (text, rows still to fill)
    for tr in rows:
        cells = [c for c in tr.find_all(["td", "th"]) if c.find_parent("tr") is tr]
        if not cells and not carry:
            continue
        out: list[str] = []
        col = 0

        def fill_carried() -> None:
            nonlocal col
            while col in carry:
                text, left = carry[col]
                out.append(text)
                if left <= 1:
                    del carry[col]
                else:
                    carry[col] = (text, left - 1)
                col += 1

        for cell in cells:
            fill_carried()
            text = _text(cell)
            for _ in range(_span(cell, "colspan")):
                out.append(text)
                rs = _span(cell, "rowspan")
                if rs > 1:
                    carry[col] = (text, rs - 1)
                col += 1
        fill_carried()
        grid.append(out)
        header_row.append(_is_header_row(tr, cells))
    return grid, header_row


def _unique(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for i, n in enumerate(names):
        n = n or f"column_{i + 1}"
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n} {seen[n]}")
    return out


def extract_tables(html: str) -> list[Table]:
    """Every data table on the page, in document order."""
    soup = BeautifulSoup(html, "lxml")
    tables: list[Table] = []
    for t in soup.find_all("table"):
        grid, is_header = _grid(_own_rows(t))
        grid_rows = [r for r in grid if any(c for c in r)]
        if not grid_rows:
            continue
        headers: list[str] = []
        # A bold first row is a header only if the rows under it are not all
        # bold too (a table of all-bold text has no header).
        if is_header and is_header[0] and not (len(is_header) > 1 and all(is_header)):
            headers = grid[0]
            data = [r for r, h in zip(grid[1:], is_header[1:], strict=False) if not h and any(r)]
        else:
            data = grid_rows
        if headers:
            # The header row defines the columns; a stray or oversized
            # colspan further down must not widen the table.
            width = len(headers)
            data = [r[:width] for r in data]
        else:
            width = max((len(r) for r in data), default=0)
        if len(data) < 1 or (len(data) < 2 and not headers) or (width < 2 and not headers):
            continue  # a layout table, not data
        data = [r + [""] * (width - len(r)) for r in data]
        headers = _unique(headers + [""] * (width - len(headers))) if headers else []
        cap = t.find("caption")
        caption = _WS.sub(" ", cap.get_text(" ", strip=True)) if cap else ""
        tables.append(Table(caption=caption, headers=headers, rows=data))
    return tables
