"""Blocking and reported checks for gold_project_portfolio (Spark side; the independent Python recomputation is in
verify_project_portfolio_gold.py). Personal-data checks return counts only."""
from __future__ import annotations

import re
from decimal import Decimal

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from gold.project_portfolio import transform as T
from silver.common.dq import DQReport
from silver.notices.transform import EMAIL_RE, PHONE_RE
from silver.project_details.checks import personal_values

CONTRACT_TOTAL_USD = Decimal("85248987799.45")  # accepted Silver audit total (silver_audit_20261024T043611Z)
COUNT_COLUMNS = ["contract_award_count", "multi_supplier_contract_count", "contracts_with_missing_line_amount_count", "notice_count",
                 *T.NOTICE_TYPES.values(), "notices_in_identical_metadata_groups", "ibrd_loan_count", "ida_credit_count"]
PROHIBITED_NAME = re.compile(r"supplier_(name|id|text)|description|notice_text|bid_desc|team|upi|email|phone|contact|borrower|implementing|abstract|objective|raw_record$", re.I)


def _n(df: DataFrame, cond) -> int:
    return df.filter(cond).count()


def check(g: DataFrame, dfs: dict[str, DataFrame], cohort_ids: set[str], rep: DQReport) -> dict:
    """Run every data-quality rule for gold_project_portfolio and add the results to `rep`; returns the totals."""
    p, ca, cl = dfs["projects"], dfs["contract_awards"], dfs["contract_award_lines"]
    nt, nd, ib, ida, ieg, pd = (dfs[k] for k in ("procurement_notices", "procurement_notice_details", "ibrd_loans", "ida_credits", "ieg_project_ratings", "project_details_v3"))
    rows = g.count()
    ids = {r[0] for r in g.select("project_id").collect()}
    rep.equals("rows_exactly_2000", rows, 2000)
    rep.equals("project_id_unique_not_null", (len(ids), _n(g, "project_id IS NULL")), (rows, 0))
    rep.equals("project_ids_equal_cohort", (sorted(ids - cohort_ids)[:5], sorted(cohort_ids - ids)[:5]), ([], []))
    rep.equals("rows_equal_silver_projects (no multiplication)", rows, p.count())
    for name, agg in (("contracts", T.contracts_agg(ca)), ("notices", T.notices_agg(nt))):
        rep.equals(f"no_multiplication:{name}_aggregate_one_row_per_project", agg.count(), agg.select("project_id").distinct().count())

    s = g.agg(*[F.sum(c).alias(c) for c in COUNT_COLUMNS + ["contract_award_amount_usd", "api_notice_detail_count"]]).collect()[0].asDict()
    ca_total = ca.agg(F.sum("contract_amount_usd")).collect()[0][0]
    cl_total = cl.agg(F.sum("supplier_amount_usd")).collect()[0][0]
    rep.equals("contracts:count_equals_silver_contract_awards", s["contract_award_count"], ca.count())
    rep.equals("contracts:amount_equals_audit_total_85248987799.45", s["contract_award_amount_usd"], CONTRACT_TOTAL_USD)
    rep.equals("contracts:amount_equals_silver_contract_awards_and_lines", (s["contract_award_amount_usd"], s["contract_award_amount_usd"]), (ca_total, cl_total))
    rep.equals("contracts:multi_supplier_and_missing_line_counts", (s["multi_supplier_contract_count"], s["contracts_with_missing_line_amount_count"]),
               (_n(ca, "is_multi_supplier"), _n(ca, "supplier_lines_missing_amount > 0")))
    rep.equals("contracts:amount_null_iff_no_contracts", _n(g, "(contract_award_count = 0) <> (contract_award_amount_usd IS NULL)"), 0)
    rep.equals("contracts:dates_within_silver_range", g.agg(F.min("first_contract_signing_date"), F.max("last_contract_signing_date")).collect()[0][:],
               ca.agg(F.min("contract_signing_date"), F.max("contract_signing_date")).collect()[0][:])

    rep.equals("notices:total_equals_silver", s["notice_count"], nt.count())
    by_type = {r[0]: r[1] for r in nt.groupBy("notice_type").count().collect()}
    rep.equals("notices:per_type_totals_equal_silver", {t: s[c] for t, c in T.NOTICE_TYPES.items()}, {t: by_type.get(t, 0) for t in T.NOTICE_TYPES})
    rep.equals("notices:silver_notice_types_all_mapped", sorted(set(by_type) - set(T.NOTICE_TYPES)), [])
    rep.equals("notices:type_counts_sum_to_notice_count_per_row", _n(g, " + ".join(T.NOTICE_TYPES.values()) + " <> notice_count"), 0)
    rep.equals("notices:identical_metadata_rows_equal_silver", s["notices_in_identical_metadata_groups"], _n(nt, "identical_metadata_group_size > 1"))
    rep.equals("notices:api_detail_total_and_projects", (s["api_notice_detail_count"], _n(g, "has_api_notice_details")), (nd.count(), nd.select("project_id").distinct().count()))
    rep.equals("notices:api_detail_count_null_iff_no_detail_rows", _n(g, "has_api_notice_details <> (api_notice_detail_count IS NOT NULL)"), 0)

    rep.equals("financing:ibrd_ida_record_totals", (s["ibrd_loan_count"], s["ida_credit_count"]), (ib.count(), ida.count()))
    fin_projects = ib.select("project_id").union(ida.select("project_id")).distinct().count()
    rep.equals("financing:projects_with_ibrd_ida", _n(g, "has_ibrd_ida_financing"), fin_projects)

    rep.equals("ieg:rated_projects_equal_silver", _n(g, "has_ieg_rating"), ieg.count())
    rep.equals("ieg:outcome_distribution_equal_silver", {r[0]: r[1] for r in g.filter("has_ieg_rating").groupBy("ieg_outcome_rating").count().collect()},
               {r[0]: r[1] for r in ieg.groupBy("outcome_rating").count().collect()})
    rep.equals("ieg:unrated_projects_have_null_outcome (not zero/poor)", _n(g, "NOT has_ieg_rating AND (ieg_outcome_rating IS NOT NULL OR ieg_outcome_score IS NOT NULL "
                                                                            "OR ieg_evaluation_fiscal_year IS NOT NULL)"), 0)

    rep.equals("details_v3:covered_projects_equal_silver", _n(g, "has_project_details_v3"), pd.count())
    rep.equals("details_v3:uncovered_projects_all_null", _n(g, "NOT has_project_details_v3 AND (product_line_code IS NOT NULL OR environmental_social_risk_rating IS NOT NULL "
                                                              "OR results_indicator_count IS NOT NULL OR geo_location_count IS NOT NULL OR loan_transaction_count IS NOT NULL)"), 0)
    rep.equals("details_v3:indicator_geo_transaction_sums", g.agg(F.sum("results_indicator_count"), F.sum("geo_location_count"), F.sum("loan_transaction_count")).collect()[0][:],
               (3175, 2159, 16643))
    rep.equals("details_v3:null_counts_where_array_unpublished", (_n(g, "has_project_details_v3 AND results_indicator_count IS NULL"),
                                                                   _n(g, "has_project_details_v3 AND geo_location_count IS NULL"),
                                                                   _n(g, "has_project_details_v3 AND loan_transaction_count IS NULL")), (25, 10, 50))

    rep.equals("country:single_country_matched", _n(g, "geography_type = 'single_country' AND country_context_status = 'single_country_matched' AND wdi_country_code IS NOT NULL "
                                                      "AND wdi_country_iso3 IS NOT NULL"), _n(p, "geography_type = 'single_country'"))
    rep.equals("country:regional_projects_no_country_context", _n(g, "geography_type <> 'single_country' AND country_context_status = 'regional_not_applicable' AND wdi_country_code IS NULL "
                                                                     "AND wdi_country_iso3 IS NULL AND income_group_code_at_approval IS NULL "
                                                                     "AND income_group_status_at_approval = 'regional_not_applicable'"), 139)
    rep.equals("country:wdi_code_equals_validated_silver_code", _n(g.join(p.select("project_id", F.col("wdi_country_code").alias("p_w")), "project_id"),
                                                                   "NOT (wdi_country_code <=> p_w)"), 0)
    rep.add("country:income_group_status_at_approval (reported)", {r[0]: r[1] for r in g.groupBy("income_group_status_at_approval").count().collect()}, "reported", True, "warn")

    rep.equals("missing:count_columns_never_null", {c: _n(g, f"{c} IS NULL") for c in COUNT_COLUMNS}, {c: 0 for c in COUNT_COLUMNS})
    lineage = ["projects_source_retrieved_at", "contract_awards_source_as_of_date", "notices_source_retrieved_at", "financing_source_end_of_period", "ieg_source_as_of_date",
               "project_details_source_retrieved_at", "portfolio_snapshot_semantics", "projects_raw_record_sha256", "silver_source_versions", "gold_run_id", "transform_version"]
    rep.equals("lineage:columns_not_null", {c: _n(g, f"{c} IS NULL") for c in lineage}, {c: 0 for c in lineage})
    rep.equals("lineage:raw_record_sha256_equals_silver_projects", _n(g.join(p.select("project_id", "raw_record_sha256"), "project_id"),
                                                                      "projects_raw_record_sha256 <> raw_record_sha256"), 0)
    dtypes = dict((f.name, f.dataType.simpleString()) for f in g.schema.fields)
    rep.equals("schema:decimal_precision", (dtypes["contract_award_amount_usd"], dtypes["current_total_commitment_usd_snapshot"]), ("decimal(38,2)", "decimal(22,2)"))

    rep.equals("privacy:no_prohibited_column_names", [c for c in g.columns if PROHIBITED_NAME.search(c)], [])
    strings = [f.name for f in g.schema.fields if f.dataType.simpleString() == "string"]
    payload = F.concat_ws(" ", *[F.col(c) for c in strings])
    rep.equals("privacy:email_or_phone_in_any_string_value (counts)", (_n(g, payload.rlike(EMAIL_RE)), _n(g, payload.rlike("(?i)" + PHONE_RE))), (0, 0))
    names, upis = personal_values(dfs["bronze_projects_v3_allfields"])
    texts = [r[0] for r in g.select(payload).collect()]
    rep.equals("privacy:team_lead_names_or_staff_ids_in_output (counts)", (sum(any(nm in x for nm in names) for x in texts),
                                                                          sum(any(re.search(rf"(?<![0-9]){u}(?![0-9])", x) for u in upis) for x in texts)), (0, 0))
    sup = cl.select(F.upper(F.trim("supplier_name")).alias("v")).distinct()
    vals = g.select(F.explode(F.array(*[F.upper(F.trim(F.col(c))) for c in strings])).alias("v")).distinct()
    rep.add("privacy:output_string_values_equal_to_a_supplier_name (count; reported)", vals.join(sup, "v").count(), "reported", True, "warn")
    return {"totals": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in s.items()}}
