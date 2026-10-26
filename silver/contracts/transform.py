"""Contract awards: Bronze -> Silver (three tables, no cross-source merging).

Sources (CSV ingested with original column names; empty CSV fields arrive as NULL):
  workspace.wbg_bronze.bronze_contract_awards_ds00005         DS00005, FY2020+ (as of 10/20/2026), 186,907 rows
  workspace.wbg_bronze.bronze_contract_awards_legacy_ds01004  DS01004, prior-review contracts FY2006-2016 (as of 03/11/2022), 6,448 rows

Grains established from the data (see the dry-run report):
  * DS00005 row = contract x supplier line. WB Contract Number is a numeric, never-blank contract ID; 5,843 contracts
    have >1 line, always one line per supplier, and every contract-level field is identical across a contract's lines.
    The amount on a line is that supplier's share: for 36/42 multi-supplier contracts with a single matching USD award
    notice, the SUM of the lines equals the notice's signed price (0 cases where each line equals the full price).
  * DS01004 row = contract (Contract Number unique); the supplier field is free text and may name several JV members;
    no Supplier ID. Different source, period (2005-12 to 2016-06) and coverage (prior review only), and 0 shared
    contract numbers with DS00005, so it stays a separate table.

Tables:
  contract_award_lines   (DS00005)  key line_key = wb_contract_number + supplier_id (or a hash of the name when the
                                    supplier ID is blank; 1 row)
  contract_awards        (DS00005)  key wb_contract_number; contract_amount_usd = SUM of its supplier lines
  contract_awards_legacy (DS01004)  key contract_number
Never join lines to contracts and sum both: amounts live at exactly one grain per table.
"""
from __future__ import annotations

import json
from pathlib import Path

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from silver.common.sourcefiles import row_hash_expr

TRANSFORM_VERSION = "contracts_silver_v1.0.0"
BRONZE_DS5 = "workspace.wbg_bronze.bronze_contract_awards_ds00005"
BRONZE_DS1 = "workspace.wbg_bronze.bronze_contract_awards_legacy_ds01004"
REPO = Path(__file__).resolve().parents[2]
BRONZE_INPUTS_MANIFEST = REPO / "databricks_upload" / "bronze_inputs" / "_manifest" / "bronze_inputs_manifest.json"
PHASE1_CACHE_MANIFEST = REPO / "discovery" / "data_cache" / "MANIFEST.jsonl"
META = ("source_file", "cohort_id", "ingested_at")
DEC = "decimal(22,2)"
HEX_ID = r"^[0-9a-f]{16}$"


def source_columns(df: DataFrame) -> list[str]:
    return [c for c in df.columns if c not in META]


def load_provenance(bronze_table_short: str) -> dict:
    m = json.loads(BRONZE_INPUTS_MANIFEST.read_text())
    e = next(f for f in m["files"] if f["bronze_table"] == bronze_table_short)
    src_name = Path(e["source_file"]).name
    pull = next(json.loads(ln) for ln in PHASE1_CACHE_MANIFEST.read_text().splitlines() if src_name in ln)
    return {"bronze_input_file_sha256": e["output_sha256"], "records": int(e["records_selected"]), "source_population_sha256": e["source_sha256"],
            "source_retrieved_at": pull["extracted_at_utc"], "source_url_note": pull["source"]}


def _c(name: str) -> F.Column:
    return F.col(f"`{name}`")


def _date(name: str) -> F.Column:
    return F.to_date(_c(name), "MM/dd/yyyy")


def _ws(c: F.Column) -> F.Column:
    """Whitespace-normalised copy (published value kept separately)."""
    return F.trim(F.regexp_replace(c, r"\s+", " "))


