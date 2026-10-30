"""Run the historical income classification Silver transformation (Databricks Connect, Serverless).

Default is a DRY RUN: reads the Bronze workbook, parses `Country Analytical History`, builds the Silver
DataFrame, runs all checks, reports country-code matching, and saves reports locally. Nothing is written.

Writing requires --mode write --confirm-create, all "error" checks passing, and the target not existing.

Usage (from procurement-intelligence/):
  python -m silver.income_class.run_income_class_silver
  python -m silver.income_class.run_income_class_silver --mode write --confirm-create
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.common.pipeline import CATALOG, runs_dir
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.common.write import create_table
from silver.income_class import transform as T
from silver.income_class.checks import check

TARGET = f"{CATALOG}.wbg_silver.income_classification_history"
WDI_COUNTRY = f"{CATALOG}.wbg_silver.wdi_country"
REPO = Path(__file__).resolve().parents[2]
RUNS = runs_dir(REPO / "silver" / "runs")
COHORT_WINDOW = (2005, 2027)
NOT_NULL = ["iso3_code", "country_name", "fiscal_year", "fiscal_year_label", "gni_data_year", "classification_status",
            "is_classified", "source_cell", "bronze_file_sha256", "bronze_source_file"]
CONSTRAINTS = {
    "status_valid": "classification_status IN ('classified', 'unclassified', 'no_entry')",
    "code_valid": "income_group_code IS NULL OR income_group_code IN ('L', 'LM', 'UM', 'H')",
    "fiscal_vs_data_year": "fiscal_year = gni_data_year + 2",
}
COLUMN_COMMENTS = {
    "iso3_code": "Country code exactly as published in OGHIST (ISO3; historical entities kept, incl. YUGf)",
    "fiscal_year": "World Bank fiscal year (July 1 of fiscal_year-1 to June 30 of fiscal_year)",
    "gni_data_year": "Calendar year of the Atlas GNI per capita data used for this classification",
    "income_group_code_raw": "Cell value exactly as published (L, LM, UM, H, .., footnoted e.g. LM*); NULL = empty cell",
    "income_group_code": "L/LM/UM/H without footnote marker; NULL when unclassified (..) or no entry",
    "classification_status": "classified | unclassified (published ..) | no_entry (empty cell). Never filled from other years",
}


def matching_report(spark, df, run_dir: Path) -> dict:
    """Read-only comparison with WDI Silver country metadata and the approved cohort (no mappings invented)."""
    sys.path.insert(0, str(REPO / "discovery"))
    from wbdisc.countries import WB_TO_ISO2, is_regional  # curated D-31 aliases only

    og = {r.iso3_code: r.country_name for r in df.select("iso3_code", "country_name").distinct().collect()}
    wdi = {r.country_wb_id: (r.country_code, r.country_name, r.entity_type) for r in spark.table(WDI_COUNTRY).collect()}
    iso2_to_wb = {v[0]: k for k, v in wdi.items()}
    og_unmatched = sorted(c for c in og if c not in wdi)
    wdi_econ_missing = sorted(k for k, v in wdi.items() if v[2] == "economy" and k not in og)
    rows = [{"iso3_code": c, "oghist_name": og[c], "in_wdi_country": c in wdi,
             "wdi_name": wdi[c][1] if c in wdi else "", "wdi_entity_type": wdi[c][2] if c in wdi else ""} for c in sorted(og)]
    rows += [{"iso3_code": k, "oghist_name": "", "in_wdi_country": True, "wdi_name": wdi[k][1], "wdi_entity_type": "economy"}
             for k in wdi_econ_missing]
    with (run_dir / "country_code_matching.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    import pandas as pd
    cohort = pd.read_csv(REPO / "cohort" / "poc_cohort_2000_manifest.csv", dtype=str, keep_default_na=False)
    single = cohort[~cohort.country_code.map(is_regional)]
    econ = single.groupby("country_code").size()
    win = df.filter((F.col("fiscal_year") >= COHORT_WINDOW[0]) & (F.col("fiscal_year") <= COHORT_WINDOW[1]))
    status = {(r.iso3_code, r.fiscal_year): r.classification_status for r in win.select("iso3_code", "fiscal_year", "classification_status").collect()}
    cov, gaps, unmatched = 0, {}, []
    for wb_code, n in econ.items():
        iso3 = iso2_to_wb.get(WB_TO_ISO2.get(wb_code, wb_code))
        if iso3 is None or iso3 not in og:
            unmatched.append({"cohort_country_code": wb_code, "projects": int(n)})
            continue
        cov += 1
        miss = {fy: status[(iso3, fy)] for fy in range(COHORT_WINDOW[0], COHORT_WINDOW[1] + 1) if status[(iso3, fy)] != "classified"}
        if miss:
            gaps[iso3] = {"projects": int(n), "years": miss}
    return {
        "oghist_codes": len(og), "oghist_codes_not_in_wdi_country": {c: og[c] for c in og_unmatched},
        "wdi_economies_not_in_oghist": {k: wdi[k][1] for k in wdi_econ_missing},
        "name_differences_for_matched_codes": sum(1 for c in og if c in wdi and og[c] != wdi[c][1]),
        "cohort_single_country_economies": int(len(econ)), "cohort_economies_matched": cov,
        "cohort_economies_unmatched": unmatched,
        f"cohort_economies_with_non_classified_years_FY{COHORT_WINDOW[0]}_{COHORT_WINDOW[1]}": gaps,
        "mapping_method": "cohort WB code -> curated alias (ZR->CD, GZ->PS, TP->TL, RY->YE) -> wdi_country.country_code -> country_wb_id -> OGHIST iso3",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry-run", "write"], default="dry-run")
    ap.add_argument("--confirm-create", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    if args.mode == "write" and not args.confirm_create:
        ap.error("--mode write requires --confirm-create")

    run_id = "income_class_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(args.profile)
    h = spark.sql(f"DESCRIBE HISTORY {T.BRONZE_TABLE} LIMIT 1").collect()[0]
    (run_dir / "bronze_version_before_write.json").write_text(json.dumps(
        {"table": T.BRONZE_TABLE, "version": h.version, "timestamp": str(h.timestamp), "rows": spark.table(T.BRONZE_TABLE).count(),
         "target_exists": spark.catalog.tableExists(TARGET)}, indent=1))
    bronze = T.read_bronze(spark)
    df, parsed = T.build(spark, bronze, run_id)
    report = DQReport(TARGET)
    check(df, parsed, bronze, report)
    match = matching_report(spark, df, run_dir)

    print(f"run_id={run_id} mode={args.mode} transform={T.TRANSFORM_VERSION}")
    print(f"layout: fiscal-year header row {parsed.fy_header_row}, data-year row {parsed.data_year_row}, "
          f"{len(parsed.fy_columns)} FY columns {parsed.fy_columns[0]['letter']}..{parsed.fy_columns[-1]['letter']} "
          f"(FY{parsed.fy_columns[0]['fiscal_year']}..FY{parsed.fy_columns[-1]['fiscal_year']}, GNI {parsed.fy_columns[0]['gni_data_year']}..{parsed.fy_columns[-1]['gni_data_year']})")
    print(f"entities: {len(parsed.entities)}; note rows: {[n['row'] for n in parsed.notes]}")
    print(f"\n== proposed schema: {TARGET}")
    for f in df.schema.fields:
        print(f"   {f.name:26s} {f.dataType.simpleString():10s} nullable={f.nullable}")
    print("\n== status / raw value counts")
    df.groupBy("classification_status", "income_group_code_raw").count().orderBy("classification_status", "income_group_code_raw").show(truncate=False)
    print("== sample")
    (df.filter("iso3_code IN ('YEM', 'ETH', 'VEN', 'YUGf', 'SSD') AND fiscal_year IN (1989, 1990, 1993, 2012, 2013, 2022, 2026, 2027)")
       .select("iso3_code", "country_name", "fiscal_year_label", "fiscal_year", "gni_data_year", "income_group_code_raw",
               "income_group_code", "income_group_name", "classification_status", "footnote_marker", "source_cell")
       .orderBy("iso3_code", "fiscal_year").show(40, truncate=False))
    print(report.render())
    print("\n== country-code matching (read-only)")
    print(json.dumps(match, indent=1, default=str)[:6000])

    (run_dir / "dq_report.json").write_text(json.dumps(
        {"run_id": run_id, "mode": args.mode, "transform_version": T.TRANSFORM_VERSION, "target": TARGET,
         "layout": {"fy_header_row": parsed.fy_header_row, "data_year_row": parsed.data_year_row, "fy_columns": parsed.fy_columns,
                    "entities": len(parsed.entities), "notes": parsed.notes},
         "dq": report.to_dict(), "matching": match}, indent=1, default=str))
    print(f"\nreports: {run_dir}")
    if not report.passed:
        print("DQ FAILED: nothing written")
        return 1
    if args.mode == "dry-run":
        print("DRY RUN: nothing written to Databricks")
        return 0
    create_table(spark, df, TARGET,
                 {"silver.run_id": run_id, "silver.transform_version": T.TRANSFORM_VERSION, "silver.source_table": T.BRONZE_TABLE,
                  "silver.source_sha256": bronze["computed_sha256"], "silver.decisions": "D-28,D-30,D-31"},
                 "Historical World Bank income classifications (OGHIST, as published on July 1 of each fiscal year); "
                 "one row per economy x fiscal year; .. and empty cells kept, never filled",
                 COLUMN_COMMENTS, NOT_NULL, CONSTRAINTS)
    print(f"written {TARGET}: {spark.table(TARGET).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
