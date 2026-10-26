"""Stacked numeric values in one table cell (P1 fix 1).

Root cause: pdfplumber returns a cell's lines joined with '\\n'. When a table has no row rules, several logical rows
share one cell ('340.00\\n18.00\\n12.00'). The active cleaner (`cell_clean.clean_cell`) closes every line break between
a line ending in a digit and a line starting with a digit, which is right for ONE number wrapped in a narrow column
('9,890,912.0\\n7') but glues independent values ('340.0018.0012.00').

This module:
* `clean_cell_lines` - cleans like `clean_cell` (it reuses it per segment) but keeps a line boundary between two lines
  that are each a complete value, so independent values are never concatenated;
* `split_stacked_row` - splits a row into sub-rows only when the alignment of the stacked values can be proven from the
  page geometry; otherwise the row is kept (with line boundaries) and flagged `needs_review`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .cell_clean import clean_cell

# A complete value on its own line: a number (optional sign/parentheses/currency, well-formed thousands groups, decimals,
# %) or a date (2020-11-20, 12-Aug-2019, 12/08/2019) - the same gluing defect hits stacked dates.
NUMBER_LINE = r"\(?[-–]?\s?(?:US\$|\$|€|£)?\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?\s?%?"
DATE_LINE = r"\d{4}-\d{2}-\d{2}|\d{1,2}-[A-Z][a-z]{2}-\d{4}|\d{1,2}/\d{1,2}/\d{2,4}"
VALUE_LINE = re.compile(rf"^(?:{NUMBER_LINE}|{DATE_LINE})$")
CONNECTOR_END = re.compile(r"(?:\b(?:and|or|of|the|to|for|in|with|&)|[,;:/-])$", re.I)
TOP_TOLERANCE = 2.5          # points: a text line and a value line are on the same baseline


def is_value_line(text: str) -> bool:
    return bool(VALUE_LINE.fullmatch(text.strip()))


def is_wrap(left: str, right: str) -> bool:
    """True when `right` continues ONE number wrapped from `left` (and the joined text is a well-formed number):
    an incomplete left part ('140,348,30', '290,000,000.', '1,') or a decimal finished by digits ('9,890,912.0'+'7').
    Two complete values ('57' / '37', '340.00' / '18.00', '0.0' / '100.0') are NOT a wrap."""
    left, right = left.strip(), right.strip()
    joined = left + right
    if not VALUE_LINE.fullmatch(joined):
        return False
    if not VALUE_LINE.fullmatch(left):
        return True                                   # left is a fragment: '140,348,30', '290,000,000.'
    decimals = re.search(r"\.(\d+)$", left)
    return bool(decimals) and len(decimals.group(1)) == 1 and re.fullmatch(r"\d", right) is not None


def stacked_boundaries(lines: list[str]) -> list[int]:
    """Indices i where the break between lines[i] and lines[i+1] separates two independent values."""
    return [i for i in range(len(lines) - 1)
            if is_value_line(lines[i]) and is_value_line(lines[i + 1]) and not is_wrap(lines[i], lines[i + 1])]


@dataclass
class CleanedCell:
    """A cell cleaned without gluing stacked values: segments (each cleaned by the active `clean_cell`) and the index of
    each segment's first source line. `text` joins the segments with '\\n' (one segment = same text as `clean_cell`)."""

    text: str
    segments: list[str]
    starts: list[int]
    closed: int
    stacked: bool


def clean_cell_lines(raw: str | None, vocab: set[str]) -> CleanedCell:
    """Clean a cell like `clean_cell`, but keep a line boundary wherever two independent values were stacked."""
    if not raw:
        return CleanedCell("", [], [], 0, False)
    lines = [p.strip() for p in raw.replace("\r", "").split("\n") if p.strip()]
    cuts = stacked_boundaries(lines)
    if not cuts:
        text, closed = clean_cell(raw, vocab)
        return CleanedCell(text, [text] if text else [], [0] if text else [], closed, False)
    segments, starts, start, closed = [], [], 0, 0
    for cut in cuts + [len(lines) - 1]:
        text, n = clean_cell("\n".join(lines[start:cut + 1]), vocab)
        segments.append(text)
        starts.append(start)
        closed += n
        start = cut + 1
    return CleanedCell("\n".join(segments), segments, starts, closed, True)


# ------------------------------------------------------------------------------------------------- row splitting
@dataclass
class CellLines:
    """One cell of a row for splitting: `segments` are the stacked values (stacked cell) or the source lines (other
    cells), `tops` the top coordinate of each segment's first source line, `text` the whole cell as one value."""

    text: str
    segments: list[str]
    tops: list[float]
    stacked: bool                                     # independent values stacked in this cell


@dataclass
class SplitResult:
    status: str                                       # split_aligned | needs_review
    sub_rows: list[list[str]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _index_tops(columns: list[CellLines]) -> list[float] | None:
    """Representative top per value index, if every stacked column has the same count and the i-th lines of all columns
    lie within half the smallest gap between neighbouring indices (alignment proven by geometry)."""
    counts = {len(c.tops) for c in columns}
    if len(counts) != 1:
        return None
    n = counts.pop()
    by_index = [[c.tops[i] for c in columns] for i in range(n)]
    centres = [sorted(t)[len(t) // 2] for t in by_index]
    if any(b <= a for a, b in zip(centres, centres[1:])):
        return None
    spreads = [max(t) - min(t) for t in by_index]
    gaps = [b - a for a, b in zip(centres, centres[1:])]
    if gaps and max(spreads) >= 0.5 * min(gaps):
        return None
    return centres


def _text_column_split(cell: CellLines, centres: list[float]) -> list[str] | None:
    """One line per index on the same baseline, none of them a wrapped continuation; otherwise None (ambiguous)."""
    if len(cell.segments) != len(centres):
        return None
    if any(abs(top - centre) > TOP_TOLERANCE for top, centre in zip(cell.tops, centres)):
        return None
    for previous, current in zip(cell.segments, cell.segments[1:]):
        if CONNECTOR_END.search(previous) or current[:1].islower():
            return None
    return list(cell.segments)


def split_stacked_row(cells: list[CellLines], sanitise: Callable[[str], str]) -> SplitResult:
    """Split a row into one sub-row per stacked value only when every non-empty column maps 1:1 onto the aligned values.
    If any column cannot be mapped (different line count, other baseline, wrapped phrase), nothing is split: the row
    keeps its line-preserved cells and is flagged needs_review with the reasons."""
    stacked = [c for c in cells if c.stacked]
    centres = _index_tops(stacked) if stacked else None
    if centres is None:
        return SplitResult("needs_review", reasons=["stacked_values_unaligned"])
    n = len(centres)
    columns: list[list[str]] = []
    reasons: list[str] = []
    for index, cell in enumerate(cells):
        if cell.stacked:
            columns.append(list(cell.segments))
        elif not cell.segments:
            columns.append([""] * n)
        else:
            parts = _text_column_split(cell, centres)
            if parts is None:
                reasons.append(f"col{index + 1}_alignment_ambiguous")
            else:
                columns.append(parts)
    if reasons:
        return SplitResult("needs_review", reasons=reasons)
    return SplitResult("split_aligned", [[sanitise(column[i]) for column in columns] for i in range(n)])
