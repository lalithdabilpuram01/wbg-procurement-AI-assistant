"""CPIA ratings: Bronze -> Silver (pure DataFrame functions).

Source: workspace.wbg_ref_bronze.cpia_ratings (raw_record JSON from WDI source 2; 6 series x 17,556 records;
rows 1960-2025 for 266 economies and aggregates; scores published 2005-2025 only).

Rules:
  * Key = (country_code, year, indicator_code); country_code is the World Bank API `country.id`, present on
    every record. countryiso3code is blank for the 5 income-group aggregates, so it is not a key.
  * Economies vs aggregates come from workspace.wbg_silver.wdi_country by exact country_code (no mapping).
  * Every Bronze record is kept, including the ~86% without a published score; missing scores stay NULL
    (`is_value_missing`). Nothing is imputed, averaged, rounded or clipped.
  * Lineage: original raw_record, its SHA-256, Bronze file and ingestion time, plus source vintage, URL,
    retrieval time and file SHA-256 from the staging manifest (verified against the Volume files).
"""
from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T
from silver.common.pipeline import CATALOG

TRANSFORM_VERSION = "cpia_silver_v1.0.0"
BRONZE_TABLE = f"{CATALOG}.wbg_ref_bronze.cpia_ratings"
BRONZE_FOLDER = "cpia_ratings"
WDI_COUNTRY = f"{CATALOG}.wbg_silver.wdi_country"
EXPECTED_INDICATORS = ("IQ.CPA.FINQ.XQ", "IQ.CPA.IRAI.XQ", "IQ.CPA.PADM.XQ", "IQ.CPA.PROP.XQ", "IQ.CPA.PUBS.XQ", "IQ.CPA.TRAN.XQ")
# criterion ratings are assigned in 0.5 steps; IRAI and the PUBS cluster are averages (reported only)
CRITERION_INDICATORS = ("IQ.CPA.FINQ.XQ", "IQ.CPA.PADM.XQ", "IQ.CPA.PROP.XQ", "IQ.CPA.TRAN.XQ")
SCALE = (1.0, 6.0)

RECORD_SCHEMA = T.StructType([
    T.StructField("indicator", T.StructType([T.StructField("id", T.StringType()), T.StructField("value", T.StringType())])),
    T.StructField("country", T.StructType([T.StructField("id", T.StringType()), T.StructField("value", T.StringType())])),
    T.StructField("countryiso3code", T.StringType()),
    T.StructField("date", T.StringType()),
    T.StructField("value", T.DoubleType()),
    T.StructField("unit", T.StringType()),
    T.StructField("obs_status", T.StringType()),
    T.StructField("decimal", T.IntegerType()),
])


def _blank_to_null(c: F.Column) -> F.Column:
    return F.when(F.trim(c) == "", F.lit(None)).otherwise(c)


def build(bronze: DataFrame, wdi_country: DataFrame, vintages: DataFrame, run_id: str) -> DataFrame:
    p = bronze.select(
        F.from_json("raw_record", RECORD_SCHEMA).alias("r"),
        F.get_json_object("raw_record", "$.value").alias("_raw_value_text"),
        F.col("indicator_code").alias("_bronze_indicator_code"),
        F.col("raw_record").alias("bronze_raw_record"),
        F.sha2("raw_record", 256).alias("raw_record_sha256"),
        F.col("source_file").alias("bronze_source_file"),
        F.col("ingested_at").alias("bronze_ingested_at"),
        F.element_at(F.split("source_file", "/"), -1).alias("_file_name"),
    )
    typed = p.select(
        _blank_to_null(F.col("r.country.id")).alias("country_code"),
        _blank_to_null(F.col("r.countryiso3code")).alias("country_iso3_code"),
        F.trim("r.country.value").alias("country_name"),
        F.col("r.indicator.id").alias("indicator_code"),
        F.col("r.indicator.value").alias("indicator_name"),
        F.regexp_extract(F.col("r.indicator.value"), r"\(([^()]*)\)\s*$", 1).alias("rating_scale"),
        F.col("r.date").cast("int").alias("year"),
        F.col("r.value").alias("value"),
        F.col("r.value").isNull().alias("is_value_missing"),
        _blank_to_null(F.col("r.unit")).alias("unit"),
        _blank_to_null(F.col("r.obs_status")).alias("obs_status"),
        F.col("r.decimal").alias("decimal_places"),
        "_raw_value_text", "_bronze_indicator_code", "_file_name",
        "bronze_raw_record", "raw_record_sha256", "bronze_source_file", "bronze_ingested_at",
    )
    w = wdi_country.select(F.col("country_code").alias("_wdi_code"), F.col("country_wb_id").alias("wdi_country_wb_id"),
                           F.col("entity_type").alias("wdi_entity_type"))
    out = (typed.join(F.broadcast(w), typed.country_code == w._wdi_code, "left")
           .join(F.broadcast(vintages), typed._file_name == vintages.file_name, "left"))
    return out.select(
        "country_code", "country_iso3_code", "country_name", "wdi_country_wb_id", "wdi_entity_type",
        F.when(F.col("_wdi_code").isNotNull(), F.lit("matched"))
         .when(F.col("country_code").isNull(), F.lit("unmatched_blank_source_code"))
         .otherwise(F.lit("unmatched_code_not_in_wdi")).alias("wdi_match_status"),
        "indicator_code", "indicator_name", "rating_scale", "year", "value", "is_value_missing",
        "unit", "obs_status", "decimal_places",
        F.lit("2").alias("source_id"), F.lit("World Development Indicators (CPIA database)").alias("source_name"),
        F.to_date("source_lastupdated").alias("source_lastupdated"),
        F.to_timestamp("source_retrieved_utc").alias("source_retrieved_at"), "source_file_sha256", "source_url",
        F.lit(BRONZE_TABLE).alias("bronze_table"), "bronze_source_file", "bronze_ingested_at",
        "bronze_raw_record", "raw_record_sha256",
        F.lit(run_id).alias("silver_run_id"), F.current_timestamp().alias("silver_processed_at"),
        F.lit(TRANSFORM_VERSION).alias("transform_version"),
        "_raw_value_text", "_bronze_indicator_code", "_file_name", "manifest_records",
    )


VALIDATION_ONLY_COLUMNS = ["_raw_value_text", "_bronze_indicator_code", "_file_name", "manifest_records"]
