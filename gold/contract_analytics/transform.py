"""gold_contract_analytics: one row per current DS00005 contract (178,921), contract-grain fact (design for dry run 2026-10-24).

Driving table: wbg_silver.contract_awards (one row per wb_contract_number; exact decimal contract_amount_usd = sum of its
supplier lines). Everything else is aggregated to the contract (or looked up N:1) BEFORE joining, so no join multiplies rows:

  projects (N:1 on project_id)                 project name, region, geography, validated wdi_country_code, approval date/FY
  wdi_country (N:1 on validated code)          ISO3, single-country projects only
  income_classification_history (unique key)  income group for the contract's signing fiscal year (point-in-time)
  contract_award_lines -> per contract         supplier-ID type flags, distinct supplier country codes, missing counts,
                                               lineage hash of the line row hashes (no supplier names or IDs leave Silver)
  notice_contract_links -> per contract        link count / status / earliest linked award-notice date, only for contracts
                                               of projects covered by the API notice-detail pull (NULL elsewhere)

Country semantics: country context comes ONLY from the project's validated wdi_country_code; regional projects get NULL.
DS00005's own borrower_country_code (legacy codes such as ZR, GZ, YF, RY, TP; NULL for some countries; regional codes) is
kept as published with a consistency status and never mapped or used for joins.

Dates: contract_signing_date is the contract event date. It is NOT the date the record became public; DS00005 has no
publication date. The dataset snapshot date (source_as_of_date) and, where available, the earliest linked award-notice
date are recorded separately.

Excluded: supplier names and IDs, line keys (they embed supplier IDs), contract descriptions, borrower contract
references (free text), notice text, legacy contracts (FY2006-2016).
"""
from __future__ import annotations

import json

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from silver.common.pipeline import CATALOG

TRANSFORM_VERSION = "gold_contract_analytics_v1.0.0"
TARGET = f"{CATALOG}.wbg_gold.gold_contract_analytics"
S = f"{CATALOG}.wbg_silver."
SOURCES = ["contract_awards", "contract_award_lines", "projects", "wdi_country", "income_classification_history",
           "notice_contract_links", "procurement_notice_details"]
SNAPSHOT_SEMANTICS = "ds00005_dataset_snapshot_as_of_source_as_of_date"
MISSING_COUNTRY = ["null", ""]  # literal "null" strings are published in DS00005 supplier country codes


def load_sources(spark) -> tuple[dict[str, DataFrame], str]:
    """Read every Silver source, plus their current Delta versions as a JSON string (recorded on each Gold row)."""
    dfs = {t: spark.table(S + t) for t in SOURCES}
    versions = {t: spark.sql(f"DESCRIBE HISTORY {S}{t} LIMIT 1").collect()[0].version for t in SOURCES}
    return dfs, json.dumps(dict(sorted(versions.items())), separators=(",", ":"))


def lines_agg(lines: DataFrame) -> DataFrame:
    """Per contract: supplier-ID type flags, supplier countries, missing-country count and a hash of the line hashes."""
    missing = F.col("supplier_country_code").isNull() | F.col("supplier_country_code").isin(MISSING_COUNTRY)
    return lines.groupBy("wb_contract_number").agg(
        F.count("*").alias("_line_count"),
        F.max((F.col("supplier_id_type") == "anonymized_individual_consultant").cast("int")).cast("boolean").alias("has_anonymized_individual_consultant"),
        F.max((F.col("supplier_id_type") == "numeric").cast("int")).cast("boolean").alias("has_supplier_with_numeric_id"),
        F.max((F.col("supplier_id_type") == "missing").cast("int")).cast("boolean").alias("has_supplier_with_missing_id"),
        F.array_sort(F.collect_set(F.when(~missing, F.col("supplier_country_code")))).alias("supplier_country_codes_published"),
        F.sum(missing.cast("int")).cast("bigint").alias("supplier_lines_missing_country"),
        F.sha2(F.concat_ws(",", F.array_sort(F.collect_list("source_row_sha256"))), 256).alias("source_rows_sha256"))


def links_agg(links: DataFrame) -> DataFrame:
    """Per contract: number of notice links, whether any is ambiguous, and the earliest linked award notice date."""
    return links.groupBy("wb_contract_number").agg(
        F.count("*").alias("_links"), F.max((F.col("ambiguity_status") == "ambiguous").cast("int")).alias("_ambiguous"),
        F.min("notice_date").alias("earliest_linked_award_notice_date"))


