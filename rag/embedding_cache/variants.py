"""Retrieval-unit lists for the pilot (pilot_v2) and for the Step 6A experimental table rows, built with the unchanged
`rag/retrieval/core.Corpus`. Only the source of `shared/table_rows.jsonl` is swapped, in memory, while a corpus is
built; nothing is written and core.py is not modified.

Variants
  pilot_v2       the indexed corpus as it is today
  p1_stacked     Step 6A approved scope: stacked-value cells only; recovered page-break values left out
  p1_full        Step 6A output as produced (stacked values + recovered values, flagged needs_review)
  pilot_v2_1     the released extraction 2.1.0 corpus (rag/corpus_v3/processed/pilot_v2_1): every file read from there
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "rag" / "retrieval"))
import core  # noqa: E402  (read-only use of the active retrieval code)

P1_ROWS = ROOT / "rag" / "corpus_v3" / "processed" / "experimental_p1" / "table_rows.jsonl"
PILOT_V2_1 = ROOT / "rag" / "corpus_v3" / "processed" / "pilot_v2_1"
SCOPES = {"A+tables": ("A", True), "A-tables": ("A", False), "B+tables": ("B", True), "C+tables": ("C", True)}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def stacked_only(p1_rows: list[dict], pilot_rows: list[dict]) -> list[dict]:
    """Step 6A rows restricted to the approved stacked-value fix: recovered cells reverted to pilot_v2."""
    pilot = {(r["table_id"], r["row_index"]): r for r in pilot_rows}
    out = []
    for row in p1_rows:
        base = pilot[(row["table_id"], row["row_index"])]
        p1 = row.get("p1", {})
        if p1.get("status") == "unchanged" or "stacked_columns" not in p1:
            out.append(base)
            continue
        cells = list(row["cells_clean"])
        lost = p1.get("lost_values") or {}
        if lost.get("status") == "values_recovered":
            for col in lost.get("columns", []):
                cells[col] = base["cells_clean"][col]
        out.append({**base, "cells_clean": cells})
    return out


def table_rows(variant: str) -> list[dict]:
    pilot_rows = core.jl("shared/table_rows.jsonl")
    if variant == "pilot_v2":
        return pilot_rows
    p1_rows = _jsonl(P1_ROWS)
    if variant == "p1_full":
        return [{k: v for k, v in r.items() if k != "p1"} for r in p1_rows]
    if variant == "p1_stacked":
        return stacked_only(p1_rows, pilot_rows)
    raise ValueError(f"unknown variant {variant}")


@contextmanager
def _table_rows_source(rows: list[dict]) -> Iterator[None]:
    original = core.jl

    def jl(name: str, base: Path | None = None) -> list[dict]:
        return [dict(r) for r in rows] if name == "shared/table_rows.jsonl" else original(name, base)

    core.jl = jl
    try:
        yield
    finally:
        core.jl = original


def corpus(scope: str, variant: str = "pilot_v2") -> "core.Corpus":
    strategy, include_tables = SCOPES[scope]
    if variant == "pilot_v2":
        return core.Corpus(strategy, include_tables=include_tables)
    if variant == "pilot_v2_1":
        if not (PILOT_V2_1 / "run_manifest.json").is_file():
            raise FileNotFoundError("pilot_v2_1 has not been built")
        return core.Corpus(strategy, include_tables=include_tables, processed_dir=PILOT_V2_1)
    with _table_rows_source(table_rows(variant)):
        return core.Corpus(strategy, include_tables=include_tables)
