"""Shadow (experimental) table extraction with the P1 fixes, over the pilot. Never changes the active pipeline's outputs.

    /opt/anaconda3/bin/python3 -B -m rag.ingestion_p1.shadow_run          (from procurement-intelligence/)

Reads (read-only): pilot_v2/shared/{document_versions,tables,table_rows}.jsonl, pilot_v2/reference/vocab.json and the
pilot PDFs. Tables are re-detected with the pipeline's pdfplumber settings and numbering. Writes only
rag/corpus_v3/processed/experimental_p1/:
  table_rows.jsonl   every pilot_v2 row; unaffected rows verbatim, affected rows with P1 cells and a `p1` block
  changes.jsonl      one record per affected row (pilot_v2 cells vs P1 cells, status, reasons)
  summary.json       counts
Only sanitised text is written. Staff tables are never modified and no word of them is written (counts only).
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber

from rag.ingestion.cell_clean import clean_cell
from rag.ingestion.sanitize import sanitize

from .lost_values import LostValuesResult, check_row, column_ranges
from .stacked_values import CellLines, SplitResult, clean_cell_lines, split_stacked_row

REPO = Path(__file__).resolve().parents[2]
PILOT = REPO / "rag" / "corpus_v3" / "processed" / "pilot_v2"
OUT = REPO / "rag" / "corpus_v3" / "processed" / "experimental_p1"
SEGMENT_SEP = " ¦ "             # stands in for a preserved line boundary while the table text is sanitised
MASKS = ("[PERSON]", "[EMAIL]", "[PHONE]")
LINE_TOLERANCE = 3.0


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def mask_count(cells: list[str]) -> int:
    return sum(c.count(m) for c in cells for m in MASKS)


# ---------------------------------------------------------------------------------------------- geometry helpers
def line_tops(box: tuple[float, float, float, float] | None, words: list[dict[str, Any]]) -> list[float]:
    """Top of each text line inside a cell (words grouped by baseline), top to bottom."""
    if box is None:
        return []
    inside = sorted((w for w in words if box[0] <= (w["x0"] + w["x1"]) / 2 <= box[2] and box[1] <= (w["top"] + w["bottom"]) / 2 <= box[3]),
                    key=lambda w: w["top"])
    tops: list[float] = []
    for w in inside:
        if not tops or w["top"] - tops[-1] > LINE_TOLERANCE:
            tops.append(w["top"])
    return tops


def cell_lines(raw: str | None, cleaned, box, words, vocab: set[str]) -> CellLines | None:
    """CellLines for splitting, or None when the source lines cannot be matched to the page geometry."""
    lines = [p.strip() for p in (raw or "").replace("\r", "").split("\n") if p.strip()]
    tops = line_tops(box, words)
    if len(tops) != len(lines):
        return None
    if cleaned.stacked:
        return CellLines(cleaned.text, cleaned.segments, [tops[i] for i in cleaned.starts], True)
    return CellLines(cleaned.text, [clean_cell(line, vocab)[0] for line in lines], tops, False)


# -------------------------------------------------------------------------------------------------- per table
@dataclass
class RowFix:
    row_index: int
    statuses: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    stacked_columns: list[int] = field(default_factory=list)
    split: SplitResult | None = None
    lost: LostValuesResult | None = None


def sanitise_table(values: list[list[list[str]]]) -> list[list[list[str]]]:
    """Sanitise the whole table at once (as the active `_sanitise_table` does for non-staff tables), keeping segment
    boundaries. values[row][col] = segments; returns the same shape, sanitised."""
    text = "\n".join(" | ".join(SEGMENT_SEP.join(segs) for segs in row) for row in values)
    safe = sanitize(text, staff_rows=False)[0].split("\n")
    if len(safe) != len(values):
        raise ValueError("sanitisation changed the row structure")
    out = []
    for row, line in zip(values, safe):
        cells = line.split(" | ")
        if len(cells) != len(row):
            raise ValueError("sanitisation changed the cell structure")
        out.append([c.split(SEGMENT_SEP) if c else [] for c in cells])
    return out


def process_table(table, found, page, words, vocab: set[str], pilot_rows: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Return (output rows, change records, stats) for one non-staff table."""
    data = found.extract()
    ncols = max(len(r) for r in data)
    row_boxes = [r.cells for r in found.rows]
    ranges = column_ranges(row_boxes, ncols)
    stats: Counter = Counter()
    cleaned = [[clean_cell_lines(c, vocab) for c in row] for row in data]
    fixes: dict[int, RowFix] = {}

    for ri, row in enumerate(data, start=1):
        fix = RowFix(ri)
        fix.stacked_columns = [j for j, c in enumerate(cleaned[ri - 1]) if c.stacked]
        if fix.stacked_columns:
            boxes = row_boxes[ri - 1] if ri - 1 < len(row_boxes) else [None] * len(row)
            lines = [cell_lines(raw, c, boxes[j] if j < len(boxes) else None, words, vocab) for j, (raw, c) in enumerate(zip(row, cleaned[ri - 1]))]
            if any(cl is None and raw for cl, raw in zip(lines, row)):
                fix.split = SplitResult("needs_review", reasons=["line_geometry_unavailable"])
            else:
                fix.split = split_stacked_row([cl or CellLines("", [], [], False) for cl in lines], lambda s: s)
            fix.statuses.append("stacked_values_preserved")
            fix.statuses.append(fix.split.status)
            fix.reasons += fix.split.reasons
        if ri - 1 < len(row_boxes):
            lost = check_row(row_boxes[ri - 1], found.cells, words, ranges, found.bbox)
            below = row_boxes[ri] if ri < len(row_boxes) else None
            for j in lost.recovered:
                if below is not None and below[j] is None:          # the value may span the next row too: not copied down
                    lost.reasons.append(f"col{j + 1}_possible_row_span")
            if lost.status != "unchanged":
                fix.lost = lost
                fix.statuses += [lost.status] + (["needs_review"] if lost.status == "values_recovered" else [])
                fix.reasons += lost.reasons
        if fix.statuses:
            fixes[ri] = fix

    if not fixes:
        return [dict(r, p1={"status": "unchanged"}) for r in pilot_rows], [], stats

    # P1 cell values (segments), recovered text read from the source characters of the recovered words
    values = [[list(c.segments) for c in row] + [[] for _ in range(ncols - len(row))] for row in cleaned]
    for ri, fix in fixes.items():
        if fix.lost and fix.lost.status == "values_recovered":
            for j, box in fix.lost.recovered_boxes.items():
                text = page.within_bbox((box[0] - 0.5, box[1] - 0.5, box[2] + 0.5, box[3] + 0.5)).extract_text() or fix.lost.recovered[j]
                values[ri - 1][j] = [clean_cell(text, vocab)[0]]
    safe = sanitise_table(values)

    out_rows, changes = [], []
    for pr in pilot_rows:
        ri = pr["row_index"]
        new_cells = ["\n".join(segs) for segs in safe[ri - 1]]
        if ri not in fixes:
            if new_cells != pr["cells_clean"]:
                stats["unaffected_row_resanitised_differently"] += 1        # self-check: must stay 0
            out_rows.append(dict(pr, p1={"status": "unchanged"}))
            continue
        fix = fixes[ri]
        p1: dict[str, Any] = {"status": sorted(set(fix.statuses)), "reasons": fix.reasons, "needs_review": "needs_review" in fix.statuses}
        if fix.stacked_columns:
            p1["stacked_columns"] = fix.stacked_columns
            p1["cells_lines"] = [list(segs) for segs in safe[ri - 1]]
            if fix.split and fix.split.status == "split_aligned":
                n = len(fix.split.sub_rows)
                p1["sub_rows"] = [[segs[k] if len(segs) == n else "" for segs in safe[ri - 1]] for k in range(n)]
        if fix.lost:
            p1["lost_values"] = {"status": fix.lost.status, "columns": sorted(fix.lost.recovered),
                                 "unassigned_word_count": len(fix.lost.unassigned_words), "provenance": "pdf_source_words"}
        if mask_count(new_cells) < mask_count(pr["cells_clean"]):
            stats["sanitisation_guard_fallback"] += 1                       # fail closed: keep the pilot_v2 cells
            new_cells, p1 = pr["cells_clean"], {**p1, "status": ["sanitisation_guard_fallback"], "needs_review": True}
            p1.pop("cells_lines", None)
            p1.pop("sub_rows", None)
        out_rows.append(dict(pr, cells_clean=new_cells, p1=p1))
        changes.append({"table_id": pr["table_id"], "row_index": ri, "page": pr["page"], "pilot_v2_cells": pr["cells_clean"],
                        "p1_cells": new_cells, **{k: v for k, v in p1.items() if k != "cells_lines"}})
        for s in set(fix.statuses):
            stats[s] += 1
    return out_rows, changes, stats


