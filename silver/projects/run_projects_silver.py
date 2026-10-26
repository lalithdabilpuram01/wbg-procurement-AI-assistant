"""Run the Projects Silver transformation (Databricks Connect, Serverless).

Default is a DRY RUN: records the pre-write baseline, verifies the Bronze input file in the Volume against the
extraction manifest (SHA-256 + line-level), builds the Silver DataFrame, runs all checks, and saves reports.
Nothing is written.

Writing requires --mode write --confirm-create, all "error" checks passing, and the target not existing.

Usage (from procurement-intelligence/):
  python -m silver.projects.run_projects_silver
  python -m silver.projects.run_projects_silver --mode write --confirm-create
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.common.sourcefiles import verify_source_files
from silver.common.write import create_table
from silver.projects import transform as P
from silver.projects.checks import check, cohort_df

TARGET = "workspace.wbg_silver.projects"
RUNS = Path(__file__).resolve().parents[1] / "runs"
EXISTING_SILVER = ["wdi_indicator_observations", "wdi_country", "income_classification_history", "wgi_estimates", "cpia_ratings"]
NOT_NULL = ["project_id", "project_name", "country_code", "geography_type", "wdi_match_method", "project_status", "approval_fiscal_year_raw",
            "bronze_raw_record", "raw_record_sha256", "bronze_source_file", "bronze_ingested_at", "bronze_input_file_sha256"]
CONSTRAINTS = {
    "project_id_format": "project_id RLIKE '^P[0-9]{6}$'",
    "geography_type_valid": "geography_type IN ('single_country', 'regional_multicountry')",
    "regional_not_mapped": "geography_type = 'single_country' OR wdi_country_code IS NULL",
    "wdi_match_method_valid": "wdi_match_method IN ('exact', 'curated_alias', 'not_applicable_regional', 'unmatched')",
}
COLUMN_COMMENTS = {
    "project_id": "World Bank project ID (P-number); join key to contracts, notices, IEG and loan statements",
    "country_code": "Projects API countrycode as published (WB code; regional codes such as 3W kept)",
    "geography_type": "single_country | regional_multicountry (approved curated rule, identical to the cohort manifest flag)",
    "wdi_country_code": "wdi_country.country_code for single-country projects (exact or approved curated alias); NULL for regional (D-29)",
    "regional_code_collides_with_wdi_code": "TRUE when a regional project code is also a WDI code with a different meaning; never joined",
    "approval_fiscal_year": "WB fiscal year of approval; NULL where the source publishes 0 (no approval date)",
    "isr_ratings_snapshot": "Latest ISR ratings at extraction (current values only; no history, D-20)",
    "bronze_raw_record": "Original Bronze raw_record JSON string (all 39 published fields)",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry-run", "write"], default="dry-run")
    ap.add_argument("--confirm-create", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    if args.mode == "write" and not args.confirm_create:
        ap.error("--mode write requires --confirm-create")

    run_id = "projects_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(args.profile)
    baseline: dict = {"bronze": {}, "silver": {}, "target_exists": spark.catalog.tableExists(TARGET)}
    for group, tables in (("bronze", [P.BRONZE_TABLE]), ("silver", [f"workspace.wbg_silver.{t}" for t in EXISTING_SILVER])):
        for t in tables:
            h = spark.sql(f"DESCRIBE HISTORY {t} LIMIT 1").collect()[0]
            baseline[group][t] = {"version": h.version, "rows": spark.table(t).count()}
    cohort_pd = pd.read_csv(P.COHORT_MANIFEST, dtype=str, keep_default_na=False)
    baseline["cohort_manifest_sha256"] = hashlib.sha256(P.COHORT_MANIFEST.read_bytes()).hexdigest()
    (run_dir / "baseline_before_write.json").write_text(json.dumps(baseline, indent=1))

    prov = P.load_provenance()
    bronze = spark.table(P.BRONZE_TABLE)
    df = P.build(bronze, spark.table(P.WDI_COUNTRY), prov, run_id)
    report = DQReport(TARGET)
    report.equals("cohort_manifest_sha256_unchanged", baseline["cohort_manifest_sha256"], prov["cohort_manifest_sha256"])
    verify_source_files(spark, bronze, [{"file_name": prov["file_name"], "source_file_sha256": prov["source_file_sha256"]}], report)
    check(spark, df, bronze, cohort_df(spark, cohort_pd), prov, report)
    out = df.drop(*P.validation_only_columns(df))

    print(f"run_id={run_id} mode={args.mode} transform={P.TRANSFORM_VERSION}")
    print(f"\n== proposed schema: {TARGET}")
    for f in out.schema.fields:
        print(f"   {f.name:38s} {f.dataType.simpleString()[:110]}")
    print("\n== sample")
    (out.filter("project_id IN ('P074191', 'P075941', 'P120417', 'P163870', 'P127676')")
        .select("project_id", "country_code", "geography_type", "wdi_country_code", "wdi_match_method", "project_status",
                "approval_fiscal_year", "board_approval_date", "closing_date", "current_total_commitment_usd", "grant_amount_usd")
        .orderBy("project_id").show(truncate=False))
    print(report.render())
    (run_dir / "dq_report.json").write_text(json.dumps({"run_id": run_id, "mode": args.mode, "transform_version": P.TRANSFORM_VERSION,
                                                        "target": TARGET, "provenance": prov, "dq": report.to_dict()}, indent=1, default=str))
    print(f"\nreports: {run_dir}")
    if not report.passed:
        print("DQ FAILED: nothing written")
        return 1
    if args.mode == "dry-run":
        print("DRY RUN: nothing written to Databricks")
        return 0
    if baseline["target_exists"]:
        raise SystemExit(f"{TARGET} already exists: refusing to overwrite")
    create_table(spark, out, TARGET,
                 {"silver.run_id": run_id, "silver.transform_version": P.TRANSFORM_VERSION, "silver.source_table": P.BRONZE_TABLE,
                  "silver.cohort": "D-26 2,000 projects", "silver.decisions": "D-07,D-20,D-26,D-29,D-31"},
                 "World Bank projects of the approved 2,000-project PoC cohort (one row per project); typed from wbg_bronze.bronze_projects_v3; "
                 "regional projects kept and not mapped to countries; ratings arrays are current snapshots",
                 COLUMN_COMMENTS, NOT_NULL, CONSTRAINTS)
    print(f"written {TARGET}: {spark.table(TARGET).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
