"""Data-quality rules for workspace.wbg_silver.income_classification_history.

"error" checks block writes; "warn" checks are reported. Raw cell counts are recomputed independently of
the parser (openpyxl cell-coordinate access in non-read-only mode) and reconciled with the Silver rows.
"""
from __future__ import annotations

import collections
import io
import re

import openpyxl
from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.income_class.parse import ENTITY_CODE, SHEET, ParsedWorkbook
from silver.income_class.transform import EXPECTED_SHA256

ALLOWED_RAW = {"L", "LM", "UM", "H", "..", None}
FOOTNOTED = re.compile(r"^(L|LM|UM|H)[*†‡§]$")


def independent_cell_counts(content: bytes, header_row: int, data_year_row: int) -> collections.Counter:
    """Count published values in FY columns of entity rows without using the parser's output."""
    ws = openpyxl.load_workbook(io.BytesIO(content), data_only=True)[SHEET]
    fy_cols = [c.column for c in ws[header_row] if isinstance(c.value, str) and re.fullmatch(r"FY\d{2}", c.value)]
    counts: collections.Counter = collections.Counter()
    for r in range(data_year_row + 1, ws.max_row + 1):
        code, name = ws.cell(r, 1).value, ws.cell(r, 2).value
        if isinstance(code, str) and ENTITY_CODE.fullmatch(code.strip()) and isinstance(name, str) and name.strip():
            for c in fy_cols:
                v = ws.cell(r, c).value
                counts["<empty>" if v is None or str(v).strip() == "" else str(v).strip()] += 1
    return counts


def check(df: DataFrame, parsed: ParsedWorkbook, bronze: dict, report: DQReport) -> None:
    # --- source integrity
    report.equals("bronze_row_count", bronze["rows"], 1)
    report.equals("bronze_sha256_matches_stored_column", bronze["computed_sha256"], bronze["stored_sha256"])
    report.add("bronze_sha256_matches_R2_download", bronze["computed_sha256"] == EXPECTED_SHA256, True,
               bronze["computed_sha256"] == EXPECTED_SHA256, "warn", "a new OGHIST release would differ; review before writing")
    report.equals("sheet_present", SHEET in parsed.sheet_names, True)
    # --- layout detection
    report.add("header_rows_detected (fiscal year / data year)", [parsed.fy_header_row, parsed.data_year_row], "detected by label",
               parsed.data_year_row > parsed.fy_header_row)
    fys = [c["fiscal_year"] for c in parsed.fy_columns]
    report.equals("fiscal_year_columns_unique", len(set(fys)), len(fys))
    report.equals("fiscal_years_contiguous", fys, list(range(min(fys), max(fys) + 1)))
    report.equals("fiscal_year_equals_gni_data_year_plus_2", sum(c["fiscal_year"] - c["gni_data_year"] != 2 for c in parsed.fy_columns), 0)
    report.add("fiscal_year_range", [min(fys), max(fys)], [1989, 2027], [min(fys), max(fys)] == [1989, 2027], "warn")
    report.equals("parser_issues", parsed.issues, [])
    # --- entities
    codes = [e["code"] for e in parsed.entities]
    report.equals("entity_codes_unique", len(set(codes)), len(codes))
    report.add("entity_count", len(codes), 224, len(codes) == 224, "warn", "223 ISO3 codes + YUGf at the 2026-10-08 release")
    nonstd = [c for c in codes if not re.fullmatch(r"[A-Z]{3}", c)]
    report.add("non_standard_codes (kept as published)", nonstd, ["YUGf"], nonstd == ["YUGf"], "warn")
    # --- completeness and uniqueness of the grid
    n = df.count()
    report.equals("rows_equal_entities_x_fiscal_years", n, len(codes) * len(fys))
    report.equals("duplicate_key (iso3_code, fiscal_year)", df.groupBy("iso3_code", "fiscal_year").count().filter("count > 1").count(), 0)
    report.equals("source_cell_unique", df.select("source_cell").distinct().count(), n)
    # --- values exactly as published
    raw = {r[0]: r[1] for r in df.groupBy(F.coalesce("income_group_code_raw", F.lit("<empty>"))).count().collect()}
    indep = independent_cell_counts(bronze["content"], parsed.fy_header_row, parsed.data_year_row)
    report.equals("raw_value_counts_reconcile_with_independent_read", dict(sorted(raw.items())), dict(sorted(indep.items())))
    unknown = [v for v in raw if v != "<empty>" and v not in ALLOWED_RAW and not FOOTNOTED.match(v)]
    report.equals("unrecognised_raw_values", unknown, [])
    st = {r[0]: r[1] for r in df.groupBy("classification_status").count().collect()}
    report.equals("unclassified_rows_equal_raw_dotdot", st.get("unclassified", 0), indep.get("..", 0))
    report.equals("no_entry_rows_equal_empty_cells", st.get("no_entry", 0), indep.get("<empty>", 0))
    report.equals("dotdot_never_mapped_to_a_group", df.filter((F.col("income_group_code_raw") == "..") & F.col("income_group_code").isNotNull()).count(), 0)
    report.equals("classified_iff_code_present", df.filter(F.col("is_classified") != F.col("income_group_code").isNotNull()).count(), 0)
    report.equals("code_name_mapping_consistent",
                  sorted(tuple(r) for r in df.filter("is_classified").select("income_group_code", "income_group_name").distinct().collect()),
                  [("H", "High income"), ("L", "Low income"), ("LM", "Lower middle income"), ("UM", "Upper middle income")])
    report.equals("raw_code_preserved (raw minus footnote marker == code)",
                  df.filter("is_classified").filter(F.regexp_replace("income_group_code_raw", r"[*†‡§]$", "") != F.col("income_group_code")).count(), 0)
    report.equals("footnoted_cells_have_note_text", df.filter(F.col("footnote_marker").isNotNull() & F.col("footnote_text").isNull()).count(), 0)
    # --- lineage
    lineage = ["source_sheet", "source_row_number", "source_cell", "bronze_source_file", "bronze_file_sha256", "bronze_ingested_at", "silver_run_id"]
    report.equals("lineage_nulls", {c: df.filter(F.col(c).isNull()).count() for c in lineage}, {c: 0 for c in lineage})
    report.equals("single_bronze_file", df.select("bronze_file_sha256").distinct().count(), 1)
