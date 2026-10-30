"""Run the contract-award Silver transformations (Databricks Connect, Serverless).

Default is a DRY RUN: records the pre-write baseline, verifies both Volume CSVs against the extraction manifest
(SHA-256 + every record equal to a Bronze row), builds the three Silver DataFrames, runs all checks, prints
schemas and summaries, and saves reports. Nothing is written.

Writing requires --mode write --confirm-create, all "error" checks passing, and none of the three targets existing.

Usage (from procurement-intelligence/):
  python -m silver.contracts.run_contracts_silver
  python -m silver.contracts.run_contracts_silver --mode write --confirm-create
"""
from __future__ import annotations

import argparse
import hashlib
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
from silver.common.sourcefiles import verify_csv_source_file
from silver.common.write import create_table
from silver.contracts import transform as T
from silver.contracts.checks import check_contracts, check_legacy, check_lines

S = f"{CATALOG}.wbg_silver"
TARGETS = {"lines": f"{S}.contract_award_lines", "contracts": f"{S}.contract_awards", "legacy": f"{S}.contract_awards_legacy"}
RUNS = runs_dir(Path(__file__).resolve().parents[1] / "runs")
EXISTING_SILVER = ["wdi_indicator_observations", "wdi_country", "income_classification_history", "wgi_estimates", "cpia_ratings", "projects"]
COHORT = T.REPO / "cohort" / "poc_cohort_2000_manifest.csv"
SPEC: dict[str, dict[str, Any]] = {
    "lines": {
        "not_null": ["line_key", "wb_contract_number", "supplier_id_type", "is_amount_missing", "contract_supplier_line_count", "project_id",
                     "source_row_sha256", "bronze_source_file", "bronze_ingested_at", "bronze_input_file_sha256"],
        "constraints": {"project_id_format": "project_id RLIKE '^P[0-9]{6}$'",
                        "supplier_id_type_valid": "supplier_id_type IN ('numeric', 'anonymized_individual_consultant', 'missing')",
                        "missing_flag_consistent": "is_amount_missing = (supplier_amount_usd IS NULL)",
                        "amount_non_negative": "supplier_amount_usd IS NULL OR supplier_amount_usd >= 0"},
        "comment": "DS00005 contract awards at contract x supplier-line grain (supplier share of the contract amount); all 2,000 cohort projects",
        "columns": {"line_key": "wb_contract_number:supplier_id (NO_SUPPLIER_ID-<hash of name> for the 1 line without a supplier ID)",
                    "supplier_amount_usd": "Supplier share of the contract amount as published (sum of lines = contract amount); NULL = blank in source",
                    "supplier_id_type": "numeric | anonymized_individual_consultant (16-hex IDs, name INDIVIDUAL CONSULTANT) | missing",
                    "source_row_sha256": "SHA-256 of the published CSV field values (row-level lineage)"},
    },
    "contracts": {
        "not_null": ["wb_contract_number", "project_id", "supplier_line_count", "is_multi_supplier", "contract_amount_basis", "bronze_source_file"],
        "constraints": {"project_id_format": "project_id RLIKE '^P[0-9]{6}$'", "line_count_positive": "supplier_line_count >= 1",
                        "amount_non_negative": "contract_amount_usd IS NULL OR contract_amount_usd >= 0"},
        "comment": "DS00005 contract awards, one row per WB Contract Number; contract_amount_usd = sum of its supplier lines (do not add line amounts again)",
        "columns": {"contract_amount_usd": "Sum of supplier-line amounts (supplier shares); the only contract-level amount",
                    "contracts_sharing_reference_in_project": "Contracts in the same project with the same published reference; not interpreted (lots, repeats, frameworks)"},
    },
    "legacy": {
        "not_null": ["contract_number", "project_id", "coverage_scope", "source_row_sha256", "bronze_source_file", "bronze_ingested_at"],
        "constraints": {"project_id_format": "project_id RLIKE '^P[0-9]{6}$'", "amount_non_negative": "contract_amount_usd IS NULL OR contract_amount_usd >= 0"},
        "comment": "DS01004 legacy major contract awards (prior review only, FY2006-2016, as of 2022-03-11), one row per contract; separate source, not merged with DS00005",
        "columns": {"supplier_text": "Supplier as published (may name several JV members; no supplier ID in this source)",
                    "coverage_scope": "prior_review_only: DS01004 covers Bank prior-review contracts only"},
    },
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry-run", "write"], default="dry-run")
    ap.add_argument("--confirm-create", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    if args.mode == "write" and not args.confirm_create:
        ap.error("--mode write requires --confirm-create")

    run_id = "contracts_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(args.profile)
    baseline: dict = {"bronze": {}, "silver": {}, "targets_exist": {k: spark.catalog.tableExists(v) for k, v in TARGETS.items()},
                      "cohort_manifest_sha256": hashlib.sha256(COHORT.read_bytes()).hexdigest()}
    for group, tables in (("bronze", [T.BRONZE_DS5, T.BRONZE_DS1]), ("silver", [f"{S}.{t}" for t in EXISTING_SILVER])):
        for t in tables:
            h = spark.sql(f"DESCRIBE HISTORY {t} LIMIT 1").collect()[0]
            baseline[group][t] = {"version": h.version, "rows": spark.table(t).count()}
    (run_dir / "baseline_before_write.json").write_text(json.dumps(baseline, indent=1))

    ds5, ds1 = spark.table(T.BRONZE_DS5), spark.table(T.BRONZE_DS1)
    prov5, prov1 = T.load_provenance("bronze_contract_awards_ds00005"), T.load_provenance("bronze_contract_awards_legacy_ds01004")
    projects = spark.table(f"{S}.projects").select("project_id", "country_code", "geography_type")
    rep = DQReport(", ".join(TARGETS.values()))
    verify_csv_source_file(spark, ds5, T.source_columns(ds5), prov5["bronze_input_file_sha256"], "ds00005", rep)
    verify_csv_source_file(spark, ds1, T.source_columns(ds1), prov1["bronze_input_file_sha256"], "ds01004", rep)
    lines = T.build_lines(ds5, prov5, run_id)
    contracts = T.build_contracts(lines, run_id)
    legacy = T.build_legacy(ds1, prov1, run_id)
    check_lines(lines, ds5, projects, rep)
    check_contracts(contracts, lines, rep)
    check_legacy(legacy, ds1, projects, contracts, rep)
    out = {"lines": T.drop_validation(lines), "contracts": T.drop_validation(contracts), "legacy": T.drop_validation(legacy)}

    summary = {
        "lines": out["lines"].count(), "contracts": out["contracts"].count(), "legacy": out["legacy"].count(),
        "distinct_supplier_ids_ds00005": out["lines"].select("supplier_id").distinct().count(),
        "projects_ds00005": out["lines"].select("project_id").distinct().count(), "projects_legacy": out["legacy"].select("project_id").distinct().count(),
        "notice_linkage_fields": {
            "ds00005_contracts_with_reference": out["contracts"].filter("borrower_contract_reference IS NOT NULL").count(),
            "ds00005_distinct_project_reference_keys": out["contracts"].select("project_id", "borrower_contract_reference").distinct().count(),
            "legacy_contracts_with_reference": out["legacy"].filter("borrower_contract_reference IS NOT NULL").count()},
        "amounts_usd": {"ds00005_total": str(out["contracts"].agg(F.sum("contract_amount_usd")).collect()[0][0]),
                        "legacy_total": str(out["legacy"].agg(F.sum("contract_amount_usd")).collect()[0][0])},
    }
    print(f"run_id={run_id} mode={args.mode} transform={T.TRANSFORM_VERSION}")
    for k, df in out.items():
        print(f"\n== proposed schema: {TARGETS[k]}")
        for f in df.schema.fields:
            print(f"   {f.name:40s} {f.dataType.simpleString()[:70]}")
    print("\n== summary\n" + json.dumps(summary, indent=1, default=str))
    print(rep.render())
    (run_dir / "dq_report.json").write_text(json.dumps({"run_id": run_id, "mode": args.mode, "transform_version": T.TRANSFORM_VERSION,
                                                        "targets": TARGETS, "provenance": {"ds00005": prov5, "ds01004": prov1},
                                                        "summary": summary, "dq": rep.to_dict()}, indent=1, default=str))
    print(f"\nreports: {run_dir}")
    if not rep.passed:
        print("DQ FAILED: nothing written")
        return 1
    if args.mode == "dry-run":
        print("DRY RUN: nothing written to Databricks")
        return 0
    if any(baseline["targets_exist"].values()) and not pipeline.ENABLED:
        raise SystemExit(f"target(s) already exist: {baseline['targets_exist']}; refusing to overwrite")
    for k in ("lines", "contracts", "legacy"):
        spec = SPEC[k]
        create_table(spark, out[k], TARGETS[k],
                     {"silver.run_id": run_id, "silver.transform_version": T.TRANSFORM_VERSION,
                      "silver.source_table": T.BRONZE_DS1 if k == "legacy" else T.BRONZE_DS5, "silver.decisions": "D-03,D-04,D-06,D-26"},
                     spec["comment"], spec["columns"], spec["not_null"], spec["constraints"])
        print(f"written {TARGETS[k]}: {spark.table(TARGETS[k]).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
