"""gold_procurement_notices: one row per published DS00979 procurement notice (243,533), notice-grain fact (dry run 2026-10-24).

Driving table: wbg_silver.procurement_notices (unique notice_op_id; notice_id is also unique). Other sources are looked up
N:1 or pre-aggregated per notice, so no join multiplies rows:

  projects (N:1)                              project name, current region, geography, validated wdi_country_code
  wdi_country, income_classification_history  ISO3 and income group at the publication fiscal year (single-country only)
  procurement_notice_details (1:0..1)         API status / award-notice version (publication-revision metadata), API
                                              submission deadline, bid-reference sharing counts (184 covered projects only)
  notice_contract_links -> per notice         linked-contract count and ambiguity, award notices of covered projects only

Notice classes: tender_invitation (Invitation for Bids), prequalification_invitation, expression_of_interest,
contract_award_announcement (Contract Award, Small Contracts Award), general_procurement_notice.

Deadlines: deadline_date is kept exactly as published. A response window is computed ONLY for solicitation notices
(IFB, IFP, REOI) with deadline > publication; award notices and GPNs publish deadline = publication date (not a deadline)
and never get a window. Equal and negative deadlines are flagged, never replaced. Windows describe the PUBLISHED period;
they are not observed tender durations and are not ML labels.

Excluded: notice text, bid descriptions (4 rows with phone numbers; D-14b addendum), notice URL, bid references and
award references (free text), supplier identifiers, contact fields. Notices present only in the API (9) are outside this grain.
"""
from __future__ import annotations

import json

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F
from silver.common.pipeline import CATALOG

TRANSFORM_VERSION = "gold_procurement_notices_v1.0.0"
TARGET = f"{CATALOG}.wbg_gold.gold_procurement_notices"
S = f"{CATALOG}.wbg_silver."
SOURCES = ["procurement_notices", "projects", "wdi_country", "income_classification_history", "procurement_notice_details", "notice_contract_links"]
SNAPSHOT_SEMANTICS = "ds00979_snapshot_retrieved_2026_10_21"
LONG_WINDOW_DAYS = 180  # review flag only: IFB p99 = 73 days, REOI p99 = 49 days
CLASSES = {"Invitation for Bids": "tender_invitation", "Invitation for Prequalification": "prequalification_invitation",
           "Request for Expression of Interest": "expression_of_interest", "Contract Award": "contract_award_announcement",
           "Small Contracts Award": "contract_award_announcement", "General Procurement Notice": "general_procurement_notice"}
SOLICITATION = ["Invitation for Bids", "Invitation for Prequalification", "Request for Expression of Interest"]


def load_sources(spark) -> tuple[dict[str, DataFrame], str]:
    """Read every Silver source, plus their current Delta versions as a JSON string (recorded on each Gold row)."""
    dfs = {t: spark.table(S + t) for t in SOURCES}
    versions = {t: spark.sql(f"DESCRIBE HISTORY {S}{t} LIMIT 1").collect()[0].version for t in SOURCES}
    return dfs, json.dumps(dict(sorted(versions.items())), separators=(",", ":"))


def details_lookup(d: DataFrame) -> DataFrame:
    """Per API notice: status, award version, deadline and how many notices share its bid reference in the project."""
    ref = Window.partitionBy("project_id", "bid_reference_normalized")
    ref_type = Window.partitionBy("project_id", "bid_reference_normalized", "notice_type")
    has_ref = F.col("bid_reference_normalized").isNotNull()
    return d.select(
        "notice_op_id", F.col("notice_status").alias("api_notice_status"), F.col("award_notice_version_no").alias("api_award_notice_version_no"),
        F.col("submission_deadline_date").alias("api_submission_deadline_date"),
        F.when(has_ref, F.count(F.lit(1)).over(ref)).cast("bigint").alias("api_notices_sharing_bid_ref_count"),
        F.when(has_ref, F.count(F.lit(1)).over(ref_type)).cast("bigint").alias("api_same_type_notices_sharing_bid_ref_count"),
        F.lit(True).alias("_api"))


def links_agg(k: DataFrame) -> DataFrame:
    return k.groupBy("notice_op_id").agg(F.count("*").cast("bigint").alias("_links"), F.max((F.col("ambiguity_status") == "ambiguous").cast("int")).alias("_amb"))


