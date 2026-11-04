"""Data-quality rules for workspace.wbg_silver.wgi_estimates. "error" blocks writes; "warn" is reported."""
from __future__ import annotations

import gzip
import hashlib

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.wgi.transform import EXPECTED_SHORT_CODES

NOMINAL_RANGE = 2.5   # "approx. -2.5 to +2.5" per the official series names


def _counts(df: DataFrame, key: str) -> dict:
    return {r[0]: r[1] for r in df.groupBy(key).count().collect()}


def verify_source_files(spark: SparkSession, bronze: DataFrame, vintages: list[dict], report: DQReport) -> None:
    """Re-read the Volume files Bronze came from; check SHA-256 against the manifest and lines against Bronze records."""
    paths = sorted(r[0] for r in bronze.select("source_file").distinct().collect())
    files = {r.path.split("/")[-1]: bytes(r.content) for r in spark.read.format("binaryFile").load(paths).select("path", "content").collect()}
    by_name = {v["file_name"]: v for v in vintages}
    report.equals("source_files_in_manifest", sorted(set(files) - set(by_name)), [])
    report.equals("source_file_sha256_matches_manifest",
                  {n: hashlib.sha256(b).hexdigest() == by_name.get(n, {}).get("source_file_sha256") for n, b in files.items()},
                  {n: True for n in files})
    bronze_hashes: dict[str, list[str]] = {}
    for r in bronze.select(F.element_at(F.split("source_file", "/"), -1).alias("n"), F.sha2("raw_record", 256).alias("h")).collect():
        bronze_hashes.setdefault(r.n, []).append(r.h)
    mismatch: dict[str, dict] = {}
    for n, b in files.items():
        lines = [ln for ln in gzip.decompress(b).decode("utf-8").splitlines() if ln.strip()]
        file_hashes = sorted(hashlib.sha256(ln.encode("utf-8")).hexdigest() for ln in lines)
        if file_hashes != sorted(bronze_hashes.get(n, [])):
            mismatch[n] = {"file_lines": len(lines), "bronze_rows": len(bronze_hashes.get(n, []))}
    report.equals("bronze_records_equal_source_file_lines (exact, per file)", mismatch, {})


