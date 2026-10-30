"""Run the gold_contract_analytics transformation (Databricks Connect, Serverless).

Default is a DRY RUN: records the baseline (all Bronze, Silver and existing Gold tables: versions, row counts, grants;
column masks; cohort), builds the contract-grain DataFrame, runs all checks (incl. reconciliation with Silver and with
gold_project_portfolio, the Delta CHECK constraints and NOT NULL columns evaluated on the data and a personal-data scan),
prints the schema and saves reports. Nothing is written to Databricks.

Writing requires --mode write --confirm-create, all "error" checks passing and the target not existing (create-only;
the wbg_gold schema must already exist).

Usage (from procurement-intelligence/):
  python -m gold.contract_analytics.run_contract_analytics_gold
  python -m gold.contract_analytics.run_contract_analytics_gold --mode write --confirm-create
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gold.common import baseline as B
from gold.contract_analytics import transform as T
from gold.contract_analytics.checks import check
from silver.common.dq import DQReport
from silver.common.pipeline import CATALOG, runs_dir
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.common.write import create_table

REPO = Path(__file__).resolve().parents[2]
RUNS = runs_dir(REPO / "gold" / "runs")
PORTFOLIO = f"{CATALOG}.wbg_gold.gold_project_portfolio"
NOT_NULL = ["wb_contract_number", "project_id", "geography_type", "country_context_status", "borrower_country_code_status", "income_group_status_at_signing_fy",
            "procurement_category", "procurement_method", "contract_signing_date", "contract_fiscal_year", "contract_signed_calendar_year", "contract_amount_usd",
            "contract_amount_basis", "supplier_line_count", "is_multi_supplier", "supplier_lines_missing_amount", "has_anonymized_individual_consultant",
            "has_supplier_with_numeric_id", "has_supplier_with_missing_id", "supplier_lines_missing_country", "supplier_country_relation",
            "contracts_sharing_borrower_reference_in_project", "award_notice_link_coverage", "award_notice_link_status", "source_dataset", "source_as_of_date",
            "source_retrieved_at", "source_rows_sha256", "contract_snapshot_semantics", "silver_source_versions", "gold_run_id", "transform_version"]
CONSTRAINTS = {
    "contract_number_format": "wb_contract_number RLIKE '^[0-9]{7}$'",
    "project_id_format": "project_id RLIKE '^P[0-9]{6}$'",
    "amount_non_negative": "contract_amount_usd >= 0",
    "amount_basis_value": "contract_amount_basis = 'sum_of_supplier_lines'",
    "line_counts_valid": "supplier_line_count >= 1 AND supplier_lines_missing_amount >= 0 AND supplier_lines_missing_amount <= supplier_line_count "
                         "AND supplier_lines_missing_country >= 0 AND supplier_lines_missing_country <= supplier_line_count",
    "multi_supplier_consistent": "is_multi_supplier = (supplier_line_count > 1)",
    "fiscal_year_consistent": "contract_fiscal_year = CASE WHEN month(contract_signing_date) >= 7 THEN year(contract_signing_date) + 1 ELSE year(contract_signing_date) END",
    "calendar_year_consistent": "contract_signed_calendar_year = year(contract_signing_date)",
    "signing_not_after_snapshot": "contract_signing_date <= source_as_of_date",
    "country_semantics": "(geography_type = 'single_country' AND country_context_status IN ('single_country_matched', 'single_country_unmatched')) OR "
                         "(geography_type <> 'single_country' AND country_context_status = 'regional_not_applicable' AND wdi_country_code IS NULL "
                         "AND wdi_country_iso3 IS NULL AND income_group_code_at_signing_fy IS NULL AND income_group_status_at_signing_fy = 'regional_not_applicable' "
                         "AND borrower_country_code_status = 'regional_project')",
    "borrower_status_valid": "borrower_country_code_status IN ('equal_to_project_wdi_code', 'differs_from_project_wdi_code', 'missing_in_source', 'regional_project')",
    "supplier_relation_valid": "supplier_country_relation IN ('all_suppliers_same_country', 'includes_other_country', 'undetermined')",
    "approval_lag_consistent": "(days_from_board_approval_to_signing IS NULL AND signed_before_board_approval IS NULL) OR "
                               "(signed_before_board_approval = (days_from_board_approval_to_signing < 0))",
    "link_semantics": "(NOT award_notice_link_coverage AND linked_award_notice_count IS NULL AND award_notice_link_status = 'not_covered' "
                      "AND earliest_linked_award_notice_date IS NULL) OR (award_notice_link_coverage AND linked_award_notice_count >= 0 "
                      "AND award_notice_link_status IN ('no_link_found', 'unambiguous', 'ambiguous') "
                      "AND (linked_award_notice_count = 0) = (award_notice_link_status = 'no_link_found'))",
    "snapshot_semantics_value": f"contract_snapshot_semantics = '{T.SNAPSHOT_SEMANTICS}'",
    "source_dataset_value": "source_dataset = 'DS00005'",
}
SNAP = " Not known at contract signing: never a historical ML feature."
COMMENTS: dict[str, Any] = {
    "table": "Contract-grain fact: one row per current World Bank contract award (DS00005, signed FY2020+; 178,921) for the 2,000 cohort projects. "
             "contract_amount_usd = exact sum of published supplier lines (US$85,248,987,799.45 in total). Supplier lines and notice links are pre-aggregated per "
             "contract. No supplier names or IDs, descriptions, references or personal data. Legacy FY2006-2016 contracts excluded",
    "columns": {
        "wb_contract_number": "World Bank contract number (primary key)",
        "project_name": "Project name from wbg_silver.projects (not the DS00005 variant)",
        "wdi_country_code": "Project's validated WDI country code; NULL for regional projects. The only country key for reference joins",
        "borrower_country_code_published": "DS00005 borrower country code as published (legacy codes such as ZR/GZ/YF/RY/TP, regional codes, NULL for some "
                                           "countries); never used for joins",
        "borrower_country_code_status": "equal_to_project_wdi_code / differs_from_project_wdi_code / missing_in_source / regional_project",
        "income_group_code_at_signing_fy": "WB income group for the contract's signing fiscal year (point-in-time); NULL for regional projects",
        "global_practices": "Global practices as published in DS00005 (NULL when not published)",
        "contract_signing_date": "Contract signing date (event date). NOT the date the record became public: DS00005 has no publication date",
        "contract_fiscal_year": "WB fiscal year of signing as published (Jul-Jun; consistent with the signing date)",
        "days_from_board_approval_to_signing": "contract_signing_date - project board approval date (negative = signed before approval: advance/retroactive)",
        "contract_amount_usd": "Exact contract value in US$ = sum of published supplier-line amounts (DECIMAL; never summed over lines or links)",
        "supplier_line_count": "Number of supplier lines (joint ventures / lots)." + SNAP,
        "has_anonymized_individual_consultant": "A supplier line is a WB-anonymised individual consultant (16-hex ID; never re-identified)." + SNAP,
        "supplier_country_codes_published": "Distinct supplier country codes as published (DS00005 code system); missing values counted separately." + SNAP,
        "supplier_lines_missing_country": "Supplier lines with no country (NULL or the literal 'null' published by the source)",
        "supplier_country_relation": "Supplier countries vs borrower country, only where the borrower code equals the project's validated code; otherwise "
                                     "undetermined = UNKNOWN, never domestic or foreign (no code aliasing). Domestic/foreign percentages must state their "
                                     "denominator and report the undetermined share." + SNAP,
        "contracts_sharing_borrower_reference_in_project": "Contracts in the project with the same borrower reference: descriptive snapshot measure computed "
                                                           "over the whole snapshot." + SNAP,
        "award_notice_link_coverage": "True when the project is in the API notice-detail pull (184 projects); links exist only there",
        "linked_award_notice_count": "Evidence-supported award-notice links; NULL outside coverage (unavailable, not zero). 0 / no_link_found does NOT prove "
                                     "that no award notice exists",
        "award_notice_link_status": "not_covered / no_link_found / unambiguous / ambiguous. no_link_found means no evidence-supported link was found, not that "
                                    "no award notice exists",
        "earliest_linked_award_notice_date": "Earliest linked award-notice date: supporting evidence of publication, NOT a guaranteed first public availability "
                                             "date; covered contracts only." + SNAP,
        "source_as_of_date": "DS00005 snapshot date (latest date by which every row was public)",
        "source_rows_sha256": "SHA-256 of the sorted Silver supplier-line row hashes of this contract (lineage without supplier identities)",
        "silver_source_versions": "JSON {silver table: Delta version} read by this run",
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
    run_id = "gold_contract_analytics_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(args.profile)
    target_exists = spark.catalog.tableExists(T.TARGET)
    base = B.capture(spark, exclude={T.TARGET})
    base["target_exists"] = target_exists
    (run_dir / "baseline_before_write.json").write_text(json.dumps(base, indent=1))

    rep = DQReport(T.TARGET)
    rep.equals("target_absent_before_write", target_exists, False)
    rep.equals("baseline_table_counts (Bronze, Silver, Gold)", (len(base["bronze"]), len(base["silver"]), len(base["gold"])), (17, 18, 1))
    with B.COHORT.open(newline="", encoding="utf-8") as fh:
        cohort_ids = {r["project_id"] for r in csv.DictReader(fh)}
    dfs, versions = T.load_sources(spark)
    g = T.build(dfs, versions, run_id)
    summary = check(g, {**dfs, "bronze_projects_v3_allfields": spark.table(f"{CATALOG}.wbg_bronze.bronze_projects_v3_allfields")}, spark.table(PORTFOLIO), cohort_ids, rep)
    rep.equals("delta_check_constraints_hold_before_write", {c: g.filter(f"NOT coalesce(({e}), false)").count() for c, e in CONSTRAINTS.items()}, {c: 0 for c in CONSTRAINTS})
    rep.equals("not_null_columns_have_no_nulls", {c: g.filter(f"`{c}` IS NULL").count() for c in NOT_NULL}, {c: 0 for c in NOT_NULL})
    summary["silver_source_versions"] = json.loads(versions)
    summary["null_counts_nullable_columns"] = {c: g.filter(f"`{c}` IS NULL").count() for c in g.columns if c not in NOT_NULL}
    summary["distributions"] = {c: {str(r[0]): r[1] for r in g.groupBy(c).count().collect()} for c in
                                ("geography_type", "country_context_status", "borrower_country_code_status", "income_group_status_at_signing_fy", "income_group_code_at_signing_fy",
                                 "procurement_category", "review_type", "contract_fiscal_year", "supplier_country_relation", "award_notice_link_status",
                                 "has_anonymized_individual_consultant", "is_multi_supplier", "signed_before_board_approval")}
    print(f"run_id={run_id} mode={args.mode} transform={T.TRANSFORM_VERSION}")
    print(f"\n== proposed schema: {T.TARGET} ({len(g.columns)} columns)")
    for f in g.schema.fields:
        print(f"   {f.name:48s} {f.dataType.simpleString()}")
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
                                       "gold.model": "contract-grain fact, DS00005 snapshot"}, COMMENTS["table"], COMMENTS["columns"], NOT_NULL, CONSTRAINTS)
    print(f"written {T.TARGET}: {spark.table(T.TARGET).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