def build(dfs: dict[str, DataFrame], silver_versions: str, run_id: str) -> DataFrame:
    """One row per DS00979 notice with project, country, income, API-detail and link context, and its bid window."""
    n, p, wc, ic = dfs["procurement_notices"], dfs["projects"], dfs["wdi_country"], dfs["income_classification_history"]
    covered = dfs["procurement_notice_details"].select("project_id").distinct().withColumn("_covered", F.lit(True))
    proj = p.select("project_id", "project_name", F.col("region_name").alias("_pregion"), "geography_type", F.col("country_name").alias("project_country_name"),
                    F.col("wdi_country_code").alias("_w"))
    iso = wc.select(F.col("country_code").alias("_w"), F.col("country_wb_id").alias("_iso3"))
    inc = ic.select(F.col("iso3_code").alias("_iso3"), F.col("fiscal_year").alias("publication_fiscal_year"), F.col("income_group_code").alias("_inc"),
                    F.col("classification_status").alias("_inc_status"))
    j = (n.join(proj, "project_id", "left").join(iso, "_w", "left").join(inc, ["_iso3", "publication_fiscal_year"], "left")
         .join(details_lookup(dfs["procurement_notice_details"]), "notice_op_id", "left").join(links_agg(dfs["notice_contract_links"]), "notice_op_id", "left")
         .join(covered, "project_id", "left"))
    single = F.col("geography_type") == "single_country"
    solicitation = F.col("notice_type").isin(SOLICITATION)
    window = F.datediff("deadline_date", "publication_date")
    status = (F.when(~solicitation, F.lit("not_applicable_notice_type")).when(F.col("deadline_date").isNull(), F.lit("missing"))
              .when(window == 0, F.lit("equals_publication_date_no_window")).when(window < 0, F.lit("before_publication_date_invalid"))
              .when(window > LONG_WINDOW_DAYS, F.lit("positive_window_over_180_days_review")).otherwise(F.lit("positive_window")))
    cstatus = (F.when(~single, F.lit("regional_project")).when(F.col("country_code").isNull(), F.lit("missing_in_source"))
               .when(F.col("country_code") == F.col("_w"), F.lit("equal_to_project_wdi_code")).otherwise(F.lit("differs_from_project_wdi_code")))
    award = F.col("notice_type").isin("Contract Award", "Small Contracts Award")
    cov = F.coalesce(F.col("_covered"), F.lit(False))
    classes = F.create_map(*[F.lit(x) for kv in CLASSES.items() for x in kv])
    return j.select(
        "notice_op_id", "notice_id", "project_id", "project_name", F.col("_pregion").alias("region_name"), "geography_type", "project_country_name",
        F.when(single, F.col("_w")).alias("wdi_country_code"), F.when(single, F.col("_iso3")).alias("wdi_country_iso3"),
        F.when(single & F.col("_w").isNotNull(), F.lit("single_country_matched")).when(~single, F.lit("regional_not_applicable"))
         .otherwise(F.lit("single_country_unmatched")).alias("country_context_status"),
        F.col("country_code").alias("notice_country_code_published"), F.col("country_name").alias("notice_country_name_published"),
        cstatus.alias("notice_country_code_status"), F.col("region_name_source").alias("notice_region_name_published"),
        F.when(single, F.col("_inc")).alias("income_group_code_at_publication_fy"),
        F.when(~single, F.lit("regional_not_applicable")).when(F.col("_inc_status").isNull(), F.lit("no_row_in_source"))
         .otherwise(F.col("_inc_status")).alias("income_group_status_at_publication_fy"),
        "notice_type", classes[F.col("notice_type")].alias("notice_class"),
        (F.col("notice_type") == "Invitation for Bids").alias("is_tender_invitation"), solicitation.alias("is_solicitation_notice"),
        award.alias("is_award_announcement"),
        "procurement_category", "procurement_method", "sectors",
        "publication_date", "publication_fiscal_year", "publication_calendar_year",
        F.col("deadline_date").alias("deadline_date_published"), status.alias("deadline_status"),
        F.when(solicitation & (window > 0), window).alias("published_response_window_days"),
        F.when(solicitation & (window > 0), F.col("deadline_date") > F.lit("2026-10-21").cast("date")).alias("deadline_after_source_snapshot"),
        "identical_metadata_group_size", "has_api_detail",
        "api_notice_status", "api_award_notice_version_no", "api_submission_deadline_date",
        "api_notices_sharing_bid_ref_count", "api_same_type_notices_sharing_bid_ref_count",
        F.when(award & cov, F.coalesce(F.col("_links"), F.lit(0)).cast("bigint")).alias("linked_contract_count"),
        F.when(~award, F.lit("not_award_notice")).when(~cov, F.lit("not_covered")).when(F.col("_links").isNull(), F.lit("no_link_found"))
         .when(F.col("_amb") == 1, F.lit("ambiguous")).otherwise(F.lit("unambiguous")).alias("contract_link_status"),
        "source_dataset", "source_retrieved_at", "source_row_sha256", "bronze_source_file", "bronze_input_file_sha256", "source_population_sha256",
        F.lit(SNAPSHOT_SEMANTICS).alias("notice_snapshot_semantics"), F.col("silver_run_id").alias("silver_notices_run_id"),
        F.lit(silver_versions).alias("silver_source_versions"), F.lit(run_id).alias("gold_run_id"), F.current_timestamp().alias("gold_processed_at"),
        F.lit(TRANSFORM_VERSION).alias("transform_version"),
    )
