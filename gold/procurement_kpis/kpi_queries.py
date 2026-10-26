"""Proposed procurement dashboard KPI views over the five verified Gold tables (D-23 proposal; NOT approved, NOT created).

Each KPI is one SQL template over named inputs:
  {pf} gold_project_portfolio, {ca} gold_contract_analytics, {pn} gold_procurement_notices,
  {cc} gold_country_context, {fa} gold_financing_analytics.
The same text is executed read-only in the dry run (inputs bound to the Gold tables), in the unit tests (inputs bound to
synthetic frames), and rendered as CREATE VIEW DDL for a later, separately approved write.

Rules encoded in every query:
  * each query aggregates ONE fact table (or joins per-source aggregates on a unique key), so financial values are never
    multiplied; contract amounts are exact DECIMAL sums of contract-grain rows;
  * contract signing fiscal year, notice publication fiscal year and financing approval fiscal year are separate dimensions;
  * financing appears only as instrument counts, never with contract amounts;
  * response-window statistics use eligible solicitation notices only and positive published windows <= 180 days;
  * country = validated wdi_country_code; regional projects stay a separate geography (country NULL);
  * views expose numerators and denominators; shares are computed at display time with the documented denominators;
  * snapshot dates and source Gold run ids are carried for lineage. No supplier, contact, free-text or description fields.
"""
from __future__ import annotations

VIEW_SCHEMA = "workspace.wbg_gold"
SOURCES = {"pf": "gold_project_portfolio", "ca": "gold_contract_analytics", "pn": "gold_procurement_notices", "cc": "gold_country_context",
           "fa": "gold_financing_analytics"}
PCTS = (("p25", 0.25), ("p50", 0.5), ("p75", 0.75), ("p90", 0.9))

