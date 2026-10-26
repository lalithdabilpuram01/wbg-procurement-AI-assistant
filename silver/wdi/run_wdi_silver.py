"""Run the WDI Silver transformation over Databricks Connect (Serverless).

Default is a DRY RUN: reads Bronze, builds both Silver DataFrames, runs all data-quality checks,
prints the schema and samples, and saves a JSON report locally. Nothing is written to Databricks.

Writing requires all of:
  --mode write --confirm-create
  every "error" check passing
  the target table NOT existing yet (existing tables are never overwritten or replaced)

Usage (from procurement-intelligence/):
  python -m silver.wdi.run_wdi_silver                        # dry run
  python -m silver.wdi.run_wdi_silver --mode write --confirm-create
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.wdi import transform as W
from silver.wdi.checks import check_countries, check_observations

TARGET_SCHEMA = "workspace.wbg_silver"
TARGETS = {"observations": f"{TARGET_SCHEMA}.wdi_indicator_observations", "countries": f"{TARGET_SCHEMA}.wdi_country"}
RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"
NOT_NULL = {
    TARGETS["observations"]: ["country_code", "country_wb_id", "entity_type", "indicator_code", "year", "is_value_missing",
                              "source_lastupdated", "bronze_source_file", "bronze_ingested_at", "raw_record_sha256"],
    TARGETS["countries"]: ["country_code", "country_wb_id", "entity_type"],
}

COLUMN_COMMENTS = {
    "observations": {
        "country_code": "World Bank API country.id (2-character). Key component; present for every economy and aggregate",
        "country_iso3_code": "countryiso3code as published; NULL where blank (5 income-group aggregates). Not a key",
        "country_wb_id": "3-character World Bank id from /v2/country (e.g. NZL, HIC); present for all entities",
        "entity_type": "economy | aggregate (aggregate = region.id 'NA' in /v2/country)",
        "value": "Indicator value as published (DOUBLE); NULL = not published for that country-year",
        "is_value_missing": "TRUE when value is NULL; missing values are never imputed",
        "source_lastupdated": "API page-header lastupdated of the source response (source vintage)",
        "raw_record_sha256": "SHA-256 of the Bronze raw_record string (row-level lineage)",
    },
    "countries": {
        "country_code": "2-character World Bank code; joins to observations.country_code",
        "income_level_code_current": "Income level at metadata retrieval (today's value) - NOT historical",
        "lending_type_code_current": "Lending type at metadata retrieval (today's value) - NOT historical",
    },
}


def build(spark, run_id: str):
    b = W.BRONZE
    obs_bronze = spark.table(f"{b}.wdi_indicator_observations")
    countries = W.parse_country_metadata(spark.table(f"{b}.wdi_country_metadata"))
    vintages = W.parse_vintages(spark.table(f"{b}.wdi_api_responses"))
    indicators = W.parse_indicator_metadata(spark.table(f"{b}.wdi_indicator_metadata"))
    obs = W.build_observations(obs_bronze, countries, vintages, indicators, run_id)
    cty = W.build_countries(countries, obs, run_id)
    return obs_bronze, obs, cty


def write_table(spark, df, target: str, run_id: str, comments: dict, table_comment: str) -> None:
    if spark.catalog.tableExists(target):
        raise SystemExit(f"{target} already exists: refusing to overwrite (no replace mode without explicit approval)")
    (df.writeTo(target).using("delta")
       .tableProperty("silver.run_id", run_id).tableProperty("silver.transform_version", W.TRANSFORM_VERSION)
       .tableProperty("silver.source_schema", W.BRONZE).tableProperty("silver.decisions", "D-28,D-30,D-31")
       .create())
    spark.sql(f"COMMENT ON TABLE {target} IS '{table_comment}'")
    for col, text in comments.items():
        spark.sql(f"ALTER TABLE {target} ALTER COLUMN {col} COMMENT '{text.replace(chr(39), '')}'")
    # enforce in Delta what the DQ checks proved (keys, classification, lineage never null)
    for col in NOT_NULL[target]:
        spark.sql(f"ALTER TABLE {target} ALTER COLUMN {col} SET NOT NULL")
    spark.sql(f"ALTER TABLE {target} ADD CONSTRAINT entity_type_valid CHECK (entity_type IN ('economy', 'aggregate'))")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry-run", "write"], default="dry-run")
    ap.add_argument("--confirm-create", action="store_true", help="required with --mode write")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--sample", type=int, default=5)
    args = ap.parse_args()
    if args.mode == "write" and not args.confirm_create:
        ap.error("--mode write requires --confirm-create")

    run_id = "wdi_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    spark = get_spark(args.profile)
    obs_bronze, obs, cty = build(spark, run_id)

    obs_report = DQReport(TARGETS["observations"])
    check_observations(obs, obs_bronze, obs_report)
    cty_report = DQReport(TARGETS["countries"])
    check_countries(cty, obs, cty_report)

    obs_out = obs.drop(*W.VALIDATION_ONLY_COLUMNS)
    print(f"run_id={run_id} mode={args.mode} profile={args.profile} transform={W.TRANSFORM_VERSION}\n")
    for name, df in (("observations", obs_out), ("countries", cty)):
        print(f"== proposed schema: {TARGETS[name]}")
        for f in df.schema.fields:
            print(f"   {f.name:28s} {f.dataType.simpleString():14s} nullable={f.nullable}")
    print(f"\n== sample: {TARGETS['observations']} (incl. a missing value and a blank-ISO3 aggregate)")
    sample = (obs_out.filter("is_value_missing").limit(2)
              .unionByName(obs_out.filter("country_iso3_code IS NULL").limit(2))
              .unionByName(obs_out.filter("entity_type = 'economy' AND NOT is_value_missing").limit(args.sample)))
    sample.select("country_code", "country_iso3_code", "country_wb_id", "country_name", "entity_type", "indicator_code",
                  "year", "value", "is_value_missing", "source_lastupdated").show(20, truncate=False)
    print(obs_report.render())
    print(cty_report.render())

    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "dq_report.json").write_text(json.dumps(
        {"run_id": run_id, "mode": args.mode, "transform_version": W.TRANSFORM_VERSION, "targets": TARGETS,
         "observations": obs_report.to_dict(), "countries": cty_report.to_dict()}, indent=1, default=str))
    print(f"\nreport: {run_dir / 'dq_report.json'}")

    if not (obs_report.passed and cty_report.passed):
        print("DQ FAILED: nothing written")
        return 1
    if args.mode == "dry-run":
        print("DRY RUN: nothing written to Databricks")
        return 0
    write_table(spark, obs_out, TARGETS["observations"], run_id, COLUMN_COMMENTS["observations"],
                "WDI GDP growth, inflation, GDP per capita (2000-2025), all economies and aggregates; typed from wbg_ref_bronze")
    write_table(spark, cty, TARGETS["countries"], run_id, COLUMN_COMMENTS["countries"],
                "WDI country and aggregate reference from /v2/country; *_current attributes are not historical")
    for name, t in TARGETS.items():
        print(f"written {t}: {spark.table(t).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
