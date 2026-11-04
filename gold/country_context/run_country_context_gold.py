"""Run the gold_country_context transformation (Databricks Connect, Serverless).

Default is a DRY RUN: records the baseline (all Bronze, Silver and Gold tables: versions, row counts, grants; column masks;
cohort), builds the economy x year DataFrame, runs all checks (incl. exact reconciliation of every published value with
Silver, coverage invariants, fiscal-year alignment, join cardinality with the existing Gold tables, the Delta CHECK
constraints and NOT NULL columns evaluated on the data), prints the schema and saves reports. Nothing is written.

Writing requires --mode write --confirm-create, all "error" checks passing and the target not existing (create-only).

Usage (from procurement-intelligence/):
  python -m gold.country_context.run_country_context_gold
  python -m gold.country_context.run_country_context_gold --mode write --confirm-create
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyspark.sql import functions as F

from gold.common import baseline as B
from gold.country_context import transform as T
from gold.country_context.checks import check
from silver.common.dq import DQReport
from silver.common.pipeline import CATALOG, runs_dir
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.common.write import create_table

REPO = Path(__file__).resolve().parents[2]
RUNS = runs_dir(REPO / "gold" / "runs")
GOLD = ["gold_project_portfolio", "gold_contract_analytics", "gold_procurement_notices"]
NOT_NULL = ["country_code", "country_iso3", "country_name", "year", "indicator_year_basis", "wdi_values_present", "wgi_values_present", "wgi_coverage_status",
            "cpia_values_present", "cpia_coverage_status", "income_fy_ending_in_year", "income_status_fy_ending_in_year", "income_fy_starting_in_year",
            "income_status_fy_starting_in_year", "income_source_workbook", "income_source_file_sha256", "context_snapshot_semantics", "silver_source_versions",
            "gold_run_id", "transform_version"]
INC = "('L', 'LM', 'UM', 'H')"


def _count(cols) -> str:
    return " + ".join(f"CASE WHEN {c} IS NOT NULL THEN 1 ELSE 0 END" for c in cols)


CONSTRAINTS = {
    "country_code_format": "country_code RLIKE '^[A-Z0-9]{2}$' AND country_iso3 RLIKE '^[A-Z]{3}$'",
    "year_range": "year BETWEEN 2000 AND 2025",
    "year_basis_value": "indicator_year_basis = 'calendar_year'",
    "wdi_count_consistent": f"wdi_values_present = {_count(T.WDI.values())}",
    "wgi_count_consistent": f"wgi_values_present = {_count(T.WGI.values())}",
    "cpia_count_consistent": f"cpia_values_present = {_count(T.CPIA.values())}",
    "wgi_status_consistent": "(wgi_coverage_status = 'published') = (wgi_values_present > 0) AND wgi_coverage_status IN "
                             "('published', 'all_values_missing', 'year_not_published', 'economy_not_in_source')",
    "cpia_status_consistent": "(cpia_coverage_status = 'published') = (cpia_values_present > 0) AND cpia_coverage_status IN "
                              "('published', 'not_rated_in_year', 'economy_never_rated')",
    "cpia_range": " AND ".join(f"({c} IS NULL OR {c} BETWEEN 1 AND 6)" for c in T.CPIA.values()),
    "income_fiscal_years_explicit": "income_fy_ending_in_year = year AND income_fy_starting_in_year = year + 1",
    "income_codes_valid": f"(income_group_fy_ending_in_year IS NULL OR income_group_fy_ending_in_year IN {INC}) AND "
                          f"(income_group_fy_starting_in_year IS NULL OR income_group_fy_starting_in_year IN {INC})",
    "income_code_iff_classified": "(income_group_fy_ending_in_year IS NOT NULL) = (income_status_fy_ending_in_year = 'classified') AND "
                                  "(income_group_fy_starting_in_year IS NOT NULL) = (income_status_fy_starting_in_year = 'classified')",
    "snapshot_semantics_value": f"context_snapshot_semantics = '{T.SEMANTICS}'",
}
VINTAGE = " Current-vintage value (may be revised after the fact): not evidence of what was known at the time."
COMMENTS: dict[str, Any] = {
    "table": "Country economic and governance context: one row per WDI economy (217; aggregates excluded) x calendar year 2000-2025 (5,642). WDI, WGI and "
             "CPIA values copied as published (no interpolation, imputation, normalisation or derived scores; unavailable = NULL). WB income groups are "
             "FISCAL-year classifications carried for both fiscal years overlapping the calendar year. Keyed strictly by published calendar year; join on "
             "the validated wdi_country_code (regional projects have no country context). Downstream join lags and ML feature policies are reviewed "
             "separately. All values are current-vintage estimates, not verified point-in-time observations and not historically available ML features",
    "columns": {
        "country_code": "WDI economy code (2 characters); join key = validated wdi_country_code in the Gold project, contract and notice tables",
        "country_iso3": "WB 3-letter code (wdi_country.country_wb_id); join key to income_classification_history",
        "year": "Calendar (observation) year of the WDI, WGI and CPIA values. NOT a World Bank fiscal year",
        "indicator_year_basis": "calendar_year: WDI/WGI/CPIA values refer to the calendar year",
        "gdp_growth_annual_pct": "WDI NY.GDP.MKTP.KD.ZG, GDP growth (annual %), as published." + VINTAGE,
        "inflation_consumer_prices_annual_pct": "WDI FP.CPI.TOTL.ZG, inflation, consumer prices (annual %), as published." + VINTAGE,
        "gdp_per_capita_current_usd": "WDI NY.GDP.PCAP.CD, GDP per capita (current US$), as published." + VINTAGE,
        "wdi_values_present": "Number of the 3 WDI values present (0-3)",
        "wgi_control_of_corruption_est": "WGI CC.EST estimate (approx. -2.5 to 2.5), as published." + VINTAGE,
        "wgi_coverage_status": "published / all_values_missing / year_not_published (WGI had no 2001 edition) / economy_not_in_source (10 economies; "
                               "no successor or name-based matching)",
        "cpia_irai_overall": "CPIA IDA resource allocation index (1-6), as published; IDA-eligible economies only." + VINTAGE,
        "cpia_coverage_status": "published / not_rated_in_year / economy_never_rated (not IDA-assessed: missing, never zero)",
        "income_fy_ending_in_year": "WB fiscal year ending 30 June of `year` (runs 1 July year-1 to 30 June year)",
        "income_group_fy_ending_in_year": "WB income group for income_fy_ending_in_year (based on GNI per capita of year-2); point-in-time classification",
        "income_fy_starting_in_year": "WB fiscal year starting 1 July of `year` (= year + 1)",
        "income_group_fy_starting_in_year": "WB income group for income_fy_starting_in_year (based on GNI per capita of year-1)",
        "income_status_fy_ending_in_year": "classified / unclassified / no_entry (as published in OGHIST)",
        "silver_source_versions": "JSON {silver table: Delta version} read by this run",
    },
}


def main() -> int:
    """Dry run by default: build and check gold_country_context and save the reports; write mode creates the table."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry-run", "write"], default="dry-run")
    ap.add_argument("--confirm-create", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    if args.mode == "write" and not args.confirm_create:
        ap.error("--mode write requires --confirm-create")
    run_id = "gold_country_context_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(args.profile)
    target_exists = spark.catalog.tableExists(T.TARGET)
    # Version, row count and grants of every other table before the write, so the verify script can prove no change.
    base = B.capture(spark, exclude={T.TARGET})
    base["target_exists"] = target_exists
    (run_dir / "baseline_before_write.json").write_text(json.dumps(base, indent=1))

    rep = DQReport(T.TARGET)
    rep.equals("target_absent_before_write", target_exists, False)
    rep.equals("baseline_table_counts (Bronze, Silver, Gold)", (len(base["bronze"]), len(base["silver"]), len(base["gold"])), (17, 18, 3))
    dfs, versions = T.load_sources(spark)
    g = T.build(dfs, versions, run_id)
    summary = check(g, dfs, {t: spark.table(f"{CATALOG}.wbg_gold.{t}") for t in GOLD}, rep)
    rep.equals("delta_check_constraints_hold_before_write", {c: g.filter(f"NOT coalesce(({e}), false)").count() for c, e in CONSTRAINTS.items()}, {c: 0 for c in CONSTRAINTS})
    rep.equals("not_null_columns_have_no_nulls", {c: g.filter(f"`{c}` IS NULL").count() for c in NOT_NULL}, {c: 0 for c in NOT_NULL})
    summary["silver_source_versions"] = json.loads(versions)
    summary["null_counts_value_columns"] = {c: g.filter(f"`{c}` IS NULL").count() for c in [*T.WDI.values(), *T.WGI.values(), *T.CPIA.values(),
                                                                                              "income_group_fy_ending_in_year", "income_group_fy_starting_in_year"]}
    summary["distributions"] = {c: {str(r[0]): r[1] for r in g.groupBy(c).count().collect()} for c in
                                ("wdi_values_present", "wgi_coverage_status", "cpia_coverage_status", "income_status_fy_ending_in_year", "income_status_fy_starting_in_year")}
    summary["coverage_by_year"] = {r[0]: {"wdi_any": r[1], "wgi_published": r[2], "cpia_published": r[3]} for r in g.groupBy("year").agg(
        F.sum((F.col("wdi_values_present") > 0).cast("int")), F.sum((F.col("wgi_coverage_status") == "published").cast("int")),
        F.sum((F.col("cpia_coverage_status") == "published").cast("int"))).orderBy("year").collect()}
    print(f"run_id={run_id} mode={args.mode} transform={T.TRANSFORM_VERSION}")
    print(f"\n== proposed schema: {T.TARGET} ({len(g.columns)} columns)")
    for f in g.schema.fields:
        print(f"   {f.name:40s} {f.dataType.simpleString()}")
    print("\n== summary\n" + json.dumps(summary, indent=1, default=str))
    print(rep.render())
    (run_dir / "dq_report.json").write_text(json.dumps({"run_id": run_id, "mode": args.mode, "transform_version": T.TRANSFORM_VERSION, "target": T.TARGET,
                                                        "schema": [(f.name, f.dataType.simpleString()) for f in g.schema.fields], "summary": summary,
                                                        "constraints": CONSTRAINTS, "not_null": NOT_NULL, "dq": rep.to_dict()}, indent=1, default=str))
    print(f"\nreports: {run_dir}")
    if not rep.passed:
        print("DQ FAILED: nothing written")
        return 1
    if args.mode == "dry-run":
        print("DRY RUN: nothing written to Databricks")
        return 0
    create_table(spark, g, T.TARGET, {"gold.run_id": run_id, "gold.transform_version": T.TRANSFORM_VERSION, "gold.silver_source_versions": versions,
                                       "gold.model": "economy x calendar-year context (current vintages)"}, COMMENTS["table"], COMMENTS["columns"], NOT_NULL, CONSTRAINTS)
    print(f"written {T.TARGET}: {spark.table(T.TARGET).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
