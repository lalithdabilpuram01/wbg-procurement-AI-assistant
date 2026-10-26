"""Values lost when a table row was detected only partly (P1 fix 2).

Root cause: pdfplumber's line-based table finder builds cells from ruling lines. When a continuation part of a table
has no top border above some columns (ISR 40089196 p. 5), the first row gets a cell only in the columns that are
closed by lines; the other cells come back as None and their printed values are silently dropped.

Detection uses page geometry and the original source words: words inside the row's vertical band that are not inside
any detected cell of the table (a cell of a neighbouring row may span this band) are lost content. A lost word is recovered only when it lies inside exactly one column
of the table grid (column x-ranges learned from the table's complete rows); otherwise the row is `values_missing`.
Words inside spanning (merged) cells are inside a detected cell and are never treated as lost.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Box = tuple[float, float, float, float]          # x0, top, x1, bottom
X_TOLERANCE = 1.5


@dataclass
class LostValuesResult:
    status: str                                  # unchanged | values_recovered | values_missing
    recovered: dict[int, str] = field(default_factory=dict)       # column index -> recovered text (from the words)
    recovered_boxes: dict[int, Box] = field(default_factory=dict)  # column index -> bbox of the recovered words
    unassigned_words: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def column_ranges(rows_cells: list[list[Box | None]], n_columns: int) -> list[tuple[float, float]] | None:
    """x-range of each column from rows where all n cells were detected (median edges); None if no such row."""
    complete = [cells for cells in rows_cells if len(cells) == n_columns and all(c is not None for c in cells)]
    if not complete:
        return None
    ranges = []
    for j in range(n_columns):
        x0s = sorted(cells[j][0] for cells in complete)
        x1s = sorted(cells[j][2] for cells in complete)
        ranges.append((x0s[len(x0s) // 2], x1s[len(x1s) // 2]))
    return ranges


def _inside(word: dict[str, Any], box: Box) -> bool:
    centre_x, centre_y = (word["x0"] + word["x1"]) / 2, (word["top"] + word["bottom"]) / 2
    return box[0] - X_TOLERANCE <= centre_x <= box[2] + X_TOLERANCE and box[1] - X_TOLERANCE <= centre_y <= box[3] + X_TOLERANCE


def lost_words(table_cells: list[Box], row_band: tuple[float, float], words: list[dict[str, Any]],
               table_x: tuple[float, float]) -> list[dict[str, Any]]:
    """Source words in the row band and table width that no detected cell of the table contains."""
    top, bottom = row_band
    return [w for w in words
            if top <= (w["top"] + w["bottom"]) / 2 <= bottom and table_x[0] <= w["x0"] and w["x1"] <= table_x[1]
            and not any(_inside(w, c) for c in table_cells)]


def assign_column(word: dict[str, Any], ranges: list[tuple[float, float]]) -> int | None:
    """The single column whose x-range fully contains the word; None if none or several (ambiguous)."""
    hits = [j for j, (x0, x1) in enumerate(ranges) if x0 - X_TOLERANCE <= word["x0"] and word["x1"] <= x1 + X_TOLERANCE]
    return hits[0] if len(hits) == 1 else None


def check_row(row_cells: list[Box | None], table_cells: list[Box], words: list[dict[str, Any]],
              ranges: list[tuple[float, float]] | None, table_box: Box) -> LostValuesResult:
    """Detect and, where unambiguous, recover values the detector dropped from a partly detected row.
    `table_cells` are all detected cells of the table (pdfplumber `Table.cells`)."""
    if all(c is not None for c in row_cells) or not any(c is not None for c in row_cells):
        return LostValuesResult("unchanged")
    detected = [c for c in row_cells if c is not None]
    band = (min(c[1] for c in detected), max(c[3] for c in detected))
    lost = lost_words(table_cells, band, words, (table_box[0], table_box[2]))
    if not lost:
        return LostValuesResult("unchanged")
    if ranges is None:
        return LostValuesResult("values_missing", unassigned_words=[w["text"] for w in lost],
                                reasons=["no complete row to learn the column grid from"])
    recovered: dict[int, list[dict[str, Any]]] = {}
    unassigned = []
    for word in lost:
        column = assign_column(word, ranges)
        if column is None or row_cells[column] is not None:
            unassigned.append(word["text"])
        else:
            recovered.setdefault(column, []).append(word)
    if unassigned:
        return LostValuesResult("values_missing", unassigned_words=unassigned + [w["text"] for ws in recovered.values() for w in ws],
                                reasons=["lost words without a unique, empty target column"])
    texts = {j: " ".join(w["text"] for w in sorted(ws, key=lambda w: (round(w["top"]), w["x0"]))) for j, ws in recovered.items()}
    boxes = {j: (min(w["x0"] for w in ws), min(w["top"] for w in ws), max(w["x1"] for w in ws), max(w["bottom"] for w in ws))
             for j, ws in recovered.items()}
    return LostValuesResult("values_recovered", recovered=texts, recovered_boxes=boxes,
                            reasons=["recovered from source words by column geometry"])
