"""Phase 1, step 1: read-only extraction of the Silver/Gold columns needed for label validation and EDA.

Usage (from procurement-intelligence/):  /opt/anaconda3/bin/python3 ml/phase1/s01_extract.py

Writes ml/eda/extract_<UTC>/data/*.parquet plus extract_manifest.json (row counts, Delta versions before
and after, cohort manifest SHA-256). The local parquet files are a working cache of public data.

Privacy and leakage rules applied here:
- No notice text, bid/contract references, descriptions, supplier names or supplier IDs are written.
  References are used in memory only; a package is identified by a salted-free SHA-256 prefix of
  (project_id, normalised reference), which is enough to group lots but does not reproduce the reference.
- Free-text re-bid / amendment patterns from discovery/scripts/p09 (unchanged) are evaluated in memory and
  only their boolean result is written, so Task 3 counts can be re-measured on Silver.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO_ROOT, TENDER_TYPES, new_run_dir, ro_table, spark, table_versions, write_json  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "discovery" / "scripts"))
import p09_ml_label_feasibility as p09  # noqa: E402  (patterns only; its main() is not run)
from wbdisc.normalize import normalize_ref  # noqa: E402

TABLES = [
    "wbg_gold.gold_contract_analytics", "wbg_gold.gold_procurement_notices", "wbg_gold.gold_project_portfolio",
    "wbg_gold.gold_country_context", "wbg_silver.projects", "wbg_silver.contract_awards",
    "wbg_silver.procurement_notices", "wbg_silver.procurement_notice_details",
    "wbg_silver.notice_contract_link_candidates", "wbg_silver.notice_contract_links",
]


def pkg_key(project_id, ref) -> str | None:
    if not ref:
        return None
    return hashlib.sha256(f"{project_id}|{ref}".encode()).hexdigest()[:16]


def main() -> None:
    out = new_run_dir("extract")
    data = out / "data"
    data.mkdir()
    s = spark()
    v_before = table_versions(s, TABLES)
    rows = {}

    def save(name, pdf):
        pdf.attrs = {}  # Databricks Connect attaches query metrics that parquet cannot serialise
        pdf.to_parquet(data / f"{name}.parquet", index=False)
        rows[name] = len(pdf)

    # Gold contract fact: categorical / date / amount columns only (no lineage, no free text)
    ca = ro_table(s, "wbg_gold.gold_contract_analytics").select(
        "wb_contract_number", "project_id", "region_name", "geography_type", "project_country_name", "wdi_country_code",
        "wdi_country_iso3", "country_context_status", "income_group_code_at_signing_fy", "project_approval_fiscal_year",
        "project_board_approval_date", "procurement_category", "procurement_method", "review_type", "global_practices",
        "contract_signing_date", "contract_fiscal_year", "contract_signed_calendar_year",
        "days_from_board_approval_to_signing", "signed_before_board_approval", "contract_amount_usd",
        "supplier_line_count", "is_multi_supplier", "has_anonymized_individual_consultant", "supplier_country_relation",
        "contracts_sharing_borrower_reference_in_project", "award_notice_link_coverage", "linked_award_notice_count",
        "award_notice_link_status", "earliest_linked_award_notice_date").toPandas()
    ca["contract_amount_usd"] = ca["contract_amount_usd"].astype(float)
    ca["global_practices"] = ca["global_practices"].map(lambda a: "|".join(a) if a is not None else None)
    save("gold_contract_analytics", ca)

    gn = ro_table(s, "wbg_gold.gold_procurement_notices").select(
        "notice_op_id", "project_id", "region_name", "geography_type", "project_country_name", "wdi_country_code",
        "country_context_status", "income_group_code_at_publication_fy", "notice_type", "notice_class",
        "is_tender_invitation", "is_solicitation_notice", "is_award_announcement", "procurement_category",
        "procurement_method", "publication_date", "publication_fiscal_year", "publication_calendar_year",
        "deadline_date_published", "deadline_status", "published_response_window_days", "identical_metadata_group_size",
        "has_api_detail", "api_notice_status", "api_award_notice_version_no", "linked_contract_count",
        "contract_link_status").toPandas()
    save("gold_procurement_notices", gn)

    pf = ro_table(s, "wbg_gold.gold_project_portfolio").select(
        "project_id", "region_name", "geography_type", "wdi_country_code", "country_context_status",
        "income_group_code_at_approval", "approval_fiscal_year", "board_approval_date", "project_status_snapshot",
        "closing_date_snapshot", "current_total_commitment_usd_snapshot", "contract_award_count", "notice_count",
        "has_api_notice_details", "has_ieg_rating", "ieg_outcome_score", "product_line_code",
        "environmental_social_risk_rating").toPandas()
    pf["current_total_commitment_usd_snapshot"] = pf["current_total_commitment_usd_snapshot"].astype(float)
    save("gold_project_portfolio", pf)

    cc = ro_table(s, "wbg_gold.gold_country_context").drop(
        "silver_source_versions", "gold_run_id", "gold_processed_at", "transform_version", "income_source_workbook",
        "income_source_file_sha256").toPandas()
    save("gold_country_context", cc)

    pj = ro_table(s, "wbg_silver.projects").select(
        "project_id", "board_approval_date", "approval_fiscal_year", "effectiveness_date", "loan_effective_date",
        "closing_date", "environmental_assessment_category", "major_sectors", "financing_types_distinct").toPandas()
    def primary_sector(ms):
        # major sector with the largest summed sector percentage (ties: first published)
        if ms is None or len(ms) == 0:
            return None
        return max(ms, key=lambda m: sum((x["sector_percent"] or 0) for x in (m["sectors"] if m["sectors"] is not None else [])))["major_sector_name"]

    pj["primary_major_sector"] = pj["major_sectors"].map(primary_sector)
    pj["financing_types_distinct"] = pj["financing_types_distinct"].map(lambda a: "|".join(a) if a is not None else None)
    save("silver_projects", pj.drop(columns=["major_sectors"]))

    # ---- label-validation inputs (184 API-detail projects); references handled in memory only ----
    api_projects = set(gn.loc[gn["has_api_detail"], "project_id"])
    sc = ro_table(s, "wbg_silver.contract_awards").select(
        "wb_contract_number", "project_id", "borrower_contract_reference", "contract_description").toPandas()
    sc["_ref"] = sc["borrower_contract_reference"].map(normalize_ref)
    sc["package_key"] = [pkg_key(p, r) for p, r in zip(sc["project_id"], sc["_ref"])]
    sc["in_api_project"] = sc["project_id"].isin(api_projects)
    sc["has_reference"] = sc["_ref"].notna()
    sc["ref_rebid_marker_strong"] = sc["borrower_contract_reference"].fillna("").str.contains(p09.REF_REBID_STRONG, regex=True)
    weak = sc["borrower_contract_reference"].fillna("").str.contains(p09.REF_REBID_WEAK, regex=True)
    sc["ref_rebid_marker_weak_only"] = weak & ~sc["ref_rebid_marker_strong"]
    sc["description_amendment_pattern"] = sc["contract_description"].fillna("").str.contains(p09.CONTRACT_AMENDMENT, regex=True)
    save("contract_keys", sc[["wb_contract_number", "project_id", "package_key", "in_api_project", "has_reference",
                              "ref_rebid_marker_strong", "ref_rebid_marker_weak_only", "description_amendment_pattern"]])

    nd = ro_table(s, "wbg_silver.procurement_notice_details").select(
        "notice_op_id", "notice_id", "project_id", "notice_type", "notice_status", "notice_date",
        "submission_deadline_date", "bid_reference_normalized", "award_reference_in_text_normalized",
        "award_notice_version_no", "bid_description", "bid_reference_no").toPandas()
    nd["package_key"] = [pkg_key(p, r) for p, r in zip(nd["project_id"], nd["bid_reference_normalized"])]
    nd["award_text_package_key"] = [pkg_key(p, r) for p, r in zip(nd["project_id"], nd["award_reference_in_text_normalized"])]
    nd["desc_rebid_marker"] = nd["bid_description"].fillna("").str.contains(p09.REBID_STRONG, regex=True)
    nd["ref_rebid_marker"] = nd["bid_reference_no"].fillna("").str.contains(p09.REF_REBID_STRONG, regex=True)
    nd["is_tender_type"] = nd["notice_type"].isin(TENDER_TYPES)
    save("notice_details_keys", nd.drop(columns=["bid_reference_normalized", "award_reference_in_text_normalized",
                                                 "bid_description", "bid_reference_no"]))

    sn = ro_table(s, "wbg_silver.procurement_notices").select(
        "notice_op_id", "project_id", "notice_type", "publication_date", "deadline_date", "bid_description").toPandas()
    sn["desc_rebid_marker"] = sn["bid_description"].fillna("").str.contains(p09.REBID_STRONG, regex=True)
    save("ds00979_rebid_flags", sn.drop(columns=["bid_description"]))

    lc = ro_table(s, "wbg_silver.notice_contract_link_candidates").select(
        "candidate_key", "notice_op_id", "wb_contract_number", "project_id", "notice_type", "notice_status",
        "notice_date", "match_method", "matched_on_bid_reference_no", "matched_on_award_reference_in_text",
        "supplier_id_confirmed", "candidate_contracts_for_notice", "candidate_notices_for_contract").toPandas()
    save("link_candidates", lc)
    lk = ro_table(s, "wbg_silver.notice_contract_links").select(
        "link_key", "notice_op_id", "wb_contract_number", "project_id", "notice_date", "link_cardinality",
        "ambiguity_status").toPandas()
    save("verified_links", lk)

    v_after = table_versions(s, TABLES)
    cohort = REPO_ROOT / "cohort" / "poc_cohort_2000_manifest.csv"
    write_json(out / "extract_manifest.json", {
        "rows": rows, "delta_versions_before": v_before, "delta_versions_after": v_after,
        "unchanged": v_before == v_after, "cohort_manifest_sha256": hashlib.sha256(cohort.read_bytes()).hexdigest(),
        "api_detail_projects": len(api_projects),
        "notes": "Read-only extraction. No references, free text or supplier identifiers written.",
    })
    print(out)
    print(rows, "unchanged" if v_before == v_after else "VERSIONS CHANGED")


if __name__ == "__main__":
    main()
