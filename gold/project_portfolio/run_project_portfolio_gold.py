"""Run the gold_project_portfolio transformation (Databricks Connect, Serverless).

Default is a DRY RUN: records the baseline (17 Bronze + 18 Silver tables, column masks, cohort, Gold schema/target
absent), builds the 2,000-row DataFrame from project-level aggregates, runs all checks (incl. the Delta CHECK
constraints and NOT NULL columns evaluated on the data and a personal-data scan), prints the schema and saves reports.
Nothing is written to Databricks.

Writing requires --mode write --confirm-create, all "error" checks passing and the target not existing (create-only).
If the schema workspace.wbg_gold does not exist the write also requires --create-schema (CREATE SCHEMA, never replace).

Usage (from procurement-intelligence/):
  python -m gold.project_portfolio.run_project_portfolio_gold
  python -m gold.project_portfolio.run_project_portfolio_gold --mode write --confirm-create [--create-schema]
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

from gold.project_portfolio import transform as T
from gold.project_portfolio.checks import COUNT_COLUMNS, check
from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.common.write import create_table

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "gold" / "runs"
COHORT = REPO / "cohort" / "poc_cohort_2000_manifest.csv"
SILVER = ["wdi_indicator_observations", "wdi_country", "income_classification_history", "wgi_estimates", "cpia_ratings", "projects", "contract_award_lines",
          "contract_awards", "contract_awards_legacy", "procurement_notices", "procurement_notice_details", "notice_contract_link_candidates",
          "notice_contract_links", "ieg_project_ratings", "ibrd_loans", "ida_credits", "project_details_v3", "project_loan_transactions"]
NOT_NULL = ["project_id", "geography_type", "country_context_status", "income_group_status_at_approval", *COUNT_COLUMNS, "contract_awards_coverage",
            "has_api_notice_details", "has_ibrd_ida_financing", "has_ieg_rating", "has_project_details_v3", "projects_source_retrieved_at",
            "portfolio_snapshot_semantics", "projects_raw_record_sha256", "silver_source_versions", "gold_run_id", "transform_version"]
CONSTRAINTS = {
    "project_id_format": "project_id RLIKE '^P[0-9]{6}$'",
    "counts_non_negative": " AND ".join(f"{c} >= 0" for c in COUNT_COLUMNS),
    "notice_type_counts_sum": " + ".join(T.NOTICE_TYPES.values()) + " = notice_count",
    "contract_amount_null_iff_no_contracts": "(contract_award_count = 0) = (contract_award_amount_usd IS NULL)",
    "contract_amount_non_negative": "contract_award_amount_usd IS NULL OR contract_award_amount_usd >= 0",
    "country_semantics": "(geography_type = 'single_country' AND country_context_status IN ('single_country_matched', 'single_country_unmatched')) OR "
                         "(geography_type <> 'single_country' AND country_context_status = 'regional_not_applicable' AND wdi_country_code IS NULL "
                         "AND wdi_country_iso3 IS NULL AND income_group_code_at_approval IS NULL AND income_group_status_at_approval = 'regional_not_applicable')",
    "api_detail_consistent": "has_api_notice_details = (api_notice_detail_count IS NOT NULL)",
    "financing_flag_consistent": "has_ibrd_ida_financing = (ibrd_loan_count + ida_credit_count > 0)",
    "ieg_null_when_unrated": "has_ieg_rating OR (ieg_outcome_rating IS NULL AND ieg_outcome_score IS NULL AND ieg_evaluation_fiscal_year IS NULL)",
    "details_null_when_uncovered": "has_project_details_v3 OR (product_line_code IS NULL AND environmental_social_risk_rating IS NULL AND results_indicator_count IS NULL "
                                   "AND geo_location_count IS NULL AND loan_transaction_count IS NULL)",
    "risk_rating_valid": "environmental_social_risk_rating IS NULL OR environmental_social_risk_rating IN ('H', 'S', 'M', 'L')",
    "snapshot_semantics_value": f"portfolio_snapshot_semantics = '{T.SNAPSHOT_SEMANTICS}'",
    "contract_coverage_value": f"contract_awards_coverage = '{T.CONTRACT_COVERAGE}'",
}
SNAP = " Current-state snapshot value: not a historical ML feature."
COMMENTS: dict[str, Any] = {
    "table": "Current-state project portfolio, one row per cohort project (2,000; D-26). Accumulating snapshot as of source retrievals 2026-10 "
             "(projects 2026-10-21, contracts 2026-10-20, financing end of period 2026-09-30, IEG 2026-10-18). Each source aggregated to project_id before "
             "joining; contract values, IBRD/IDA counts and project commitments are separate measures and never combined. No personal or free-text fields",
    "columns": {
        "project_id": "World Bank project ID; unique; equals the approved 2,000-project cohort",
        "geography_type": "single_country or regional_multicountry (from wbg_silver.projects)",
        "source_country_code": "Country code as published by the projects API (regional codes such as 3E are not WDI countries)",
        "wdi_country_code": "Validated WDI country code (wbg_silver.projects.wdi_country_code); NULL for regional projects",
        "wdi_country_iso3": "ISO3 / WB id from wbg_silver.wdi_country.country_wb_id; NULL for regional projects",
        "country_context_status": "single_country_matched or regional_not_applicable (country indicators must not be assigned to regional projects)",
        "income_group_code_at_approval": "WB income group for the approval fiscal year (income_classification_history, point-in-time); NULL for regional projects",
        "income_group_status_at_approval": "classified / unclassified / no_entry / no_row_in_source / approval_fiscal_year_missing / regional_not_applicable",
        "project_status_snapshot": "Project status on 2026-10-21." + SNAP,
        "closing_date_snapshot": "Closing date on 2026-10-21 (may have been extended)." + SNAP,
        "current_total_commitment_usd_snapshot": "Current total commitment published by the projects API on 2026-10-21 (US$); not reconciled with IBRD/IDA records "
                                                 "or contract awards." + SNAP,
        "contract_award_count": "Contracts in wbg_silver.contract_awards (DS00005, signed FY2020+); 0 = none published in this complete cohort-filtered source",
        "contract_award_amount_usd": "Exact sum of contract_awards.contract_amount_usd at contract grain (never supplier lines or notice links); NULL when no contracts. "
                                     "DS00005 awards from all financiers: not comparable with IBRD/IDA commitments",
        "contract_awards_coverage": "ds00005_contracts_signed_fy2020_onwards: legacy FY2006-2016 prior-review contracts are not included",
        "notice_count": "Notices in wbg_silver.procurement_notices (DS00979); 0 = none published. Includes identical-metadata rows as published",
        "has_api_notice_details": "True when wbg_silver.procurement_notice_details has rows for the project (Phase 1 cluster, 184 projects)",
        "api_notice_detail_count": "Rows in procurement_notice_details; NULL (unavailable, not zero) for projects outside the API detail pull",
        "ibrd_loan_count": "IBRD loans in wbg_silver.ibrd_loans (DS00047 snapshot 2026-09-30); 0 = none",
        "ida_credit_count": "IDA credits/grants in wbg_silver.ida_credits (DS00001 snapshot 2026-09-30); 0 = none",
        "has_ieg_rating": "True when the project has an IEG ICR review (750). False means no review published, NOT a poor outcome",
        "ieg_outcome_rating": "IEG outcome rating (latest snapshot 2026-10-18); NULL when not reviewed. Post-completion: never an ML feature before evaluation",
        "has_project_details_v3": "True for the 184 projects with v3 all-fields records (non-random Phase 1 cluster)",
        "environmental_social_risk_rating": "Latest E&S risk rating (H/S/M/L); NULL when not covered or not published." + SNAP,
        "results_indicator_count": "Results indicators in project_details_v3; NULL when not covered or no array published (unavailable, not zero)",
        "geo_location_count": "Geo-locations in project_details_v3; NULL when not covered or no array published",
        "loan_transaction_count": "Published loan transactions in project_details_v3; NULL when not covered or no array published",
        "silver_source_versions": "JSON {silver table: Delta version} read by this run",
    },
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry-run", "write"], default="dry-run")
    ap.add_argument("--confirm-create", action="store_true")
    ap.add_argument("--create-schema", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    if args.mode == "write" and not args.confirm_create:
        ap.error("--mode write requires --confirm-create")
    run_id = "gold_project_portfolio_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(args.profile)

    schema_exists = bool(spark.sql("SHOW SCHEMAS IN workspace LIKE 'wbg_gold'").collect())
    baseline: dict = {"bronze": {}, "silver": {}, "gold_schema_exists": schema_exists, "target_exists": schema_exists and spark.catalog.tableExists(T.TARGET),
                      "cohort_manifest_sha256": hashlib.sha256(COHORT.read_bytes()).hexdigest()}
    bronze = [f"workspace.{sc}.{r.tableName}" for sc in ("wbg_bronze", "wbg_ref_bronze") for r in spark.sql(f"SHOW TABLES IN workspace.{sc}").collect()]
    for group, tables in (("bronze", bronze), ("silver", [T.S + t for t in SILVER])):
        for tbl in tables:
            h = spark.sql(f"DESCRIBE HISTORY {tbl} LIMIT 1").collect()[0]
            baseline[group][tbl] = {"version": h.version, "rows": spark.table(tbl).count()}
    baseline["grants"] = {tbl: sorted([str(x) for x in r] for r in spark.sql(f"SHOW GRANTS ON TABLE {tbl}").collect())
                          for tbl in [*baseline["bronze"], *baseline["silver"]]}
    baseline["column_masks"] = sorted([r[0], r[1], r[2], r[3]] for r in spark.sql(
        "SELECT table_schema, table_name, column_name, mask_name FROM workspace.information_schema.column_masks").collect())
    (run_dir / "baseline_before_write.json").write_text(json.dumps(baseline, indent=1))

    rep = DQReport(T.TARGET)
    rep.equals("target_absent_before_write", baseline["target_exists"], False)
    rep.equals("baseline_table_counts (Bronze, Silver)", (len(baseline["bronze"]), len(baseline["silver"])), (17, 18))
    with COHORT.open(newline="", encoding="utf-8") as fh:
        cohort_ids = {r["project_id"] for r in csv.DictReader(fh)}
    dfs, versions = T.load_sources(spark)
    g = T.build(dfs, versions, run_id)
    chk_dfs = {**dfs, "contract_award_lines": spark.table(T.S + "contract_award_lines"),
               "bronze_projects_v3_allfields": spark.table("workspace.wbg_bronze.bronze_projects_v3_allfields")}
    summary = check(g, chk_dfs, cohort_ids, rep)
    rep.equals("delta_check_constraints_hold_before_write", {c: g.filter(f"NOT coalesce(({e}), false)").count() for c, e in CONSTRAINTS.items()}, {c: 0 for c in CONSTRAINTS})
    rep.equals("not_null_columns_have_no_nulls", {c: g.filter(f"`{c}` IS NULL").count() for c in NOT_NULL}, {c: 0 for c in NOT_NULL})
    summary["silver_source_versions"] = json.loads(versions)
    summary["distributions"] = {c: {str(r[0]): r[1] for r in g.groupBy(c).count().collect()} for c in
                                ("geography_type", "country_context_status", "income_group_status_at_approval", "income_group_code_at_approval",
                                 "project_status_snapshot", "has_api_notice_details", "has_ibrd_ida_financing", "has_ieg_rating", "has_project_details_v3")}
    print(f"run_id={run_id} mode={args.mode} transform={T.TRANSFORM_VERSION}")
    print(f"\n== proposed schema: {T.TARGET} ({len(g.columns)} columns)")
    for f in g.schema.fields:
        print(f"   {f.name:42s} {f.dataType.simpleString()}")
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
    if not schema_exists:
        if not args.create_schema:
            raise SystemExit("workspace.wbg_gold does not exist: re-run with --create-schema (approved create-only)")
        spark.sql("CREATE SCHEMA workspace.wbg_gold COMMENT 'Gold layer: analytical models built from wbg_silver (create-only runs)'")
    create_table(spark, g, T.TARGET, {"gold.run_id": run_id, "gold.transform_version": T.TRANSFORM_VERSION, "gold.silver_source_versions": versions,
                                       "gold.model": "current-state accumulating snapshot, project grain"},
                 COMMENTS["table"], COMMENTS["columns"], NOT_NULL, CONSTRAINTS)
    print(f"written {T.TARGET}: {spark.table(T.TARGET).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
