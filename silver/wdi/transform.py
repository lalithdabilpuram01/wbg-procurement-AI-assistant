"""WDI economic indicators: Bronze -> Silver transformation (pure DataFrame functions, no I/O).

Sources (workspace.wbg_ref_bronze, read-only):
  wdi_indicator_observations  one raw API record per row (raw_record JSON string)
  wdi_country_metadata        one row: the raw /v2/country response (296 entries)
  wdi_api_responses           one row per observation file: raw response incl. page header (lastupdated)
  wdi_indicator_metadata      one row per indicator: raw /v2/indicator response

Rules:
  * Key = (country_code, year, indicator_code), where country_code is the World Bank API `country.id`
    (2-character code, present for every row). countryiso3code is blank for 5 income-group aggregates
    and is therefore NOT a key; blanks become NULL in `country_iso3_code`.
  * Missing indicator values stay NULL (`is_value_missing` flags them); nothing is imputed or dropped.
  * Economies vs aggregates come from the country metadata (`region.id = "NA"` marks an aggregate).
    Both are kept (D-28: reference data is not filtered to the cohort).
  * Country attributes in the metadata are TODAY's values. Only stable identifiers and the
    economy/aggregate flag go on the observation table; current region / income / lending attributes
    stay on wdi_country with a `_current` suffix.
  * Lineage: Bronze table, source file, Bronze ingestion time, SHA-256 of the raw record, source vintage
    (`lastupdated` from the API page header), run id and transform version.
"""
from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T
from silver.common.pipeline import CATALOG

TRANSFORM_VERSION = "wdi_silver_v1.0.0"
BRONZE = f"{CATALOG}.wbg_ref_bronze"
EXPECTED_INDICATORS = ("NY.GDP.MKTP.KD.ZG", "FP.CPI.TOTL.ZG", "NY.GDP.PCAP.CD")

OBSERVATION_SCHEMA = T.StructType([
    T.StructField("indicator", T.StructType([T.StructField("id", T.StringType()), T.StructField("value", T.StringType())])),
    T.StructField("country", T.StructType([T.StructField("id", T.StringType()), T.StructField("value", T.StringType())])),
    T.StructField("countryiso3code", T.StringType()),
    T.StructField("date", T.StringType()),
    T.StructField("value", T.DoubleType()),
    T.StructField("unit", T.StringType()),
    T.StructField("obs_status", T.StringType()),
    T.StructField("decimal", T.IntegerType()),
])
_CODE_VALUE = T.StructType([T.StructField("id", T.StringType()), T.StructField("iso2code", T.StringType()),
                            T.StructField("value", T.StringType())])
COUNTRY_SCHEMA = T.ArrayType(T.StructType([
    T.StructField("id", T.StringType()), T.StructField("iso2Code", T.StringType()), T.StructField("name", T.StringType()),
    T.StructField("region", _CODE_VALUE), T.StructField("adminregion", _CODE_VALUE),
    T.StructField("incomeLevel", _CODE_VALUE), T.StructField("lendingType", _CODE_VALUE),
    T.StructField("capitalCity", T.StringType()), T.StructField("longitude", T.StringType()), T.StructField("latitude", T.StringType()),
]))


def _blank_to_null(col: F.Column) -> F.Column:
    return F.when(F.trim(col) == "", F.lit(None)).otherwise(col)


def parse_country_metadata(meta: DataFrame) -> DataFrame:
    """Explode the raw /v2/country response into one row per entity (economies and aggregates)."""
    entries = meta.select(
        F.col("source_file").alias("metadata_source_file"), F.col("ingested_at").alias("metadata_ingested_at"),
        F.get_json_object("raw_response", "$[0].total").cast("int").alias("metadata_api_total"),
        F.explode(F.from_json(F.get_json_object("raw_response", "$[1]"), COUNTRY_SCHEMA)).alias("c"))
    return entries.select(
        F.col("c.iso2Code").alias("country_code"),
        F.col("c.id").alias("country_wb_id"),
        F.trim("c.name").alias("country_name"),
        F.when(F.col("c.region.id") == "NA", F.lit("aggregate")).otherwise(F.lit("economy")).alias("entity_type"),
        _blank_to_null(F.col("c.region.id")).alias("region_code_current"),
        _blank_to_null(F.trim("c.region.value")).alias("region_name_current"),
        _blank_to_null(F.col("c.adminregion.id")).alias("admin_region_code_current"),
        _blank_to_null(F.trim("c.adminregion.value")).alias("admin_region_name_current"),
        _blank_to_null(F.col("c.incomeLevel.id")).alias("income_level_code_current"),
        _blank_to_null(F.trim("c.incomeLevel.value")).alias("income_level_name_current"),
        _blank_to_null(F.col("c.lendingType.id")).alias("lending_type_code_current"),
        _blank_to_null(F.trim("c.lendingType.value")).alias("lending_type_name_current"),
        _blank_to_null(F.col("c.capitalCity")).alias("capital_city"),
        _blank_to_null(F.col("c.longitude")).cast("double").alias("longitude"),
        _blank_to_null(F.col("c.latitude")).cast("double").alias("latitude"),
        F.sha2(F.to_json("c"), 256).alias("raw_entry_sha256"),
        "metadata_source_file", "metadata_ingested_at", "metadata_api_total",
    ).withColumn(
        # aggregates carry region "Aggregates" etc. in the raw metadata; null them so they cannot be read as a region
        "region_code_current", F.when(F.col("entity_type") == "aggregate", F.lit(None)).otherwise(F.col("region_code_current"))
    ).withColumn(
        "region_name_current", F.when(F.col("entity_type") == "aggregate", F.lit(None)).otherwise(F.col("region_name_current")))


