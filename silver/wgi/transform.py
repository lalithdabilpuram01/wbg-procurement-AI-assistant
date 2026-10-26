"""WGI governance estimates: Bronze -> Silver (pure DataFrame functions plus a small vintage loader).

Source: workspace.wbg_ref_bronze.wgi_estimates (raw_record JSON, indicator_code short form e.g. "GE.EST",
source_file in the Volume, ingested_at). Six series, 5,832 records each.

Rules:
  * Entity identifier: the World Bank API `country.id` (2-character, same scheme as WDI) when present.
    Nine territories are published with a blank code and blank ISO3 (Anguilla, Cook Islands, ...); they are
    keyed by their published name (`entity_key = "name:<country_name>"`, `entity_key_type = "country_name"`).
    No code is invented for them and they are not matched to WDI.
  * WDI relationship: `country_code` is looked up in workspace.wbg_silver.wdi_country (exact match only).
  * Values: estimates kept exactly as published (DOUBLE); NULL when not published (`is_value_missing`).
    Zero is a valid value and is never confused with NULL. Nothing is imputed; no years are added
    (WGI has no 1997, 1999, 2001 rows).
  * Lineage: the raw Bronze record, its SHA-256, Bronze source file and ingestion time, plus the source
    vintage (`lastupdated` 2026-09-25), retrieval time and file SHA-256 from the staging manifest. The
    manifest is verified against the Volume files by the checks.
"""
from __future__ import annotations

import json
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

TRANSFORM_VERSION = "wgi_silver_v1.0.0"
BRONZE_TABLE = "workspace.wbg_ref_bronze.wgi_estimates"
WDI_COUNTRY = "workspace.wbg_silver.wdi_country"
EXPECTED_SHORT_CODES = ("CC.EST", "GE.EST", "PV.EST", "RL.EST", "RQ.EST", "VA.EST")
MANIFEST = Path(__file__).resolve().parents[2] / "databricks_upload" / "ref_bronze_inputs" / "_manifest" / "ref_bronze_inputs_manifest.json"

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


def load_vintages(manifest_path: Path = MANIFEST) -> list[dict]:
    """Source vintage per WGI file from the staging manifest (file name, lastupdated, retrieval time, sha256)."""
    files = json.loads(manifest_path.read_text())["files"]
    return [{"file_name": Path(f["staged_file"]).name, "source_lastupdated": f["source_lastupdated"],
             "source_retrieved_utc": f["retrieved_utc"], "source_file_sha256": f["sha256"], "manifest_records": int(f["records"]),
             "source_url": f["source_url"]}
            for f in files if f["bronze_table"] == "wgi_estimates"]


def _blank_to_null(c: F.Column) -> F.Column:
    return F.when(F.trim(c) == "", F.lit(None)).otherwise(c)


def build(bronze: DataFrame, wdi_country: DataFrame, vintages: DataFrame, run_id: str) -> DataFrame:
    p = bronze.select(
        F.from_json("raw_record", RECORD_SCHEMA).alias("r"),
        F.get_json_object("raw_record", "$.value").alias("_raw_value_text"),
        F.col("raw_record").alias("bronze_raw_record"),
        F.sha2("raw_record", 256).alias("raw_record_sha256"),
        F.col("indicator_code").alias("indicator_short_code"),
        F.col("source_file").alias("bronze_source_file"),
        F.col("ingested_at").alias("bronze_ingested_at"),
        F.element_at(F.split("source_file", "/"), -1).alias("_file_name"),
    )
    code = _blank_to_null(F.col("r.country.id"))
    typed = p.select(
        F.coalesce(code, F.concat(F.lit("name:"), F.trim("r.country.value"))).alias("entity_key"),
        F.when(code.isNotNull(), F.lit("wb_country_code")).otherwise(F.lit("country_name")).alias("entity_key_type"),
        code.alias("country_code"),
        _blank_to_null(F.col("r.countryiso3code")).alias("country_iso3_code"),
        F.trim("r.country.value").alias("country_name"),
        F.col("r.indicator.id").alias("indicator_code"),
        "indicator_short_code",
        F.col("r.indicator.value").alias("indicator_name"),
        F.trim(F.split(F.col("r.indicator.value"), " - ").getItem(0)).alias("governance_dimension"),
        F.col("r.date").cast("int").alias("year"),
        F.col("r.value").alias("value"),
        F.col("r.value").isNull().alias("is_value_missing"),
        _blank_to_null(F.col("r.unit")).alias("unit"),
        _blank_to_null(F.col("r.obs_status")).alias("obs_status"),
        F.col("r.decimal").alias("decimal_places"),
        "_raw_value_text", "_file_name", "bronze_raw_record", "raw_record_sha256", "bronze_source_file", "bronze_ingested_at",
    )
    w = wdi_country.select(F.col("country_code").alias("_wdi_code"), F.col("country_wb_id").alias("wdi_country_wb_id"),
                           F.col("entity_type").alias("wdi_entity_type"))
    out = (typed.join(F.broadcast(w), typed.country_code == w._wdi_code, "left")
           .join(F.broadcast(vintages), typed._file_name == vintages.file_name, "left"))
    return out.select(
        "entity_key", "entity_key_type", "country_code", "country_iso3_code", "country_name",
        "wdi_country_wb_id", "wdi_entity_type",
        F.when(F.col("_wdi_code").isNotNull(), F.lit("matched"))
         .when(F.col("country_code").isNull(), F.lit("unmatched_blank_source_code"))
         .otherwise(F.lit("unmatched_code_not_in_wdi")).alias("wdi_match_status"),
        "indicator_code", "indicator_short_code", "indicator_name", "governance_dimension",
        "year", "value", "is_value_missing", "unit", "obs_status", "decimal_places",
        F.lit("3").alias("source_id"), F.lit("Worldwide Governance Indicators").alias("source_name"),
        F.to_date("source_lastupdated").alias("source_lastupdated"),
        F.to_timestamp("source_retrieved_utc").alias("source_retrieved_at"), "source_file_sha256", "source_url",
        F.lit(BRONZE_TABLE).alias("bronze_table"), "bronze_source_file", "bronze_ingested_at",
        "bronze_raw_record", "raw_record_sha256",
        F.lit(run_id).alias("silver_run_id"), F.current_timestamp().alias("silver_processed_at"),
        F.lit(TRANSFORM_VERSION).alias("transform_version"),
        "_raw_value_text", "_file_name", "manifest_records",
    )


VALIDATION_ONLY_COLUMNS = ["_raw_value_text", "_file_name", "manifest_records"]


def vintages_df(spark: SparkSession, vintages: list[dict]) -> DataFrame:
    return spark.createDataFrame(vintages, "file_name string, source_lastupdated string, source_retrieved_utc string, "
                                           "source_file_sha256 string, manifest_records int, source_url string")