def check(silver: DataFrame, bronze: DataFrame, report: DQReport) -> None:
    """Run every data-quality rule for wgi_estimates and add the results to `report`."""
    n = silver.count()
    # --- completeness
    report.equals("row_count_equals_bronze", n, bronze.count())
    report.equals("rows_per_indicator_equal_bronze", _counts(silver, "indicator_short_code"), _counts(bronze, "indicator_code"))
    report.equals("indicator_set", sorted(_counts(silver, "indicator_short_code")), sorted(EXPECTED_SHORT_CODES))
    report.equals("record_indicator_matches_bronze_column (GOV_WGI_ + short code)",
                  silver.filter(F.col("indicator_code") != F.concat(F.lit("GOV_WGI_"), F.col("indicator_short_code"))).count(), 0)
    report.equals("rows_equal_manifest_records", {r[0]: r[1] for r in silver.groupBy("_file_name").agg(F.count("*")).collect()},
                  {r[0]: r[1] for r in silver.select("_file_name", "manifest_records").distinct().collect()})
    # --- parsing, keys, entities
    report.equals("json_parse_failures (country_name null)", silver.filter(F.col("country_name").isNull()).count(), 0)
    report.equals("year_null", silver.filter(F.col("year").isNull()).count(), 0)
    report.equals("duplicate_key (entity_key, year, indicator_code)",
                  silver.groupBy("entity_key", "year", "indicator_code").count().filter("count > 1").count(), 0)
    names = silver.filter("entity_key_type = 'country_name'")
    report.equals("name_keyed_entities_have_no_code_or_iso3",
                  names.filter(F.col("country_code").isNotNull() | F.col("country_iso3_code").isNotNull()).count(), 0)
    report.add("name_keyed_entities (blank source code, kept, not mapped)",
               sorted(r[0] for r in names.select("country_name").distinct().collect()), "9 territories", True, "warn")
    report.equals("coded_entities_without_iso3", silver.filter(F.col("country_code").isNotNull() & F.col("country_iso3_code").isNull()).count(), 0)
    report.equals("coded_entities_not_in_wdi_country", silver.filter("country_code IS NOT NULL AND wdi_match_status != 'matched'").count(), 0)
    report.equals("iso3_differs_from_wdi_wb_id", silver.filter("wdi_country_wb_id IS NOT NULL AND country_iso3_code != wdi_country_wb_id").count(), 0)
    agg = silver.filter("wdi_entity_type = 'aggregate'").select("country_code").distinct().count()
    report.add("wgi_entities_that_are_wdi_aggregates", agg, 0, agg == 0, "warn")
    ent = silver.groupBy("entity_key_type").agg(F.countDistinct("entity_key").alias("n")).collect()
    report.add("entity_counts", {r.entity_key_type: r.n for r in ent}, {"wb_country_code": 207, "country_name": 9},
               {r.entity_key_type: r.n for r in ent} == {"wb_country_code": 207, "country_name": 9}, "warn")
    # --- years exactly as published
    years = sorted(r[0] for r in silver.select("year").distinct().collect())
    bronze_years = sorted(r[0] for r in bronze.select(F.get_json_object("raw_record", "$.date").cast("int")).distinct().collect())
    report.equals("years_equal_bronze_years (no years added)", years, bronze_years)
    report.add("years_absent_in_source (not filled)", sorted(set(range(min(years), max(years) + 1)) - set(years)), [1997, 1999, 2001],
               sorted(set(range(min(years), max(years) + 1)) - set(years)) == [1997, 1999, 2001], "warn")
    per = silver.groupBy("entity_key", "indicator_code").agg(F.countDistinct("year").alias("n")).filter(F.col("n") != len(years)).count()
    report.add("entity_indicator_series_with_fewer_rows_than_published_years", per, 0, per == 0, "warn")
    # --- values: NULL preserved, zero distinct from NULL, no parse loss
    raw_nulls = _counts(bronze.filter(F.get_json_object("raw_record", "$.value").isNull()), "indicator_code")
    report.equals("missing_values_preserved_per_indicator", _counts(silver.filter("is_value_missing"), "indicator_short_code"), raw_nulls)
    report.equals("value_parse_failures (raw non-null, typed null)", silver.filter(F.col("value").isNull() & F.col("_raw_value_text").isNotNull()).count(), 0)
    report.equals("is_value_missing_consistent", silver.filter(F.col("is_value_missing") != F.col("value").isNull()).count(), 0)
    zeros_raw = bronze.filter(F.get_json_object("raw_record", "$.value").cast("double") == 0).count()
    report.equals("exact_zero_values_equal_bronze (zero is a value, not missing)", silver.filter(F.col("value") == 0).count(), zeros_raw)
    b = {r[0]: r[1] for r in bronze.groupBy("indicator_code").agg(F.sum(F.get_json_object("raw_record", "$.value").cast("double"))).collect()}
    s = {r[0]: r[1] for r in silver.groupBy("indicator_short_code").agg(F.sum("value")).collect()}
    report.add("value_sum_reconciles_with_bronze", {k: round(v, 6) for k, v in s.items()}, {k: round(v, 6) for k, v in b.items()},
               all(abs(s[k] - b[k]) <= 1e-9 * max(1.0, abs(b[k])) for k in b))
    # --- lineage
    lineage = ["bronze_raw_record", "raw_record_sha256", "bronze_source_file", "bronze_ingested_at", "source_lastupdated",
               "source_retrieved_at", "source_file_sha256", "silver_run_id", "transform_version"]
    report.equals("lineage_nulls", {c: silver.filter(F.col(c).isNull()).count() for c in lineage}, {c: 0 for c in lineage})
    report.equals("raw_record_sha256_unique", silver.select("raw_record_sha256").distinct().count(), n)
    report.add("source_lastupdated_values", [str(r[0]) for r in silver.select("source_lastupdated").distinct().collect()], ["2026-09-25"],
               [str(r[0]) for r in silver.select("source_lastupdated").distinct().collect()] == ["2026-09-25"], "warn")
    # --- unusual values (reported, kept as published)
    out = silver.filter(F.abs("value") > NOMINAL_RANGE)
    report.add(f"values_outside_nominal_range_|x|>{NOMINAL_RANGE} (kept as published)", out.count(), "reported only", True, "warn",
               "; ".join(f"{r.entity_key} {r.year} {r.indicator_short_code}={round(r.value, 3)}"
                         for r in out.orderBy(F.abs("value").desc()).limit(10).collect()))
    report.add("unit_and_obs_status_empty_in_source", silver.filter(F.col("unit").isNotNull() | F.col("obs_status").isNotNull()).count(), 0, True, "warn")
