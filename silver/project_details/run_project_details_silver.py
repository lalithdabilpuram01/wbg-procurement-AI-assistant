"""Run the project_details_v3 / project_loan_transactions Silver transformations (Databricks Connect, Serverless).

Default is a DRY RUN: records the pre-write baseline (all Bronze + existing Silver tables, column masks, cohort),
verifies the Volume JSONL against the extraction manifest (SHA-256 + every line equal to a Bronze raw_record), builds
both DataFrames, runs all checks (incl. a personal-data scan over every output value, the Delta CHECK constraints and
NOT NULL columns evaluated on the data), writes the known-anomaly list, prints schemas and summaries, and saves
reports. Nothing is written to Databricks.

Writing requires --mode write --confirm-create, all "error" checks passing, and neither target existing (create-only).
wbg_silver.projects / ibrd_loans / ida_credits are read for validation only; `projects` is never modified.

Usage (from procurement-intelligence/):
  python -m silver.project_details.run_project_details_silver
  python -m silver.project_details.run_project_details_silver --mode write --confirm-create
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

from pyspark.sql import functions as F

from silver.common import pipeline
from silver.common.dq import DQReport
from silver.common.pipeline import CATALOG, runs_dir
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.common.sourcefiles import verify_jsonl_source_file
from silver.common.write import create_table
from silver.project_details import transform as T
from silver.project_details.checks import check_details, check_transactions, personal_data_scan, personal_values

S = f"{CATALOG}.wbg_silver"
TARGETS = {"details": f"{S}.project_details_v3", "transactions": f"{S}.project_loan_transactions"}
EXPECTED = {"details": 184, "transactions": 16643}
RUNS = runs_dir(Path(__file__).resolve().parents[1] / "runs")
EXISTING_SILVER = ["wdi_indicator_observations", "wdi_country", "income_classification_history", "wgi_estimates", "cpia_ratings", "projects",
                   "contract_award_lines", "contract_awards", "contract_awards_legacy", "procurement_notices", "procurement_notice_details",
                   "notice_contract_link_candidates", "notice_contract_links", "ieg_project_ratings", "ibrd_loans", "ida_credits"]
COHORT = T.REPO / "cohort" / "poc_cohort_2000_manifest.csv"
LINEAGE_NN = ["source_api", "raw_record_sha256", "bronze_source_file", "bronze_ingested_at", "bronze_input_file_sha256"]
PID = "project_id RLIKE '^P[0-9]{6}$'"
SPEC: dict[str, dict[str, Any]] = {
    "details": {
        "not_null": ["project_id", "theme_level1_count", "indicator_count", "geo_location_count", "loan_transaction_count", "detail_coverage",
                     "record_snapshot_semantics", *LINEAGE_NN],
        "constraints": {
            "project_id_format": PID,
            "counts_non_negative": "theme_level1_count >= 0 AND indicator_count >= 0 AND geo_location_count >= 0 AND loan_transaction_count >= 0",
            "risk_rating_valid": "environmental_social_risk_rating IS NULL OR environmental_social_risk_rating IN ('H', 'S', 'M', 'L')",
            "parent_project_format": "parent_project_id IS NULL OR parent_project_id RLIKE '^P[0-9]{6}$'",
            "detail_coverage_value": f"detail_coverage = '{T.DETAIL_COVERAGE}'",
            "snapshot_semantics_value": f"record_snapshot_semantics = '{T.RECORD_SEMANTICS}'",
        },
        "comment": "Additional v3 project fields (fl=*) NOT in wbg_silver.projects, one row per project, for 184 cohort projects only (Phase 1 notice cluster; "
                   "non-random partial coverage: absence for the other 1,816 projects is not zero / no risk). Latest snapshot retrieved 2026-10-21; no history. "
                   "No personal identifiers; raw JSON not copied",
        "columns": {
            "environmental_social_risk_rating": "E&S risk rating as published (H/S/M/L); latest snapshot, may have been revised during implementation",
            "product_line_code": "Product line as published (e.g. PE, RE, GE, SF)",
            "themes": "Theme hierarchy (levels 1-3) in original order; authoritative theme structure. seqno/percent as published integers; percent is not a share (a level-1 percent is about the sum of its level-2 children and can exceed 100). Absent child arrays are NULL",
            "themes_level2": "themev2_level2_exact labels as published; not 1:1 with themes.level2 (use themes for the hierarchy)",
            "themes_level3": "themev2_level3_exact labels as published; not 1:1 with themes.level3 (use themes for the hierarchy)",
            "results_indicators": "Results indicators in original order (indicator_index = source position); values kept as published text; current values are the latest snapshot. NULL = no indicators array published",
            "geo_locations": "Geo-locations in original order (location_index = source position); no natural unique id. NULL = no geo_locations array published",
            "theme_level1_count": "Number of level-1 themes; 0 with a NULL array means the source published no array (information unavailable), not necessarily a genuine zero",
            "indicator_count": "Number of results indicators; 0 with a NULL array means the source published no array (information unavailable), not necessarily a genuine zero",
            "geo_location_count": "Number of geo-locations; 0 with a NULL array means the source published no array (information unavailable), not necessarily a genuine zero",
            "loan_transaction_count": "Number of loan_transactions elements (rows in project_loan_transactions); 0 with a NULL array means the source published no array (information unavailable), not necessarily a genuine zero",
            "detail_coverage": "phase1_cluster_detail_pull: detail exists only for the Phase 1 cluster projects",
            "raw_record_sha256": "SHA-256 of the original source record (lineage; the raw JSON is not copied: it contains staff identifiers)"},
    },
    "transactions": {
        "not_null": ["transaction_key", "project_id", "transaction_index", "financier_loan", "financing_number", "transaction_type",
                     "transaction_period_published", "transaction_date_is_placeholder", "amount_published", "identical_transaction_group_size",
                     "transaction_snapshot_semantics", *LINEAGE_NN],
        "constraints": {
            "project_id_format": PID,
            "transaction_key_consistent": "transaction_key = concat(project_id, ':', cast(transaction_index AS STRING))",
            "index_non_negative": "transaction_index >= 0",
            "financing_number_consistent": "financing_number = replace(financier_loan, '-', '')",
            "transaction_type_valid": "transaction_type IN (" + ", ".join(f"'{x}'" for x in T.TRANSACTION_TYPES) + ")",
            "placeholder_consistent": "(transaction_date_is_placeholder AND transaction_date IS NULL) OR (NOT transaction_date_is_placeholder AND transaction_date IS NOT NULL)",
            "identical_group_positive": "identical_transaction_group_size >= 1",
        },
        "comment": "Project loan_transactions as published in the v3 snapshot retrieved 2026-10-21 (134 cohort projects), one row per source array element. "
                   "Identical rows are kept (no source transaction id). Dates are dated records from the snapshot and do not prove historical availability",
        "columns": {
            "transaction_key": "Technical key project_id:transaction_index (source-array position); may change on an API refresh",
            "financier_loan": "Loan reference exactly as published (e.g. IDA-12345, TF-A9875)",
            "financing_number": "financier_loan without hyphens; join key to ibrd_loans.loan_number / ida_credits.credit_number (227/230 match; others not forced)",
            "transaction_date": "Date part of the published period; NULL for the 1900-01-01 placeholder. A dated record from a 2026-10-21 snapshot, not proof of when it became available",
            "amount_published": "Amount string exactly as published (may use scientific notation)",
            "amount": "Exact decimal of amount_published, sign preserved. Currency/unit NOT independently verified (commitments agree with IBRD/IDA original "
                      "principal US$ for 223/224 loans: evidence only); do not assume USD or convert",
            "identical_transaction_group_size": "Rows in the project sharing every published field (>=1); not de-duplicated"},
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

    run_id = "project_details_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(args.profile)
    baseline: dict = {"bronze": {}, "silver": {}, "targets_exist": {k: spark.catalog.tableExists(v) for k, v in TARGETS.items()},
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
    rep = DQReport(", ".join(TARGETS.values()))
    rep.equals("targets_absent_before_write", baseline["targets_exist"], {k: False for k in TARGETS})
    rep.equals("baseline_table_counts (Bronze, existing Silver)", (len(baseline["bronze"]), len(baseline["silver"])), (17, 16))
    rep.equals("bronze_rows", bronze.count(), EXPECTED["details"])
    rep.equals("cohort_manifest_project_count", len(cohort_ids), 2000)
    verify_jsonl_source_file(spark, bronze, prov["bronze_input_file_sha256"], "projects_v3_allfields", rep)
    details = T.build_details(bronze, prov, run_id)
    tx = T.build_transactions(bronze, prov, run_id)
    check_details(details, bronze, projects, cohort_ids, prov, rep, EXPECTED["details"])
    link = check_transactions(tx, details, spark.table(f"{S}.ibrd_loans"), spark.table(f"{S}.ida_credits"), rep, EXPECTED["transactions"])
    out = {"details": T.drop_validation(details), "transactions": T.drop_validation(tx)}
    names, upis = personal_values(bronze)
    personal_data_scan(out, names, upis, rep)
    for k, df in out.items():
        rep.equals(f"{k}:delta_check_constraints_hold_before_write", {c: df.filter(f"NOT coalesce(({e}), false)").count() for c, e in SPEC[k]["constraints"].items()},
                   {c: 0 for c in SPEC[k]["constraints"]})
        rep.equals(f"{k}:not_null_columns_have_no_nulls", {c: df.filter(f"`{c}` IS NULL").count() for c in SPEC[k]["not_null"]}, {c: 0 for c in SPEC[k]["not_null"]})
    tdf, ddf = out["transactions"], out["details"]
    geo = ddf.select("project_id", F.explode("geo_locations").alias("g"))
    ind = ddf.select("project_id", F.explode("results_indicators").alias("x"))
    anomalies = {
        "coverage": {"projects_with_detail": ddf.count(), "cohort_projects_without_detail": 2000 - ddf.count(),
                     "projects_with_loan_transactions": tdf.select("project_id").distinct().count()},
        # keyed by the group's transaction keys (positions), the same identity as the window: loan, period, type, published amount
        "identical_transaction_groups": sorted(",".join(sorted(r.keys, key=lambda k: int(k.split(":")[1]))) for r in tdf.filter("identical_transaction_group_size > 1")
                                               .groupBy("project_id", "financier_loan", "transaction_period_published", "transaction_type", "amount_published")
                                               .agg(F.collect_list("transaction_key").alias("keys")).collect()),
        "negative_amount_transaction_keys_by_type": {r[0]: r[1] for r in tdf.filter("amount < 0").groupBy("transaction_type").agg(F.sort_array(F.collect_list("transaction_key"))).collect()},
        "placeholder_date_transaction_keys": sorted(r[0] for r in tdf.filter("transaction_date_is_placeholder").select("transaction_key").collect()),
        "transactions_dated_after_2026_09_30": sorted(r[0] for r in tdf.filter("transaction_date > DATE'2026-09-30'").select("transaction_key").collect()),
        "references_not_in_ibrd_ida_tables": link["unmatched_references"],
        "geo_repeated_geo_loc_id_within_project": {f"{r.project_id}|{r.geo_loc_id}": r.c for r in geo.groupBy("project_id", F.col("g.geo_loc_id").alias("geo_loc_id"))
                                                   .agg(F.count("*").alias("c")).filter("c > 1").collect()},
        "theme_percent_over_100": sorted([f"L1|{r.project_id}|{r.t.code}|{r.t.percent}" for r in ddf.select("project_id", F.explode("themes").alias("t"))
                                          .filter("t.percent > 100").collect()]
                                         + [f"L2|{r.project_id}|{r.t.code}|{r.t.percent}" for r in ddf.select("project_id", F.explode("themes").alias("t1"))
                                            .select("project_id", F.explode("t1.level2").alias("t")).filter("t.percent > 100").collect()]),
        "geo_zero_zero_points": [f"{r.project_id}|{r.i}" for r in geo.filter("g.latitude = 0 AND g.longitude = 0").select("project_id", F.col("g.location_index").alias("i")).collect()],
        "indicator_name_type_repeats": sorted(f"{r.project_id}|" + ",".join(map(str, sorted(r.idx))) for r in ind.groupBy(
            "project_id", F.col("x.name").alias("n"), F.col("x.indicator_type").alias("t")).agg(F.collect_list("x.indicator_index").alias("idx")).filter("size(idx) > 1").collect()),
    }
    (run_dir / "known_anomalies.json").write_text(json.dumps(anomalies, indent=1, default=str))
    summary = {"rows": {k: v.count() for k, v in out.items()}, "references": link,
               "nested_elements": ddf.agg(F.sum("theme_level1_count").alias("themes_l1"), F.sum("indicator_count").alias("indicators"),
                                        F.sum("geo_location_count").alias("geo"), F.sum("loan_transaction_count").alias("transactions")).collect()[0].asDict(),
               "anomaly_counts": {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in anomalies.items() if k != "coverage"} | anomalies["coverage"]}
    print(f"run_id={run_id} mode={args.mode} transform={T.TRANSFORM_VERSION}")
    for k, df in out.items():
        print(f"\n== proposed schema: {TARGETS[k]} ({len(df.columns)} columns)")
        for fld in df.schema.fields:
            print(f"   {fld.name:36s} {fld.dataType.simpleString()}")
    print("\n== summary\n" + json.dumps(summary, indent=1, default=str))
    print(rep.render())
    (run_dir / "dq_report.json").write_text(json.dumps({"run_id": run_id, "mode": args.mode, "transform_version": T.TRANSFORM_VERSION, "targets": TARGETS,
                                                        "summary": summary, "constraints": {k: SPEC[k]["constraints"] for k in SPEC}, "dq": rep.to_dict()},
                                                       indent=1, default=str))
    print(f"\nreports: {run_dir}")
    if not rep.passed:
        print("DQ FAILED: nothing written")
        return 1
    if args.mode == "dry-run":
        print("DRY RUN: nothing written to Databricks")
        return 0
    if any(baseline["targets_exist"].values()) and not pipeline.ENABLED:
        raise SystemExit(f"target(s) already exist: {baseline['targets_exist']}; refusing to overwrite (create-only)")
    for k in ("details", "transactions"):
        sp = SPEC[k]
        create_table(spark, out[k], TARGETS[k],
                     {"silver.run_id": run_id, "silver.transform_version": T.TRANSFORM_VERSION, "silver.source_table": T.BRONZE,
                      "silver.decisions": "D-26; 184-project non-random coverage; latest snapshot 2026-10-21; personal identifiers excluded"},
                     sp["comment"], sp["columns"], sp["not_null"], sp["constraints"])
        print(f"written {TARGETS[k]}: {spark.table(TARGETS[k]).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