def build(dfs: dict[str, DataFrame], silver_versions: str, run_id: str) -> DataFrame:
    """One row per Silver contract with project, country, income-group, supplier and notice-link context."""
    a, p, wc, ic = dfs["contract_awards"], dfs["projects"], dfs["wdi_country"], dfs["income_classification_history"]
    covered = dfs["procurement_notice_details"].select("project_id").distinct().withColumn("_covered", F.lit(True))
    proj = p.select("project_id", F.col("project_name"), F.col("region_name"), "geography_type", F.col("country_name").alias("project_country_name"),
                    F.col("wdi_country_code").alias("_w"), F.col("approval_fiscal_year").alias("project_approval_fiscal_year"),
                    F.col("board_approval_date").alias("project_board_approval_date"))
    iso = wc.select(F.col("country_code").alias("_w"), F.col("country_wb_id").alias("_iso3"))
    inc = ic.select(F.col("iso3_code").alias("_iso3"), F.col("fiscal_year").alias("fiscal_year"), F.col("income_group_code").alias("_inc"),
                    F.col("classification_status").alias("_inc_status"))
    j = (a.join(proj, "project_id", "left").join(iso, "_w", "left").join(inc, ["_iso3", "fiscal_year"], "left")
         .join(lines_agg(dfs["contract_award_lines"]), "wb_contract_number", "left").join(links_agg(dfs["notice_contract_links"]), "wb_contract_number", "left")
         .join(covered, "project_id", "left"))
    single = F.col("geography_type") == "single_country"
    cov = F.coalesce(F.col("_covered"), F.lit(False))
    bstatus = (F.when(~single, F.lit("regional_project")).when(F.col("borrower_country_code").isNull(), F.lit("missing_in_source"))
               .when(F.col("borrower_country_code") == F.col("_w"), F.lit("equal_to_project_wdi_code")).otherwise(F.lit("differs_from_project_wdi_code")))
    codes = F.col("supplier_country_codes_published")
    # Supplier countries are compared only when the borrower code matches the project and every line has a country.
    relation = (F.when((bstatus != "equal_to_project_wdi_code") | (F.col("supplier_lines_missing_country") > 0), F.lit("undetermined"))
                .when(F.forall(codes, lambda c: c == F.col("borrower_country_code")), F.lit("all_suppliers_same_country"))
                .otherwise(F.lit("includes_other_country")))
    days = F.datediff("contract_signing_date", "project_board_approval_date")
    return j.select(
        "wb_contract_number", "project_id", "project_name", "region_name", "geography_type", "project_country_name",
        F.when(single, F.col("_w")).alias("wdi_country_code"), F.when(single, F.col("_iso3")).alias("wdi_country_iso3"),
        F.when(single & F.col("_w").isNotNull(), F.lit("single_country_matched")).when(~single, F.lit("regional_not_applicable"))
         .otherwise(F.lit("single_country_unmatched")).alias("country_context_status"),
        F.col("borrower_country_code").alias("borrower_country_code_published"), F.col("borrower_country_name").alias("borrower_country_name_published"),
        bstatus.alias("borrower_country_code_status"),
        F.when(single, F.col("_inc")).alias("income_group_code_at_signing_fy"),
        F.when(~single, F.lit("regional_not_applicable")).when(F.col("_inc_status").isNull(), F.lit("no_row_in_source"))
         .otherwise(F.col("_inc_status")).alias("income_group_status_at_signing_fy"),
        "project_approval_fiscal_year", "project_board_approval_date",
        "procurement_category", "procurement_method", "review_type", "global_practices",
        "contract_signing_date", F.col("fiscal_year").alias("contract_fiscal_year"), "contract_signed_calendar_year",
        days.alias("days_from_board_approval_to_signing"), (days < 0).alias("signed_before_board_approval"),
        "contract_amount_usd", "contract_amount_basis", "supplier_line_count", "is_multi_supplier", "supplier_lines_missing_amount",
        "has_anonymized_individual_consultant", "has_supplier_with_numeric_id", "has_supplier_with_missing_id",
        "supplier_country_codes_published", "supplier_lines_missing_country", relation.alias("supplier_country_relation"),
        F.col("contracts_sharing_reference_in_project").alias("contracts_sharing_borrower_reference_in_project"),
        cov.alias("award_notice_link_coverage"),
        F.when(cov, F.coalesce(F.col("_links"), F.lit(0)).cast("bigint")).alias("linked_award_notice_count"),
        F.when(~cov, F.lit("not_covered")).when(F.col("_links").isNull(), F.lit("no_link_found"))
         .when(F.col("_ambiguous") == 1, F.lit("ambiguous")).otherwise(F.lit("unambiguous")).alias("award_notice_link_status"),
        "earliest_linked_award_notice_date",
        "source_dataset", "source_as_of_date", "source_retrieved_at", "bronze_source_file", "bronze_input_file_sha256", "source_population_sha256",
        "source_rows_sha256", F.lit(SNAPSHOT_SEMANTICS).alias("contract_snapshot_semantics"), F.col("silver_run_id").alias("silver_contracts_run_id"),
        F.lit(silver_versions).alias("silver_source_versions"), F.lit(run_id).alias("gold_run_id"), F.current_timestamp().alias("gold_processed_at"),
        F.lit(TRANSFORM_VERSION).alias("transform_version"),
    )
