"""Data-quality rules for the WDI Silver tables. "error" checks block writes; "warn" checks are reported."""
from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.wdi.transform import EXPECTED_INDICATORS

YEARS = (2000, 2025)


def _counts(df: DataFrame, key: str) -> dict:
    return {r[key]: r["count"] for r in df.groupBy(key).count().collect()}


def check_observations(silver: DataFrame, bronze: DataFrame, report: DQReport) -> None:
    n_years = YEARS[1] - YEARS[0] + 1
    # --- completeness: one Silver row per Bronze row
    report.equals("row_count_equals_bronze", silver.count(), bronze.count())
    report.equals("rows_per_indicator_equal_bronze", _counts(silver, "indicator_code"), _counts(bronze, "indicator_code"))
    report.equals("indicator_set", sorted(_counts(silver, "indicator_code")), sorted(EXPECTED_INDICATORS))
    report.equals("record_indicator_matches_bronze_column",
                  silver.filter(F.col("indicator_code") != F.col("_bronze_indicator_code")).count(), 0)
    # --- parsing and keys
    report.equals("json_parse_failures (country_code null)", silver.filter(F.col("country_code").isNull()).count(), 0)
    report.equals("blank_country_code", silver.filter(F.trim("country_code") == "").count(), 0)
    report.equals("year_null", silver.filter(F.col("year").isNull()).count(), 0)
    dup = silver.groupBy("country_code", "year", "indicator_code").count().filter("count > 1").count()
    report.equals("duplicate_key (country_code, year, indicator_code)", dup, 0)
    # --- entity resolution against country metadata
    report.equals("unresolved_entities (no metadata match)", silver.filter(F.col("entity_type").isNull()).count(), 0)
    report.equals("country_wb_id_null", silver.filter(F.col("country_wb_id").isNull()).count(), 0)
    ent = silver.groupBy("entity_type").agg(F.countDistinct("country_code").alias("n")).collect()
    report.add("entity_counts (economies 217, aggregates 49 at 2026-10-08 vintage)", {r.entity_type: r.n for r in ent},
               {"economy": 217, "aggregate": 49}, {r.entity_type: r.n for r in ent} == {"economy": 217, "aggregate": 49}, "warn")
    iso_null_economy = silver.filter(F.col("country_iso3_code").isNull() & (F.col("entity_type") == "economy")).count()
    report.equals("economies_with_null_iso3", iso_null_economy, 0)
    iso_null = silver.filter(F.col("country_iso3_code").isNull()).groupBy("country_code").count().collect()
    report.add("null_iso3_entities (expected: 5 income-group aggregates x 78 rows)",
               {r.country_code: r["count"] for r in iso_null}, "XD, XM, XN, XT, XY x 78 = 390",
               sorted(r.country_code for r in iso_null) == ["XD", "XM", "XN", "XT", "XY"] and sum(r["count"] for r in iso_null) == 390, "warn")
    # --- year coverage
    yr = silver.agg(F.min("year").alias("lo"), F.max("year").alias("hi")).collect()[0]
    report.equals("year_range", [yr.lo, yr.hi], list(YEARS))
    incomplete = silver.groupBy("country_code", "indicator_code").agg(F.countDistinct("year").alias("n")).filter(F.col("n") != n_years).count()
    report.equals(f"series_without_all_{n_years}_years", incomplete, 0)
    # --- missing values preserved exactly
    raw_nulls = _counts(bronze.filter(F.get_json_object("raw_record", "$.value").isNull()), "indicator_code")
    report.equals("missing_values_preserved_per_indicator", _counts(silver.filter("is_value_missing"), "indicator_code"), raw_nulls)
    report.equals("value_parse_failures (raw non-null, typed null)",
                  silver.filter(F.col("value").isNull() & F.col("_raw_value_text").isNotNull()).count(), 0)
    report.equals("is_value_missing_consistent", silver.filter(F.col("is_value_missing") != F.col("value").isNull()).count(), 0)
    # --- value reconciliation with Bronze (sum per indicator)
    b = {r.indicator_code: r.s for r in bronze.groupBy("indicator_code").agg(
        F.sum(F.get_json_object("raw_record", "$.value").cast("double")).alias("s")).collect()}
    s = {r.indicator_code: r.s for r in silver.groupBy("indicator_code").agg(F.sum("value").alias("s")).collect()}
    report.add("value_sum_reconciles_with_bronze", {k: round(v, 6) for k, v in s.items()},
               {k: round(v, 6) for k, v in b.items()}, all(abs(s[k] - b[k]) <= 1e-9 * max(1.0, abs(b[k])) for k in b))
    # --- vintage and lineage
    report.equals("source_lastupdated_null", silver.filter(F.col("source_lastupdated").isNull()).count(), 0)
    report.equals("vintage_from_same_source_file", silver.filter(F.col("_vintage_source_file") != F.col("bronze_source_file")).count(), 0)
    tot = {r.indicator_code: r.t for r in silver.select("indicator_code", F.col("source_api_total").alias("t")).distinct().collect()}
    report.equals("api_header_total_equals_rows", tot, _counts(bronze, "indicator_code"))
    report.add("source_lastupdated_values", [str(r[0]) for r in silver.select("source_lastupdated").distinct().collect()],
               "single vintage per indicator", silver.select("indicator_code", "source_lastupdated").distinct().count() == len(EXPECTED_INDICATORS))
    lineage = ["bronze_table", "bronze_source_file", "bronze_ingested_at", "raw_record_sha256", "silver_run_id", "transform_version"]
    report.equals("lineage_nulls", {c: silver.filter(F.col(c).isNull()).count() for c in lineage}, {c: 0 for c in lineage})
    report.equals("raw_record_sha256_unique", silver.select("raw_record_sha256").distinct().count(), silver.count())
    # --- plausibility (warnings only; values are never altered)
    report.add("gdp_per_capita_non_positive",
               silver.filter((F.col("indicator_code") == "NY.GDP.PCAP.CD") & (F.col("value") <= 0)).count(), 0,
               silver.filter((F.col("indicator_code") == "NY.GDP.PCAP.CD") & (F.col("value") <= 0)).count() == 0, "warn")
    ext = silver.filter(((F.col("indicator_code") == "FP.CPI.TOTL.ZG") & (F.abs("value") > 100)) |
                        ((F.col("indicator_code") == "NY.GDP.MKTP.KD.ZG") & (F.abs("value") > 50)))
    report.add("extreme_values (inflation |x|>100 or GDP growth |x|>50; kept as published)", ext.count(), "reported only", True, "warn",
               "; ".join(f"{r.country_code} {r.year} {r.indicator_code}={round(r.value, 1)}" for r in ext.orderBy("indicator_code", "country_code", "year").limit(12).collect()))
    report.add("unit_and_obs_status_empty_in_source", silver.filter(F.col("unit").isNotNull() | F.col("obs_status").isNotNull()).count(), 0,
               True, "warn", "source publishes empty unit/obs_status; unit is in indicator_name")


def check_countries(countries: DataFrame, observations: DataFrame, report: DQReport) -> None:
    n = countries.count()
    api_total = countries.select("metadata_api_total").distinct().collect()
    report.equals("row_count_equals_api_total", n, api_total[0][0] if len(api_total) == 1 else api_total)
    report.equals("country_code_unique", countries.select("country_code").distinct().count(), n)
    report.equals("country_wb_id_unique", countries.select("country_wb_id").distinct().count(), n)
    report.equals("entity_type_null", countries.filter(F.col("entity_type").isNull()).count(), 0)
    obs_codes = observations.select("country_code").distinct().count()
    report.equals("observation_entities_present", countries.filter("has_wdi_observations").count(), obs_codes)
    report.add("entity_type_counts", {r.entity_type: r["count"] for r in countries.groupBy("entity_type").count().collect()},
               "reported", True, "warn")
    report.equals("aggregates_with_region", countries.filter((F.col("entity_type") == "aggregate") & F.col("region_code_current").isNotNull()).count(), 0)
