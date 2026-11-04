"""Blocking and reported checks for gold_financing_analytics (Spark side). Personal-data checks return counts only."""
from __future__ import annotations

import re

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from gold.financing_analytics import transform as T
from silver.common.dq import DQReport
from silver.notices.transform import EMAIL_RE, PHONE_RE

PROHIBITED_NAME = re.compile(r"borrower_name|guarantor_name|description|contact|email|phone|team|upi|supplier|contract_amount|raw_record$", re.I)


def _n(df: DataFrame, cond) -> int:
    return df.filter(cond).count()


def check(g: DataFrame, dfs: dict[str, DataFrame], portfolio: DataFrame, cohort_ids: set[str], rep: DQReport) -> dict:
    """Run every data-quality rule for gold_financing_analytics and add the results to `rep`; returns a summary."""
    ib, ida, tx = dfs["ibrd_loans"], dfs["ida_credits"], dfs["project_loan_transactions"]
    rows = g.count()
    rep.equals("rows_equal_612_ibrd_plus_2073_ida", (rows, _n(g, "lender = 'IBRD'"), _n(g, "lender = 'IDA'")), (2685, ib.count(), ida.count()))
    rep.equals("financing_number_unique_not_null", (g.select("financing_number").distinct().count(), _n(g, "financing_number IS NULL")), (rows, 0))
    rep.equals("source_identifiers_by_lender", _n(g, "(lender = 'IBRD' AND (ibrd_loan_number IS NULL OR ida_credit_number IS NOT NULL OR ibrd_loan_number <> financing_number)) "
                                                     "OR (lender = 'IDA' AND (ida_credit_number IS NULL OR ibrd_loan_number IS NOT NULL OR ida_credit_number <> financing_number))"), 0)
    keys = {r[0] for r in g.select("financing_number").collect()}
    src = {r[0] for r in ib.select("loan_number").collect()} | {r[0] for r in ida.select("credit_number").collect()}
    rep.equals("instrument_set_equals_silver", len(keys ^ src), 0)
    tagg = tx.groupBy("financing_number").count()
    rep.equals("no_multiplication:transaction_lookup_unique_per_instrument", tagg.count(), tagg.select("financing_number").distinct().count())
    f = T.instruments(ib, ida)
    cmp = g.join(f.select("financing_number", *[F.col(c).alias(f"s_{c}") for c in T.COMMON]), "financing_number")
    gold_name = {"silver_run_id": "silver_financing_run_id"}
    shared = [c for c in T.COMMON if c not in ("country_code", "country_name")]
    rep.equals("fidelity:shared_fields_equal_silver", {c: _n(cmp, f"NOT ({gold_name.get(c, c)} <=> s_{c})") for c in shared}, {c: 0 for c in shared})
    rep.equals("fidelity:published_country_unchanged", _n(cmp, "NOT (financing_country_code_published <=> s_country_code) OR NOT (financing_country_name_published <=> s_country_name)"), 0)
    for lender, df in (("IBRD", ib), ("IDA", ida)):
        rep.equals(f"amounts:{lender}_column_sums_equal_silver (exact decimal)", {a: g.filter(f"lender = '{lender}'").agg(F.sum(a)).collect()[0][0] for a in T.AMOUNTS},
                   {a: df.agg(F.sum(a)).collect()[0][0] for a in T.AMOUNTS})
    rep.equals("amounts:decimal_types", {f.name: f.dataType.simpleString() for f in g.schema.fields if f.name in T.AMOUNTS}, {a: "decimal(22,2)" for a in T.AMOUNTS})
    rep.add("amounts:negative_published_amount_instruments (kept; reported)", {r[0]: r[1] for r in g.filter("has_negative_published_amount").groupBy("lender").count().collect()},
            "reported", True, "warn")
    rep.equals("currency:ibrd_not_published_never_assumed", (_n(g, "lender = 'IBRD' AND currency_of_commitment IS NOT NULL"), _n(g, "lender = 'IBRD' AND currency_status <> 'not_published_by_source'")), (0, 0))
    rep.add("currency:ida_distribution (reported)", {str(r[0]): r[1] for r in g.filter("lender = 'IDA'").groupBy("currency_of_commitment").count().collect()}, "reported", True, "warn")
    rep.equals("terms:lender_specific_columns_null_for_other_lender", _n(g, "(lender = 'IDA' AND (loan_type IS NOT NULL OR interest_rate IS NOT NULL OR guarantor_country_code IS NOT NULL)) "
                                                                         "OR (lender = 'IBRD' AND (service_charge_rate IS NOT NULL OR has_repayment_schedule IS NOT NULL))"), 0)

    ids = {r[0] for r in g.select("project_id").distinct().collect()}
    rep.equals("projects:in_cohort_and_count", (len(ids), len(ids - cohort_ids)), (1597, 0))
    per = g.groupBy("project_id").agg(F.sum((F.col("lender") == "IBRD").cast("int")).alias("i"), F.sum((F.col("lender") == "IDA").cast("int")).alias("d"))
    rep.equals("gold_reconciliation:per_project_counts_equal_gold_project_portfolio",
               _n(portfolio.join(per, "project_id", "left"), "coalesce(i, 0) <> ibrd_loan_count OR coalesce(d, 0) <> ida_credit_count"), 0)
    pj = g.join(dfs["projects"].select("project_id", F.col("wdi_country_code").alias("pw")), "project_id")
    rep.equals("country:wdi_code_from_validated_project", _n(pj, "geography_type = 'single_country' AND NOT (wdi_country_code <=> pw)"), 0)
    rep.equals("country:regional_no_country_context", _n(g, "geography_type <> 'single_country' AND (wdi_country_code IS NOT NULL OR wdi_country_iso3 IS NOT NULL)"), 0)
    rep.add("country:financing_country_code_status (reported)", {f"{r[0]}|{r[1]}": r[2] for r in g.groupBy("lender", "financing_country_code_status").count().collect()},
            "reported", True, "warn")

    rep.equals("transactions:counted_instruments_and_rows", (_n(g, "published_loan_transaction_count > 0"), g.agg(F.sum("published_loan_transaction_count")).collect()[0][0]),
               (227, tx.join(g.select("financing_number"), "financing_number").count()))
    unmatched = sorted(r[0] for r in tx.select("financing_number").distinct().join(g.select("financing_number"), "financing_number", "left_anti").collect())
    rep.equals("transactions:unmatched_references_preserved_outside_table", unmatched, ["IBRD99510", "TFA9875", "TFC3832"])
    rep.equals("transactions:count_null_iff_not_covered", _n(g, "loan_transaction_coverage <> (published_loan_transaction_count IS NOT NULL)"), 0)

    rep.add("anomalies:flags (kept; reported)", {"approved_before_project_approval": _n(g, "approved_before_project_approval"),
                                                 "last_disbursement_after_end_of_period": _n(g, "last_disbursement_after_end_of_period"),
                                                 "last_disbursement_over_183_days_after_closing": _n(g, "last_disbursement_over_183_days_after_closing"),
                                                 "signing_placeholder": _n(g, "agreement_signing_date_is_placeholder"), "effective_placeholder": _n(g, "effective_date_is_placeholder")},
            "reported", True, "warn")
    rep.equals("dates:placeholder_flag_implies_null_date", _n(g, "(agreement_signing_date_is_placeholder AND agreement_signing_date IS NOT NULL) "
                                                                 "OR (effective_date_is_placeholder AND effective_date_most_recent IS NOT NULL)"), 0)

    lineage = ["source_dataset", "source_row_sha256", "bronze_source_file", "bronze_input_file_sha256", "source_population_sha256", "source_retrieved_at",
               "source_end_of_period", "financing_snapshot_semantics", "silver_financing_run_id", "silver_source_versions", "gold_run_id", "transform_version"]
    rep.equals("lineage:columns_not_null", {c: _n(g, f"{c} IS NULL") for c in lineage}, {c: 0 for c in lineage})
    rep.equals("privacy:no_prohibited_column_names", [c for c in g.columns if PROHIBITED_NAME.search(c)], [])
    strings = [f.name for f in g.schema.fields if f.dataType.simpleString() == "string" and not f.name.endswith("_sha256")]
    payload = F.concat_ws(" ", *[F.col(c) for c in strings])
    rep.equals("privacy:email_or_phone_in_any_value (counts)", (_n(g, payload.rlike(EMAIL_RE)), _n(g, payload.rlike("(?i)" + PHONE_RE))), (0, 0))
    return {"rows": rows}
