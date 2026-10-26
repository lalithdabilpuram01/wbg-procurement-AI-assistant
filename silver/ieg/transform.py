"""IEG project evaluation ratings (DS00053): Bronze -> Silver, one row per project.

Source: workspace.wbg_bronze.bronze_ieg_ratings_ds00053 (cohort extract of Finances One DS00053, as of 10/18/2026;
21 string columns with original names; empty CSV fields arrive as NULL), 750 rows.

Semantics (see silver/runs/ieg_discovery_20261023/IEG_DISCOVERY_REPORT.md):
  * Latest-only, post-evaluation snapshot: DS00053 keeps only the latest IEG evaluation per project and overwrites
    earlier ones (e.g. a later PPAR replaces an ICR Review). It is not a history.
  * Ratings are produced after project completion and judge the whole implementation, including procurement. They must
    NOT be used as features for predictions made before they became available.
  * Availability: DS00053 publishes only the evaluation fiscal year. No exact publication date is inferred from it, so
    rating_available_from_date is NULL unless a verified public availability date exists (none in this source).

Scores are ordinal category codes (1 = lowest rating, maximum = highest), not continuous measurements:
  6-point (Outcome, Quality at Entry, Quality of Supervision, Bank Performance):
      Highly Unsatisfactory 1, Unsatisfactory 2, Moderately Unsatisfactory 3, Moderately Satisfactory 4,
      Satisfactory 5, Highly Satisfactory 6
  4-point (M&E Quality): Negligible 1, Modest 2, Substantial 3, High 4
  "Not Rated" (the only unrated value in the full DS00053 population) and blank -> NULL score; the published text is kept.
  Any other text is outside the vocabulary: NULL score and a blocking check.
"""
from __future__ import annotations

import json
from pathlib import Path

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from silver.common.sourcefiles import row_hash_expr

TRANSFORM_VERSION = "ieg_silver_v1.0.0"
BRONZE = "workspace.wbg_bronze.bronze_ieg_ratings_ds00053"
REPO = Path(__file__).resolve().parents[2]
BRONZE_INPUTS_MANIFEST = REPO / "databricks_upload" / "bronze_inputs" / "_manifest" / "bronze_inputs_manifest.json"
PHASE1_CACHE_MANIFEST = REPO / "discovery" / "data_cache" / "MANIFEST.jsonl"
META = ("source_file", "cohort_id", "ingested_at")

SIX_POINT = {"Highly Unsatisfactory": 1, "Unsatisfactory": 2, "Moderately Unsatisfactory": 3,
             "Moderately Satisfactory": 4, "Satisfactory": 5, "Highly Satisfactory": 6}
FOUR_POINT_ME = {"Negligible": 1, "Modest": 2, "Substantial": 3, "High": 4}
UNRATED = ["Not Rated"]
SATISFACTORY_MIN_SCORE = 4  # IEG convention: Moderately Satisfactory or better
# source column -> (output stem, scale)
RATING_FIELDS: dict[str, tuple[str, dict[str, int]]] = {
    "Outcome": ("outcome", SIX_POINT), "Quality at Entry": ("quality_at_entry", SIX_POINT),
    "Quality of Supervision": ("quality_of_supervision", SIX_POINT), "Bank Performance": ("bank_performance", SIX_POINT),
    "M&E Quality": ("me_quality", FOUR_POINT_ME)}
EVALUATION_TYPES = ["ICRR", "PPAR", "PCR", "EVM", "PCN", "PCM", "CSSR"]  # all codes in the full DS00053 population
EVALUATION_TYPE_NAMES = {"ICRR": "Implementation Completion and Results Report Review",
                         "PPAR": "Project Performance Assessment Report"}  # other codes: NULL name (not guessed)
LENDING_INSTRUMENTS = ["IPF", "DPF"]
SNAPSHOT_SEMANTICS = "latest_evaluation_as_of_source_date"


def source_columns(df: DataFrame) -> list[str]:
    return [c for c in df.columns if c not in META]


def load_provenance() -> dict:
    m = json.loads(BRONZE_INPUTS_MANIFEST.read_text())
    e = next(f for f in m["files"] if f["bronze_table"] == "bronze_ieg_ratings_ds00053")
    src_name = Path(e["source_file"]).name
    pull = next(json.loads(ln) for ln in PHASE1_CACHE_MANIFEST.read_text().splitlines() if src_name in ln)
    return {"bronze_input_file_sha256": e["output_sha256"], "records": int(e["records_selected"]), "source_population_sha256": e["source_sha256"],
            "source_retrieved_at": pull["extracted_at_utc"], "source_url_note": pull["source"], "source_population_records": int(e["source_records"])}


