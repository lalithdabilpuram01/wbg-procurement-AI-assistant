# Metadata inventory (Structured Data Agent)

Generated from registry build `registry_20261025T211626Z` (Databricks snapshot `databricks_metadata_20261025T203808Z.json`, live verification `metadata_verification_20261025T205101Z.json`).

**Status labels.** *verified* = read from Databricks metadata or confirmed by a live read-only data check; *curated* = written by hand and grounded in the cited code or document; *unverified* = needs review.

## Summary

- Objects: 30 (18 Silver tables, 5 Gold tables, 7 Gold KPI views). Bronze and reference-Bronze schemas are not registered.
- Columns: 1035; classifications: public 810, lineage 187, restricted 20, prohibited 18
- Columns with a Databricks comment: 302 (others have no description; none was invented).
- Grains verified unique on live data: 30/30.
- Relationships verified on live data: 21/21.
- Metrics: 14 (status: curated 14).
- Business synonyms: 110 terms (status: unverified until procurement SME review; every target exists).
- SQL examples: 12 (verified 10, curated 2).
- Column masks: `wbg_silver.procurement_notice_details.notice_text` (D-14). Row filters: none.

## Objects

| Object | Type | Rows | Cols | Grain (columns) | Grain status / source | Description source | CHECKs | Non-public columns |
|---|---|---:|---:|---|---|---|---:|---|
| `wbg_gold.gold_contract_analytics` | gold table | 178,921 | 54 | contract award (one row per current DS00005 contract, 178,921) (`wb_contract_number`) | verified: Databricks table comment and column comment (wb_contract_number = primary key); gold/README.md | databricks_table_comment | 16 | lineage 8 |
| `wbg_gold.gold_country_context` | gold table | 5,642 | 41 | economy x calendar year (217 economies x 2000-2025, 5,642) (`country_code, year`) | verified: Databricks table comment (one row per WDI economy x calendar year) | databricks_table_comment | 13 | lineage 4 |
| `wbg_gold.gold_financing_analytics` | gold table | 2,685 | 65 | financing instrument (one IBRD loan or IDA credit/grant, 2,685) (`financing_number`) | verified: Databricks table comment and column comment (financing_number = primary key) | databricks_table_comment | 14 | lineage 8 |
| `wbg_gold.gold_procurement_notices` | gold table | 243,533 | 52 | procurement notice (one row per DS00979 notice, 243,533) (`notice_op_id`) | verified: Databricks table comment; gold/README.md; Silver audit key of procurement_notices (notice_op_id) | databricks_table_comment | 17 | lineage 8 |
| `wbg_gold.gold_project_portfolio` | gold table | 2,000 | 61 | project (one row per cohort project, 2,000) (`project_id`) | verified: Databricks table comment; gold/README.md (gold_project_portfolio) | databricks_table_comment | 13 | lineage 4 |
| `wbg_gold.vw_kpi_contract_awards` | gold view (certified KPI) | 9,204 | 20 | one row per contract_fiscal_year x region_name x geography_type x wdi_country_code x procurement_category x procurement_method (`contract_fiscal_year, region_name, geography_type, wdi_country_code, procurement_category, procurement_method`) | verified: gold/procurement_kpis/kpi_queries.py GRAINS | databricks_table_comment | 0 | none |
| `wbg_gold.vw_kpi_contract_value_distribution` | gold view (certified KPI) | 45 | 13 | one row per grouping_level x contract_fiscal_year x procurement_category (`grouping_level, contract_fiscal_year, procurement_category`) | verified: gold/procurement_kpis/kpi_queries.py GRAINS | databricks_table_comment | 0 | none |
| `wbg_gold.vw_kpi_country_comparison` | gold view (certified KPI) | 135 | 14 | one row per geography_type x wdi_country_code (`geography_type, wdi_country_code`) | verified: gold/procurement_kpis/kpi_queries.py GRAINS | databricks_table_comment | 0 | none |
| `wbg_gold.vw_kpi_financing_instruments` | gold view (certified KPI) | 1,478 | 12 | one row per lender x board_approval_fiscal_year x region_name x geography_type x wdi_country_code x financing_status (`lender, board_approval_fiscal_year, region_name, geography_type, wdi_country_code, financing_status`) | verified: gold/procurement_kpis/kpi_queries.py GRAINS | databricks_table_comment | 0 | none |
| `wbg_gold.vw_kpi_notice_activity` | gold view (certified KPI) | 5,145 | 12 | one row per publication_fiscal_year x notice_type x region_name x geography_type x wdi_country_code (`publication_fiscal_year, notice_type, region_name, geography_type, wdi_country_code`) | verified: gold/procurement_kpis/kpi_queries.py GRAINS | databricks_table_comment | 0 | none |
| `wbg_gold.vw_kpi_overview` | gold view (certified KPI) | 1 | 34 | one row (headline totals) (`-`) | verified: gold/procurement_kpis/kpi_queries.py GRAINS | databricks_table_comment | 0 | none |
| `wbg_gold.vw_kpi_response_windows` | gold view (certified KPI) | 302 | 18 | one row per grouping_level x notice_type x publication_fiscal_year x procurement_method (`grouping_level, notice_type, publication_fiscal_year, procurement_method`) | verified: gold/procurement_kpis/kpi_queries.py GRAINS | databricks_table_comment | 0 | none |
| `wbg_silver.contract_award_lines` | silver table | 186,907 | 38 | contract x supplier line (`line_key`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 4 | lineage 10, prohibited 2, restricted 2 |
| `wbg_silver.contract_awards` | silver table | 178,921 | 38 | contract (FY2020+) (`wb_contract_number`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 3 | lineage 9, prohibited 2, restricted 2 |
| `wbg_silver.contract_awards_legacy` | silver table | 6,448 | 33 | contract (FY2006-2016, prior review only) (`contract_number`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 2 | lineage 10, prohibited 4, restricted 1 |
| `wbg_silver.cpia_ratings` | silver table | 105,336 | 29 | entity x indicator x year (`country_code, indicator_code, year`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 2 | lineage 8, prohibited 1 |
| `wbg_silver.ibrd_loans` | silver table | 612 | 52 | IBRD loan (`loan_number`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 9 | lineage 10 |
| `wbg_silver.ida_credits` | silver table | 2,073 | 50 | IDA credit/grant (`credit_number`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 10 | lineage 10 |
| `wbg_silver.ieg_project_ratings` | silver table | 750 | 44 | project ICR review (`project_id`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 7 | lineage 10 |
| `wbg_silver.income_classification_history` | silver table | 8,736 | 26 | economy x fiscal year (`iso3_code, fiscal_year`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 3 | lineage 9, prohibited 1 |
| `wbg_silver.notice_contract_link_candidates` | silver table | 57,247 | 24 | notice x contract candidate (`candidate_key`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 3 | lineage 3, restricted 5 |
| `wbg_silver.notice_contract_links` | silver table | 12,039 | 26 | evidence-supported notice x contract link (`link_key`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 5 | lineage 3, restricted 5 |
| `wbg_silver.procurement_notice_details` | silver table | 20,305 | 46 | notice detail (API, 184 projects) (`notice_op_id`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 0 | lineage 12, prohibited 4, restricted 5 |
| `wbg_silver.procurement_notices` | silver table | 243,533 | 32 | notice (DS00979) (`notice_op_id`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 4 | lineage 10, prohibited 2 |
| `wbg_silver.project_details_v3` | silver table | 184 | 29 | project (Phase 1 cluster subset) (`project_id`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 6 | lineage 10 |
| `wbg_silver.project_loan_transactions` | silver table | 16,643 | 26 | published loan transaction (array position) (`transaction_key`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 7 | lineage 10 |
| `wbg_silver.projects` | silver table | 2,000 | 66 | project (`project_id`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 4 | lineage 11, prohibited 1 |
| `wbg_silver.wdi_country` | silver table | 296 | 24 | WDI entity (`country_code`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 1 | lineage 5 |
| `wbg_silver.wdi_indicator_observations` | silver table | 20,748 | 24 | entity x indicator x year (`country_code, indicator_code, year`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 1 | lineage 7 |
| `wbg_silver.wgi_estimates` | silver table | 34,992 | 32 | entity x indicator x year (`entity_key, indicator_code, year`) | verified: Silver audit inventory (silver_audit_20261024T043611Z) | databricks_table_comment | 3 | lineage 8, prohibited 1 |

## Personal, sensitive and restricted columns

Prohibited columns are never selectable, filterable or searchable; restricted columns may be used by data stewards in filters and joins only. Lineage columns are visible to data stewards only.

| Column | Classification | Policy source |
|---|---|---|
| `contract_award_lines.supplier_id` | restricted | D-14b (supplier_id pseudonymised in outputs; internal joins only) |
| `contract_award_lines.supplier_name` | prohibited | Silver audit §9; D-14b |
| `contract_award_lines.contract_description` | prohibited | Silver audit §9 (free text; redaction required before display) |
| `contract_award_lines.borrower_contract_reference` | restricted | Gold table comments (references excluded from Gold) |
| `contract_awards.contract_description` | prohibited | Silver audit §9 (free text; redaction required before display) |
| `contract_awards.borrower_contract_reference` | restricted | Gold table comments (references excluded from Gold) |
| `contract_awards.supplier_ids` | restricted | D-14b (supplier_id pseudonymised in outputs; internal joins only) |
| `contract_awards.supplier_names` | prohibited | Silver audit §9; D-14b |
| `contract_awards_legacy.contract_description` | prohibited | Silver audit §9 (free text; redaction required before display) |
| `contract_awards_legacy.contract_description_normalized` | prohibited | Silver audit §9 (free text; redaction required before display) |
| `contract_awards_legacy.borrower_contract_reference` | restricted | Gold table comments (references excluded from Gold) |
| `contract_awards_legacy.supplier_text` | prohibited | Silver audit §9; D-14b |
| `contract_awards_legacy.supplier_text_normalized` | prohibited | Silver audit §9; D-14b |
| `cpia_ratings.bronze_raw_record` | prohibited | Gold build rule 5 (no raw records) |
| `income_classification_history.footnote_text` | prohibited | Silver audit §9 (free text; redaction required before display) |
| `notice_contract_link_candidates.bid_reference_no` | restricted | Gold table comments (references excluded from Gold) |
| `notice_contract_link_candidates.award_reference_in_text` | restricted | Gold table comments (references excluded from Gold) |
| `notice_contract_link_candidates.borrower_contract_reference` | restricted | Gold table comments (references excluded from Gold) |
| `notice_contract_link_candidates.matched_reference_normalized` | restricted | Gold table comments (references excluded from Gold) |
| `notice_contract_link_candidates.matched_supplier_ids` | restricted | D-14b (supplier_id pseudonymised in outputs; internal joins only) |
| `notice_contract_links.bid_reference_no` | restricted | Gold table comments (references excluded from Gold) |
| `notice_contract_links.award_reference_in_text` | restricted | Gold table comments (references excluded from Gold) |
| `notice_contract_links.borrower_contract_reference` | restricted | Gold table comments (references excluded from Gold) |
| `notice_contract_links.matched_reference_normalized` | restricted | Gold table comments (references excluded from Gold) |
| `notice_contract_links.matched_supplier_ids` | restricted | D-14b (supplier_id pseudonymised in outputs; internal joins only) |
| `procurement_notice_details.bid_reference_no` | restricted | Gold table comments (references excluded from Gold) |
| `procurement_notice_details.bid_reference_normalized` | restricted | Gold table comments (references excluded from Gold) |
| `procurement_notice_details.award_reference_in_text` | restricted | Gold table comments (references excluded from Gold) |
| `procurement_notice_details.award_reference_in_text_normalized` | restricted | Gold table comments (references excluded from Gold) |
| `procurement_notice_details.bid_description` | prohibited | Silver audit §9 (free text; redaction required before display) |
| `procurement_notice_details.bid_description_normalized` | prohibited | Silver audit §9 (free text; redaction required before display) |
| `procurement_notice_details.contact_organization` | prohibited | Silver audit §9 (free text; redaction required before display) |
| `procurement_notice_details.notice_text` | prohibited | D-14; column mask workspace.wbg_silver.mask_restricted_notice_text |
| `procurement_notice_details.awarded_supplier_ids` | restricted | D-14b (supplier_id pseudonymised in outputs; internal joins only) |
| `procurement_notices.bid_description` | prohibited | Silver audit §9 (free text; redaction required before display) |
| `procurement_notices.bid_description_normalized` | prohibited | Silver audit §9 (free text; redaction required before display) |
| `projects.bronze_raw_record` | prohibited | Gold build rule 5 (no raw records) |
| `wgi_estimates.bronze_raw_record` | prohibited | Gold build rule 5 (no raw records) |

## Approved relationships

| Relationship | Join | Cardinality | Live check | Status | Source |
|---|---|---|---|---|---|
| `gold_contracts_to_portfolio` | `gold_contract_analytics.project_id` = `gold_project_portfolio.project_id` | N:1 | orphans 0, max to-rows/key 1 | verified | gold/README.md rule 1; gold_contract_analytics verifier (per-project totals equal to Gold #1) |
| `gold_notices_to_portfolio` | `gold_procurement_notices.project_id` = `gold_project_portfolio.project_id` | N:1 | orphans 0, max to-rows/key 1 | verified | gold/README.md rule 1; gold_procurement_notices verifier (per-project per-type equal to Gold #1) |
| `gold_financing_to_portfolio` | `gold_financing_analytics.project_id` = `gold_project_portfolio.project_id` | N:1 | orphans 0, max to-rows/key 1 | verified | gold/README.md rules 1-2; gold_financing_analytics verifier (per-project counts equal to Gold #1) |
| `gold_portfolio_to_country_context` | `gold_project_portfolio.wdi_country_code` = `gold_country_context.country_code` + year fixed | N:1 | orphans 0, max to-rows/key 1, NULL from-keys 139 | verified | gold_country_context table comment (join on validated wdi_country_code; keyed strictly by calendar year; join lags reviewed separately) |
| `gold_contracts_to_country_context` | `gold_contract_analytics.wdi_country_code` = `gold_country_context.country_code` + year fixed | N:1 | orphans 0, max to-rows/key 1, NULL from-keys 17,230 | verified | gold_country_context table comment; gold_contract_analytics column comment (wdi_country_code = the only country key for reference joins) |
| `gold_notices_to_country_context` | `gold_procurement_notices.wdi_country_code` = `gold_country_context.country_code` + year fixed | N:1 | orphans 0, max to-rows/key 1, NULL from-keys 24,971 | verified | gold_country_context table comment |
| `gold_financing_to_country_context` | `gold_financing_analytics.wdi_country_code` = `gold_country_context.country_code` + year fixed | N:1 | orphans 0, max to-rows/key 1, NULL from-keys 423 | verified | gold_country_context table comment |
| `silver_contracts_to_projects` | `contract_awards.project_id` = `projects.project_id` | N:1 | orphans 0, max to-rows/key 1 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue |
| `silver_lines_to_contracts` | `contract_award_lines.wb_contract_number` = `contract_awards.wb_contract_number` | N:1 | orphans 0, max to-rows/key 1 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue and row-multiplication table |
| `silver_legacy_to_projects` | `contract_awards_legacy.project_id` = `projects.project_id` | N:1 | orphans 0, max to-rows/key 1 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue |
| `silver_notices_to_projects` | `procurement_notices.project_id` = `projects.project_id` | N:1 | orphans 0, max to-rows/key 1 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue |
| `silver_notices_to_details` | `procurement_notices.notice_op_id` = `procurement_notice_details.notice_op_id` | 1:0..1 | orphans 9, max to-rows/key 1, unmatched from-rows (coverage) 223,237 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue |
| `silver_candidates_to_details` | `notice_contract_link_candidates.notice_op_id` = `procurement_notice_details.notice_op_id` | N:1 | orphans 0, max to-rows/key 1 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue |
| `silver_candidates_to_contracts` | `notice_contract_link_candidates.wb_contract_number` = `contract_awards.wb_contract_number` | N:1 | orphans 0, max to-rows/key 1 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue |
| `silver_links_to_details` | `notice_contract_links.notice_op_id` = `procurement_notice_details.notice_op_id` | N:1 | orphans 0, max to-rows/key 1 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue |
| `silver_links_to_contracts` | `notice_contract_links.wb_contract_number` = `contract_awards.wb_contract_number` | N:1 | orphans 0, max to-rows/key 1 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue |
| `silver_ibrd_to_projects` | `ibrd_loans.project_id` = `projects.project_id` | N:1 | orphans 0, max to-rows/key 1 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue |
| `silver_ida_to_projects` | `ida_credits.project_id` = `projects.project_id` | N:1 | orphans 0, max to-rows/key 1 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue |
| `silver_projects_to_details_v3` | `projects.project_id` = `project_details_v3.project_id` | 1:0..1 | orphans 0, max to-rows/key 1, unmatched from-rows (coverage) 1,816 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue |
| `silver_projects_to_ieg` | `projects.project_id` = `ieg_project_ratings.project_id` | 1:0..1 | orphans 0, max to-rows/key 1, unmatched from-rows (coverage) 1,250 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue |
| `silver_projects_to_wdi_country` | `projects.wdi_country_code` = `wdi_country.country_code` | N:1 | orphans 0, max to-rows/key 1, NULL from-keys 139 | verified | SILVER_RELATIONSHIP_REPORT.md join catalogue |

## Metrics

| Metric | Object | Expression | Unit | Ambiguity group | Status | Source |
|---|---|---|---|---|---|---|
| `project_count` | `gold_project_portfolio` | `count(*)` | count | - | curated | kpi_queries.py vw_kpi_overview |
| `contract_award_count` | `gold_contract_analytics` | `count(*)` | count | awards | curated | kpi_queries.py vw_kpi_contract_awards |
| `contract_award_announcement_count` | `gold_procurement_notices` | `count_if(notice_class = 'contract_award_announcement')` | count | awards | curated | kpi_queries.py vw_kpi_overview |
| `contract_award_value_usd` | `gold_contract_analytics` | `sum(contract_amount_usd)` | USD | monetary_value | curated | kpi_queries.py vw_kpi_contract_awards; SILVER_RELATIONSHIP_REPORT.md |
| `ibrd_ida_original_principal_usd` | `gold_financing_analytics` | `sum(original_principal_amount_usd)` | USD (as published) | monetary_value | curated | SILVER_RELATIONSHIP_REPORT.md (sum original_principal_amount_usd per lender; never mix with contract amounts) |
| `project_current_commitment_usd` | `gold_project_portfolio` | `sum(current_total_commitment_usd_snapshot)` | USD | monetary_value | curated | SILVER_RELATIONSHIP_REPORT.md (Project commitment: current, not original) |
| `procurement_notice_count` | `gold_procurement_notices` | `count(*)` | count | tenders | curated | kpi_queries.py vw_kpi_notice_activity |
| `tender_invitation_notice_count` | `gold_procurement_notices` | `count_if(is_tender_invitation)` | count | tenders | curated | kpi_queries.py vw_kpi_overview / vw_kpi_country_comparison |
| `solicitation_notice_count` | `gold_procurement_notices` | `count_if(is_solicitation_notice)` | count | tenders | curated | kpi_queries.py vw_kpi_response_windows; is_solicitation_notice column comment |
| `prior_review_share` | `vw_kpi_contract_awards` | `sum(prior_review_contracts) / (sum(prior_review_contracts) + sum(post_review_contracts))` | ratio | - | curated | vw_kpi_contract_awards view comment |
| `other_country_supplier_share` | `vw_kpi_contract_awards` | `sum(other_country_supplier_contracts) / (sum(same_country_supplier_contracts) + sum(other_country_supplier_contracts))` | ratio | - | curated | vw_kpi_contract_awards view comment |
| `median_published_response_window_days` | `vw_kpi_response_windows` | `p50_window_days (filter grouping_level)` | days | - | curated | kpi_queries.py vw_kpi_response_windows |
| `financing_instrument_count` | `gold_financing_analytics` | `count(*)` | count | - | curated | kpi_queries.py vw_kpi_financing_instruments |
| `linked_award_notice_coverage` | `gold_contract_analytics` | `count_if(award_notice_link_status IN ('unambiguous', 'ambiguous'))` | count | - | curated | gold_contract_analytics column comments (award_notice_link_status, award_notice_link_coverage) |

## Items requiring review

- All business synonyms (procurement SME review).
- Metric definitions are grounded in `kpi_queries.py` and the Silver relationship report, but the dashboard KPI decision D-23 is still pending.
- Column descriptions missing in Databricks remain empty; adding them needs an approved Databricks comment change.
- Join lags for country context are not approved; the registry requires an explicit year instead of assuming one.
