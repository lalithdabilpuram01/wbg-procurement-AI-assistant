"""gold_project_portfolio: one row per cohort project, current-state portfolio (design approved for dry run 2026-10-24).

Model: a current-state *accumulating snapshot* at project grain (2,000 rows = the D-26 cohort). Descriptors come from
wbg_silver.projects; every other source is aggregated to project_id FIRST and then left-joined, so no join can multiply
rows or amounts (see silver/runs/silver_audit_20261024T043611Z/SILVER_RELATIONSHIP_REPORT.md):

  contract_awards        contract grain -> count / exact decimal sum / dates  (never supplier lines or link rows)
  procurement_notices    notice grain   -> count per notice type / dates      (DS00979 full cohort population)
  procurement_notice_details            -> detail count where API detail rows exist (184 projects); NULL elsewhere
  ibrd_loans, ida_credits               -> record counts only (no monetary sums in v1: see GOLD design report)
  ieg_project_ratings    1:0..1         -> latest ICR review outcome, NULL when the project has no review
  project_details_v3     1:0..1         -> selected v3 fields, NULL when the project is outside the 184 covered
  wdi_country + income_classification_history -> ISO3 and income group at the approval FY, single-country only

Missing-value semantics: a count is 0 only when the source is a complete cohort-filtered population (DS00005 contracts,
DS00979 notices, DS00047/DS00001 financing) and has no record for the project. Measures from partial sources
(API notice details, project details v3) are NULL outside their coverage, flagged by has_* columns. No IEG review means
NULL outcome, never a poor or zero outcome. Regional projects get NULL country context.

Excluded: supplier names/IDs, contract or notice descriptions, notice text, borrower / implementing agency, PDO /
abstract, team-leader identities, raw records. Snapshot fields are named *_snapshot and must not be used as historical
ML features.
"""
from __future__ import annotations

import json

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F
from silver.common.pipeline import CATALOG

TRANSFORM_VERSION = "gold_project_portfolio_v1.0.0"
TARGET = f"{CATALOG}.wbg_gold.gold_project_portfolio"
S = f"{CATALOG}.wbg_silver."
SOURCES = ["projects", "contract_awards", "procurement_notices", "procurement_notice_details", "ibrd_loans", "ida_credits",
           "ieg_project_ratings", "project_details_v3", "wdi_country", "income_classification_history"]
CONTRACT_COVERAGE = "ds00005_contracts_signed_fy2020_onwards"
SNAPSHOT_SEMANTICS = "current_state_as_of_source_retrievals_2026_10"
NOTICE_TYPES = {"Contract Award": "contract_award_notice_count", "Invitation for Bids": "invitation_for_bids_notice_count",
                "Request for Expression of Interest": "expression_of_interest_notice_count", "General Procurement Notice": "general_procurement_notice_count",
                "Invitation for Prequalification": "prequalification_notice_count", "Small Contracts Award": "small_contracts_award_notice_count"}


def load_sources(spark) -> tuple[dict[str, DataFrame], str]:
    """Silver inputs plus a JSON manifest {table: delta version} recorded on every row."""
    dfs = {t: spark.table(S + t) for t in SOURCES}
    versions = {t: spark.sql(f"DESCRIBE HISTORY {S}{t} LIMIT 1").collect()[0].version for t in SOURCES}
    return dfs, json.dumps(dict(sorted(versions.items())), separators=(",", ":"))


def _one(df: DataFrame, col: str) -> Column:
    """Source-wide constant (each Silver source has a single retrieval / as-of value)."""
    return F.lit(df.select(col).distinct().collect()[0][0])


def contracts_agg(c: DataFrame) -> DataFrame:
    return c.groupBy("project_id").agg(
        F.count("*").alias("contract_award_count"), F.sum("contract_amount_usd").cast("decimal(38,2)").alias("contract_award_amount_usd"),
        F.sum(F.col("is_multi_supplier").cast("int")).cast("bigint").alias("multi_supplier_contract_count"),
        F.sum(F.when(F.col("supplier_lines_missing_amount") > 0, 1).otherwise(0)).cast("bigint").alias("contracts_with_missing_line_amount_count"),
        F.min("contract_signing_date").alias("first_contract_signing_date"), F.max("contract_signing_date").alias("last_contract_signing_date"))


def notices_agg(n: DataFrame) -> DataFrame:
    return n.groupBy("project_id").agg(
        F.count("*").alias("notice_count"),
        *[F.sum(F.when(F.col("notice_type") == t, 1).otherwise(0)).cast("bigint").alias(c) for t, c in NOTICE_TYPES.items()],
        F.min("publication_date").alias("first_notice_publication_date"), F.max("publication_date").alias("last_notice_publication_date"),
        F.sum(F.when(F.col("identical_metadata_group_size") > 1, 1).otherwise(0)).cast("bigint").alias("notices_in_identical_metadata_groups"))


