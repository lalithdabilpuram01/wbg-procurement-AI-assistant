"""Load the staged files into the 17 Bronze tables (replaced on every run; Delta keeps the previous versions).

The columns are the ones the Silver jobs read:
  procurement CSV      every published column as STRING (original names, BOM removed; empty field = NULL)
                       + source_file, cohort_id, ingested_at
  procurement JSONL    raw_record (the exact line) + source_file, cohort_id, ingested_at
  WGI / CPIA series    raw_record, indicator_code (from the file name), source_file, ingested_at
  WDI observations     raw_record (one API record), indicator_code, source_file, ingested_at
  WDI api responses    indicator_code, raw_response (the whole file), source_file, cohort_id, ingested_at
  WDI metadata         [indicator_code,] raw_response, source_file, ingested_at
  income groups        raw_file (exact XLSX bytes), source_file, sha256, ingested_at
"""
from __future__ import annotations

import json
from functools import reduce
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from etl.datasets import COHORT_ID, DATASETS, REFERENCE_COHORT_ID, Dataset, series_code
from silver.common.dq import DQReport


def _meta(df: DataFrame, cohort_id: str | None) -> DataFrame:
    # The xlsx reader already sets source_file; every other reader takes it from the file metadata.
    """Add source_file (without the dbfs: prefix), cohort_id when one is given, and ingested_at."""
    df = df.withColumn("source_file", F.col("_metadata.file_path")) if "source_file" not in df.columns else df
    df = df.withColumn("source_file", F.regexp_replace("source_file", "^dbfs:", ""))
    if cohort_id is not None:
        df = df.withColumn("cohort_id", F.lit(cohort_id))
    return df.withColumn("ingested_at", F.current_timestamp())


def read_csv(spark: SparkSession, path: Path) -> DataFrame:
    """Every column as published (STRING); RFC 4180 quoting with embedded line breaks; empty fields become NULL."""
    df = (spark.read.format("csv").option("header", True).option("inferSchema", False).option("multiLine", True)
          .option("quote", '"').option("escape", '"').option("encoding", "UTF-8").option("mode", "FAILFAST").load(str(path)))
    first = df.columns[0]
    df = df.withColumnRenamed(first, first.lstrip("﻿"))
    cols = [F.when(F.col(f"`{c}`") == "", F.lit(None)).otherwise(F.col(f"`{c}`")).alias(c) for c in df.columns]
    return _meta(df.select(*cols, "_metadata"), COHORT_ID).drop("_metadata")


def read_lines(spark: SparkSession, paths: list[Path], column: str = "raw_record") -> DataFrame:
    """One row per non-blank line, unchanged (gzip files are decompressed by the reader)."""
    df = spark.read.text([str(p) for p in paths]).select(F.col("value").alias(column), "_metadata").filter(F.trim(column) != "")
    return df


def read_whole(spark: SparkSession, paths: list[Path]) -> DataFrame:
    """One row per file: the exact text in raw_response."""
    return spark.read.option("wholetext", True).text([str(p) for p in paths]).select(F.col("value").alias("raw_response"), "_metadata")


def with_code(df: DataFrame, folder: str) -> DataFrame:
    """indicator_code from the file name, as the Phase 1 downloads were named."""
    names = [r[0] for r in df.select(F.element_at(F.split("_metadata.file_path", "/"), -1)).distinct().collect()]
    mapping = F.create_map(*[x for n in names for x in (F.lit(n), F.lit(series_code(folder, n)))])
    return df.withColumn("indicator_code", mapping[F.element_at(F.split("_metadata.file_path", "/"), -1)])


def wdi_observations(spark: SparkSession, paths: list[Path]) -> DataFrame:
    """One row per API record of each /v2/country/all/indicator response (the [header, records] array)."""
    rows = [(json.dumps(rec, ensure_ascii=False, separators=(",", ":")), series_code("wdi_indicator_observations", p.name), str(p))
            for p in paths for rec in json.loads(p.read_bytes())[1]]
    return spark.createDataFrame(rows, "raw_record string, indicator_code string, source_file string")


def build(spark: SparkSession, ds: Dataset, paths: list[Path]) -> dict[str, DataFrame]:
    """Bronze DataFrames for one dataset folder: {table: frame}."""
    if ds.kind == "csv":
        return {ds.tables[0]: read_csv(spark, paths[0])}
    if ds.kind == "jsonl":
        return {ds.tables[0]: _meta(read_lines(spark, paths), COHORT_ID).drop("_metadata")}
    if ds.kind == "series":
        df = with_code(read_lines(spark, paths), ds.folder)
        return {ds.tables[0]: _meta(df.select("raw_record", "indicator_code", "_metadata"), None).drop("_metadata")}
    if ds.kind == "wdi_observations":
        responses = with_code(read_whole(spark, paths), ds.folder).select("indicator_code", "raw_response", "_metadata")
        return {ds.tables[0]: _meta(wdi_observations(spark, paths), None),
                ds.tables[1]: _meta(responses, REFERENCE_COHORT_ID).drop("_metadata")}
    if ds.kind == "wdi_indicator":
        df = with_code(read_whole(spark, paths), ds.folder).select("indicator_code", "raw_response", "_metadata")
        return {ds.tables[0]: _meta(df, None).drop("_metadata")}
    if ds.kind == "json_response":
        return {ds.tables[0]: _meta(read_whole(spark, paths), None).drop("_metadata")}
    if ds.kind == "xlsx":
        df = (spark.read.format("binaryFile").load([str(p) for p in paths])
              .select(F.col("content").alias("raw_file"), F.col("path").alias("source_file"), F.sha2("content", 256).alias("sha256")))
        return {ds.tables[0]: _meta(df, None)}
    raise ValueError(f"unknown dataset kind {ds.kind}")


def load(spark: SparkSession, catalog: str, staged: dict[str, list[Path]], records: dict[str, int], run_id: str) -> DQReport:
    """Replace every Bronze table from the staged files and check rows against the staging manifests."""
    rep = DQReport(f"{catalog}.wbg_bronze, {catalog}.wbg_ref_bronze")
    for ds in DATASETS:
        for table, df in build(spark, ds, staged[ds.folder]).items():
            target = f"{catalog}.{table}"
            (df.writeTo(target).using("delta").tableProperty("etl.run_id", run_id)
               .tableProperty("etl.source_files", ",".join(p.name for p in staged[ds.folder])).createOrReplace())
            rows = spark.table(target).count()
            if table in records:
                rep.equals(f"{table}:rows_equal_staged_records", rows, records[table])
            rep.add(f"{table}:rows", rows, "> 0", rows > 0)
    cohort_tables = [f"{catalog}.{t}" for d in DATASETS if d.cohort_bounded for t in d.tables]
    ids = reduce(set.union, ({r[0] for r in spark.table(t).select("cohort_id").distinct().collect()} for t in cohort_tables))
    rep.equals("cohort_id_values", sorted(ids), [COHORT_ID])
    return rep
