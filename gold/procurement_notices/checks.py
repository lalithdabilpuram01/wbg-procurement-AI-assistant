"""Blocking and reported checks for gold_procurement_notices (Spark side). Personal-data checks return counts only."""
from __future__ import annotations

import re

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from gold.procurement_notices import transform as T
from silver.common.dq import DQReport
from silver.notices.transform import EMAIL_RE, PHONE_RE
from silver.project_details.checks import personal_values

MASK_PLACEHOLDER = "[RESTRICTED: requires membership of wbg_restricted_notice_text]"
PROHIBITED_NAME = re.compile(r"notice_text|description|bid_reference$|award_reference|notice_url|supplier_(name|id|text)|contact|email|phone|team|upi|raw_record$", re.I)
PORTFOLIO_TYPES = {"Contract Award": "contract_award_notice_count", "Invitation for Bids": "invitation_for_bids_notice_count",
                   "Request for Expression of Interest": "expression_of_interest_notice_count", "General Procurement Notice": "general_procurement_notice_count",
                   "Invitation for Prequalification": "prequalification_notice_count", "Small Contracts Award": "small_contracts_award_notice_count"}


def _n(df: DataFrame, cond) -> int:
    return df.filter(cond).count()


def check(g: DataFrame, dfs: dict[str, DataFrame], portfolio: DataFrame, cohort_ids: set[str], rep: DQReport) -> dict:
    """Run every data-quality rule for gold_procurement_notices and add the results to `rep`; returns a summary."""
    n, d, k = dfs["procurement_notices"], dfs["procurement_notice_details"], dfs["notice_contract_links"]
    rows = g.count()
    rep.equals("rows_equal_silver_243533", (rows, n.count()), (243533, 243533))
    rep.equals("notice_op_id_and_notice_id_unique_not_null", (g.select("notice_op_id").distinct().count(), g.select("notice_id").distinct().count(),
                                                               _n(g, "notice_op_id IS NULL OR notice_id IS NULL")), (rows, rows, 0))
    rep.equals("notice_set_equals_silver", g.select("notice_op_id").exceptAll(n.select("notice_op_id")).count() + n.select("notice_op_id").exceptAll(g.select("notice_op_id")).count(), 0)
    dl, la = T.details_lookup(d), T.links_agg(k)
    rep.equals("no_multiplication:details_and_links_lookups_unique_per_notice", (dl.count() - dl.select("notice_op_id").distinct().count(),
                                                                                   la.count() - la.select("notice_op_id").distinct().count()), (0, 0))
    by_type = {r[0]: r[1] for r in g.groupBy("notice_type").count().collect()}
    rep.equals("types:counts_equal_silver", by_type, {r[0]: r[1] for r in n.groupBy("notice_type").count().collect()})
    rep.equals("types:all_mapped_to_a_class", _n(g, "notice_class IS NULL"), 0)
    rep.add("types:class_counts (reported)", {r[0]: r[1] for r in g.groupBy("notice_class").count().collect()}, "reported", True, "warn")
    gp = g.groupBy("project_id").pivot("notice_type", list(PORTFOLIO_TYPES)).count().na.fill(0)
    cmp = gp.join(portfolio.select("project_id", *PORTFOLIO_TYPES.values()), "project_id", "right")
    rep.equals("gold_reconciliation:per_project_per_type_counts_equal_gold_project_portfolio",
               _n(cmp, " OR ".join(f"coalesce(`{t}`, 0) <> {c}" for t, c in PORTFOLIO_TYPES.items())), 0)
    ids = {r[0] for r in g.select("project_id").distinct().collect()}
    rep.equals("projects:all_in_cohort", (len(ids), len(ids - cohort_ids)), (2000, 0))

    status = {f"{r[0]}|{r[1]}": r[2] for r in g.groupBy("notice_type", "deadline_status").count().collect()}
    rep.add("deadline:status_by_type (reported)", status, "reported", True, "warn")
    rep.equals("deadline:non_solicitation_never_get_a_window", _n(g, "NOT is_solicitation_notice AND (published_response_window_days IS NOT NULL "
                                                                    "OR deadline_status <> 'not_applicable_notice_type')"), 0)
    rep.equals("deadline:award_and_gpn_deadline_equals_publication (source default)", _n(g, "NOT is_solicitation_notice AND deadline_date_published <> publication_date"), 0)
    rep.equals("deadline:published_value_unchanged", _n(g.join(n.select("notice_op_id", F.col("deadline_date").alias("dd")), "notice_op_id"),
                                                        "NOT (deadline_date_published <=> dd)"), 0)
    rep.equals("deadline:window_equals_date_difference_when_present", _n(g, "published_response_window_days IS NOT NULL AND "
                                                                            "published_response_window_days <> datediff(deadline_date_published, publication_date)"), 0)
    rep.equals("deadline:window_present_iff_positive_status", _n(g, "(published_response_window_days IS NOT NULL) <> "
                                                                    "(deadline_status IN ('positive_window', 'positive_window_over_180_days_review'))"), 0)
    api = g.filter("api_submission_deadline_date IS NOT NULL")
    rep.add("deadline:api_submission_deadline_vs_published (reported)", {"api_deadlines": api.count(), "equal_to_published": _n(api, "api_submission_deadline_date = deadline_date_published"),
                                                                         "api_present_where_published_equals_publication": _n(api, "deadline_status = 'equals_publication_date_no_window'")},
            "reported", True, "warn")
    rep.add("deadline:open_at_snapshot (reported)", _n(g, "deadline_after_source_snapshot"), "reported", True, "warn")

    p = dfs["projects"]
    pj = g.join(p.select("project_id", F.col("wdi_country_code").alias("pw"), F.col("region_name").alias("pr")), "project_id")
    rep.equals("country:single_country_matched_with_iso3", _n(g, "geography_type = 'single_country' AND (country_context_status <> 'single_country_matched' "
                                                                 "OR wdi_country_code IS NULL OR wdi_country_iso3 IS NULL)"), 0)
    rep.equals("country:wdi_code_and_region_from_validated_project", _n(pj, "(geography_type = 'single_country' AND NOT (wdi_country_code <=> pw)) OR NOT (region_name <=> pr)"), 0)
    rep.equals("country:regional_notices_no_country_context", _n(g, "geography_type <> 'single_country' AND (wdi_country_code IS NOT NULL OR wdi_country_iso3 IS NOT NULL "
                                                                    "OR income_group_code_at_publication_fy IS NOT NULL)"), 0)
    rep.add("country:notice_country_code_status (reported)", {r[0]: r[1] for r in g.groupBy("notice_country_code_status").count().collect()}, "reported", True, "warn")
    rep.add("country:income_group_status_at_publication_fy (reported)", {r[0]: r[1] for r in g.groupBy("income_group_status_at_publication_fy").count().collect()},
            "reported", True, "warn")

    rep.equals("api:detail_rows_attached_equal_flag", (_n(g, "api_notice_status IS NOT NULL"), _n(g, "has_api_detail")), (_n(n, "has_api_detail"), _n(n, "has_api_detail")))
    rep.equals("api:fields_null_without_detail", _n(g, "NOT has_api_detail AND (api_notice_status IS NOT NULL OR api_award_notice_version_no IS NOT NULL "
                                                      "OR api_submission_deadline_date IS NOT NULL OR api_notices_sharing_bid_ref_count IS NOT NULL)"), 0)
    rep.add("api:status_and_versions (reported)", {"status": {r[0]: r[1] for r in g.groupBy("api_notice_status").count().collect()},
                                                   "award_versions_gt0": _n(g, "api_award_notice_version_no > 0"),
                                                   "same_type_reference_repeats": _n(g, "api_same_type_notices_sharing_bid_ref_count > 1")}, "reported", True, "warn")
    linked = k.join(n.select("notice_op_id"), "notice_op_id")
    rep.equals("links:linked_contract_total_equals_link_rows_on_ds00979_notices", g.agg(F.sum("linked_contract_count")).collect()[0][0], linked.count())
    rep.equals("links:count_only_for_covered_award_notices", _n(g, "linked_contract_count IS NOT NULL AND NOT (is_award_announcement AND contract_link_status <> 'not_covered')"), 0)
    rep.add("links:status (reported)", {r[0]: r[1] for r in g.groupBy("contract_link_status").count().collect()}, "reported", True, "warn")

    lineage = ["source_dataset", "source_retrieved_at", "source_row_sha256", "bronze_source_file", "bronze_input_file_sha256", "source_population_sha256",
               "notice_snapshot_semantics", "silver_notices_run_id", "silver_source_versions", "gold_run_id", "transform_version"]
    rep.equals("lineage:columns_not_null", {c: _n(g, f"{c} IS NULL") for c in lineage}, {c: 0 for c in lineage})
    rep.equals("lineage:row_hash_equals_silver", _n(g.join(n.select("notice_op_id", F.col("source_row_sha256").alias("h")), "notice_op_id"), "source_row_sha256 <> h"), 0)

    rep.equals("privacy:no_prohibited_column_names", [c for c in g.columns if PROHIBITED_NAME.search(c)], [])
    types = {f.name: f.dataType.simpleString() for f in g.schema.fields}
    strings = [c for c, t in types.items() if t in ("string", "array<string>") and not c.endswith("_sha256")]
    payload = F.concat_ws(" ", *[F.col(c) if types[c] == "string" else F.concat_ws(" ", F.col(c)) for c in strings])
    rep.equals("privacy:email_phone_mask_placeholder_in_any_value (counts)", (_n(g, payload.rlike(EMAIL_RE)), _n(g, payload.rlike("(?i)" + PHONE_RE)),
                                                                              _n(g, payload.contains(MASK_PLACEHOLDER))), (0, 0, 0))
    names, upis = personal_values(dfs["bronze_projects_v3_allfields"])
    texts = [r[0] for r in g.select(payload).collect()]
    upi_re = re.compile(r"(?<![0-9])(?:" + "|".join(map(re.escape, sorted(upis))) + r")(?![0-9])") if upis else None
    rep.equals("privacy:team_lead_names_or_staff_ids (counts)", (sum(any(nm in x for nm in names) for x in texts), sum(bool(upi_re and upi_re.search(x)) for x in texts)), (0, 0))
    bid = n.filter("bid_description IS NOT NULL").select(F.upper(F.trim("bid_description")).alias("v")).distinct()
    hits = {}
    for c in strings:
        vals = g.select(F.col(c).alias("x")) if types[c] == "string" else g.select(F.explode(F.col(c)).alias("x"))
        hits[c] = vals.select(F.upper(F.trim("x")).alias("v")).distinct().join(bid, "v").count()
    rep.add("privacy:distinct_output_values_equal_to_a_bid_description_by_column (reported)", {c: v for c, v in hits.items() if v}, "reported", True, "warn")
    return {"rows": rows, "by_type": by_type}
