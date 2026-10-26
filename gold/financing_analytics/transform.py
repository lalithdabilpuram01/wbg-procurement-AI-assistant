"""gold_financing_analytics: one row per IBRD loan or IDA credit/grant instrument (612 + 2,073 = 2,685), financing-instrument
fact (dry run 2026-10-24).

Sources: wbg_silver.ibrd_loans (DS00047) and wbg_silver.ida_credits (DS00001), both monthly statements as of 2026-09-30,
unioned on their shared published fields (identical meaning: the same World Bank Finances statement layout) with a lender
column, separate IBRD / IDA source identifiers, and lender-specific terms kept in their own nullable columns. Lookups
(projects, wdi_country) are N:1; published loan transactions are reduced to a COUNT per instrument (no amounts), so no
join can multiply instruments. Transaction-level analysis belongs in a separate future table.

Amounts are copied exactly as published (US$ equivalents that mix exchange-rate bases for non-USD commitments). IBRD
currency of commitment is not published (NULL, never assumed USD). No conversions, totals, ratios or reconciliations.
Contract-award amounts are not joined. Free-text borrower / guarantor names are excluded.
"""
from __future__ import annotations

import json

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

TRANSFORM_VERSION = "gold_financing_analytics_v1.0.0"
TARGET = "workspace.wbg_gold.gold_financing_analytics"
S = "workspace.wbg_silver."
SOURCES = ["ibrd_loans", "ida_credits", "projects", "wdi_country", "project_loan_transactions", "project_details_v3"]
AMOUNTS = ["original_principal_amount_usd", "cancelled_amount_usd", "undisbursed_amount_usd", "disbursed_amount_usd", "repaid_to_lender_usd",
           "due_to_lender_usd", "exchange_adjustment_usd", "borrower_obligation_usd", "sold_third_party_usd", "repaid_third_party_usd",
           "due_third_party_usd", "held_amount_usd"]
COMMON = ["financing_number_prefix", "lender", "project_id", "country_code", "country_name", "financing_status", "currency_of_commitment", *AMOUNTS,
          "first_repayment_date", "last_repayment_date", "agreement_signing_date", "agreement_signing_date_is_placeholder", "board_approval_date",
          "effective_date_most_recent", "effective_date_is_placeholder", "closed_date_most_recent", "last_disbursement_date", "board_approval_fiscal_year",
          "board_approval_calendar_year", "source_end_of_period", "financing_snapshot_semantics", "source_dataset", "source_row_sha256", "bronze_source_file",
          "bronze_input_file_sha256", "source_population_sha256", "source_retrieved_at", "silver_run_id"]
LATE_DISBURSEMENT_DAYS = 183  # same threshold as the Silver anomaly list


def load_sources(spark) -> tuple[dict[str, DataFrame], str]:
    dfs = {t: spark.table(S + t) for t in SOURCES}
    versions = {t: spark.sql(f"DESCRIBE HISTORY {S}{t} LIMIT 1").collect()[0].version for t in SOURCES}
    return dfs, json.dumps(dict(sorted(versions.items())), separators=(",", ":"))


def instruments(ib: DataFrame, ida: DataFrame) -> DataFrame:
    n = lambda t: F.lit(None).cast(t)  # noqa: E731
    a = ib.select(F.col("loan_number").alias("financing_number"), F.col("loan_number").alias("ibrd_loan_number"), n("string").alias("ida_credit_number"), *COMMON,
                  "loan_type", "interest_rate", "guarantor_country_code", n("decimal(12,6)").alias("service_charge_rate"), n("boolean").alias("has_repayment_schedule"))
    b = ida.select(F.col("credit_number").alias("financing_number"), n("string").alias("ibrd_loan_number"), F.col("credit_number").alias("ida_credit_number"), *COMMON,
                   n("string").alias("loan_type"), n("decimal(12,6)").alias("interest_rate"), n("string").alias("guarantor_country_code"), "service_charge_rate",
                   "has_repayment_schedule")
    return a.unionByName(b)