def build(dfs: dict[str, DataFrame], silver_versions: str, run_id: str) -> DataFrame:
    p = dfs["projects"]
    ca, nt, nd, ib, ida = dfs["contract_awards"], dfs["procurement_notices"], dfs["procurement_notice_details"], dfs["ibrd_loans"], dfs["ida_credits"]
    ieg, pd, wc, ic = dfs["ieg_project_ratings"], dfs["project_details_v3"], dfs["wdi_country"], dfs["income_classification_history"]
    single = F.col("geography_type") == "single_country"
    details_cnt = nd.groupBy("project_id").agg(F.count("*").cast("bigint").alias("api_notice_detail_count"))
    fin = (ib.select("project_id", F.lit(1).alias("ibrd"), F.lit(0).alias("ida"))
           .unionByName(ida.select("project_id", F.lit(0).alias("ibrd"), F.lit(1).alias("ida")))
           .groupBy("project_id").agg(F.sum("ibrd").cast("int").alias("ibrd_loan_count"), F.sum("ida").cast("int").alias("ida_credit_count")))
    ieg1 = ieg.select("project_id", F.col("evaluation_fiscal_year").alias("ieg_evaluation_fiscal_year"), F.col("outcome_rating").alias("ieg_outcome_rating"),
                      F.col("outcome_score").alias("ieg_outcome_score"), F.lit(True).alias("_ieg"))
    pd1 = pd.select("project_id", "product_line_code", "environmental_social_risk_rating", F.lit(True).alias("_pd"),
                    F.when(F.col("results_indicators").isNotNull(), F.col("indicator_count")).alias("results_indicator_count"),
                    F.when(F.col("geo_locations").isNotNull(), F.col("geo_location_count")).alias("geo_location_count"),
                    F.when(F.col("loan_transaction_count") > 0, F.col("loan_transaction_count")).alias("loan_transaction_count"))
    iso = wc.select(F.col("country_code").alias("wdi_country_code"), F.col("country_wb_id").alias("_iso3"))
    inc = ic.select(F.col("iso3_code").alias("_iso3"), F.col("fiscal_year").alias("approval_fiscal_year"),
                    F.col("income_group_code").alias("_inc_code"), F.col("classification_status").alias("_inc_status"))
    cnt0 = lambda c: F.coalesce(F.col(c), F.lit(0)).cast("bigint")  # noqa: E731  complete cohort-filtered source: absent = verified 0
    j = (p.join(contracts_agg(ca), "project_id", "left").join(notices_agg(nt), "project_id", "left").join(details_cnt, "project_id", "left")
         .join(fin, "project_id", "left").join(ieg1, "project_id", "left").join(pd1, "project_id", "left")
         .join(iso, "wdi_country_code", "left").join(inc, ["_iso3", "approval_fiscal_year"], "left"))
    has_pd = F.coalesce(F.col("_pd"), F.lit(False))
    return j.select(
        "project_id", "project_name", "region_name", "geography_type",
        F.col("country_code").alias("source_country_code"), F.col("country_name").alias("source_country_name"),
        F.when(single, F.col("wdi_country_code")).alias("wdi_country_code"), F.when(single, F.col("_iso3")).alias("wdi_country_iso3"),
        "wdi_match_method",
        F.when(single & F.col("wdi_country_code").isNotNull(), F.lit("single_country_matched"))
         .when(~single, F.lit("regional_not_applicable")).otherwise(F.lit("single_country_unmatched")).alias("country_context_status"),
        F.when(single, F.col("_inc_code")).alias("income_group_code_at_approval"),
        F.when(~single, F.lit("regional_not_applicable")).when(F.col("approval_fiscal_year").isNull(), F.lit("approval_fiscal_year_missing"))
         .when(F.col("_inc_status").isNull(), F.lit("no_row_in_source")).otherwise(F.col("_inc_status")).alias("income_group_status_at_approval"),
        "approval_fiscal_year", "board_approval_date",
        F.col("project_status").alias("project_status_snapshot"), F.col("closing_date").alias("closing_date_snapshot"),
        F.col("current_total_commitment_usd").alias("current_total_commitment_usd_snapshot"),
        cnt0("contract_award_count").alias("contract_award_count"), "contract_award_amount_usd",
        cnt0("multi_supplier_contract_count").alias("multi_supplier_contract_count"),
        cnt0("contracts_with_missing_line_amount_count").alias("contracts_with_missing_line_amount_count"),
        "first_contract_signing_date", "last_contract_signing_date", F.lit(CONTRACT_COVERAGE).alias("contract_awards_coverage"),
        cnt0("notice_count").alias("notice_count"), *[cnt0(c).alias(c) for c in NOTICE_TYPES.values()],
        "first_notice_publication_date", "last_notice_publication_date", cnt0("notices_in_identical_metadata_groups").alias("notices_in_identical_metadata_groups"),
        F.col("api_notice_detail_count").isNotNull().alias("has_api_notice_details"), "api_notice_detail_count",
        F.coalesce(F.col("ibrd_loan_count"), F.lit(0)).alias("ibrd_loan_count"), F.coalesce(F.col("ida_credit_count"), F.lit(0)).alias("ida_credit_count"),
        (F.coalesce(F.col("ibrd_loan_count"), F.lit(0)) + F.coalesce(F.col("ida_credit_count"), F.lit(0)) > 0).alias("has_ibrd_ida_financing"),
        F.coalesce(F.col("_ieg"), F.lit(False)).alias("has_ieg_rating"), "ieg_evaluation_fiscal_year", "ieg_outcome_rating", "ieg_outcome_score",
        has_pd.alias("has_project_details_v3"), "product_line_code", "environmental_social_risk_rating",
        "results_indicator_count", "geo_location_count", "loan_transaction_count",
        F.col("source_retrieved_at").alias("projects_source_retrieved_at"),
        _one(ca, "source_as_of_date").alias("contract_awards_source_as_of_date"), _one(nt, "source_retrieved_at").alias("notices_source_retrieved_at"),
        _one(ib, "source_end_of_period").alias("financing_source_end_of_period"), _one(ieg, "source_as_of_date").alias("ieg_source_as_of_date"),
        _one(pd, "source_retrieved_at").alias("project_details_source_retrieved_at"),
        F.lit(SNAPSHOT_SEMANTICS).alias("portfolio_snapshot_semantics"), F.col("raw_record_sha256").alias("projects_raw_record_sha256"),
        F.lit(silver_versions).alias("silver_source_versions"), F.lit(run_id).alias("gold_run_id"), F.current_timestamp().alias("gold_processed_at"),
        F.lit(TRANSFORM_VERSION).alias("transform_version"),
    )
