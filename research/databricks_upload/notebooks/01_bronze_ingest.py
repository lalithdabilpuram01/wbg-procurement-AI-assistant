# Databricks notebook source
# MAGIC %md
# MAGIC # Bronze ingestion: 2,000-project PoC cohort (D-26)
# MAGIC
# MAGIC **Status: prepared, not run.** Review [BRONZE_INGESTION_GUIDE.md](../BRONZE_INGESTION_GUIDE.md) first.
# MAGIC
# MAGIC Loads the cohort-bounded raw files in `/Volumes/workspace/default/wbg_procurement_test/bronze_inputs/` into
# MAGIC Bronze Delta tables with minimal processing:
# MAGIC - CSV: every source column as STRING, original column names (Delta column mapping), no trimming or casting.
# MAGIC - JSON Lines: the original line as `raw_record` STRING, plus a `record` VARIANT parsed from it.
# MAGIC - Ingestion metadata columns (`_source_*`, `_ingested_at`, ...). No cleaning, deduplication or joins.
# MAGIC
# MAGIC Set `TABLES` to the tables you want to load (the recommended first upload is the default).

# COMMAND ----------

import json

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

VOLUME = "/Volumes/workspace/default/wbg_procurement_test"
BASE = f"{VOLUME}/bronze_inputs"
CATALOG, SCHEMA = "workspace", "wbg_bronze"
COHORT_ID = "D-26/seed-20261021"

# Recommended first upload; extend with the remaining tables after validation passes.
TABLES = ["bronze_projects_v3", "bronze_contract_awards_ds00005", "bronze_ieg_ratings_ds00053"]

with open(f"{BASE}/_manifest/bronze_inputs_manifest.json") as f:
    MANIFEST = json.load(f)
FILES = {e["bronze_table"]: e for e in MANIFEST["files"]}

# Delta needs column mapping to keep source column names that contain spaces or parentheses,
# e.g. "Supplier Contract Amount (USD)".
TABLE_PROPERTIES = {
    "delta.columnMapping.mode": "name",
    "delta.minReaderVersion": "2",
    "delta.minWriterVersion": "5",
}

# COMMAND ----------


def add_ingest_metadata(df: DataFrame, entry: dict) -> DataFrame:
    return (df
            .withColumn("_source_file_path", F.col("_metadata.file_path"))
            .withColumn("_source_file_size", F.col("_metadata.file_size"))
            .withColumn("_source_file_modified_at", F.col("_metadata.file_modification_time"))
            .withColumn("_source_file_sha256", F.lit(entry["output_sha256"]))
            .withColumn("_upstream_file", F.lit(entry["source_file"]))
            .withColumn("_upstream_sha256", F.lit(entry["source_sha256"]))
            .withColumn("_extracted_at_utc", F.lit(entry["extracted_at_utc"]))
            .withColumn("_cohort_id", F.lit(COHORT_ID))
            .withColumn("_ingested_at", F.current_timestamp()))


def read_csv(entry: dict) -> tuple[DataFrame, bool]:
    df = (spark.read.format("csv")
          .option("header", True)
          .option("inferSchema", False)        # every column STRING, values as published
          .option("multiLine", True)           # DS00005/DS01004/DS00979 have line breaks inside quoted fields
          .option("quote", '"')
          .option("escape", '"')               # RFC 4180 doubled quotes (Spark's default escape is backslash)
          .option("encoding", "UTF-8")
          .option("nullValue", "\u0000")       # do not turn empty fields into NULL; verify after load
          .option("mode", "PERMISSIVE")
          .option("rescuedDataColumn", "_rescued_data")
          .load(entry["volume_path"]))
    source_cols = [c for c in df.columns if c != "_rescued_data"]
    bom_removed = False
    # The source header starts with a UTF-8 BOM. If Spark keeps it in the first column name, remove only
    # the BOM character and record it in the table property source.header_bom_removed (values are untouched).
    if source_cols and source_cols[0].startswith("\ufeff"):
        df = df.withColumnRenamed(source_cols[0], source_cols[0].lstrip("\ufeff"))
        source_cols[0] = source_cols[0].lstrip("\ufeff")
        bom_removed = True
    df = df.withColumn("_row_sha256", F.sha2(F.to_json(F.struct(*[F.col(f"`{c}`") for c in source_cols])), 256))
    return add_ingest_metadata(df, entry), bom_removed