def _lineage(prov: dict, bronze_table: str, dataset: str, run_id: str, cols: list[str]) -> list[F.Column]:
    return [F.lit(dataset).alias("source_dataset"), row_hash_expr(cols).alias("source_row_sha256"),
            F.lit(bronze_table).alias("bronze_table"), F.col("source_file").alias("bronze_source_file"),
            F.col("cohort_id").alias("bronze_cohort_id"), F.col("ingested_at").alias("bronze_ingested_at"),
            F.lit(prov["bronze_input_file_sha256"]).alias("bronze_input_file_sha256"),
            F.lit(prov["source_population_sha256"]).alias("source_population_sha256"),
            F.to_timestamp(F.lit(prov["source_retrieved_at"])).alias("source_retrieved_at"),
            F.lit(run_id).alias("silver_run_id"), F.current_timestamp().alias("silver_processed_at"), F.lit(TRANSFORM_VERSION).alias("transform_version")]


def build_lines(ds5: DataFrame, prov: dict, run_id: str) -> DataFrame:
    cols = source_columns(ds5)
    sid = _c("Supplier ID")
    sid_type = (F.when(sid.isNull(), F.lit("missing")).when(sid.rlike(HEX_ID), F.lit("anonymized_individual_consultant"))
                .otherwise(F.lit("numeric")))
    w = Window.partitionBy(_c("WB Contract Number"))
    return ds5.select(
        F.concat_ws(":", _c("WB Contract Number"), F.coalesce(sid, F.concat(F.lit("NO_SUPPLIER_ID-"), F.substring(F.sha2(F.coalesce(_c("Supplier"), F.lit("")), 256), 1, 12)))).alias("line_key"),
        _c("WB Contract Number").alias("wb_contract_number"),
        sid.alias("supplier_id"), sid_type.alias("supplier_id_type"),
        _c("Supplier").alias("supplier_name"), _c("Supplier Country / Economy").alias("supplier_country_name"),
        _c("Supplier Country / Economy Code").alias("supplier_country_code"),
        _c("Supplier Contract Amount (USD)").cast(DEC).alias("supplier_amount_usd"),
        _c("Supplier Contract Amount (USD)").isNull().alias("is_amount_missing"),
        F.count(F.lit(1)).over(w).alias("contract_supplier_line_count"),
        _c("Project ID").alias("project_id"), _c("Project Name").alias("project_name_source"),
        _c("Region").alias("region_name_source"), _c("Borrower Country / Economy").alias("borrower_country_name"),
        _c("Borrower Country / Economy Code").alias("borrower_country_code"),
        _c("Project Global Practice").alias("global_practices_published"),
        F.when(_c("Project Global Practice").isNotNull(), F.transform(F.split(_c("Project Global Practice"), ";"), lambda x: F.trim(x))).alias("global_practices"),
        _c("Procurement Category").alias("procurement_category"), _c("Procurement Method").alias("procurement_method"),
        _c("Contract Description").alias("contract_description"), _c("Borrower Contract Reference Number").alias("borrower_contract_reference"),
        _date("Contract Signing Date").alias("contract_signing_date"), _c("Fiscal Year").cast("int").alias("fiscal_year"),
        _c("Contract signed - Calendar year").cast("int").alias("contract_signed_calendar_year"), _c("Review type").alias("review_type"),
        _date("As of Date").alias("source_as_of_date"),
        *_lineage(prov, BRONZE_DS5, "DS00005", run_id, cols),
        _c("Contract Signing Date").alias("_raw_signing_date"), _c("Supplier Contract Amount (USD)").alias("_raw_amount"),
    )


CONTRACT_LEVEL = ["project_id", "project_name_source", "region_name_source", "borrower_country_name", "borrower_country_code",
                  "global_practices_published", "global_practices", "procurement_category", "procurement_method", "contract_description",
                  "borrower_contract_reference", "contract_signing_date", "fiscal_year", "contract_signed_calendar_year", "review_type",
                  "source_as_of_date", "source_dataset", "bronze_table", "bronze_source_file", "bronze_cohort_id", "bronze_ingested_at",
                  "bronze_input_file_sha256", "source_population_sha256", "source_retrieved_at"]


