"""Historical income classifications: Bronze workbook -> Silver DataFrame.

The workbook (~116 KB) is read from the Bronze binary column, parsed on the client with openpyxl
(silver/income_class/parse.py), and turned into a typed Spark DataFrame with lineage columns.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import types as T

from silver.common.pipeline import CATALOG
from silver.income_class.parse import ParsedWorkbook, parse_oghist

TRANSFORM_VERSION = "income_class_silver_v1.0.0"
BRONZE_TABLE = f"{CATALOG}.wbg_ref_bronze.income_classification_history"
EXPECTED_SHA256 = "a4024eb8052150155034559daf053aabbc16336d8290efeb5629e6eeb6515a60"  # OGHIST_2026_10_08.xlsx (R2)

SCHEMA = T.StructType([
    T.StructField("iso3_code", T.StringType(), False),
    T.StructField("country_name", T.StringType(), False),
    T.StructField("fiscal_year", T.IntegerType(), False),
    T.StructField("fiscal_year_label", T.StringType(), False),
    T.StructField("fiscal_year_start_date", T.DateType(), False),
    T.StructField("fiscal_year_end_date", T.DateType(), False),
    T.StructField("gni_data_year", T.IntegerType(), False),
    T.StructField("income_group_code_raw", T.StringType(), True),
    T.StructField("income_group_code", T.StringType(), True),
    T.StructField("income_group_name", T.StringType(), True),
    T.StructField("classification_status", T.StringType(), False),
    T.StructField("is_classified", T.BooleanType(), False),
    T.StructField("footnote_marker", T.StringType(), True),
    T.StructField("footnote_text", T.StringType(), True),
    T.StructField("source_sheet", T.StringType(), False),
    T.StructField("source_row_number", T.IntegerType(), False),
    T.StructField("source_column_letter", T.StringType(), False),
    T.StructField("source_cell", T.StringType(), False),
    T.StructField("workbook_file_name", T.StringType(), False),
    T.StructField("bronze_table", T.StringType(), False),
    T.StructField("bronze_source_file", T.StringType(), False),
    T.StructField("bronze_file_sha256", T.StringType(), False),
    T.StructField("bronze_ingested_at", T.TimestampType(), False),
    T.StructField("silver_run_id", T.StringType(), False),
    T.StructField("silver_processed_at", T.TimestampType(), False),
    T.StructField("transform_version", T.StringType(), False),
])


def read_bronze(spark: SparkSession) -> dict:
    rows = spark.table(BRONZE_TABLE).collect()
    if len(rows) != 1:
        raise ValueError(f"{BRONZE_TABLE}: expected 1 row, found {len(rows)}")
    r = rows[0]
    content = bytes(r.raw_file)
    return {"content": content, "source_file": r.source_file, "stored_sha256": r.sha256, "ingested_at": r.ingested_at,
            "computed_sha256": hashlib.sha256(content).hexdigest(), "rows": len(rows)}


def build(spark: SparkSession, bronze: dict, run_id: str) -> tuple[DataFrame, ParsedWorkbook]:
    parsed = parse_oghist(bronze["content"])
    processed = datetime.now(timezone.utc).replace(tzinfo=None)
    name = bronze["source_file"].rsplit("/", 1)[-1]
    rows = [{**c, "workbook_file_name": name, "bronze_table": BRONZE_TABLE, "bronze_source_file": bronze["source_file"],
             "bronze_file_sha256": bronze["computed_sha256"], "bronze_ingested_at": bronze["ingested_at"],
             "silver_run_id": run_id, "silver_processed_at": processed, "transform_version": TRANSFORM_VERSION}
            for c in parsed.cells]
    df = spark.createDataFrame([tuple(r[f.name] for f in SCHEMA.fields) for r in rows], SCHEMA)
    return df, parsed
