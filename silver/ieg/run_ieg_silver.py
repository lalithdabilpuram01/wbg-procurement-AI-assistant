"""Run the IEG ratings Silver transformation (Databricks Connect, Serverless).

Default is a DRY RUN: records the pre-write baseline, verifies the Volume CSV against the extraction manifest
(SHA-256 + every record equal to a Bronze row), builds the Silver DataFrame, runs all checks, writes the known-anomaly
list, prints the schema and summaries, and saves reports. Nothing is written to Databricks.

Writing requires --mode write --confirm-create, all "error" checks passing, and the target not existing (create-only).
Silver `projects` is read for validation only.

Usage (from procurement-intelligence/):
  python -m silver.ieg.run_ieg_silver
  python -m silver.ieg.run_ieg_silver --mode write --confirm-create
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from silver.common import pipeline
from silver.common.dq import DQReport
from silver.common.pipeline import CATALOG, runs_dir
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.common.sourcefiles import verify_csv_source_file
from silver.common.write import create_table
from silver.ieg import transform as T
from silver.ieg.checks import check, known_anomalies

S = f"{CATALOG}.wbg_silver"
TARGET = f"{S}.ieg_project_ratings"
RUNS = runs_dir(Path(__file__).resolve().parents[1] / "runs")
EXISTING_SILVER = ["wdi_indicator_observations", "wdi_country", "income_classification_history", "wgi_estimates", "cpia_ratings", "projects",
                   "contract_award_lines", "contract_awards", "contract_awards_legacy", "procurement_notices", "procurement_notice_details",
                   "notice_contract_link_candidates", "notice_contract_links"]
COHORT = T.REPO / "cohort" / "poc_cohort_2000_manifest.csv"
EXPECTED_ROWS = 750
SCORE_COLS = [f"{stem}_score" for stem, _ in T.RATING_FIELDS.values()]
SPEC: dict[str, Any] = {
    "not_null": ["project_id", "evaluation_type", "evaluation_fiscal_year", "approval_fiscal_year", "source_as_of_date", "rating_snapshot_semantics",
                 "source_dataset", "source_row_sha256", "bronze_source_file", "bronze_ingested_at", "bronze_input_file_sha256"],
    "constraints": {
        "project_id_format": "project_id RLIKE '^P[0-9]{6}$'",
        "six_point_score_range": " AND ".join(f"({c} IS NULL OR {c} BETWEEN 1 AND 6)" for c in SCORE_COLS if not c.startswith("me_")),
        "me_score_range": "me_quality_score IS NULL OR me_quality_score BETWEEN 1 AND 4",
        "unrated_has_null_score": " AND ".join(f"(({s}_rating IS NULL OR {s}_rating = 'Not Rated') = ({s}_score IS NULL))" for s, _ in T.RATING_FIELDS.values()),
        "satisfactory_consistent": "(outcome_score IS NULL AND outcome_satisfactory IS NULL) OR outcome_satisfactory = (outcome_score >= 4)",
        "lag_consistent": "evaluation_lag_fiscal_years <=> (evaluation_fiscal_year - final_closing_fiscal_year)",
        "precedes_consistent": "evaluation_precedes_final_closing <=> (evaluation_fiscal_year < final_closing_fiscal_year)",
    },
    "comment": "IEG project evaluation ratings (DS00053, as of 2026-10-18): one row per cohort project with its LATEST IEG evaluation. "
               "Latest-only post-evaluation snapshot (earlier evaluations overwritten at source); must not be used as features for predictions "
               "made before the rating was available. Scores are ordinal codes, not continuous measures",
    "columns": {
        "evaluation_fiscal_year": "Evaluation FY as published (World Bank FY, July-June); no exact date is inferred from it",
        "rating_available_from_date": "Verified public availability date of the rating; NULL when none exists (DS00053 publishes the evaluation FY only)",
        "evaluation_precedes_final_closing": "Evaluation FY < Final Closing FY: the closing date was extended after the evaluation; the rating covers an earlier phase",
        "evaluation_lag_fiscal_years": "Evaluation FY - Final Closing FY (NULL when Final Closing FY is blank)",
        "outcome_score": "Ordinal code: Highly Unsatisfactory 1 ... Highly Satisfactory 6; Not Rated/blank -> NULL",
        "quality_at_entry_score": "Ordinal code 1-6 (same 6-point mapping)", "quality_of_supervision_score": "Ordinal code 1-6 (same 6-point mapping)",
        "bank_performance_score": "Ordinal code 1-6 (same 6-point mapping)",
        "me_quality_score": "Ordinal code: Negligible 1, Modest 2, Substantial 3, High 4; Not Rated/blank -> NULL",
        "outcome_satisfactory": "Descriptive: outcome_score >= 4 (Moderately Satisfactory or better); not an ML label",
        "evaluation_type_name": "Documented name for ICRR/PPAR; NULL for other codes (not guessed)",
        "rating_snapshot_semantics": "latest_evaluation_as_of_source_date",
        "source_row_sha256": "SHA-256 of the 21 published field values (row-level lineage)"},
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry-run", "write"], default="dry-run")
    ap.add_argument("--confirm-create", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    if args.mode == "write" and not args.confirm_create:
        ap.error("--mode write requires --confirm-create")

    run_id = "ieg_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(args.profile)
    baseline: dict = {"bronze": {}, "silver": {}, "target_exists": spark.catalog.tableExists(TARGET),
                      "cohort_manifest_sha256": hashlib.sha256(COHORT.read_bytes()).hexdigest()}
    bronze_tables = [f"{CATALOG}.{sc}.{r.tableName}" for sc in ("wbg_bronze", "wbg_ref_bronze") for r in spark.sql(f"SHOW TABLES IN {CATALOG}.{sc}").collect()]
    for group, tables in (("bronze", bronze_tables), ("silver", [f"{S}.{t}" for t in EXISTING_SILVER])):
        for t in tables:
            h = spark.sql(f"DESCRIBE HISTORY {t} LIMIT 1").collect()[0]
            baseline[group][t] = {"version": h.version, "rows": spark.table(t).count()}
    baseline["column_masks"] = sorted([r[0], r[1], r[2], r[3]] for r in spark.sql(
        f"SELECT table_schema, table_name, column_name, mask_name FROM {CATALOG}.information_schema.column_masks").collect())
    (run_dir / "baseline_before_write.json").write_text(json.dumps(baseline, indent=1))

    bronze = spark.table(T.BRONZE)
    prov = T.load_provenance()
    projects = spark.table(f"{S}.projects")
    with COHORT.open(newline="", encoding="utf-8") as f:
        cohort_ids = {r["project_id"] for r in csv.DictReader(f)}
    rep = DQReport(TARGET)
    rep.equals("target_absent_before_write", baseline["target_exists"], False)
    rep.equals("baseline_table_counts (Bronze, existing Silver)", (len(baseline["bronze"]), len(baseline["silver"])), (17, 13))
    rep.equals("bronze_ieg_rows", baseline["bronze"][T.BRONZE]["rows"], EXPECTED_ROWS)
    rep.equals("cohort_manifest_project_count", len(cohort_ids), 2000)
    verify_csv_source_file(spark, bronze, T.source_columns(bronze), prov["bronze_input_file_sha256"], "ds00053", rep)
    ieg = T.build(bronze, prov, run_id)
    check(ieg, bronze, projects, cohort_ids, prov, rep, EXPECTED_ROWS)
    out = T.drop_validation(ieg)
    # the Delta CHECK constraints must parse and hold on every row before the write (NULL counts as a violation here)
    rep.equals("delta_check_constraints_hold_before_write", {k: out.filter(f"NOT coalesce(({e}), false)").count() for k, e in SPEC["constraints"].items()},
               {k: 0 for k in SPEC["constraints"]})
    rep.equals("not_null_columns_have_no_nulls", {c: out.filter(f"`{c}` IS NULL").count() for c in SPEC["not_null"]}, {c: 0 for c in SPEC["not_null"]})
    anomalies = known_anomalies(out, projects)
    (run_dir / "known_anomalies.json").write_text(json.dumps(anomalies, indent=1))
    summary = {"rows": out.count(), "evaluation_types": {r[0]: r[1] for r in out.groupBy("evaluation_type").count().collect()},
               "known_anomaly_counts": {k: (len(v) if isinstance(v, list) else v) for k, v in anomalies.items()}}
    print(f"run_id={run_id} mode={args.mode} transform={T.TRANSFORM_VERSION}")
    print(f"\n== proposed schema: {TARGET}")
    for fld in out.schema.fields:
        print(f"   {fld.name:40s} {fld.dataType.simpleString()}")
    print("\n== summary\n" + json.dumps(summary, indent=1, default=str))
    print(rep.render())
    (run_dir / "dq_report.json").write_text(json.dumps({"run_id": run_id, "mode": args.mode, "transform_version": T.TRANSFORM_VERSION, "target": TARGET,
                                                        "provenance": prov, "summary": summary, "dq": rep.to_dict()}, indent=1, default=str))
    print(f"\nreports: {run_dir}")
    if not rep.passed:
        print("DQ FAILED: nothing written")
        return 1
    if args.mode == "dry-run":
        print("DRY RUN: nothing written to Databricks")
        return 0
    if baseline["target_exists"] and not pipeline.ENABLED:
        raise SystemExit(f"{TARGET} already exists; refusing to overwrite (create-only)")
    create_table(spark, out, TARGET,
                 {"silver.run_id": run_id, "silver.transform_version": T.TRANSFORM_VERSION, "silver.source_table": T.BRONZE,
                  "silver.decisions": "D-10,D-20,D-26; latest-only snapshot; no inferred availability date"},
                 SPEC["comment"], SPEC["columns"], SPEC["not_null"], SPEC["constraints"])
    print(f"written {TARGET}: {spark.table(TARGET).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