def build(dfs: dict[str, DataFrame], silver_versions: str, run_id: str) -> DataFrame:
    f = instruments(dfs["ibrd_loans"], dfs["ida_credits"])
    p = dfs["projects"].select("project_id", "project_name", "region_name", "geography_type", F.col("wdi_country_code").alias("_w"),
                               F.col("board_approval_date").alias("_p_approval"))
    iso = dfs["wdi_country"].select(F.col("country_code").alias("_w"), F.col("country_wb_id").alias("_iso3"))
    tx = dfs["project_loan_transactions"].groupBy("financing_number").agg(F.count("*").cast("bigint").alias("_tx"))
    cov = dfs["project_details_v3"].select("project_id").distinct().withColumn("_cov", F.lit(True))
    j = f.join(p, "project_id", "left").join(iso, "_w", "left").join(tx, "financing_number", "left").join(cov, "project_id", "left")
    single = F.col("geography_type") == "single_country"
    covered = F.coalesce(F.col("_cov"), F.lit(False))
    negative = F.coalesce(F.greatest(*[F.col(a) < 0 for a in AMOUNTS]), F.lit(False))
    return j.select(
        "financing_number", "lender", "ibrd_loan_number", "ida_credit_number", "financing_number_prefix", "source_dataset",
        "project_id", "project_name", "region_name", "geography_type",
        F.when(single, F.col("_w")).alias("wdi_country_code"), F.when(single, F.col("_iso3")).alias("wdi_country_iso3"),
        F.when(single & F.col("_w").isNotNull(), F.lit("single_country_matched")).when(~single, F.lit("regional_not_applicable"))
         .otherwise(F.lit("single_country_unmatched")).alias("country_context_status"),
        F.col("country_code").alias("financing_country_code_published"), F.col("country_name").alias("financing_country_name_published"),
        F.when(~single, F.lit("regional_project")).when(F.col("country_code").isNull(), F.lit("missing_in_source"))
         .when(F.col("country_code") == F.col("_w"), F.lit("equal_to_project_wdi_code")).otherwise(F.lit("differs_from_project_wdi_code"))
         .alias("financing_country_code_status"),
        "financing_status", "currency_of_commitment",
        F.when(F.col("lender") == "IBRD", F.lit("not_published_by_source")).when(F.col("currency_of_commitment").isNull(), F.lit("missing_in_source"))
         .otherwise(F.lit("published")).alias("currency_status"),
        "loan_type", "interest_rate", "guarantor_country_code", "service_charge_rate", "has_repayment_schedule",
        *AMOUNTS, negative.alias("has_negative_published_amount"),
        "board_approval_date", "board_approval_fiscal_year", "board_approval_calendar_year",
        (F.col("board_approval_date") < F.col("_p_approval")).alias("approved_before_project_approval"),
        "agreement_signing_date", "agreement_signing_date_is_placeholder", "effective_date_most_recent", "effective_date_is_placeholder",
        "closed_date_most_recent", "last_disbursement_date",
        (F.col("last_disbursement_date") > F.col("source_end_of_period")).alias("last_disbursement_after_end_of_period"),
        (F.datediff("last_disbursement_date", "closed_date_most_recent") > LATE_DISBURSEMENT_DAYS).alias("last_disbursement_over_183_days_after_closing"),
        "first_repayment_date", "last_repayment_date",
        covered.alias("loan_transaction_coverage"),
        F.when(covered, F.coalesce(F.col("_tx"), F.lit(0)).cast("bigint")).alias("published_loan_transaction_count"),
        "source_end_of_period", "financing_snapshot_semantics", "source_row_sha256", "bronze_source_file", "bronze_input_file_sha256",
        "source_population_sha256", "source_retrieved_at", F.col("silver_run_id").alias("silver_financing_run_id"),
        F.lit(silver_versions).alias("silver_source_versions"), F.lit(run_id).alias("gold_run_id"), F.current_timestamp().alias("gold_processed_at"),
        F.lit(TRANSFORM_VERSION).alias("transform_version"),
    )