def read_jsonl(entry: dict) -> DataFrame:
    df = (spark.read.format("text").load(entry["volume_path"])   # one JSON record per line, unchanged
          .withColumnRenamed("value", "raw_record")
          .withColumn("record", F.expr("parse_json(raw_record)"))
          .withColumn("_project_id", F.get_json_object("raw_record", f"$.{entry['selection_key']}"))
          .withColumn("_row_sha256", F.sha2("raw_record", 256)))
    return add_ingest_metadata(df, entry)


def create_table(df: DataFrame, name: str, entry: dict, extra: dict | None = None) -> None:
    writer = df.writeTo(f"{CATALOG}.{SCHEMA}.{name}").using("delta")
    props = {**TABLE_PROPERTIES, "source.file": entry["source_file"], "source.sha256": entry["source_sha256"],
             "source.coverage": entry["coverage"], "source.selection_rule": entry["selection_rule"],
             "cohort.id": COHORT_ID, **(extra or {})}
    for k, v in props.items():
        writer = writer.tableProperty(k, str(v))
    writer.create()   # fails if the table exists: Bronze is never silently overwritten

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")

# Control tables: the approved cohort and the extraction manifest (not World Bank source data).
ctl = {"output_sha256": "", "source_file": "cohort/poc_cohort_2000_manifest.csv", "source_sha256":
       MANIFEST["cohort_manifest_sha256"], "extracted_at_utc": MANIFEST["run_finished_utc"], "coverage": "control",
       "selection_rule": "n/a", "volume_path": f"{BASE}/_control/poc_cohort_2000_manifest.csv"}
cohort_df, _ = read_csv(ctl)
create_table(cohort_df, "ctl_poc_cohort", ctl)
manifest_ctl = {**ctl, "source_file": "databricks_upload/bronze_inputs/_manifest/bronze_inputs_manifest.csv",
                "volume_path": f"{BASE}/_manifest/bronze_inputs_manifest.csv"}
manifest_df, _ = read_csv(manifest_ctl)
create_table(manifest_df, "ctl_bronze_input_manifest", manifest_ctl)

for name in TABLES:
    entry = FILES[name]
    if entry["format"] == "csv":
        df, bom_removed = read_csv(entry)
        create_table(df, name, entry, {"source.header_bom_removed": str(bom_removed).lower()})
    else:
        create_table(read_jsonl(entry), name, entry)

# COMMAND ----------

# MAGIC %md ## Post-load validation (expected values come from the extraction manifest)

# COMMAND ----------

cohort_ids = spark.table(f"{CATALOG}.{SCHEMA}.ctl_poc_cohort").select("project_id").distinct()
assert cohort_ids.count() == 2000

results = []
for name in TABLES:
    entry = FILES[name]
    t = spark.table(f"{CATALOG}.{SCHEMA}.{name}")
    key = "_project_id" if entry["format"] == "jsonl" else entry["selection_key"]
    ids = t.select(F.col(f"`{key}`").alias("project_id")).distinct()
    row = {"table": name, "rows": t.count(), "expected_rows": entry["records_selected"],
           "projects": ids.count(), "expected_projects": entry["cohort_projects_with_records"],
           "ids_outside_cohort": ids.join(cohort_ids, "project_id", "left_anti").count(),
           "file_sha256_expected": entry["output_sha256"]}
    if "_rescued_data" in t.columns:
        row["rescued_rows"] = t.filter(F.col("_rescued_data").isNotNull()).count()
    if entry["format"] == "csv":
        src_cols = [c for c in t.columns if not c.startswith("_")]
        row["null_cells"] = t.select(sum(F.col(f"`{c}`").isNull().cast("int") for c in src_cols).alias("n")) \
            .agg(F.sum("n")).first()[0]
    results.append(row)
display(spark.createDataFrame(results))

for r in results:
    assert r["rows"] == r["expected_rows"], r
    assert r["projects"] == r["expected_projects"], r
    assert r["ids_outside_cohort"] == 0, r
    assert r.get("rescued_rows", 0) == 0, r
if "bronze_projects_v3" in TABLES:
    p = spark.table(f"{CATALOG}.{SCHEMA}.bronze_projects_v3").select(F.col("_project_id").alias("project_id"))
    assert p.count() == p.distinct().count() == 2000
    assert p.join(cohort_ids, "project_id", "left_anti").count() == 0
    assert cohort_ids.join(p, "project_id", "left_anti").count() == 0
