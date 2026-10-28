"""Extracted table parts -> logical tables ready for a relational store.

Rules (each one answers a pattern seen in the pilot PDFs):
  * continuation parts (same `continuation_group`) are joined in page order under the first part's header;
  * header rows and repeated header rows inside the data are dropped (`columns` already merges multi-row headers);
  * an unnamed column (col_N) is merged into the named column on its left when they never both hold a value
    (text shifted into a spacer column), and columns that are always empty are dropped;
  * stacked cells ("340.00\\n18.00\\n12.00") are split into separate rows when every multi-line cell of the row has
    the same number of lines; otherwise the row is kept and flagged `stacked`;
  * a row with a value only in its first column (a section heading such as "Component 1 ...") is not data: its text
    is carried down as the `_group` of the following rows;
  * a column is numeric (or a date) when at least 70 % of its non-empty values parse as such;
  * a table is "structured" when at least two (and at least half) of its columns are named and most rows fill
    two or more of them;
    otherwise it is "text" (layout grids, narrative matrices) and is not offered for SQL.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

UNNAMED = re.compile(r"^col_\d+$")
NUMBER = re.compile(r"^\(?-?(?:US\$|\$|USD)?\s*\d[\d,]*(?:\.\d+)?\s*%?\)?$", re.I)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PLACEHOLDER = re.compile(r"^(?:-+|–|—|n/?a|nil|\.+)$", re.I)   # an empty-looking cell in a number column
MAX_HEADER_ROWS = 4
STRUCTURED_ROW_SHARE = 0.6


@dataclass
class Column:
    header: str                    # the header as printed (merged across header rows by the extraction)
    sql_name: str = ""
    type: str = "text"             # text | number | date
    samples: list[str] = field(default_factory=list)


@dataclass
class Row:
    cells: list[str]
    page: int
    group: str | None = None
    quality: str = "ok"            # ok | stacked (values that could not be split reliably)


@dataclass
class LogicalTable:
    table_id: str                  # id of the first part
    document_id: str
    pages: list[int]
    section: str
    caption: str | None
    kind: str                      # extraction kind: generic | step_plan | staff
    columns: list[Column]
    rows: list[Row]
    structure: str = "structured"  # structured | text
    part_ids: list[str] = field(default_factory=list)


def _jsonl(path: Path) -> Iterable[dict]:
    with path.open() as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def load_parts(shared: Path, documents: Iterable[str]) -> tuple[list[dict], dict[str, list[dict]]]:
    wanted = set(documents)
    tables = [t for t in _jsonl(shared / "tables.jsonl") if t["document_id"] in wanted]
    rows: dict[str, list[dict]] = {}
    for r in _jsonl(shared / "table_rows.jsonl"):
        if r["document_id"] in wanted:
            rows.setdefault(r["table_id"], []).append(r)
    return tables, rows


def assemble(tables: list[dict], rows: dict[str, list[dict]]) -> list[LogicalTable]:
    groups: dict[str, list[dict]] = {}
    for t in sorted(tables, key=lambda t: (t["document_id"], t["page"], t["index_on_page"])):
        groups.setdefault(t["continuation_group"] or t["table_id"], []).append(t)
    return [build(parts, rows) for parts in groups.values()]


def build(parts: list[dict], rows: dict[str, list[dict]]) -> LogicalTable:
    first = parts[0]
    headers = [h for h in first["columns"]]
    data: list[Row] = []
    header_set = {h.strip().lower() for h in headers if h and not UNNAMED.match(h)}
    for part in parts:
        for r in sorted(rows.get(part["table_id"], []), key=lambda r: r["row_index"]):
            cells = (r["cells_clean"] + [""] * len(headers))[:len(headers)]
            if r["is_header"] or _is_repeated_header(cells, header_set):
                continue
            data.extend(_unstack(cells, r["page"]))
    headers, data = _absorb_header_rows(headers, data)
    columns, data = _clean_columns(headers, data)
    data = _carry_groups(data)
    _type_columns(columns, data)
    table = LogicalTable(table_id=first["table_id"], document_id=first["document_id"],
                         pages=sorted({p["page"] for p in parts}), section=first.get("section_path") or "",
                         caption=first.get("caption"), kind=first["kind"], columns=columns, rows=data,
                         part_ids=[p["table_id"] for p in parts])
    table.structure = _structure(table)
    return table


def _is_repeated_header(cells: list[str], header_set: set[str]) -> bool:
    filled = [c.strip().lower() for c in cells if c.strip()]
    return len(filled) >= 2 and sum(c in header_set for c in filled) >= max(2, len(filled) - 1)


NUMBERED_ITEM = re.compile(r"(?=(?:^|\s)\d{1,2}\.\s)")
TRAILING_TOTAL = re.compile(r"\s(?=Total\b)")


def _unstack(cells: list[str], page: int) -> list[Row]:
    counts = {len(c.split("\n")) for c in cells if "\n" in c}
    if not counts:
        return [Row(cells=[c.strip() for c in cells], page=page)]
    if len(counts) > 1:
        return [Row(cells=[" ".join(c.split()) for c in cells], page=page, quality="stacked")]
    k = counts.pop()
    labels = _split_labels(cells[0], k) if "\n" not in cells[0] else None
    out = []
    for i in range(k):
        row = [(c.split("\n")[i] if "\n" in c else "").strip() for c in cells]
        quality = "ok"
        if labels is not None:
            row[0], quality = labels[i]
        out.append(Row(cells=row, page=page, quality=quality))
    return out


def _split_labels(text: str, k: int) -> list[tuple[str, str]]:
    """Labels for k stacked values from one label cell: numbered items ("1. ... 2. ...") are matched to the first
    values; what follows the last item from "Total" on cannot be matched reliably, so those rows are flagged."""
    items = [p.strip() for p in NUMBERED_ITEM.split(text) if p.strip()]
    if len(items) < 2:
        return [(text.strip() if i == 0 else "", "ok" if i == 0 else "stacked") for i in range(k)]
    parts = TRAILING_TOTAL.split(items[-1], maxsplit=1)
    items[-1] = parts[0]
    trailing = parts[1] if len(parts) > 1 else ""
    return [(items[i], "ok") if i < len(items) else (trailing, "stacked") for i in range(k)]


def _absorb_header_rows(headers: list[str], data: list[Row]) -> tuple[list[str], list[Row]]:
    """Leading rows with no label and no number are the rest of a header printed over several lines
    ('Actual at' / 'Project' / 'Closing' / '(US$M)'): their words join the column names. The first such row fills
    at least two cells (a section title above the rows fills one)."""
    count = 0
    for r in data[:MAX_HEADER_ROWS]:
        filled = [c for c in r.cells if c.strip()]
        if r.cells[0].strip() or len(filled) < (1 if count else 2) or any(parse_number(c) is not None for c in filled):
            break
        count += 1
    if not count or count == len(data):
        return headers, data
    merged = []
    for i, header in enumerate(headers):
        words = [r.cells[i].strip() for r in data[:count] if r.cells[i].strip()]
        base = [] if UNNAMED.match(header or "") else [header]
        merged.append(" ".join(base + words) if words else header)
    return merged, data[count:]


def _clean_columns(headers: list[str], data: list[Row]) -> tuple[list[Column], list[Row]]:
    keep: list[int] = []
    for i, header in enumerate(headers):
        values = [r.cells[i] for r in data]
        if not any(values):
            continue                                            # always empty: a spacer
        if UNNAMED.match(header or "") and keep and all(not (r.cells[keep[-1]] and r.cells[i]) for r in data):
            for r in data:                                      # shifted text: move it into the named column
                if r.cells[i]:
                    r.cells[keep[-1]] = r.cells[i]
            continue
        following = i + 1
        if UNNAMED.match(header or "") and following < len(headers) and not UNNAMED.match(headers[following] or "") \
                and all(not (r.cells[i] and r.cells[following]) for r in data):
            for r in data:                                      # values printed left of their header: move them right
                if r.cells[i]:
                    r.cells[following] = r.cells[i]
            continue
        keep.append(i)
    columns = [Column(header=headers[i] or f"column {n + 1}") for n, i in enumerate(keep)]
    for r in data:
        r.cells = [r.cells[i] for i in keep]
    used: set[str] = set()
    for c in columns:
        c.sql_name = _sql_name(c.header, used)
    return columns, data


def _sql_name(header: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")[:40] or "column"
    if base[0].isdigit():
        base = f"c_{base}"
    name, n = base, 2
    while name in used or name.startswith("_"):
        name, n = f"{base}_{n}", n + 1
    used.add(name)
    return name


def _carry_groups(data: list[Row]) -> list[Row]:
    out, group = [], None
    for r in data:
        filled = [c for c in r.cells if c]
        if len(r.cells) >= 3 and len(filled) == 1 and r.cells[0]:
            group = r.cells[0][:200]
            continue
        r.group = group
        out.append(r)
    return out


def parse_number(text: str) -> float | None:
    value = text.strip()
    if not value or not NUMBER.match(value):
        return None
    negative = value.startswith("(") and value.endswith(")") or value.startswith("-")
    digits = re.sub(r"[^\d.]", "", value)
    try:
        number = float(digits)
    except ValueError:
        return None
    return -number if negative else number


def _type_columns(columns: list[Column], data: list[Row]) -> None:
    for i, column in enumerate(columns):
        values = [r.cells[i] for r in data if r.cells[i]]
        column.samples = list(dict.fromkeys(v[:60] for v in values))[:3]
        if not values:
            continue
        real = [v for v in values if not PLACEHOLDER.match(v.strip())]
        if not real:
            continue
        if all(DATE.match(v) for v in real):
            column.type = "date"
        elif all(parse_number(v) is not None for v in real):    # a word in a number column would be lost
            column.type = "number"


def _structure(table: LogicalTable) -> str:
    named = [c for c in table.columns if not UNNAMED.match(c.header)]
    if len(named) < 2 or len(named) < len(table.columns) / 2 or not table.rows:
        return "text"                                            # SQL over unnamed columns would be guesswork
    filled = sum(1 for r in table.rows if sum(bool(c) for c in r.cells) >= 2)
    return "structured" if filled / len(table.rows) >= STRUCTURED_ROW_SHARE else "text"
