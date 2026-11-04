"""Blocking and reported checks for gold_contract_analytics (Spark side). Personal-data checks return counts only."""
from __future__ import annotations

import re
from decimal import Decimal

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from gold.contract_analytics import transform as T
from silver.common.dq import DQReport
from silver.notices.transform import EMAIL_RE, PHONE_RE
from silver.project_details.checks import personal_values

CONTRACT_TOTAL_USD = Decimal("85248987799.45")
PROHIBITED_NAME = re.compile(r"supplier_(name|id|text)s?$|supplier_ids|line_key|description|reference$|notice_text|bid_desc|team|upi|email|phone|contact|"
                             r"borrower_name|implementing|abstract|objective|raw_record$", re.I)


def _n(df: DataFrame, cond) -> int:
    return df.filter(cond).count()


def check(g: DataFrame, dfs: dict[str, DataFrame], portfolio: DataFrame | None, cohort_ids: set[str], rep: DQReport) -> dict:
    """Run every data-quality rule for gold_contract_analytics and add the results to `rep`; returns summary figures."""
    a, lines, links, p = dfs["contract_awards"], dfs["contract_award_lines"], dfs["notice_contract_links"], dfs["projects"]
    rows = g.count()
    keys = {r[0] for r in g.select("wb_contract_number").collect()}
    rep.equals("rows_equal_silver_contract_awards_178921", (rows, a.count()), (178921, 178921))
    rep.equals("wb_contract_number_unique_not_null", (len(keys), _n(g, "wb_contract_number IS NULL")), (rows, 0))
    rep.equals("contract_set_equals_silver", len(keys ^ {r[0] for r in a.select("wb_contract_number").collect()}), 0)
    for name, agg, key in (("lines", T.lines_agg(lines), "wb_contract_number"), ("links", T.links_agg(links), "wb_contract_number")):
        rep.equals(f"no_multiplication:{name}_aggregate_one_row_per_contract", agg.count(), agg.select(key).distinct().count())
    total = g.agg(F.sum("contract_amount_usd")).collect()[0][0]
    rep.equals("amount:total_equals_85248987799.45", total, CONTRACT_TOTAL_USD)
    rep.equals("amount:total_equals_silver_awards_and_lines", (total, total), (a.agg(F.sum("contract_amount_usd")).collect()[0][0],
                                                                             lines.agg(F.sum("supplier_amount_usd")).collect()[0][0]))
    rep.equals("amount:per_contract_equals_silver", _n(g.join(a.select("wb_contract_number", F.col("contract_amount_usd").alias("s")), "wb_contract_number"),
                                                       "NOT (contract_amount_usd <=> s)"), 0)
    rep.equals("amount:decimal_type", dict((f.name, f.dataType.simpleString()) for f in g.schema.fields)["contract_amount_usd"], "decimal(32,2)")
    la = T.lines_agg(lines)
    rep.equals("lines:supplier_line_count_equals_lines", _n(g.join(la.select("wb_contract_number", "_line_count"), "wb_contract_number"),
                                                            "supplier_line_count <> _line_count"), 0)
    per_type = {r[0]: r[1] for r in lines.groupBy("supplier_id_type").agg(F.countDistinct("wb_contract_number")).collect()}
    rep.equals("lines:supplier_id_type_flag_totals", g.agg(F.sum(F.col("has_anonymized_individual_consultant").cast("int")), F.sum(F.col("has_supplier_with_numeric_id").cast("int")),
                                                           F.sum(F.col("has_supplier_with_missing_id").cast("int"))).collect()[0][:],
               tuple(per_type.get(t, 0) for t in ("anonymized_individual_consultant", "numeric", "missing")))
    missing_country = _n(lines, F.col("supplier_country_code").isNull() | F.col("supplier_country_code").isin(T.MISSING_COUNTRY))
    rep.equals("lines:missing_supplier_country_lines_preserved", g.agg(F.sum("supplier_lines_missing_country")).collect()[0][0], missing_country)
    rep.add("lines:supplier_country_relation (reported)", {r[0]: r[1] for r in g.groupBy("supplier_country_relation").count().collect()}, "reported", True, "warn")

    by_p = g.groupBy("project_id").agg(F.count("*").alias("c"), F.sum("contract_amount_usd").alias("v"))
    ids = {r[0] for r in by_p.select("project_id").collect()}
    rep.equals("projects:all_contract_projects_in_cohort", (len(ids), len(ids - cohort_ids)), (2000, 0))
    if portfolio is not None:
        cmp = by_p.join(portfolio.select("project_id", "contract_award_count", "contract_award_amount_usd"), "project_id", "full_outer")
        rep.equals("gold_reconciliation:per_project_count_and_amount_equal_gold_project_portfolio",
                   _n(cmp, "NOT (c <=> contract_award_count) OR NOT (v <=> contract_award_amount_usd)"), 0)
    pj = g.join(p.select("project_id", F.col("project_name").alias("pn"), F.col("wdi_country_code").alias("pw"), "board_approval_date"), "project_id")
    rep.equals("projects:attributes_equal_silver_projects", _n(pj, "NOT (project_name <=> pn) OR NOT (project_board_approval_date <=> board_approval_date)"), 0)
    rep.equals("country:single_country_contracts_matched", _n(g, "geography_type = 'single_country' AND (country_context_status <> 'single_country_matched' "
                                                                 "OR wdi_country_code IS NULL OR wdi_country_iso3 IS NULL)"), 0)
    rep.equals("country:wdi_code_equals_validated_project_code", _n(pj, "geography_type = 'single_country' AND NOT (wdi_country_code <=> pw)"), 0)
    rep.equals("country:regional_contracts_no_country_context", _n(g, "geography_type <> 'single_country' AND (wdi_country_code IS NOT NULL OR wdi_country_iso3 IS NOT NULL "
                                                                      "OR income_group_code_at_signing_fy IS NOT NULL OR country_context_status <> 'regional_not_applicable')"), 0)
    rep.equals("country:borrower_code_published_unchanged", _n(g.join(a.select("wb_contract_number", F.col("borrower_country_code").alias("b")), "wb_contract_number"),
                                                               "NOT (borrower_country_code_published <=> b)"), 0)
    rep.add("country:borrower_country_code_status (reported)", {r[0]: r[1] for r in g.groupBy("borrower_country_code_status").count().collect()}, "reported", True, "warn")
    rep.add("country:income_group_status_at_signing_fy (reported)", {r[0]: r[1] for r in g.groupBy("income_group_status_at_signing_fy").count().collect()}, "reported", True, "warn")

    rep.equals("dates:fiscal_year_consistent_with_signing_date", _n(g, "contract_fiscal_year <> (CASE WHEN month(contract_signing_date) >= 7 THEN year(contract_signing_date) + 1 "
                                                                       "ELSE year(contract_signing_date) END)"), 0)
    rep.equals("dates:signing_not_after_snapshot", _n(g, "contract_signing_date > source_as_of_date"), 0)
    rep.add("dates:signed_before_board_approval (kept; reported)", _n(g, "signed_before_board_approval"), "reported", True, "warn")
    cov = dfs["procurement_notice_details"].select("project_id").distinct()
    rep.equals("links:coverage_equals_detail_projects", _n(g, "award_notice_link_coverage"), a.join(cov, "project_id").count())
    rep.equals("links:linked_contracts_and_link_rows", (_n(g, "linked_award_notice_count > 0"), g.agg(F.sum("linked_award_notice_count")).collect()[0][0]),
               (links.select("wb_contract_number").distinct().count(), links.count()))
    rep.equals("links:count_null_iff_not_covered", _n(g, "award_notice_link_coverage <> (linked_award_notice_count IS NOT NULL)"), 0)
    rep.add("links:status (reported)", {r[0]: r[1] for r in g.groupBy("award_notice_link_status").count().collect()}, "reported", True, "warn")
    rep.add("links:earliest_linked_notice_before_signing (kept; reported)", _n(g, "earliest_linked_award_notice_date < contract_signing_date"), "reported", True, "warn")

    lineage = ["source_dataset", "source_as_of_date", "source_retrieved_at", "bronze_source_file", "bronze_input_file_sha256", "source_population_sha256",
               "source_rows_sha256", "contract_snapshot_semantics", "silver_contracts_run_id", "silver_source_versions", "gold_run_id", "transform_version"]
    rep.equals("lineage:columns_not_null", {c: _n(g, f"{c} IS NULL") for c in lineage}, {c: 0 for c in lineage})
    rep.equals("lineage:source_rows_sha256_unique", g.select("source_rows_sha256").distinct().count(), rows)

    rep.equals("privacy:no_prohibited_column_names", [c for c in g.columns if PROHIBITED_NAME.search(c)], [])
    types = {f.name: f.dataType.simpleString() for f in g.schema.fields}
    strings = [c for c, t in types.items() if t in ("string", "array<string>") and not c.endswith("_sha256")]
    payload = F.concat_ws(" ", *[F.col(c) if types[c] == "string" else F.concat_ws(" ", F.col(c)) for c in strings])
    rep.equals("privacy:email_or_phone_in_any_string_value (counts)", (_n(g, payload.rlike(EMAIL_RE)), _n(g, payload.rlike("(?i)" + PHONE_RE))), (0, 0))
    names, upis = personal_values(dfs["bronze_projects_v3_allfields"])
    texts = [r[0] for r in g.select(payload).collect()]
    upi_re = re.compile(r"(?<![0-9])(?:" + "|".join(map(re.escape, sorted(upis))) + r")(?![0-9])") if upis else None
    rep.equals("privacy:team_lead_names_or_staff_ids (counts)", (sum(any(nm in x for nm in names) for x in texts),
                                                                 sum(bool(upi_re and upi_re.search(x)) for x in texts)), (0, 0))
    hexids = {r[0] for r in lines.filter("supplier_id_type = 'anonymized_individual_consultant'").select("supplier_id").distinct().collect()}
    rep.equals("privacy:anonymized_consultant_ids_in_output (counts)", sum(bool(set(re.findall(r"[0-9a-fA-F]{16}", x)) & hexids) for x in texts), 0)
    sup = lines.select(F.upper(F.trim("supplier_name")).alias("v")).distinct()
    exact = {}
    for c in strings:
        vals = g.select(F.col(c).alias("x")) if types[c] == "string" else g.select(F.explode(F.col(c)).alias("x"))
        exact[c] = vals.select(F.upper(F.trim("x")).alias("v")).distinct().join(sup, "v").count()
    rep.add("privacy:distinct_output_values_equal_to_a_supplier_name_by_column (reported)", {k: v for k, v in exact.items() if v}, "reported", True, "warn")
    return {"total_usd": str(total), "rows": rows}
