"""Validate the shadow P1 output against the source PDFs, pilot_v2 and the Step 5 audit. Read-only except its report.

    /opt/anaconda3/bin/python3 -B -m rag.ingestion_p1.validate_p1        (after shadow_run; from procurement-intelligence/)

Writes rag/corpus_v3/processed/experimental_p1/validation.json (counts and sanitised examples only; raw cells stay
in memory). Checks:
  1. differential: active `clean_cell` vs P1 `clean_cell_lines` on every source cell of the 220 pilot tables; every
     difference must be a pure line-boundary insertion (removing the '\\n's gives back the active value exactly);
  2. audit verdicts (exact / wrap_joined / line_merge / not_in_source) on pilot_v2 vs P1 cells of all data rows;
  3. STEP plan tables: no cell cleaned differently (the typed plan fields use `clean_cell` on these cells);
  4. rows the P1 fixes did not touch are byte-identical to pilot_v2;
  5. lost-value rows, including ISR 40089196:t00501 row 1.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from typing import Any

import pdfplumber

from rag.ingestion.cell_clean import clean_cell

from .shadow_run import OUT, PILOT, REPO, jsonl
from .stacked_values import clean_cell_lines

sys.path.insert(0, str(REPO / "audits" / "pdf_table_quality"))
import audit_tables as audit  # noqa: E402  (read-only reuse of the Step 5 comparison rules)


def raw_tables(versions: list[dict], wanted: set[str]) -> dict[str, list[list[str | None]]]:
    """Source cells of the pilot tables, numbered like the pipeline (held in memory only)."""
    out = {}
    for version in versions:
        with pdfplumber.open(REPO / version["local_path"]) as pdf:
            for pno, page in enumerate(pdf.pages, start=1):
                for ti, found in enumerate(page.find_tables(), start=1):
                    tid = f"{version['document_id']}:t{pno:03d}{ti:02d}"
                    if tid in wanted:
                        out[tid] = found.extract()
    return out


def verdicts(rows: list[dict], raw: dict[str, list[list[str | None]]], skip: set[str]) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        if row["is_header"] or row["table_id"] in skip:
            continue
        source = raw[row["table_id"]][row["row_index"] - 1]
        for col, clean in enumerate(row["cells_clean"]):
            for _, verdict in audit.compare_cell(clean, source[col] if col < len(source) else None):
                counts[verdict] += 1
    return counts


def run() -> dict[str, Any]:
    versions = jsonl(PILOT / "shared" / "document_versions.jsonl")
    tables = {t["table_id"]: t for t in jsonl(PILOT / "shared" / "tables.jsonl")}
    pilot_rows = jsonl(PILOT / "shared" / "table_rows.jsonl")
    p1_rows = jsonl(OUT / "table_rows.jsonl")
    changes = jsonl(OUT / "changes.jsonl")
    vocab = set(json.loads((PILOT / "reference" / "vocab.json").read_text())["words"])
    raw = raw_tables(versions, set(tables))
    staff = {t for t, rec in tables.items() if rec["kind"] == "staff"}

    # 1 + 3. differential over every source cell
    diff = {"cells": 0, "different": 0, "pure_boundary_insertion": 0, "tables": Counter(), "staff_tables_different": 0,
            "step_plan_cells": 0, "step_plan_different": 0}
    for tid, data in raw.items():
        for row in data:
            for cell in row:
                old, _ = clean_cell(cell, vocab)
                new = clean_cell_lines(cell, vocab).text
                diff["cells"] += 1
                if tables[tid]["kind"] == "step_plan":
                    diff["step_plan_cells"] += 1
                    diff["step_plan_different"] += old != new
                if old != new:
                    diff["different"] += 1
                    diff["tables"][tid] += 1
                    diff["pure_boundary_insertion"] += new.replace("\n", "") == old
                    diff["staff_tables_different"] += tid in staff
    diff["tables"] = dict(diff["tables"])

    # 2. audit verdicts before / after
    before, after = verdicts(pilot_rows, raw, staff), verdicts(p1_rows, raw, staff)

    # 4. untouched rows identical
    pilot_by_key = {(r["table_id"], r["row_index"]): r for r in pilot_rows}
    untouched = [r for r in p1_rows if r["p1"]["status"] == "unchanged"]
    identical = sum({k: v for k, v in r.items() if k != "p1"} == pilot_by_key[(r["table_id"], r["row_index"])] for r in untouched)

    # 5. lost values
    lost = [{"table_id": c["table_id"], "row_index": c["row_index"], **c["lost_values"]} for c in changes if "lost_values" in c]
    target = next((c for c in changes if c["table_id"] == "40089196:t00501" and c["row_index"] == 1), None)

    result = {
        "differential_clean_cell": diff,
        "audit_verdicts": {"pilot_v2": dict(before), "p1": dict(after)},
        "rows": {"pilot_v2": len(pilot_rows), "p1": len(p1_rows), "untouched": len(untouched), "untouched_identical": identical,
                 "changed": len(changes)},
        "stacked_rows": [{"table_id": c["table_id"], "row_index": c["row_index"], "status": c["status"], "reasons": c["reasons"],
                          "stacked_columns": c.get("stacked_columns"), "split": "sub_rows" in c} for c in changes if "stacked_columns" in c],
        "lost_value_rows": lost,
        "isr_40089196_t00501_row1": {"detected": target is not None, "status": target and target["status"],
                                     "p1_cells": target and target["p1_cells"]},
    }
    (OUT / "validation.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, ensure_ascii=False))