def build_contracts(lines: DataFrame, run_id: str) -> DataFrame:
    agg = lines.groupBy("wb_contract_number").agg(
        *[F.first(c, ignorenulls=False).alias(c) for c in CONTRACT_LEVEL],
        *[F.countDistinct(F.coalesce(F.col(c).cast("string"), F.lit("<NULL>"))).alias(f"_distinct_{c}") for c in CONTRACT_LEVEL if c != "global_practices"],
        F.count("*").alias("supplier_line_count"),
        F.array_sort(F.collect_list("supplier_id")).alias("supplier_ids"),
        F.array_sort(F.collect_list("supplier_name")).alias("supplier_names"),
        F.array_sort(F.collect_set("supplier_id_type")).alias("supplier_id_types"),
        F.sum("supplier_amount_usd").alias("contract_amount_usd"),
        F.sum(F.col("is_amount_missing").cast("int")).alias("supplier_lines_missing_amount"),
        F.array_sort(F.collect_list("line_key")).alias("line_keys"),
    )
    ref_w = Window.partitionBy("project_id", "borrower_contract_reference")
    return agg.select(
        "wb_contract_number", *[c for c in CONTRACT_LEVEL if c not in ("source_dataset", "bronze_table", "bronze_source_file", "bronze_cohort_id",
                                                                         "bronze_ingested_at", "bronze_input_file_sha256", "source_population_sha256",
                                                                         "source_retrieved_at")],
        "supplier_line_count", (F.col("supplier_line_count") > 1).alias("is_multi_supplier"), "supplier_ids", "supplier_names", "supplier_id_types",
        "contract_amount_usd", F.lit("sum_of_supplier_lines").alias("contract_amount_basis"), "supplier_lines_missing_amount",
        F.when(F.col("borrower_contract_reference").isNotNull(), F.count(F.lit(1)).over(ref_w)).alias("contracts_sharing_reference_in_project"),
        "line_keys", "source_dataset", "bronze_table", "bronze_source_file", "bronze_cohort_id", "bronze_ingested_at",
        "bronze_input_file_sha256", "source_population_sha256", "source_retrieved_at",
        F.lit(run_id).alias("silver_run_id"), F.current_timestamp().alias("silver_processed_at"), F.lit(TRANSFORM_VERSION).alias("transform_version"),
        *[c for c in agg.columns if c.startswith("_distinct_")],
    )


def build_legacy(ds1: DataFrame, prov: dict, run_id: str) -> DataFrame:
    cols = source_columns(ds1)
    return ds1.select(
        _c("Contract Number").alias("contract_number"), _c("Project ID").alias("project_id"),
        _c("Project Name").alias("project_name_source"), _ws(_c("Project Name")).alias("project_name_source_normalized"),
        _c("Region").alias("region_name_source"), _c("Borrower Country / Economy").alias("borrower_country_name"),
        _c("Project Global Practice").alias("global_practice"), _c("Procurement Category").alias("procurement_category"),
        _c("Procurement Method").alias("procurement_method"),
        _c("Contract Description").alias("contract_description"), _ws(_c("Contract Description")).alias("contract_description_normalized"),
        _c("Borrower Contract Reference Number").alias("borrower_contract_reference"),
        _date("Contract Signing Date").alias("contract_signing_date"), _c("Fiscal Year").cast("int").alias("fiscal_year"),
        _c("Contract signed - Calendar year").cast("int").alias("contract_signed_calendar_year"),
        _c("Supplier").alias("supplier_text"), _ws(_c("Supplier")).alias("supplier_text_normalized"),
        _c("Supplier Country / Economy").alias("supplier_country_name"),
        _c("Supplier Contract Amount (US$)").cast(DEC).alias("contract_amount_usd"),
        F.lit("prior_review_only").alias("coverage_scope"),
        _date("As of Date").alias("source_as_of_date"),
        *_lineage(prov, BRONZE_DS1, "DS01004", run_id, cols),
        _c("Contract Signing Date").alias("_raw_signing_date"), _c("Supplier Contract Amount (US$)").alias("_raw_amount"),
    )


def drop_validation(df: DataFrame) -> DataFrame:
    return df.drop(*[c for c in df.columns if c.startswith("_")])