QUERIES: dict[str, str] = {
    "vw_kpi_overview": """
WITH p AS (SELECT count(*) AS total_projects, count_if(geography_type = 'single_country') AS single_country_projects,
                  count_if(geography_type <> 'single_country') AS regional_projects, count_if(has_api_notice_details) AS projects_with_api_notice_details,
                  count_if(has_project_details_v3) AS projects_with_project_details_v3, count_if(has_ieg_rating) AS projects_with_ieg_rating,
                  max(projects_source_retrieved_at) AS projects_source_retrieved_at, max(gold_run_id) AS portfolio_gold_run_id FROM {pf}),
     c AS (SELECT count(*) AS contract_awards, count(DISTINCT wb_contract_number) AS distinct_contract_awards,
                  sum(contract_amount_usd) AS total_contract_award_value_usd, count(DISTINCT project_id) AS projects_with_contract_awards,
                  min(contract_signing_date) AS first_contract_signing_date, max(contract_signing_date) AS last_contract_signing_date,
                  max(source_as_of_date) AS contracts_source_as_of_date, max(gold_run_id) AS contracts_gold_run_id FROM {ca}),
     n AS (SELECT count(*) AS procurement_notices, count_if(notice_class = 'tender_invitation') AS tender_invitation_notices,
                  count_if(notice_class = 'prequalification_invitation') AS prequalification_notices,
                  count_if(notice_class = 'expression_of_interest') AS expression_of_interest_notices,
                  count_if(notice_class = 'contract_award_announcement') AS contract_award_announcement_notices,
                  count_if(notice_class = 'general_procurement_notice') AS general_procurement_notices, count(DISTINCT project_id) AS projects_with_notices,
                  max(source_retrieved_at) AS notices_source_retrieved_at, max(gold_run_id) AS notices_gold_run_id FROM {pn}),
     f AS (SELECT count_if(lender = 'IBRD') AS ibrd_loan_instruments, count_if(lender = 'IDA') AS ida_credit_instruments,
                  count(DISTINCT project_id) AS projects_with_ibrd_ida_instruments, max(source_end_of_period) AS financing_source_end_of_period,
                  max(gold_run_id) AS financing_gold_run_id FROM {fa}),
     k AS (SELECT count(DISTINCT country_code) AS economies_in_country_context, min(year) AS country_context_first_year,
                  max(year) AS country_context_last_year, max(gold_run_id) AS country_context_gold_run_id FROM {cc})
SELECT * FROM p CROSS JOIN c CROSS JOIN n CROSS JOIN f CROSS JOIN k""",
    "vw_kpi_contract_awards": """
SELECT contract_fiscal_year, region_name, geography_type, wdi_country_code,
       CASE WHEN geography_type = 'single_country' THEN project_country_name END AS country_name, procurement_category, procurement_method,
       count(*) AS contract_awards, sum(contract_amount_usd) AS contract_award_value_usd,
       count_if(review_type = 'Prior') AS prior_review_contracts, count_if(review_type = 'Post') AS post_review_contracts,
       count_if(review_type IS NULL) AS review_type_unknown_contracts, count_if(is_multi_supplier) AS multi_supplier_contracts,
       count_if(has_anonymized_individual_consultant) AS individual_consultant_contracts,
       count_if(supplier_country_relation = 'all_suppliers_same_country') AS same_country_supplier_contracts,
       count_if(supplier_country_relation = 'includes_other_country') AS other_country_supplier_contracts,
       count_if(supplier_country_relation = 'undetermined') AS supplier_country_undetermined_contracts,
       count_if(signed_before_board_approval) AS contracts_signed_before_board_approval,
       max(source_as_of_date) AS source_as_of_date, max(gold_run_id) AS source_gold_run_id
FROM {ca}
GROUP BY contract_fiscal_year, region_name, geography_type, wdi_country_code, CASE WHEN geography_type = 'single_country' THEN project_country_name END,
         procurement_category, procurement_method""",
    "vw_kpi_contract_value_distribution": """
SELECT CASE grouping_id(contract_fiscal_year, procurement_category) WHEN 0 THEN 'fiscal_year_x_category' WHEN 1 THEN 'fiscal_year'
            WHEN 2 THEN 'category' ELSE 'all_contracts' END AS grouping_level,
       contract_fiscal_year, procurement_category, count(*) AS contract_awards, sum(contract_amount_usd) AS contract_award_value_usd,
       min(contract_amount_usd) AS min_contract_value_usd,
""" + ",\n".join(f"       CAST(percentile_disc({q}) WITHIN GROUP (ORDER BY contract_amount_usd) AS DECIMAL(32,2)) AS {n}_contract_value_usd" for n, q in PCTS) + """,
       max(contract_amount_usd) AS max_contract_value_usd, max(source_as_of_date) AS source_as_of_date, max(gold_run_id) AS source_gold_run_id
FROM {ca}
GROUP BY GROUPING SETS ((contract_fiscal_year, procurement_category), (contract_fiscal_year), (procurement_category), ())""",
    "vw_kpi_notice_activity": """
SELECT publication_fiscal_year, notice_type, notice_class, region_name, geography_type, wdi_country_code,
       CASE WHEN geography_type = 'single_country' THEN project_country_name END AS country_name,
       count(*) AS procurement_notices, count_if(identical_metadata_group_size > 1) AS notices_in_identical_metadata_groups,
       count_if(has_api_detail) AS notices_with_api_detail, max(source_retrieved_at) AS source_retrieved_at, max(gold_run_id) AS source_gold_run_id
FROM {pn}
GROUP BY publication_fiscal_year, notice_type, notice_class, region_name, geography_type, wdi_country_code,
         CASE WHEN geography_type = 'single_country' THEN project_country_name END""",
    "vw_kpi_response_windows": """
SELECT CASE grouping_id(notice_type, publication_fiscal_year, procurement_method) WHEN 0 THEN 'type_x_fiscal_year_x_method'
            WHEN 1 THEN 'type_x_fiscal_year' WHEN 2 THEN 'type_x_method' ELSE 'type' END AS grouping_level,
       notice_type, publication_fiscal_year, procurement_method,
       count(*) AS eligible_solicitation_notices, count_if(deadline_status = 'positive_window') AS positive_window_notices,
       count_if(deadline_status = 'positive_window_over_180_days_review') AS over_180_day_review_notices,
       count_if(deadline_status = 'equals_publication_date_no_window') AS no_window_notices,
       count_if(deadline_status = 'before_publication_date_invalid') AS invalid_window_notices, count_if(deadline_status = 'missing') AS missing_deadline_notices,
       min(CASE WHEN deadline_status = 'positive_window' THEN published_response_window_days END) AS min_window_days,
""" + ",\n".join(f"       CAST(percentile_disc({q}) WITHIN GROUP (ORDER BY CASE WHEN deadline_status = 'positive_window' THEN published_response_window_days END) "
                f"AS INT) AS {n}_window_days" for n, q in PCTS) + """,
       max(CASE WHEN deadline_status = 'positive_window' THEN published_response_window_days END) AS max_window_days,
       max(source_retrieved_at) AS source_retrieved_at, max(gold_run_id) AS source_gold_run_id
FROM {pn}
WHERE is_solicitation_notice
GROUP BY GROUPING SETS ((notice_type, publication_fiscal_year, procurement_method), (notice_type, publication_fiscal_year), (notice_type, procurement_method),
                        (notice_type))""",
    "vw_kpi_financing_instruments": """
SELECT lender, board_approval_fiscal_year, region_name, geography_type, wdi_country_code, financing_status,
       count(*) AS financing_instruments, count_if(approved_before_project_approval) AS instruments_approved_before_project_approval,
       count_if(has_negative_published_amount) AS instruments_with_negative_published_amount,
       count_if(loan_transaction_coverage) AS instruments_with_transaction_coverage,
       max(source_end_of_period) AS source_end_of_period, max(gold_run_id) AS source_gold_run_id
FROM {fa}
GROUP BY lender, board_approval_fiscal_year, region_name, geography_type, wdi_country_code, financing_status""",
    "vw_kpi_country_comparison": """
WITH p AS (SELECT geography_type, wdi_country_code, count(*) AS projects FROM {pf} GROUP BY geography_type, wdi_country_code),
     c AS (SELECT geography_type, wdi_country_code, count(*) AS contract_awards, sum(contract_amount_usd) AS contract_award_value_usd
           FROM {ca} GROUP BY geography_type, wdi_country_code),
     n AS (SELECT geography_type, wdi_country_code, count(*) AS procurement_notices, count_if(is_tender_invitation) AS tender_invitation_notices,
                  count_if(is_tender_invitation AND deadline_status = 'positive_window') AS tender_invitations_with_positive_window
           FROM {pn} GROUP BY geography_type, wdi_country_code),
     f AS (SELECT geography_type, wdi_country_code, count_if(lender = 'IBRD') AS ibrd_loan_instruments, count_if(lender = 'IDA') AS ida_credit_instruments
           FROM {fa} GROUP BY geography_type, wdi_country_code),
     k AS (SELECT country_code, country_iso3, country_name, income_fy_starting_in_year AS latest_income_fiscal_year,
                  income_group_fy_starting_in_year AS latest_income_group FROM {cc} WHERE year = 2025)
SELECT p.geography_type, p.wdi_country_code, k.country_iso3, k.country_name, k.latest_income_fiscal_year, k.latest_income_group, p.projects,
       coalesce(c.contract_awards, 0) AS contract_awards, c.contract_award_value_usd,
       coalesce(n.procurement_notices, 0) AS procurement_notices, coalesce(n.tender_invitation_notices, 0) AS tender_invitation_notices,
       coalesce(n.tender_invitations_with_positive_window, 0) AS tender_invitations_with_positive_window,
       coalesce(f.ibrd_loan_instruments, 0) AS ibrd_loan_instruments, coalesce(f.ida_credit_instruments, 0) AS ida_credit_instruments
FROM p
LEFT JOIN c ON p.geography_type = c.geography_type AND p.wdi_country_code <=> c.wdi_country_code
LEFT JOIN n ON p.geography_type = n.geography_type AND p.wdi_country_code <=> n.wdi_country_code
LEFT JOIN f ON p.geography_type = f.geography_type AND p.wdi_country_code <=> f.wdi_country_code
LEFT JOIN k ON p.wdi_country_code = k.country_code""",
}
GRAINS: dict[str, list[str]] = {
    "vw_kpi_overview": [],
    "vw_kpi_contract_awards": ["contract_fiscal_year", "region_name", "geography_type", "wdi_country_code", "procurement_category", "procurement_method"],
    "vw_kpi_contract_value_distribution": ["grouping_level", "contract_fiscal_year", "procurement_category"],
    "vw_kpi_notice_activity": ["publication_fiscal_year", "notice_type", "region_name", "geography_type", "wdi_country_code"],
    "vw_kpi_response_windows": ["grouping_level", "notice_type", "publication_fiscal_year", "procurement_method"],
    "vw_kpi_financing_instruments": ["lender", "board_approval_fiscal_year", "region_name", "geography_type", "wdi_country_code", "financing_status"],
    "vw_kpi_country_comparison": ["geography_type", "wdi_country_code"],
}


