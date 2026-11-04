"""Run the WGI Silver transformation (Databricks Connect, Serverless).

Default is a DRY RUN: reads Bronze, verifies the Volume source files against the staging manifest, builds
the Silver DataFrame, runs all checks, prints the schema, samples and WDI relationship, and saves reports.
Nothing is written.

Writing requires --mode write --confirm-create, all "error" checks passing, and the target not existing.

Usage (from procurement-intelligence/):
  python -m silver.wgi.run_wgi_silver
  python -m silver.wgi.run_wgi_silver --mode write --confirm-create
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import functions as F

from silver.common import pipeline
from silver.common.dq import DQReport
from silver.common.pipeline import CATALOG, runs_dir
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.common.write import create_table
from silver.wgi import transform as W
from silver.wgi.checks import check, verify_source_files

TARGET = f"{CATALOG}.wbg_silver.wgi_estimates"
RUNS = runs_dir(Path(__file__).resolve().parents[1] / "runs")
EXISTING_SILVER = ["wdi_indicator_observations", "wdi_country", "income_classification_history"]
NOT_NULL = ["entity_key", "entity_key_type", "country_name", "wdi_match_status", "indicator_code", "indicator_short_code", "year",
            "is_value_missing", "source_lastupdated", "source_file_sha256", "bronze_raw_record", "raw_record_sha256",
            "bronze_source_file", "bronze_ingested_at"]
CONSTRAINTS = {
    "entity_key_type_valid": "entity_key_type IN ('wb_country_code', 'country_name')",
    "wdi_match_status_valid": "wdi_match_status IN ('matched', 'unmatched_blank_source_code', 'unmatched_code_not_in_wdi')",
    "missing_flag_consistent": "is_value_missing = (value IS NULL)",
}
COLUMN_COMMENTS = {
    "entity_key": "country_code when published; otherwise 'name:<country_name>' (9 territories published with blank code and ISO3)",
    "country_code": "World Bank API country.id (2-character, same scheme as WDI country_code); NULL when blank in source",
    "wdi_match_status": "matched = exact country_code match in wbg_silver.wdi_country; no other mapping applied",
    "value": "WGI governance estimate as published (approx. -2.5 to +2.5, may exceed); NULL = not published",
    "source_lastupdated": "WGI source lastupdated (vintage) from the staging manifest, verified against the Volume file SHA-256",
    "bronze_raw_record": "Original Bronze raw_record JSON string",
}


def main() -> int:
    """Dry run by default: build and check wgi_estimates and save the reports; write mode creates the table."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry-run", "write"], default="dry-run")
    ap.add_argument("--confirm-create", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    if args.mode == "write" and not args.confirm_create:
        ap.error("--mode write requires --confirm-create")

    run_id = "wgi_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(args.profile)

    # Version and row count of each input table before the write, so the verify script can prove nothing else changed.
    baseline = {"bronze": {}, "silver": {}, "target_exists": spark.catalog.tableExists(TARGET)}
    h = spark.sql(f"DESCRIBE HISTORY {W.BRONZE_TABLE} LIMIT 1").collect()[0]
    baseline["bronze"][W.BRONZE_TABLE] = {"version": h.version, "rows": spark.table(W.BRONZE_TABLE).count()}
    for t in EXISTING_SILVER:
        full = f"{CATALOG}.wbg_silver.{t}"
        h = spark.sql(f"DESCRIBE HISTORY {full} LIMIT 1").collect()[0]
        baseline["silver"][full] = {"version": h.version, "rows": spark.table(full).count()}
    (run_dir / "baseline_before_write.json").write_text(json.dumps(baseline, indent=1))

    bronze = spark.table(W.BRONZE_TABLE)
    vint = W.load_vintages()
    df = W.build(bronze, spark.table(W.WDI_COUNTRY), W.vintages_df(spark, vint), run_id)
    report = DQReport(TARGET)
    verify_source_files(spark, bronze, vint, report)
    check(df, bronze, report)
    out = df.drop(*W.VALIDATION_ONLY_COLUMNS)

    wdi_econ = spark.table(W.WDI_COUNTRY).filter("entity_type = 'economy'")
    not_in_wgi = sorted((r.country_code, r.country_name) for r in wdi_econ.join(df.select("country_code").distinct(), "country_code", "left_anti").collect())
    rel = {
        "wgi_entities": out.select("entity_key").distinct().count(),
        "matched_to_wdi_by_country_code": out.filter("wdi_match_status = 'matched'").select("entity_key").distinct().count(),
        "matched_wdi_entity_types": {r[0]: r[1] for r in out.filter("wdi_match_status = 'matched'").groupBy("wdi_entity_type").agg(F.countDistinct("entity_key")).collect()},
        "name_keyed_unmatched": sorted(r[0] for r in out.filter("entity_key_type = 'country_name'").select("country_name").distinct().collect()),
        "wdi_economies_not_in_wgi": not_in_wgi,
        "iso3_vs_wdi_wb_id_mismatches": out.filter("wdi_country_wb_id IS NOT NULL AND country_iso3_code != wdi_country_wb_id").count(),
    }
    years = sorted(r[0] for r in out.select("year").distinct().collect())
    summary = {
        "rows": out.count(), "years": years, "missing_years_in_range": sorted(set(range(years[0], years[-1] + 1)) - set(years)),
        "nulls_by_indicator": {r[0]: r[1] for r in out.filter("is_value_missing").groupBy("indicator_short_code").count().collect()},
        "economies_with_values_by_indicator": {r[0]: r[1] for r in out.filter("NOT is_value_missing").groupBy("indicator_short_code").agg(F.countDistinct("entity_key")).collect()},
        "range_by_indicator": {r[0]: [round(r[1], 4), round(r[2], 4)] for r in out.groupBy("indicator_short_code").agg(F.min("value"), F.max("value")).collect()},
        "exact_zero_values": out.filter("value = 0").count(),
    }

    print(f"run_id={run_id} mode={args.mode} transform={W.TRANSFORM_VERSION}")
    print(f"\n== proposed schema: {TARGET}")
    for f in out.schema.fields:
        print(f"   {f.name:24s} {f.dataType.simpleString():10s} nullable={f.nullable}")
    print("\n== summary\n" + json.dumps(summary, indent=1, default=str))
    print("== WDI relationship (exact country_code only)\n" + json.dumps(rel, indent=1, default=str))
    print("== sample")
    (out.filter("(entity_key IN ('NZ', 'name:Taiwan, China', 'SO') AND year IN (1996, 2025)) OR (is_value_missing AND year = 1996 AND entity_key = 'TL')")
        .select("entity_key", "entity_key_type", "country_iso3_code", "country_name", "wdi_match_status", "indicator_short_code", "year",
                "value", "is_value_missing", "source_lastupdated").orderBy("entity_key", "indicator_short_code", "year").show(40, truncate=False))
    print(report.render())
    (run_dir / "dq_report.json").write_text(json.dumps({"run_id": run_id, "mode": args.mode, "transform_version": W.TRANSFORM_VERSION,
                                                        "target": TARGET, "summary": summary, "wdi_relationship": rel,
                                                        "dq": report.to_dict()}, indent=1, default=str))
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
                 {"silver.run_id": run_id, "silver.transform_version": W.TRANSFORM_VERSION, "silver.source_table": W.BRONZE_TABLE,
                  "silver.source_vintage": "2026-09-25", "silver.decisions": "D-28,D-30,D-31"},
                 "Worldwide Governance Indicators estimates (6 dimensions), all published economies and years; values as published, "
                 "NULL = not published; 1997/1999/2001 not published by WGI",
                 COLUMN_COMMENTS, NOT_NULL, CONSTRAINTS)
    print(f"written {TARGET}: {spark.table(TARGET).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
