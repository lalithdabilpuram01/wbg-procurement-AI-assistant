"""Data-quality rules for workspace.wbg_silver.cpia_ratings. "error" blocks writes; "warn" is reported."""
from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.cpia.transform import CRITERION_INDICATORS, EXPECTED_INDICATORS, SCALE


def _counts(df: DataFrame, key: str) -> dict:
    return {r[0]: r[1] for r in df.groupBy(key).count().collect()}


def check(silver: DataFrame, bronze: DataFrame, report: DQReport) -> None:
    """Run every data-quality rule for cpia_ratings and add the results to `report`."""
    n = silver.count()
    raw_val = F.get_json_object("raw_record", "$.value")
    # --- completeness: every Bronze record kept, with or without a score
    report.equals("row_count_equals_bronze", n, bronze.count())
    report.equals("rows_per_indicator_equal_bronze", _counts(silver, "indicator_code"), _counts(bronze, "indicator_code"))
    report.equals("indicator_set", sorted(_counts(silver, "indicator_code")), sorted(EXPECTED_INDICATORS))
    report.equals("record_indicator_matches_bronze_column", silver.filter(F.col("indicator_code") != F.col("_bronze_indicator_code")).count(), 0)
    report.equals("rows_equal_manifest_records", {r[0]: r[1] for r in silver.groupBy("_file_name").agg(F.count("*")).collect()},
                  {r[0]: r[1] for r in silver.select("_file_name", "manifest_records").distinct().collect()})
    # --- parsing, keys, entities
    report.equals("json_parse_failures (country_name null)", silver.filter(F.col("country_name").isNull()).count(), 0)
    report.equals("blank_country_code", silver.filter(F.col("country_code").isNull()).count(), 0)
    report.equals("year_null", silver.filter(F.col("year").isNull()).count(), 0)
    report.equals("duplicate_key (country_code, year, indicator_code)",
                  silver.groupBy("country_code", "year", "indicator_code").count().filter("count > 1").count(), 0)
    report.equals("entities_not_in_wdi_country", silver.filter("wdi_match_status != 'matched'").select("country_code").distinct().count(), 0)
    report.equals("iso3_differs_from_wdi_wb_id (where iso3 published)",
                  silver.filter("country_iso3_code IS NOT NULL AND country_iso3_code != wdi_country_wb_id").count(), 0)
    iso_null = sorted(r[0] for r in silver.filter("country_iso3_code IS NULL").select("country_code").distinct().collect())
    report.add("null_iso3_entities (expected: 5 income-group aggregates)", iso_null, ["XD", "XM", "XN", "XT", "XY"],
               iso_null == ["XD", "XM", "XN", "XT", "XY"], "warn")
    report.equals("null_iso3_only_for_aggregates", silver.filter("country_iso3_code IS NULL AND wdi_entity_type != 'aggregate'").count(), 0)
    ent = {r[0]: r[1] for r in silver.groupBy("wdi_entity_type").agg(F.countDistinct("country_code")).collect()}
    report.add("entity_counts", ent, {"economy": 217, "aggregate": 49}, ent == {"economy": 217, "aggregate": 49}, "warn")
    # --- years exactly as published (rows), no years added
    years = sorted(r[0] for r in silver.select("year").distinct().collect())
    bronze_years = sorted(r[0] for r in bronze.select(F.get_json_object("raw_record", "$.date").cast("int")).distinct().collect())
    report.equals("years_equal_bronze_years (no years added)", years, bronze_years)
    incomplete = silver.groupBy("country_code", "indicator_code").agg(F.countDistinct("year").alias("n")).filter(F.col("n") != len(years)).count()
    report.add("entity_indicator_series_without_a_row_for_every_year", incomplete, 0, incomplete == 0, "warn")
    vy = sorted(r[0] for r in silver.filter("NOT is_value_missing").select("year").distinct().collect())
    report.add("years_with_scores", [vy[0], vy[-1], len(vy)], [2005, 2025, 21], [vy[0], vy[-1], len(vy)] == [2005, 2025, 21], "warn")
    # --- scores: NULL preserved, zero distinct, no parse loss, reconcile
    raw_nulls = _counts(bronze.filter(raw_val.isNull()), "indicator_code")
    report.equals("missing_scores_preserved_per_indicator", _counts(silver.filter("is_value_missing"), "indicator_code"), raw_nulls)
    report.equals("value_parse_failures (raw non-null, typed null)", silver.filter(F.col("value").isNull() & F.col("_raw_value_text").isNotNull()).count(), 0)
    report.equals("is_value_missing_consistent", silver.filter(F.col("is_value_missing") != F.col("value").isNull()).count(), 0)
    report.equals("exact_zero_scores_equal_bronze", silver.filter("value = 0").count(), bronze.filter(raw_val.cast("double") == 0).count())
    b = {r[0]: r[1] for r in bronze.groupBy("indicator_code").agg(F.sum(raw_val.cast("double"))).collect()}
    s = {r[0]: r[1] for r in silver.groupBy("indicator_code").agg(F.sum("value")).collect()}
    report.add("score_sum_reconciles_with_bronze", {k: round(v, 6) for k, v in s.items()}, {k: round(v, 6) for k, v in b.items()},
               all(abs(s[k] - b[k]) <= 1e-9 * max(1.0, abs(b[k])) for k in b))
    # --- lineage and vintage
    lineage = ["bronze_raw_record", "raw_record_sha256", "bronze_source_file", "bronze_ingested_at", "source_lastupdated",
               "source_retrieved_at", "source_file_sha256", "source_url", "silver_run_id", "transform_version"]
    report.equals("lineage_nulls", {c: silver.filter(F.col(c).isNull()).count() for c in lineage}, {c: 0 for c in lineage})
    report.equals("raw_record_sha256_unique", silver.select("raw_record_sha256").distinct().count(), n)
    lu = sorted(str(r[0]) for r in silver.select("source_lastupdated").distinct().collect())
    report.add("source_lastupdated_values", lu, ["2026-10-08"], lu == ["2026-10-08"], "warn")
    # --- unusual values (reported, kept as published)
    out = silver.filter((F.col("value") < SCALE[0]) | (F.col("value") > SCALE[1]))
    report.add(f"scores_outside_published_scale_{SCALE}", out.count(), 0, out.count() == 0, "warn",
               "; ".join(f"{r.country_code} {r.year} {r.indicator_code}={r.value}" for r in out.limit(10).collect()))
    crit = silver.filter(F.col("indicator_code").isin(*CRITERION_INDICATORS) & (F.col("wdi_entity_type") == "economy")
                         & F.col("value").isNotNull() & ((F.col("value") * 2) != F.floor(F.col("value") * 2)))
    report.add("economy_criterion_scores_not_in_half_steps (kept as published)", crit.count(), "reported only", True, "warn",
               "; ".join(f"{r.country_code} {r.year} {r.indicator_code}={r.value}" for r in crit.limit(10).collect()))
    report.add("unit_and_obs_status_empty_in_source", silver.filter(F.col("unit").isNotNull() | F.col("obs_status").isNotNull()).count(), 0, True, "warn")
