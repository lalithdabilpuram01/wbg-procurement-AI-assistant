"""Run the gold_procurement_notices transformation (Databricks Connect, Serverless).

Default is a DRY RUN: records the baseline (all Bronze, Silver and Gold tables: versions, row counts, grants; column masks;
cohort), builds the notice-grain DataFrame, runs all checks (incl. reconciliation with Silver and gold_project_portfolio,
deadline-validity invariants, the Delta CHECK constraints and NOT NULL columns evaluated on the data and a personal-data
scan), prints the schema and saves reports. Nothing is written to Databricks.

Writing requires --mode write --confirm-create, all "error" checks passing and the target not existing (create-only).

Usage (from procurement-intelligence/):
  python -m gold.procurement_notices.run_procurement_notices_gold
  python -m gold.procurement_notices.run_procurement_notices_gold --mode write --confirm-create
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyspark.sql import functions as F

from gold.common import baseline as B
from gold.procurement_notices import transform as T
from gold.procurement_notices.checks import check
from silver.common.dq import DQReport
from silver.common.pipeline import CATALOG, runs_dir
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.common.write import create_table

REPO = Path(__file__).resolve().parents[2]
RUNS = runs_dir(REPO / "gold" / "runs")
PORTFOLIO = f"{CATALOG}.wbg_gold.gold_project_portfolio"
CLASS_CASE = "CASE notice_type " + " ".join(f"WHEN '{t}' THEN '{c}'" for t, c in T.CLASSES.items()) + " END"
NOT_NULL = ["notice_op_id", "notice_id", "project_id", "geography_type", "country_context_status", "notice_country_code_status",
            "income_group_status_at_publication_fy", "notice_type", "notice_class", "is_tender_invitation", "is_solicitation_notice", "is_award_announcement",
            "publication_date", "publication_fiscal_year", "publication_calendar_year", "deadline_date_published", "deadline_status",
            "identical_metadata_group_size", "has_api_detail", "contract_link_status", "source_dataset", "source_retrieved_at", "source_row_sha256",
            "notice_snapshot_semantics", "silver_source_versions", "gold_run_id", "transform_version"]
SOL = "('Invitation for Bids', 'Invitation for Prequalification', 'Request for Expression of Interest')"
CONSTRAINTS = {
    "notice_op_id_format": "notice_op_id RLIKE '^OP[0-9]{8}$'",
    "notice_id_numeric": "notice_id RLIKE '^[0-9]+$'",
    "project_id_format": "project_id RLIKE '^P[0-9]{6}$'",
    "notice_class_consistent": f"notice_class = {CLASS_CASE}",
    "type_flags_consistent": f"is_tender_invitation = (notice_type = 'Invitation for Bids') AND is_solicitation_notice = (notice_type IN {SOL}) "
                             "AND is_award_announcement = (notice_type IN ('Contract Award', 'Small Contracts Award'))",
    "deadline_status_valid": "deadline_status IN ('not_applicable_notice_type', 'missing', 'equals_publication_date_no_window', 'before_publication_date_invalid', "
                             "'positive_window', 'positive_window_over_180_days_review')",
    "deadline_status_by_type": "(NOT is_solicitation_notice) = (deadline_status = 'not_applicable_notice_type')",
    "window_only_for_valid_solicitations": "(published_response_window_days IS NULL AND deadline_after_source_snapshot IS NULL) OR "
                                          "(is_solicitation_notice AND published_response_window_days > 0 "
                                          "AND published_response_window_days = datediff(deadline_date_published, publication_date) "
                                          "AND deadline_status IN ('positive_window', 'positive_window_over_180_days_review'))",
    "fiscal_year_consistent": "publication_fiscal_year = CASE WHEN month(publication_date) >= 7 THEN year(publication_date) + 1 ELSE year(publication_date) END",
    "calendar_year_consistent": "publication_calendar_year = year(publication_date)",
    "publication_not_after_snapshot": "publication_date <= DATE'2026-10-21'",
    "country_semantics": "(geography_type = 'single_country' AND country_context_status IN ('single_country_matched', 'single_country_unmatched')) OR "
                         "(geography_type <> 'single_country' AND country_context_status = 'regional_not_applicable' AND wdi_country_code IS NULL "
                         "AND wdi_country_iso3 IS NULL AND income_group_code_at_publication_fy IS NULL AND notice_country_code_status = 'regional_project' "
                         "AND income_group_status_at_publication_fy = 'regional_not_applicable')",
    "notice_country_status_valid": "notice_country_code_status IN ('equal_to_project_wdi_code', 'differs_from_project_wdi_code', 'missing_in_source', 'regional_project')",
    "link_semantics": "(NOT is_award_announcement AND contract_link_status = 'not_award_notice' AND linked_contract_count IS NULL) OR "
                      "(is_award_announcement AND contract_link_status = 'not_covered' AND linked_contract_count IS NULL) OR "
                      "(is_award_announcement AND contract_link_status IN ('no_link_found', 'unambiguous', 'ambiguous') AND linked_contract_count >= 0 "
                      "AND (linked_contract_count = 0) = (contract_link_status = 'no_link_found'))",
    "api_fields_only_with_detail": "has_api_detail OR (api_notice_status IS NULL AND api_award_notice_version_no IS NULL AND api_submission_deadline_date IS NULL "
                                   "AND api_notices_sharing_bid_ref_count IS NULL AND api_same_type_notices_sharing_bid_ref_count IS NULL)",
    "identical_group_positive": "identical_metadata_group_size >= 1",
    "snapshot_and_source_values": f"notice_snapshot_semantics = '{T.SNAPSHOT_SEMANTICS}' AND source_dataset = 'DS00979'",
}
SNAP = " Snapshot or population-wide measure: never a historical ML feature."
COMMENTS: dict[str, Any] = {
    "table": "Notice-grain fact: one row per published World Bank procurement notice (DS00979; 243,533) for the 2,000 cohort projects, snapshot retrieved "
             "2026-10-21. Notice classes separate tender invitations, prequalification, expressions of interest, contract-award announcements and general "
             "notices. Deadlines kept as published with validity status; response windows only for solicitations with deadline > publication. No notice "
             "text, descriptions, references, URLs, supplier or contact data",
    "columns": {
        "notice_op_id": "Notice operation id (primary key; OP + 8 digits; key of the API details and notice-contract links)",
        "notice_id": "Published numeric notice id (also unique)",
        "region_name": "Project region from wbg_silver.projects (current naming); DS00979's own region text is notice_region_name_published",
        "wdi_country_code": "Project's validated WDI country code; NULL for regional projects. The only country key for reference joins",
        "notice_country_code_published": "DS00979 country code as published (legacy codes such as ZR/GZ/YF/TP; NULL for some countries); never used for joins",
        "income_group_code_at_publication_fy": "WB income group for the publication fiscal year (point-in-time); NULL for regional projects",
        "notice_class": "tender_invitation (IFB) / prequalification_invitation / expression_of_interest / contract_award_announcement / general_procurement_notice",
        "is_tender_invitation": "True only for Invitation for Bids (an actual tender notice)",
        "is_solicitation_notice": "True for IFB, Invitation for Prequalification and Request for Expression of Interest (notices with a response deadline)",
        "is_award_announcement": "True for Contract Award and Small Contracts Award notices (post-award announcements)",
        "deadline_date_published": "Deadline date exactly as published. For award notices and GPNs it equals the publication date and is NOT a deadline",
        "deadline_status": "not_applicable_notice_type / equals_publication_date_no_window (no usable window; the API also reports deadline = publication "
                           "for some) / before_publication_date_invalid / positive_window / positive_window_over_180_days_review (review flag) / missing",
        "published_response_window_days": "deadline - publication in days, only for solicitation notices with deadline > publication. A published "
                                          "notice-based measure, NOT a verified final bidding duration or procurement-cycle duration; not an ML label. "
                                          "Never computed for award notices or GPNs",
        "deadline_after_source_snapshot": "Deadline after the 2026-10-21 snapshot (solicitation still open at retrieval)",
        "identical_metadata_group_size": "Rows sharing all published metadata (kept as published, not de-duplicated; descriptive evidence only)." + SNAP,
        "api_notice_status": "API notice status (Published / Revised / Cancelled / Draft): descriptive publication-revision metadata for the 184 covered "
                             "projects only; does not establish amendments, cancellations or re-bidding outcomes",
        "api_award_notice_version_no": "API award-notice version number: a publication revision counter, NOT a contract amendment or re-bid",
        "api_submission_deadline_date": "API submission deadline (covered projects); equals the published deadline wherever both exist",
        "api_notices_sharing_bid_ref_count": "Notices in the project sharing the normalised bid reference (all types). Shared references are NOT proof of "
                                             "repeated bidding (normal IFB -> award lifecycle, lots, revisions)." + SNAP,
        "api_same_type_notices_sharing_bid_ref_count": "Same-type notices sharing the bid reference (revisions, lots or re-advertisements; not proven re-bids)." + SNAP,
        "linked_contract_count": "Evidence-supported linked contracts for award notices of covered projects; NULL otherwise. 0 / no_link_found does not prove "
                                 "the award has no contract record",
        "contract_link_status": "not_award_notice / not_covered / no_link_found / unambiguous / ambiguous",
        "silver_source_versions": "JSON {silver table: Delta version} read by this run",
    },
}


def main() -> int:
    """Dry run by default: build and check gold_procurement_notices, save the reports; write mode creates the table."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry-run", "write"], default="dry-run")
    ap.add_argument("--confirm-create", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    if args.mode == "write" and not args.confirm_create:
        ap.error("--mode write requires --confirm-create")
    run_id = "gold_procurement_notices_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
    rep.equals("baseline_table_counts (Bronze, Silver, Gold)", (len(base["bronze"]), len(base["silver"]), len(base["gold"])), (17, 18, 2))
    with B.COHORT.open(newline="", encoding="utf-8") as fh:
        cohort_ids = {r["project_id"] for r in csv.DictReader(fh)}
    dfs, versions = T.load_sources(spark)
    g = T.build(dfs, versions, run_id)
    summary = check(g, {**dfs, "bronze_projects_v3_allfields": spark.table(f"{CATALOG}.wbg_bronze.bronze_projects_v3_allfields")}, spark.table(PORTFOLIO), cohort_ids, rep)
    rep.equals("delta_check_constraints_hold_before_write", {c: g.filter(f"NOT coalesce(({e}), false)").count() for c, e in CONSTRAINTS.items()}, {c: 0 for c in CONSTRAINTS})
    rep.equals("not_null_columns_have_no_nulls", {c: g.filter(f"`{c}` IS NULL").count() for c in NOT_NULL}, {c: 0 for c in NOT_NULL})
    summary["silver_source_versions"] = json.loads(versions)
    summary["null_counts_nullable_columns"] = {c: g.filter(f"`{c}` IS NULL").count() for c in g.columns if c not in NOT_NULL}
    summary["deadline_status_by_type"] = {f"{r[0]}|{r[1]}": r[2] for r in g.groupBy("notice_type", "deadline_status").count().collect()}
    summary["window_quantiles_by_type"] = {r[0]: {"n": r[1], "p01_p05_p50_p95_p99": r[2], "max": r[3]} for r in g.filter("published_response_window_days IS NOT NULL")
                                           .groupBy("notice_type").agg(F.count("*"), F.percentile_approx("published_response_window_days", [0.01, 0.05, 0.5, 0.95, 0.99]),
                                                                       F.max("published_response_window_days")).collect()}
    summary["ifb_window_by_method"] = {str(r[0]): {"n": r[1], "with_window": r[2], "p50": r[3]} for r in g.filter("is_tender_invitation").groupBy("procurement_method")
                                       .agg(F.count("*"), F.count("published_response_window_days"), F.percentile_approx("published_response_window_days", 0.5)).collect()}
    summary["distributions"] = {c: {str(r[0]): r[1] for r in g.groupBy(c).count().collect()} for c in
                                ("notice_class", "country_context_status", "notice_country_code_status", "income_group_status_at_publication_fy", "contract_link_status",
                                 "api_notice_status", "procurement_category")}
    print(f"run_id={run_id} mode={args.mode} transform={T.TRANSFORM_VERSION}")
    print(f"\n== proposed schema: {T.TARGET} ({len(g.columns)} columns)")
    for f in g.schema.fields:
        print(f"   {f.name:46s} {f.dataType.simpleString()}")
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
                                       "gold.model": "notice-grain fact, DS00979 snapshot"}, COMMENTS["table"], COMMENTS["columns"], NOT_NULL, CONSTRAINTS)
    print(f"written {T.TARGET}: {spark.table(T.TARGET).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
