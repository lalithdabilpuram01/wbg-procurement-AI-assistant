"""gold_country_context: one row per WDI economy x calendar year 2000-2025 (217 x 26 = 5,642), country context (dry run 2026-10-24).

Grid: the 217 wdi_country rows with entity_type = 'economy' (aggregates excluded; they stay in Silver) crossed with the WDI
extract years 2000-2025. Each source is pivoted to (country_code, year) on its own unique key and LEFT-joined to the grid,
so no combination duplicates a country-year:

  wdi_indicator_observations  GDP growth, CPI inflation, GDP per capita (calendar-year observations; 2026-10-08 vintage)
  wgi_estimates               six WGI estimates (survey-based annual estimates; matched to WDI codes; no 2001 edition)
  cpia_ratings                six CPIA ratings (IDA-eligible economies only; calendar-year assessment)
  income_classification_history  WB FISCAL-year income groups, carried for both fiscal years that overlap the calendar year:
                              FY ending 30 June of `year` (based on GNI of year-2) and FY starting 1 July of `year` (GNI year-1)

Values are copied exactly as published (no interpolation, imputation, normalisation or derived scores); unavailable values
stay NULL with per-source coverage counts and status. All values are the CURRENT vintage of each source: historical
values may have been revised after the fact and were not necessarily available at that time.
"""
from __future__ import annotations

import json

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from silver.common.pipeline import CATALOG

TRANSFORM_VERSION = "gold_country_context_v1.0.0"
TARGET = f"{CATALOG}.wbg_gold.gold_country_context"
S = f"{CATALOG}.wbg_silver."
SOURCES = ["wdi_country", "wdi_indicator_observations", "wgi_estimates", "cpia_ratings", "income_classification_history"]
YEARS = (2000, 2025)
SEMANTICS = "current_vintage_values_not_point_in_time"
WDI = {"NY.GDP.MKTP.KD.ZG": "gdp_growth_annual_pct", "FP.CPI.TOTL.ZG": "inflation_consumer_prices_annual_pct", "NY.GDP.PCAP.CD": "gdp_per_capita_current_usd"}
WGI = {"CC.EST": "wgi_control_of_corruption_est", "GE.EST": "wgi_government_effectiveness_est", "PV.EST": "wgi_political_stability_est",
       "RL.EST": "wgi_rule_of_law_est", "RQ.EST": "wgi_regulatory_quality_est", "VA.EST": "wgi_voice_accountability_est"}
CPIA = {"IQ.CPA.IRAI.XQ": "cpia_irai_overall", "IQ.CPA.PROP.XQ": "cpia_property_rights", "IQ.CPA.PUBS.XQ": "cpia_public_sector_mgmt_cluster",
        "IQ.CPA.FINQ.XQ": "cpia_budget_financial_mgmt", "IQ.CPA.PADM.XQ": "cpia_public_administration", "IQ.CPA.TRAN.XQ": "cpia_transparency_corruption"}


def load_sources(spark) -> tuple[dict[str, DataFrame], str]:
    """Read every Silver source, plus their current Delta versions as a JSON string (recorded on each Gold row)."""
    dfs = {t: spark.table(S + t) for t in SOURCES}
    versions = {t: spark.sql(f"DESCRIBE HISTORY {S}{t} LIMIT 1").collect()[0].version for t in SOURCES}
    return dfs, json.dumps(dict(sorted(versions.items())), separators=(",", ":"))


def _pivot(df: DataFrame, code_col: str, mapping: dict[str, str], prefix: str) -> DataFrame:
    """(country_code, year) -> one column per indicator; plus row presence and source vintage. Keys are unique in Silver."""
    y = df.filter(F.col("year").between(*YEARS))
    p = y.groupBy("country_code", "year").pivot(code_col, list(mapping)).agg(F.first("value"))
    p = p.select("country_code", "year", *[F.col(f"`{k}`").alias(v) for k, v in mapping.items()])
    meta = y.groupBy("country_code", "year").agg(F.count("*").alias(f"_{prefix}_rows"), F.max("source_lastupdated").alias(f"{prefix}_source_lastupdated"))
    return p.join(meta, ["country_code", "year"])


def _one(df: DataFrame, col: str) -> str:
    """The single value of `col` across the whole source; fails if there are several."""
    vals = [r[0] for r in df.select(col).distinct().collect()]
    assert len(vals) == 1, f"{col}: expected one source-wide value, got {len(vals)}"
    return vals[0]