def parse_vintages(api_responses: DataFrame) -> DataFrame:
    """Per indicator: API page-header fields of the response the observations came from."""
    return api_responses.select(
        "indicator_code",
        F.col("source_file").alias("_vintage_source_file"),
        F.to_date(F.get_json_object("raw_response", "$[0].lastupdated")).alias("source_lastupdated"),
        F.get_json_object("raw_response", "$[0].sourceid").alias("source_id"),
        F.get_json_object("raw_response", "$[0].total").cast("int").alias("source_api_total"),
    )


def parse_indicator_metadata(ind_meta: DataFrame) -> DataFrame:
    """Indicator name, source name and source organisation from each /v2/indicator response."""
    return ind_meta.select(
        "indicator_code",
        F.get_json_object("raw_response", "$[1][0].name").alias("indicator_name"),
        F.get_json_object("raw_response", "$[1][0].source.value").alias("source_name"),
        F.get_json_object("raw_response", "$[1][0].sourceOrganization").alias("source_organization"),
    )


def build_observations(obs: DataFrame, countries: DataFrame, vintages: DataFrame, indicators: DataFrame,
                       run_id: str, bronze_table: str = f"{BRONZE}.wdi_indicator_observations") -> DataFrame:
    """Typed, lineage-carrying Silver observations: one row per Bronze row (no rows dropped or added)."""
    p = obs.select(
        F.from_json("raw_record", OBSERVATION_SCHEMA).alias("r"),
        F.get_json_object("raw_record", "$.value").alias("_raw_value_text"),
        F.col("indicator_code").alias("_bronze_indicator_code"),
        F.col("source_file").alias("bronze_source_file"),
        F.col("ingested_at").alias("bronze_ingested_at"),
        F.sha2("raw_record", 256).alias("raw_record_sha256"),
    )
    typed = p.select(
        F.col("r.country.id").alias("country_code"),
        _blank_to_null(F.col("r.countryiso3code")).alias("country_iso3_code"),
        F.trim("r.country.value").alias("country_name"),
        F.col("r.indicator.id").alias("indicator_code"),
        F.col("r.indicator.value").alias("_record_indicator_name"),
        F.col("r.date").cast("int").alias("year"),
        F.col("r.value").alias("value"),
        F.col("r.value").isNull().alias("is_value_missing"),
        _blank_to_null(F.col("r.unit")).alias("unit"),
        _blank_to_null(F.col("r.obs_status")).alias("obs_status"),
        F.col("r.decimal").alias("decimal_places"),
        "_raw_value_text", "_bronze_indicator_code", "bronze_source_file", "bronze_ingested_at", "raw_record_sha256",
    )
    c = countries.select("country_code", "country_wb_id", "entity_type")
    out = (typed.join(F.broadcast(c), "country_code", "left")
           .join(F.broadcast(vintages), "indicator_code", "left")
           .join(F.broadcast(indicators), "indicator_code", "left"))
    return out.select(
        "country_code", "country_iso3_code", "country_wb_id", "country_name", "entity_type",
        "indicator_code", F.coalesce("indicator_name", "_record_indicator_name").alias("indicator_name"),
        "year", "value", "is_value_missing", "unit", "obs_status", "decimal_places",
        "source_id", "source_name", "source_organization", "source_lastupdated",
        F.lit(bronze_table).alias("bronze_table"), "bronze_source_file", "bronze_ingested_at", "raw_record_sha256",
        F.lit(run_id).alias("silver_run_id"), F.current_timestamp().alias("silver_processed_at"),
        F.lit(TRANSFORM_VERSION).alias("transform_version"),
        # validation-only helpers, dropped before any write
        "_raw_value_text", "_bronze_indicator_code", "_vintage_source_file", "source_api_total",
    )


def build_countries(countries: DataFrame, observations: DataFrame, run_id: str) -> DataFrame:
    """Silver country/aggregate reference: one row per metadata entity, flagged if it has WDI observations."""
    has_obs = observations.select("country_code").distinct().withColumn("has_wdi_observations", F.lit(True))
    return (countries.join(has_obs, "country_code", "left")
            .withColumn("has_wdi_observations", F.coalesce("has_wdi_observations", F.lit(False)))
            .withColumn("bronze_table", F.lit(f"{BRONZE}.wdi_country_metadata"))
            .withColumn("silver_run_id", F.lit(run_id))
            .withColumn("silver_processed_at", F.current_timestamp())
            .withColumn("transform_version", F.lit(TRANSFORM_VERSION)))


VALIDATION_ONLY_COLUMNS = ["_raw_value_text", "_bronze_indicator_code", "_vintage_source_file", "source_api_total"]
