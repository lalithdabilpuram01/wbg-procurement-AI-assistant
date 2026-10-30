"""Record-level verification of pilot_v2_1 against pilot_v2 and the Step 6A validated cells (read-only).

    /opt/anaconda3/bin/python3 -B -m rag.release_v2_1.verify_corpus        (from procurement-intelligence/)

Reads only sanitised shared/ files (never restricted/) and the run manifests. Writes results/corpus_verification.json.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from rag.ingestion import config

RESULTS = Path(__file__).resolve().parent / "results"
OLD, NEW = config.OUTPUT_V2, config.OUTPUT_V2_1
STEP6A_ROWS = config.CORPUS / "processed" / "experimental_p1" / "table_rows.jsonl"
MASKS = ("[PERSON]", "[EMAIL]", "[PHONE]")
VERSION_ONLY = ("documents", "document_versions", "plan_versions")


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def masks(cells: list[str]) -> int:
    return sum(c.count(m) for c in cells for m in MASKS)


def file_comparison() -> dict[str, Any]:
    old = json.loads((OLD / "run_manifest.json").read_text())["outputs"]
    new = json.loads((NEW / "run_manifest.json").read_text())["outputs"]
    return {"identical": sorted(k for k in old if old[k] == new.get(k)), "different": sorted(k for k in old if old[k] != new.get(k)),
            "row_counts_equal": all(old[k]["rows"] == new[k]["rows"] for k in old)}


def version_only_differences() -> dict[str, Any]:
    """documents / document_versions / plan_versions may differ only in extraction_version; checksums must match."""
    out = {}
    for name in VERSION_ONLY:
        old, new = jsonl(OLD / "shared" / f"{name}.jsonl"), jsonl(NEW / "shared" / f"{name}.jsonl")
        strip = lambda r: {k: v for k, v in r.items() if k != "extraction_version"}  # noqa: E731
        out[name] = {"records": len(old), "identical_apart_from_extraction_version": [strip(a) for a in old] == [strip(b) for b in new],
                     "new_extraction_version": sorted({r["extraction_version"] for r in new})}
    old_v, new_v = jsonl(OLD / "shared" / "document_versions.jsonl"), jsonl(NEW / "shared" / "document_versions.jsonl")
    out["same_pdf_sha256"] = [(a["document_id"], a["sha256"]) for a in old_v] == [(b["document_id"], b["sha256"]) for b in new_v]
    return out


def table_rows() -> dict[str, Any]:
    old = jsonl(OLD / "shared" / "table_rows.jsonl")
    new = jsonl(NEW / "shared" / "table_rows.jsonl")
    step6a = {(r["table_id"], r["row_index"]): r for r in jsonl(STEP6A_ROWS)}
    keys_equal = [(r["table_id"], r["row_index"]) for r in old] == [(r["table_id"], r["row_index"]) for r in new]
    changed_cells, pure, matches_6a, unflagged_changes, mask_drop = [], 0, 0, [], 0
    other_fields: Counter = Counter()
    flags = Counter()
    lost_rows, lost_rows_cells_unchanged = [], 0
    for a, b in zip(old, new):
        other_fields.update(k for k in set(a) | set(b) if k not in ("cells_clean", "extraction_flags") and a.get(k) != b.get(k))
        mask_drop += masks(b["cells_clean"]) < masks(a["cells_clean"])
        f = b.get("extraction_flags") or {}
        for kind in ("stacked_values", "lost_values", "sanitisation_guard_fallback"):
            flags[kind] += kind in f
        if "lost_values" in f:
            lost_rows.append({"table_id": b["table_id"], "row_index": b["row_index"], **f["lost_values"]})
            stacked_cols = set(f.get("stacked_values", {}).get("columns", []))
            lost_rows_cells_unchanged += all(x == y for j, (x, y) in enumerate(zip(a["cells_clean"], b["cells_clean"])) if j not in stacked_cols)
        for col, (x, y) in enumerate(zip(a["cells_clean"], b["cells_clean"])):
            if x == y:
                continue
            changed_cells.append({"table_id": b["table_id"], "row_index": b["row_index"], "column": col, "pilot_v2": x, "pilot_v2_1": y})
            pure += y.replace("\n", "") == x
            reference = step6a.get((b["table_id"], b["row_index"]), {}).get("cells_clean", [])
            matches_6a += col < len(reference) and reference[col] == y
            if not (f.get("needs_review") and col in f.get("stacked_values", {}).get("columns", [])):
                unflagged_changes.append((b["table_id"], b["row_index"], col))
    return {"rows": len(new), "row_keys_identical": keys_equal, "changed_cells": len(changed_cells),
            "changed_cells_pure_line_boundary_insertion": pure, "changed_cells_equal_step6a_validated": matches_6a,
            "changed_cells_without_needs_review_flag": unflagged_changes, "rows_with_fewer_masks": mask_drop,
            "other_fields_changed": dict(other_fields), "rows_flagged": dict(flags),
            "lost_value_rows": lost_rows, "lost_value_rows_without_added_values": lost_rows_cells_unchanged,
            "changed_cell_list": changed_cells}


def descriptions() -> dict[str, Any]:
    old = jsonl(OLD / "shared" / "table_descriptions.jsonl")
    new = jsonl(NEW / "shared" / "table_descriptions.jsonl")
    changed = [(a, b) for a, b in zip(old, new) if a != b]
    return {"groups": len(new), "changed_groups": [b["table_group_id"] for _, b in changed],
            "changes_explained": all(_description_change_explained(a, b) for a, b in changed),
            "explanation": "only the example rows differ: line boundaries inserted, and the existing 200-character "
                           "example-row truncation now cuts earlier (no value is altered; descriptions are not retrieval units)"}


def _description_change_explained(old: dict, new: dict) -> bool:
    """Same metadata and text up to 'Example rows:'; each new example row (line boundaries removed) is a prefix of the
    old one (the builder truncates every example row to 200 characters)."""
    meta = lambda d: {k: v for k, v in d.items() if k not in ("description", "token_count")}  # noqa: E731
    head_old, rows_old = old["description"].split("Example rows: ", 1)
    head_new, rows_new = new["description"].split("Example rows: ", 1)
    pairs = list(zip(rows_old.split(" || "), rows_new.split(" || ")))
    return meta(old) == meta(new) and head_old == head_new and len(rows_old.split(" || ")) == len(rows_new.split(" || ")) \
        and all(o.startswith(n.replace("\n", "").rstrip(" |")) for o, n in pairs)


def run() -> dict[str, Any]:
    result = {"files": file_comparison(), "version_fields": version_only_differences(), "table_rows": table_rows(),
              "table_descriptions": descriptions()}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "corpus_verification.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    return result


if __name__ == "__main__":
    out = run()
    summary = {**out, "table_rows": {k: v for k, v in out["table_rows"].items() if k not in ("changed_cell_list", "lost_value_rows")}}
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    for row in out["table_rows"]["lost_value_rows"]:
        print(row)