def build(dfs: dict[str, DataFrame], silver_versions: str, run_id: str) -> DataFrame:
    """One row per economy and year (2000-2025) with WDI, WGI and CPIA values, coverage statuses and income groups."""
    wc = dfs["wdi_country"].filter("entity_type = 'economy'").select("country_code", F.col("country_wb_id").alias("country_iso3"), "country_name")
    grid = wc.crossJoin(dfs["wdi_country"].sparkSession.range(YEARS[0], YEARS[1] + 1).select(F.col("id").cast("int").alias("year")))
    wdi = _pivot(dfs["wdi_indicator_observations"].filter("entity_type = 'economy'"), "indicator_code", WDI, "wdi")
    wgi = _pivot(dfs["wgi_estimates"].filter("wdi_match_status = 'matched' AND wdi_entity_type = 'economy'"), "indicator_short_code", WGI, "wgi")
    cp = dfs["cpia_ratings"].filter("wdi_entity_type = 'economy'")
    cpia = _pivot(cp, "indicator_code", CPIA, "cpia")
    rated = cp.filter("NOT is_value_missing").select("country_code").distinct().withColumn("_ever_rated", F.lit(True))
    wgi_eco = dfs["wgi_estimates"].filter("wdi_match_status = 'matched'").select("country_code").distinct().withColumn("_in_wgi", F.lit(True))
    wgi_years = dfs["wgi_estimates"].select("year").distinct().withColumn("_wgi_year", F.lit(True))
    inc = dfs["income_classification_history"]
    # A calendar year overlaps two World Bank fiscal years (July-June): the one ending in it and the one starting in it.
    end = inc.select(F.col("iso3_code").alias("country_iso3"), F.col("fiscal_year").alias("year"), F.col("income_group_code").alias("income_group_fy_ending_in_year"),
                     F.col("classification_status").alias("income_status_fy_ending_in_year"))
    start = inc.select(F.col("iso3_code").alias("country_iso3"), (F.col("fiscal_year") - 1).alias("year"),
                       F.col("income_group_code").alias("income_group_fy_starting_in_year"), F.col("classification_status").alias("income_status_fy_starting_in_year"))
    j = (grid.join(wdi, ["country_code", "year"], "left").join(wgi, ["country_code", "year"], "left").join(cpia, ["country_code", "year"], "left")
         .join(rated, "country_code", "left").join(wgi_eco, "country_code", "left").join(wgi_years, "year", "left")
         .join(end, ["country_iso3", "year"], "left").join(start, ["country_iso3", "year"], "left"))
    present = lambda cols: sum((F.col(c).isNotNull().cast("int") for c in cols), F.lit(0))  # noqa: E731
    wgi_status = (F.when(F.col("_in_wgi").isNull(), F.lit("economy_not_in_source")).when(F.col("_wgi_year").isNull(), F.lit("year_not_published"))
                  .when(present(WGI.values()) > 0, F.lit("published")).otherwise(F.lit("all_values_missing")))
    cpia_status = (F.when(F.col("_ever_rated").isNull(), F.lit("economy_never_rated")).when(present(CPIA.values()) > 0, F.lit("published"))
                   .otherwise(F.lit("not_rated_in_year")))
    istat = lambda c: F.coalesce(F.col(c), F.lit("no_row_in_source"))  # noqa: E731
    return j.select(
        "country_code", "country_iso3", "country_name", "year", F.lit("calendar_year").alias("indicator_year_basis"),
        *WDI.values(), present(WDI.values()).alias("wdi_values_present"), "wdi_source_lastupdated",
        *WGI.values(), present(WGI.values()).alias("wgi_values_present"), wgi_status.alias("wgi_coverage_status"), "wgi_source_lastupdated",
        *CPIA.values(), present(CPIA.values()).alias("cpia_values_present"), cpia_status.alias("cpia_coverage_status"), "cpia_source_lastupdated",
        F.col("year").alias("income_fy_ending_in_year"), "income_group_fy_ending_in_year", istat("income_status_fy_ending_in_year").alias("income_status_fy_ending_in_year"),
        (F.col("year") + 1).alias("income_fy_starting_in_year"), "income_group_fy_starting_in_year",
        istat("income_status_fy_starting_in_year").alias("income_status_fy_starting_in_year"),
        F.lit(_one(inc, "workbook_file_name")).alias("income_source_workbook"), F.lit(_one(inc, "bronze_file_sha256")).alias("income_source_file_sha256"),
        F.lit(SEMANTICS).alias("context_snapshot_semantics"), F.lit(silver_versions).alias("silver_source_versions"), F.lit(run_id).alias("gold_run_id"),
        F.current_timestamp().alias("gold_processed_at"), F.lit(TRANSFORM_VERSION).alias("transform_version"),
    )