def _c(name: str) -> Column:
    return F.col(f"`{name}`")


def _fy(name: str) -> Column:
    """4-digit fiscal year -> int; anything else -> NULL (parse failures are counted by the checks)."""
    return F.when(_c(name).rlike(r"^[0-9]{4}$"), _c(name).cast("int"))


def _score(name: str, scale: dict[str, int]) -> Column:
    lookup = F.create_map(*[F.lit(x) for kv in scale.items() for x in kv])
    return F.when(_c(name).isin(list(scale)), lookup[_c(name)]).cast("int")


def _in_vocabulary(name: str, scale: dict[str, int]) -> Column:
    return _c(name).isNull() | _c(name).isin(list(scale) + UNRATED)


def build(bronze: DataFrame, prov: dict, run_id: str) -> DataFrame:
    cols = source_columns(bronze)
    eval_fy, close_fy = _fy("Evaluation FY"), _fy("Final Closing FY")
    names = F.create_map(*[F.lit(x) for kv in EVALUATION_TYPE_NAMES.items() for x in kv])
    ratings: list[Column] = []
    checks: list[Column] = []
    for src, (stem, scale) in RATING_FIELDS.items():
        ratings += [_c(src).alias(f"{stem}_rating"), _score(src, scale).alias(f"{stem}_score")]
        checks.append(_in_vocabulary(src, scale).alias(f"_vocab_ok_{stem}"))
    outcome_score = _score("Outcome", SIX_POINT)
    return bronze.select(
        _c("Project ID").alias("project_id"), _c("Project Name").alias("project_name_source"),
        _c("Evaluation Type").alias("evaluation_type"), names[_c("Evaluation Type")].alias("evaluation_type_name"),
        eval_fy.alias("evaluation_fiscal_year"), _fy("Approval FY").alias("approval_fiscal_year"), close_fy.alias("final_closing_fiscal_year"),
        (eval_fy - close_fy).alias("evaluation_lag_fiscal_years"),
        (eval_fy < close_fy).alias("evaluation_precedes_final_closing"),
        F.lit(None).cast("date").alias("rating_available_from_date"),
        *ratings,
        F.when(outcome_score.isNotNull(), outcome_score >= SATISFACTORY_MIN_SCORE).alias("outcome_satisfactory"),
        _c("WB Region").alias("wb_region_name"), _c("Country / Economy").alias("country_name_source"),
        _c("Country / Economy Lending Group").alias("country_lending_group"), _c("Country / Economy FCS Status").alias("fcs_status"),
        _c("Country / Economy FCS Lending Group").alias("fcs_lending_group"), _c("Practice Group").alias("practice_group"),
        _c("Global Practice").alias("global_practice"), _c("Agreement Type").alias("agreement_type"),
        _c("Lending Instrument Type").alias("lending_instrument_type"),
        F.to_date(_c("As of Date"), "MM/dd/yyyy").alias("source_as_of_date"), F.lit(SNAPSHOT_SEMANTICS).alias("rating_snapshot_semantics"),
        F.lit("DS00053").alias("source_dataset"), row_hash_expr(cols).alias("source_row_sha256"),
        F.lit(BRONZE).alias("bronze_table"), F.col("source_file").alias("bronze_source_file"),
        F.col("cohort_id").alias("bronze_cohort_id"), F.col("ingested_at").alias("bronze_ingested_at"),
        F.lit(prov["bronze_input_file_sha256"]).alias("bronze_input_file_sha256"),
        F.lit(prov["source_population_sha256"]).alias("source_population_sha256"),
        F.to_timestamp(F.lit(prov["source_retrieved_at"])).alias("source_retrieved_at"),
        F.lit(run_id).alias("silver_run_id"), F.current_timestamp().alias("silver_processed_at"), F.lit(TRANSFORM_VERSION).alias("transform_version"),
        # validation-only
        _c("As of Date").alias("_raw_as_of"), _c("Evaluation FY").alias("_raw_eval_fy"), _c("Approval FY").alias("_raw_approval_fy"),
        _c("Final Closing FY").alias("_raw_closing_fy"), *checks,
    )


def drop_validation(df: DataFrame) -> DataFrame:
    return df.drop(*[c for c in df.columns if c.startswith("_")])