def bound(name: str) -> str:
    """Query text with the inputs bound to the fully qualified Gold tables."""
    return QUERIES[name].format(**{k: f"{VIEW_SCHEMA}.{v}" for k, v in SOURCES.items()})


FY = ("World Bank fiscal year FYn = 1 July (n-1) to 30 June n, as published in the Gold source (consistent with the source date by Gold CHECK "
      "constraint)")
COMMON_NOTE = " Proposed D-23 KPI baseline: D-23 pending. Read-only aggregation of verified Gold tables, with no supplier, contact or free-text fields."
VIEW_COMMENTS = {
    "vw_kpi_overview": "Headline totals (one row): projects, contracts and exact award value, notices by class, IBRD/IDA instrument counts (no "
                       "financing amounts), snapshot dates and source Gold run ids." + COMMON_NOTE,
    "vw_kpi_contract_awards": "Additive contract-award cube. contract_fiscal_year = " + FY + " of contract_signing_date. Shares: prior review = "
                              "prior/(prior+post), unknown review excluded. other-country suppliers = other/(same+other), undetermined reported as "
                              "unknown." + COMMON_NOTE,
    "vw_kpi_contract_value_distribution": "Contract-value distribution (discrete percentiles = actual contract values). grouping_level rows are "
                                          "SUBTOTALS of each other: always filter to ONE grouping_level. never sum across levels. "
                                          "contract_fiscal_year = " + FY + "." + COMMON_NOTE,
    "vw_kpi_notice_activity": "Additive notice cube. publication_fiscal_year = " + FY + " of the notice publication_date (separate from contract "
                              "signing year). Identical-metadata rows counted as published." + COMMON_NOTE,
    "vw_kpi_response_windows": "Published response windows for eligible solicitations (IFB, IFP, REOI) only. Statistics use positive windows of "
                               "1-180 days. the over-180-day outliers (96) are excluded from statistics and counted in over_180_day_review_notices "
                               "(source records unchanged). Published windows, NOT verified tender durations. grouping_level rows are SUBTOTALS: "
                               "filter to ONE grouping_level. publication_fiscal_year = " + FY + "." + COMMON_NOTE,
    "vw_kpi_financing_instruments": "IBRD/IDA instrument COUNTS only (no financing amounts. never combined with contract values). "
                                    "board_approval_fiscal_year = " + FY + " of board_approval_date. Status as of 2026-09-30 snapshot." + COMMON_NOTE,
    "vw_kpi_country_comparison": "Per validated wdi_country_code (regional projects = one row, country NULL). each source aggregated separately then "
                                 "joined on a unique key. latest_income_group = CURRENT FY2026 World Bank income classification (OGHIST 2026-10-08), "
                                 "not a historical value." + COMMON_NOTE,
}


def view_ddl(name: str) -> str:
    """Create-only DDL (never OR REPLACE). The body is the reviewed query; the comment documents the approved decisions."""
    return f"CREATE VIEW {VIEW_SCHEMA}.{name}\nCOMMENT '{VIEW_COMMENTS[name]}'\nAS{bound(name)}"
