"""Source-file provenance helpers for JSONL(.gz) reference Bronze tables.

* load_manifest_vintages: per staged file, the vintage / retrieval time / SHA-256 / record count / URL recorded
  in databricks_upload/ref_bronze_inputs/_manifest/ref_bronze_inputs_manifest.json.
* verify_source_files: re-reads the Volume files the Bronze rows came from, checks each file's SHA-256 against
  the manifest, and checks that the file's lines equal the Bronze raw_record values exactly (multiset of hashes).
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from silver.common.dq import DQReport

MANIFEST = Path(__file__).resolve().parents[2] / "databricks_upload" / "ref_bronze_inputs" / "_manifest" / "ref_bronze_inputs_manifest.json"


def load_manifest_vintages(bronze_folder: str, manifest_path: Path = MANIFEST) -> list[dict]:
    files = json.loads(manifest_path.read_text())["files"]
    return [{"file_name": Path(f["staged_file"]).name, "source_lastupdated": f["source_lastupdated"],
             "source_retrieved_utc": f["retrieved_utc"], "source_file_sha256": f["sha256"],
             "manifest_records": int(f["records"]), "source_url": f["source_url"]}
            for f in files if f["bronze_table"] == bronze_folder]


def vintages_df(spark: SparkSession, vintages: list[dict]) -> DataFrame:
    return spark.createDataFrame(vintages, "file_name string, source_lastupdated string, source_retrieved_utc string, "
                                           "source_file_sha256 string, manifest_records int, source_url string")


FIELD_SEP = "\x1f"


def row_hash_expr(columns: list[str]):
    """SHA-256 over the published field values (NULL treated as the empty CSV field), in column order."""
    return F.sha2(F.concat_ws(FIELD_SEP, *[F.coalesce(F.col(f"`{c}`"), F.lit("")) for c in columns]), 256)


def verify_csv_source_file(spark: SparkSession, bronze: DataFrame, columns: list[str], expected_sha256: str, label: str,
                           report: DQReport) -> None:
    """Re-read the Volume CSV that Bronze came from; check its SHA-256 and that every parsed record equals a Bronze row."""
    import io

    import pandas as pd

    paths = sorted(r[0] for r in bronze.select("source_file").distinct().collect())
    report.equals(f"{label}:single_source_file", len(paths), 1)
    content = bytes(spark.read.format("binaryFile").load(paths).select("content").collect()[0][0])
    report.equals(f"{label}:source_file_sha256_matches_manifest", hashlib.sha256(content).hexdigest(), expected_sha256)
    src = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    report.equals(f"{label}:source_header_equals_bronze_columns", list(src.columns), columns)
    file_hashes = sorted(hashlib.sha256(FIELD_SEP.join(row).encode("utf-8")).hexdigest() for row in src[columns].itertuples(index=False, name=None))
    bronze_hashes = sorted(r[0] for r in bronze.select(row_hash_expr(columns)).collect())
    report.equals(f"{label}:bronze_rows_equal_source_file_records (exact, NULL = empty field)",
                  {"file_records": len(file_hashes), "bronze_rows": len(bronze_hashes), "identical": file_hashes == bronze_hashes},
                  {"file_records": len(bronze_hashes), "bronze_rows": len(bronze_hashes), "identical": True})


def verify_source_files(spark: SparkSession, bronze: DataFrame, vintages: list[dict], report: DQReport) -> None:
    paths = sorted(r[0] for r in bronze.select("source_file").distinct().collect())
    files = {r.path.split("/")[-1]: bytes(r.content) for r in spark.read.format("binaryFile").load(paths).select("path", "content").collect()}
    by_name = {v["file_name"]: v for v in vintages}
    report.equals("source_files_in_manifest", sorted(set(files) - set(by_name)), [])
    report.equals("source_file_count", len(files), len(vintages))
    report.equals("source_file_sha256_matches_manifest",
                  {n: hashlib.sha256(b).hexdigest() == by_name.get(n, {}).get("source_file_sha256") for n, b in files.items()},
                  {n: True for n in files})
    bronze_hashes: dict[str, list[str]] = {}
    for r in bronze.select(F.element_at(F.split("source_file", "/"), -1).alias("n"), F.sha2("raw_record", 256).alias("h")).collect():
        bronze_hashes.setdefault(r.n, []).append(r.h)
    mismatch: dict[str, dict] = {}
    for n, b in files.items():
        text = (gzip.decompress(b) if n.endswith(".gz") else b).decode("utf-8")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if sorted(hashlib.sha256(ln.encode("utf-8")).hexdigest() for ln in lines) != sorted(bronze_hashes.get(n, [])):
            mismatch[n] = {"file_lines": len(lines), "bronze_rows": len(bronze_hashes.get(n, []))}
    report.equals("bronze_records_equal_source_file_lines (exact, per file)", mismatch, {})


def verify_jsonl_source_file(spark: SparkSession, bronze: DataFrame, expected_sha256: str, label: str, report: DQReport) -> None:
    """Re-read the single Volume JSONL that a raw_record Bronze table came from; check its SHA-256 and that its lines equal
    the Bronze raw_record values exactly (multiset of hashes)."""
    paths = sorted(r[0] for r in bronze.select("source_file").distinct().collect())
    report.equals(f"{label}:single_source_file", len(paths), 1)
    content = bytes(spark.read.format("binaryFile").load(paths).select("content").collect()[0][0])
    report.equals(f"{label}:source_file_sha256_matches_manifest", hashlib.sha256(content).hexdigest(), expected_sha256)
    # split on LF only: str.splitlines() would also split on U+2028/U+0085 etc. inside JSON strings
    lines = [ln for ln in content.decode("utf-8").split("\n") if ln.strip()]
    file_hashes = sorted(hashlib.sha256(ln.encode("utf-8")).hexdigest() for ln in lines)
    bronze_hashes = sorted(r[0] for r in bronze.select(F.sha2("raw_record", 256)).collect())
    report.equals(f"{label}:bronze_records_equal_source_file_lines (exact)",
                  {"file_lines": len(file_hashes), "bronze_rows": len(bronze_hashes), "identical": file_hashes == bronze_hashes},
                  {"file_lines": len(bronze_hashes), "bronze_rows": len(bronze_hashes), "identical": True})