# ------------------------------------------------------------------------------------------------------- driver
def run(out_dir: Path = OUT) -> dict[str, Any]:
    versions = jsonl(PILOT / "shared" / "document_versions.jsonl")
    tables = {t["table_id"]: t for t in jsonl(PILOT / "shared" / "tables.jsonl")}
    rows_by_table: dict[str, list[dict]] = {}
    for r in jsonl(PILOT / "shared" / "table_rows.jsonl"):
        rows_by_table.setdefault(r["table_id"], []).append(r)
    vocab = set(json.loads((PILOT / "reference" / "vocab.json").read_text())["words"])

    out_rows, changes, totals = [], [], Counter()
    staff_lost_rows = 0
    for version in versions:
        did = version["document_id"]
        with pdfplumber.open(REPO / version["local_path"]) as pdf:
            for pno, page in enumerate(pdf.pages, start=1):
                words = None
                for ti, found in enumerate(page.find_tables(), start=1):        # numbered like the pipeline
                    tid = f"{did}:t{pno:03d}{ti:02d}"
                    if tid not in tables:
                        continue
                    pilot_rows = sorted(rows_by_table[tid], key=lambda r: r["row_index"])
                    words = words if words is not None else page.extract_words()
                    totals["tables"] += 1
                    if tables[tid]["kind"] == "staff":
                        ranges = column_ranges([r.cells for r in found.rows], tables[tid]["n_columns"])
                        staff_lost_rows += sum(check_row(r.cells, found.cells, words, ranges, found.bbox).status != "unchanged" for r in found.rows)
                        out_rows += [dict(r, p1={"status": "unchanged", "reason": "staff table excluded"}) for r in pilot_rows]
                        continue
                    rows, ch, stats = process_table(tables[tid], found, page, words, vocab, pilot_rows)
                    out_rows += rows
                    changes += ch
                    totals.update(stats)
    totals["staff_rows_with_lost_values_not_recovered"] = staff_lost_rows
    totals["rows"] = len(out_rows)
    totals["changed_rows"] = len(changes)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "table_rows.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in out_rows))
    (out_dir / "changes.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n" for c in changes))
    summary = {"source": "pilot_v2 (read-only)", "fixes": ["P1-1 stacked numeric values", "P1-2 values lost at page breaks"],
               "counts": dict(sorted(totals.items()))}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
