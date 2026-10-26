"""Independent post-write verification of workspace.wbg_silver.income_classification_history (read-only).

Re-reads the Delta table and re-derives the expected grid directly from the Bronze workbook bytes using
openpyxl cell-coordinate access (not the parser), then compares cell by cell. Also checks constraints,
properties and that the Bronze table version is unchanged since the pre-write baseline.

Usage (from procurement-intelligence/):
  python -m silver.income_class.verify_income_class_silver --run-id income_class_<timestamp>
Writes silver/runs/<run_id>/post_write_verification.json
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from pathlib import Path

import openpyxl
from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

BRONZE = "workspace.wbg_ref_bronze.income_classification_history"
TARGET = "workspace.wbg_silver.income_classification_history"
RUNS = Path(__file__).resolve().parents[1] / "runs"


def expected_grid(content: bytes) -> dict:
    ws = openpyxl.load_workbook(io.BytesIO(content), data_only=True)["Country Analytical History"]
    hdr = next(r for r in range(1, 20) if str(ws.cell(r, 2).value or "").lower().startswith("bank's fiscal year"))
    cols = [(c, ws.cell(hdr + 1, c).value + 2) for c in range(3, ws.max_column + 1)
            if isinstance(ws.cell(hdr, c).value, str) and re.fullmatch(r"FY\d{2}", ws.cell(hdr, c).value)]
    grid = {}
    for r in range(hdr + 2, ws.max_row + 1):
        code = ws.cell(r, 1).value
        if isinstance(code, str) and re.fullmatch(r"[A-Z]{3}[a-z]?", code.strip()) and ws.cell(r, 2).value:
            for c, fy in cols:
                v = ws.cell(r, c).value
                grid[(code.strip(), fy)] = None if v is None or str(v).strip() == "" else str(v).strip()
    return grid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    baseline = json.loads((run_dir / "bronze_version_before_write.json").read_text())
    spark = get_spark(args.profile)
    rep = DQReport(f"{TARGET} (post-write, independent)")
    t = spark.table(TARGET)
    b = spark.table(BRONZE).collect()[0]
    content = bytes(b.raw_file)
    grid = expected_grid(content)

    rows = t.select("iso3_code", "fiscal_year", "income_group_code_raw", "income_group_code", "classification_status",
                    "gni_data_year", "bronze_file_sha256", "source_cell").collect()
    got = {(r.iso3_code, r.fiscal_year): r.income_group_code_raw for r in rows}
    rep.equals("row_count", len(rows), len(grid))
    rep.equals("key_unique", len(got), len(rows))
    rep.equals("keys_match_workbook", sorted(set(got) ^ set(grid)), [])
    rep.equals("raw_values_match_workbook_cell_by_cell", sum(got.get(k) != v for k, v in grid.items()), 0)
    rep.equals("null_codes_only_for_dotdot_or_empty",
               t.filter(F.col("income_group_code").isNull() & F.col("income_group_code_raw").isNotNull() & (F.col("income_group_code_raw") != "..")).count(), 0)
    st = {r[0]: r[1] for r in t.groupBy("classification_status").count().collect()}
    rep.equals("status_counts_match_workbook",
               st, {"classified": sum(v not in (None, "..") for v in grid.values()), "unclassified": sum(v == ".." for v in grid.values()),
                    "no_entry": sum(v is None for v in grid.values())})
    raw_counts = {r[0]: r[1] for r in t.groupBy(F.coalesce("income_group_code_raw", F.lit("<empty>"))).count().collect()}
    wb_counts: dict = {}
    for v in grid.values():
        wb_counts["<empty>" if v is None else v] = wb_counts.get("<empty>" if v is None else v, 0) + 1
    rep.equals("raw_code_counts_match_workbook", dict(sorted(raw_counts.items())), dict(sorted(wb_counts.items())))
    lm = t.filter("income_group_code_raw = 'LM*'").select("iso3_code", "fiscal_year", "income_group_code", "footnote_marker", "footnote_text").collect()
    rep.equals("LM_star_entries_preserved", sorted((r.iso3_code, r.fiscal_year, r.income_group_code, r.footnote_marker) for r in lm),
               [("YEM", 1989, "LM", "*"), ("YEM", 1990, "LM", "*")])
    rep.equals("LM_star_footnote_text_present", all((r.footnote_text or "").startswith("* At this time") for r in lm), True)
    hist_codes = {"ANT", "CSK", "MYT", "SUN", "TWN", "YUG", "YUGf"}
    present = {r[0] for r in t.select("iso3_code").distinct().collect()}
    rep.equals("historical_and_nonstandard_codes_preserved", sorted(hist_codes - present), [])
    nn = {f.name for f in t.schema.fields if not f.nullable}
    rep.equals("not_null_enforced", sorted({"iso3_code", "fiscal_year", "classification_status", "is_classified", "source_cell",
                                            "bronze_file_sha256", "gni_data_year"} - nn), [])
    rep.equals("fiscal_year_range", list(t.agg(F.min("fiscal_year"), F.max("fiscal_year")).collect()[0]), [min(k[1] for k in grid), max(k[1] for k in grid)])
    rep.equals("gni_data_year_is_fiscal_year_minus_2", sum(r.gni_data_year != r.fiscal_year - 2 for r in rows), 0)
    rep.equals("economy_count", len({k[0] for k in grid}), t.select("iso3_code").distinct().count())
    rep.equals("lineage_sha256", {r.bronze_file_sha256 for r in rows}, {hashlib.sha256(content).hexdigest()})
    rep.equals("source_cell_unique", len({r.source_cell for r in rows}), len(rows))
    props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {TARGET}").collect()}
    rep.equals("constraints_present", sorted(k for k in props if k.startswith("delta.constraints.")),
               ["delta.constraints.code_valid", "delta.constraints.fiscal_vs_data_year", "delta.constraints.status_valid"])
    rep.equals("run_id_property", props.get("silver.run_id"), args.run_id)
    h = spark.sql(f"DESCRIBE HISTORY {BRONZE} LIMIT 1").collect()[0]
    rep.equals("bronze_unchanged", {"version": h.version, "rows": spark.table(BRONZE).count()},
               {"version": baseline["version"], "rows": baseline["rows"]})
    hist = [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {TARGET}").orderBy("version").collect()]
    print(rep.render())
    out = run_dir / "post_write_verification.json"
    out.write_text(json.dumps({"run_id": args.run_id, "verification": rep.to_dict(), "table_history": hist}, indent=1, default=str))
    print(f"\nreport: {out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
