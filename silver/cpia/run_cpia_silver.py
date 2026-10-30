"""Run the CPIA Silver transformation (Databricks Connect, Serverless).

Default is a DRY RUN: records the pre-write baseline, verifies Volume source files against the staging
manifest, builds the Silver DataFrame, runs all checks, profiles missing scores (by indicator x year and by
country x indicator), and saves reports. Nothing is written.

Writing requires --mode write --confirm-create, all "error" checks passing, and the target not existing.

Usage (from procurement-intelligence/):
  python -m silver.cpia.run_cpia_silver
  python -m silver.cpia.run_cpia_silver --mode write --confirm-create
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyspark.sql import functions as F

from silver.common import pipeline
from silver.common.dq import DQReport
from silver.common.pipeline import CATALOG, runs_dir
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.common.sourcefiles import load_manifest_vintages, vintages_df, verify_source_files
from silver.common.write import create_table
from silver.cpia import transform as C
from silver.cpia.checks import check

TARGET = f"{CATALOG}.wbg_silver.cpia_ratings"
RUNS = runs_dir(Path(__file__).resolve().parents[1] / "runs")
EXISTING_SILVER = ["wdi_indicator_observations", "wdi_country", "income_classification_history", "wgi_estimates"]
NOT_NULL = ["country_code", "country_name", "wdi_match_status", "indicator_code", "year", "is_value_missing",
            "source_lastupdated", "source_file_sha256", "source_url", "bronze_raw_record", "raw_record_sha256",
            "bronze_source_file", "bronze_ingested_at"]
CONSTRAINTS = {
    "wdi_match_status_valid": "wdi_match_status IN ('matched', 'unmatched_blank_source_code', 'unmatched_code_not_in_wdi')",
    "missing_flag_consistent": "is_value_missing = (value IS NULL)",
}
COLUMN_COMMENTS = {
    "country_code": "World Bank API country.id (2-character; same scheme as wdi_country.country_code)",
    "country_iso3_code": "countryiso3code as published; NULL where blank (5 income-group aggregates); not a key",
    "wdi_entity_type": "economy | aggregate, from wbg_silver.wdi_country by exact country_code",
    "value": "CPIA score as published (1=low to 6=high); NULL = no score published for that country-year",
    "source_lastupdated": "WDI source lastupdated (vintage) from the staging manifest, verified against the Volume file SHA-256",
    "bronze_raw_record": "Original Bronze raw_record JSON string",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry-run", "write"], default="dry-run")
    ap.add_argument("--confirm-create", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    if args.mode == "write" and not args.confirm_create:
        ap.error("--mode write requires --confirm-create")

    run_id = "cpia_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(args.profile)

    baseline: dict = {"bronze": {}, "silver": {}, "target_exists": spark.catalog.tableExists(TARGET)}
    for group, tables in (("bronze", [C.BRONZE_TABLE]), ("silver", [f"{CATALOG}.wbg_silver.{t}" for t in EXISTING_SILVER])):
        for t in tables:
            h = spark.sql(f"DESCRIBE HISTORY {t} LIMIT 1").collect()[0]
            baseline[group][t] = {"version": h.version, "rows": spark.table(t).count()}
    (run_dir / "baseline_before_write.json").write_text(json.dumps(baseline, indent=1))

    bronze = spark.table(C.BRONZE_TABLE)
    vint = load_manifest_vintages(C.BRONZE_FOLDER)
    df = C.build(bronze, spark.table(C.WDI_COUNTRY), vintages_df(spark, vint), run_id)
    report = DQReport(TARGET)
    verify_source_files(spark, bronze, vint, report)
    check(df, bronze, report)
    out = df.drop(*C.VALIDATION_ONLY_COLUMNS)

    # --- coverage and missing-score profiles
    by_year: Any = (out.groupBy("indicator_code", "year").agg(F.count("*").alias("rows"), F.sum(F.col("is_value_missing").cast("int")).alias("missing"),
                                                         F.countDistinct(F.when(~F.col("is_value_missing") & (F.col("wdi_entity_type") == "economy"), F.col("country_code"))).alias("economies_with_score"))
               .orderBy("indicator_code", "year").toPandas())
    by_year.to_csv(run_dir / "missing_by_indicator_year.csv", index=False)
    by_cty: Any = (out.groupBy("country_code", "country_name", "wdi_entity_type", "indicator_code")
              .agg(F.sum((~F.col("is_value_missing")).cast("int")).alias("scores"),
                   F.min(F.when(~F.col("is_value_missing"), F.col("year"))).alias("first_score_year"),
                   F.max(F.when(~F.col("is_value_missing"), F.col("year"))).alias("last_score_year"),
                   F.sum(((F.col("year") >= 2005) & F.col("is_value_missing")).cast("int")).alias("missing_2005_2025"))
              .orderBy("country_code", "indicator_code").toPandas())
    by_cty.to_csv(run_dir / "missing_by_country_indicator.csv", index=False)
    scored = by_cty[by_cty.scores > 0]
    summary = {
        "rows": out.count(),
        "rows_per_indicator": {r[0]: r[1] for r in out.groupBy("indicator_code").count().collect()},
        "row_years": [int(by_year.year.min()), int(by_year.year.max())],
        "score_years": [int(by_year[by_year.rows > by_year.missing].year.min()), int(by_year[by_year.rows > by_year.missing].year.max())],
        "non_null_per_indicator": {k: int(v) for k, v in by_year.assign(nn=by_year.rows - by_year.missing).groupby("indicator_code").nn.sum().items()},
        "null_per_indicator": {k: int(v) for k, v in by_year.groupby("indicator_code").missing.sum().items()},
        "entities_total": int(by_cty.country_code.nunique()),
        "entities_with_any_score_by_type": {k: int(v) for k, v in scored.groupby("wdi_entity_type").country_code.nunique().items()},
        "economies_with_score_per_indicator": {k: int(v) for k, v in scored[scored.wdi_entity_type == "economy"].groupby("indicator_code").country_code.nunique().items()},
        "economies_with_score_by_year_range": [int(by_year[by_year.year >= 2005].economies_with_score.min()), int(by_year[by_year.year >= 2005].economies_with_score.max())],
        "scored_economies_with_gaps_2005_2025": int(scored[(scored.wdi_entity_type == "economy") & (scored.missing_2005_2025 > 0)].country_code.nunique()),
        "range_by_indicator": {r[0]: [r[1], r[2]] for r in out.groupBy("indicator_code").agg(F.min("value"), F.max("value")).collect()},
    }
    print(f"run_id={run_id} mode={args.mode} transform={C.TRANSFORM_VERSION}")
    print(f"\n== proposed schema: {TARGET}")
    for f in out.schema.fields:
        print(f"   {f.name:22s} {f.dataType.simpleString():10s} nullable={f.nullable}")
    print("\n== summary\n" + json.dumps(summary, indent=1, default=str))
    print("== sample")
    (out.filter("(country_code IN ('ET', 'XM') AND year IN (2004, 2005, 2025) AND indicator_code IN ('IQ.CPA.IRAI.XQ', 'IQ.CPA.TRAN.XQ')) "
                "OR (country_code = 'IN' AND year = 2025 AND indicator_code = 'IQ.CPA.IRAI.XQ')")
        .select("country_code", "country_iso3_code", "country_name", "wdi_entity_type", "indicator_code", "year", "value", "is_value_missing",
                "source_lastupdated").orderBy("country_code", "indicator_code", "year").show(20, truncate=False))
    print(report.render())
    (run_dir / "dq_report.json").write_text(json.dumps({"run_id": run_id, "mode": args.mode, "transform_version": C.TRANSFORM_VERSION,
                                                        "target": TARGET, "summary": summary, "dq": report.to_dict()}, indent=1, default=str))
    print(f"\nreports: {run_dir}")
    if not report.passed:
        print("DQ FAILED: nothing written")
        return 1
    if args.mode == "dry-run":
        print("DRY RUN: nothing written to Databricks")
        return 0
    if baseline["target_exists"] and not pipeline.ENABLED:
        raise SystemExit(f"{TARGET} already exists: refusing to overwrite")
    create_table(spark, out, TARGET,
                 {"silver.run_id": run_id, "silver.transform_version": C.TRANSFORM_VERSION, "silver.source_table": C.BRONZE_TABLE,
                  "silver.source_vintage": "2026-10-08", "silver.decisions": "D-28,D-30,D-31"},
                 "CPIA ratings (6 series, WDI source 2): all 266 economies and aggregates, all published rows 1960-2025; "
                 "scores as published (2005-2025), NULL = no score; never imputed",
                 COLUMN_COMMENTS, NOT_NULL, CONSTRAINTS)
    print(f"written {TARGET}: {spark.table(TARGET).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
