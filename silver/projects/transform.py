"""Projects: Bronze -> Silver for the approved 2,000-project PoC cohort (pure DataFrame functions + provenance loader).

Source: workspace.wbg_bronze.bronze_projects_v3 (raw_record JSON from search.worldbank.org/api/v3/projects,
39 selected fields; source_file, cohort_id, ingested_at). One row per project.

Rules:
  * Key: project_id (`id`, P-number). It equals `proj_id` and joins to contracts (DS00005 "Project ID"),
    notices ("Project ID" / project_id), IEG ("Project ID") and loan statements.
  * Geography: `countrycode` is a one-element array. Regional / multi-country codes (e.g. 3W, 1W, 4E) are kept as
    published and classified `regional_multicountry` with the approved curated rule (wbdisc.countries.is_regional,
    identical to the cohort manifest flag). They are never matched to WDI (D-29): several collide with WDI aggregate
    codes that mean something else (e.g. project 4E = East Asia and Pacific region; WDI 4E = EAP excluding high income).
  * WDI compatibility for single-country projects: exact country_code match in wbg_silver.wdi_country, else the
    approved curated alias table (D-07/D-31: ZR->CD, GZ->PS, YF->RS, TP->TL, RY->YE). Nothing else is mapped.
  * Missing vs zero: an absent amount key is NULL; a published "0" is 0. fiscalyear "0" is the source's marker for
    "no approval date" and becomes NULL (raw kept in approval_fiscal_year_raw).
  * Ratings arrays (ISR / ICR / IEG) are the CURRENT values at extraction (no history; D-20) and are kept as typed
    snapshots, not interpreted. The full original record is kept in bronze_raw_record.
"""
from __future__ import annotations

from app_config.paths import BRONZE_INPUTS_MANIFEST, COHORT_MANIFEST, SOURCE_PULLS_CACHE_MANIFEST
import json
from pathlib import Path

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from silver.common.pipeline import CATALOG, manifest

TRANSFORM_VERSION = "projects_silver_v1.0.0"
BRONZE_TABLE = f"{CATALOG}.wbg_bronze.bronze_projects_v3"
WDI_COUNTRY = f"{CATALOG}.wbg_silver.wdi_country"
REPO = Path(__file__).resolve().parents[2]
COHORT_MANIFEST = COHORT_MANIFEST
BRONZE_INPUTS_MANIFEST = manifest("WBG_BRONZE_INPUTS_MANIFEST", BRONZE_INPUTS_MANIFEST)
PHASE1_CACHE_MANIFEST = manifest("WBG_SOURCE_PULLS_MANIFEST", SOURCE_PULLS_CACHE_MANIFEST)
SOURCE_API = "search.worldbank.org/api/v3/projects"

from wbdisc.countries import _REGIONAL, WB_TO_ISO2  # noqa: E402  (approved curated rules, D-07/D-31)

S = T.StringType()
DEC = T.DecimalType(22, 2)
AMOUNTS = {  # source key -> Silver column (US$ as published by the Projects API)
    "curr_total_commitment": "current_total_commitment_usd", "totalamt": "total_amount_usd", "grantamt": "grant_amount_usd",
    "idacommamt": "ida_commitment_amount_usd", "curr_ibrd_commitment": "current_ibrd_commitment_usd",
    "curr_ida_commitment": "current_ida_commitment_usd", "curr_project_cost": "current_project_cost_usd",
    "lendprojectcost": "lending_project_cost_usd",
}
MILESTONES = {
    "concpt_review_date": "concept_review_date", "dsclsr_of_concpt_pid_date": "concept_pid_disclosure_date",
    "decsn_review_date": "decision_review_date", "begin_apprsl_date": "appraisal_begin_date",
    "authorize_negotiations_date": "negotiations_authorized_date", "apprvl_date": "milestone_approval_date",
    "effctvnss_date": "effectiveness_date", "icr_nco_date": "icr_nco_date",
}
RECORD_SCHEMA = T.StructType(
    [T.StructField(k, S) for k in ["id", "proj_id", "project_name", "countryname", "countryshortname", "regionname", "status",
                                    "last_stage_reached_name", "fiscalyear", "boardapprovaldate", "closingdate", "loan_effective_date",
                                    "public_disclosure_date", "proj_last_upd_date", "envassesmentcategorycode", "cons_serv_reqd_ind",
                                    "major_sector_code", "major_sector_name", "borrower", "impagency", "pdo", "project_abstract"]]
    + [T.StructField(k, S) for k in AMOUNTS]
    + [T.StructField("countrycode", T.ArrayType(S)), T.StructField("projectfinancialtype", T.ArrayType(S)),
       T.StructField("themev2_level1_exact", T.ArrayType(S)),
       T.StructField("financers", T.ArrayType(T.StructType([T.StructField(k, S) for k in
                     ["fincrname", "fincr_id", "fincr_curr_id", "fincr_curr_amt", "fincr_usd_amt", "projectfinancialtype"]]))),
       T.StructField("major_sectors", T.ArrayType(T.StructType([T.StructField("major_sector", T.StructType([
           T.StructField("major_sector_code", S), T.StructField("major_sector_name", S),
           T.StructField("sectors", T.ArrayType(T.StructType([T.StructField(k, S) for k in ["sector_code", "sector_name", "sector_percent"]])))]))]))),
       T.StructField("milestones", T.ArrayType(T.StructType([T.StructField(k, S) for k in MILESTONES]))),
       T.StructField("isr_ratings", T.ArrayType(T.StructType([T.StructField(k, S) for k in ["name", "code", "isr_date", "value"]]))),
       T.StructField("icr_ratings", T.ArrayType(T.StructType([T.StructField(k, S) for k in
                     ["value", "overallrating", "outratingind", "bankqualityentry", "banksupervision", "completion_riskdo",
                      "borroverall", "borrgovt", "borrimplegency"]]))),
       T.StructField("ieg_ratings", T.ArrayType(T.StructType([T.StructField(k, S) for k in
                     ["evaldate", "evaltype", "outcome", "overallbankperf", "bankqualityofsupervision", "icrquality", "mequality",
                      "borrowcompliance", "overallborrowperf", "borrowimplementation", "evaluation_riskdo"]])))]
)


def load_provenance() -> dict:
    """Provenance of the Bronze input file: extraction manifest (cohort subset) + Phase 1 API pull."""
    m = json.loads(BRONZE_INPUTS_MANIFEST.read_text())
    e = next(f for f in m["files"] if f["bronze_table"] == "bronze_projects_v3")
    pull = next(json.loads(ln) for ln in PHASE1_CACHE_MANIFEST.read_text().splitlines() if "projects_v3_population" in ln)
    return {"file_name": Path(e["output_file"]).name, "source_file_sha256": e["output_sha256"], "manifest_records": int(e["records_selected"]),
            "source_population_sha256": e["source_sha256"], "cohort_subset_extracted_at": e["extracted_at_utc"],
            "source_retrieved_at": pull["extracted_at_utc"], "source_extraction_method": pull["extraction_method"],
            "cohort_manifest_sha256": m["cohort_manifest_sha256"]}


def _date(c: F.Column | str) -> F.Column:
    """ISO date or ISO timestamp string -> DATE (first 10 characters, strict yyyy-MM-dd)."""
    return F.to_date(F.substring(c, 1, 10), "yyyy-MM-dd")


def _blank_to_null(c: F.Column) -> F.Column:
    return F.when(F.trim(c) == "", F.lit(None)).otherwise(F.trim(c))


def build(bronze: DataFrame, wdi_country: DataFrame, prov: dict, run_id: str) -> DataFrame:
    p = bronze.select(F.from_json("raw_record", RECORD_SCHEMA).alias("r"), F.col("raw_record").alias("bronze_raw_record"),
                      F.sha2("raw_record", 256).alias("raw_record_sha256"), F.col("source_file").alias("bronze_source_file"),
                      F.col("cohort_id").alias("bronze_cohort_id"), F.col("ingested_at").alias("bronze_ingested_at"))
    alias = F.create_map(*[x for k, v in WB_TO_ISO2.items() for x in (F.lit(k), F.lit(v))])
    cc = F.element_at("r.countrycode", 1)
    base = p.select(
        F.col("r.id").alias("project_id"), F.col("r.proj_id").alias("_proj_id"),
        F.col("r.project_name").alias("project_name"),
        cc.alias("country_code"), F.size("r.countrycode").alias("_countrycode_size"),
        F.col("r.countryname").alias("country_name"), F.col("r.countryshortname").alias("country_short_name"),
        F.col("r.regionname").alias("region_name"),
        F.when(F.upper(F.trim(cc)).rlike(_REGIONAL.pattern), F.lit("regional_multicountry")).otherwise(F.lit("single_country")).alias("geography_type"),
        F.col("r.status").alias("project_status"), F.col("r.last_stage_reached_name").alias("last_stage_reached"),
        F.when(F.col("r.fiscalyear") == "0", F.lit(None)).otherwise(F.col("r.fiscalyear").cast("int")).alias("approval_fiscal_year"),
        F.col("r.fiscalyear").alias("approval_fiscal_year_raw"),
        _date("r.boardapprovaldate").alias("board_approval_date"), _date("r.closingdate").alias("closing_date"),
        _date("r.loan_effective_date").alias("loan_effective_date"), _date("r.public_disclosure_date").alias("public_disclosure_date"),
        F.to_timestamp("r.proj_last_upd_date").alias("project_last_updated_at"),
        *[F.col(f"r.{k}").cast(DEC).alias(v) for k, v in AMOUNTS.items()],
        F.col("r.projectfinancialtype").alias("financing_types"),
        F.array_sort(F.array_distinct("r.projectfinancialtype")).alias("financing_types_distinct"),
        F.col("r.envassesmentcategorycode").alias("environmental_assessment_category"),
        F.col("r.cons_serv_reqd_ind").alias("consulting_services_required"),
        F.col("r.major_sector_code").alias("major_sector_codes_published"), F.col("r.major_sector_name").alias("major_sector_names_published"),
        F.transform("r.major_sectors", lambda m: F.struct(
            m["major_sector"]["major_sector_code"].alias("major_sector_code"), m["major_sector"]["major_sector_name"].alias("major_sector_name"),
            F.transform(m["major_sector"]["sectors"], lambda s: F.struct(
                s["sector_code"].alias("sector_code"), s["sector_name"].alias("sector_name"),
                s["sector_percent"].cast("double").alias("sector_percent"))).alias("sectors"))).alias("major_sectors"),
        F.col("r.themev2_level1_exact").alias("themes_level1"),
        F.col("r.borrower").alias("borrower"), F.col("r.impagency").alias("implementing_agency"),
        F.col("r.pdo").alias("project_development_objective"), F.col("r.project_abstract").alias("project_abstract"),
        F.transform("r.financers", lambda f: F.struct(
            f["fincr_id"].alias("financer_id"), f["fincrname"].alias("financer_name"), f["projectfinancialtype"].alias("financing_type"),
            _blank_to_null(f["fincr_curr_id"]).alias("currency_code"), f["fincr_curr_amt"].cast(DEC).alias("amount_original_currency"),
            f["fincr_usd_amt"].cast(DEC).alias("amount_usd"))).alias("financers"),
        *[_date(F.element_at("r.milestones", 1)[k]).alias(v) for k, v in MILESTONES.items()],
        F.transform("r.isr_ratings", lambda x: F.struct(x["code"].alias("rating_code"), x["name"].alias("rating_name"),
                                                        x["value"].alias("rating_value"), _date(x["isr_date"]).alias("isr_date"))).alias("isr_ratings_snapshot"),
        F.transform("r.icr_ratings", lambda x: F.struct(_date(x["value"]).alias("icr_date"), x["overallrating"].alias("overall_rating"),
                                                        x["outratingind"].alias("outcome_rating"), x["bankqualityentry"].alias("bank_quality_at_entry"),
                                                        x["banksupervision"].alias("bank_supervision"), x["completion_riskdo"].alias("risk_to_development_outcome"),
                                                        x["borroverall"].alias("borrower_overall"), x["borrgovt"].alias("borrower_government"),
                                                        x["borrimplegency"].alias("borrower_implementing_agency"))).alias("icr_ratings_snapshot"),
        F.transform("r.ieg_ratings", lambda x: F.struct(_date(x["evaldate"]).alias("evaluation_date"), x["evaltype"].alias("evaluation_type"),
                                                        x["outcome"].alias("outcome"), x["overallbankperf"].alias("bank_performance"),
                                                        x["bankqualityofsupervision"].alias("bank_quality_of_supervision"), x["icrquality"].alias("icr_quality"),
                                                        x["mequality"].alias("me_quality"), x["borrowcompliance"].alias("borrower_compliance"))).alias("ieg_ratings_snapshot"),
        # raw strings kept for validation only
        F.col("r.boardapprovaldate").alias("_raw_boardapprovaldate"), F.col("r.closingdate").alias("_raw_closingdate"),
        F.col("r.loan_effective_date").alias("_raw_loan_effective_date"), F.col("r.public_disclosure_date").alias("_raw_public_disclosure_date"),
        F.col("r.proj_last_upd_date").alias("_raw_proj_last_upd_date"),
        *[F.col(f"r.{k}").alias(f"_raw_{k}") for k in AMOUNTS],
        F.col("r.financers").alias("_raw_financers"), F.col("r.isr_ratings").alias("_raw_isr"),
        "bronze_raw_record", "raw_record_sha256", "bronze_source_file", "bronze_cohort_id", "bronze_ingested_at",
        F.element_at(F.split("bronze_source_file", "/"), -1).alias("_file_name"),
    )
    w = wdi_country.select(F.col("country_code").alias("_w_code"), F.col("entity_type").alias("_w_type"), F.col("country_wb_id").alias("_w_id"))
    alias_code = F.coalesce(alias[F.col("country_code")], F.col("country_code"))
    j = base.withColumn("_lookup_code", F.when(F.col("geography_type") == "single_country", alias_code))
    j = j.join(F.broadcast(w), j._lookup_code == w._w_code, "left")
    collide = wdi_country.select(F.col("country_code").alias("_c_code"), F.col("entity_type").alias("_c_type"))
    j = j.join(F.broadcast(collide), (j.geography_type == "regional_multicountry") & (j.country_code == collide._c_code), "left")
    j = j.select(
        "*",
        F.when(F.col("geography_type") == "regional_multicountry", F.lit(None)).otherwise(F.col("_w_code")).alias("wdi_country_code"),
        F.col("_w_id").alias("wdi_country_wb_id"),
        F.when(F.col("geography_type") == "regional_multicountry", F.lit("not_applicable_regional"))
         .when(F.col("_w_code").isNull(), F.lit("unmatched"))
         .when(F.col("_w_code") == F.col("country_code"), F.lit("exact"))
         .otherwise(F.lit("curated_alias")).alias("wdi_match_method"),
        F.col("_c_code").isNotNull().alias("regional_code_collides_with_wdi_code"),
    )
    lit = lambda k: F.lit(prov[k])  # noqa: E731
    return j.select(
        "project_id", "project_name", "country_code", "country_name", "country_short_name", "region_name", "geography_type",
        "wdi_country_code", "wdi_country_wb_id", "wdi_match_method", "regional_code_collides_with_wdi_code",
        "project_status", "last_stage_reached", "approval_fiscal_year", "approval_fiscal_year_raw",
        "board_approval_date", "closing_date", "loan_effective_date", "public_disclosure_date", "project_last_updated_at",
        *AMOUNTS.values(), "financing_types", "financing_types_distinct", "environmental_assessment_category", "consulting_services_required",
        "major_sector_codes_published", "major_sector_names_published", "major_sectors", "themes_level1",
        "borrower", "implementing_agency", "project_development_objective", "project_abstract", "financers",
        *MILESTONES.values(), "isr_ratings_snapshot", "icr_ratings_snapshot", "ieg_ratings_snapshot",
        F.lit(SOURCE_API).alias("source_api"), F.to_timestamp(lit("source_retrieved_at")).alias("source_retrieved_at"),
        lit("source_population_sha256").alias("source_population_sha256"), lit("source_file_sha256").alias("bronze_input_file_sha256"),
        lit("cohort_manifest_sha256").alias("cohort_manifest_sha256"),
        F.lit(BRONZE_TABLE).alias("bronze_table"), "bronze_source_file", "bronze_cohort_id", "bronze_ingested_at",
        "bronze_raw_record", "raw_record_sha256",
        F.lit(run_id).alias("silver_run_id"), F.current_timestamp().alias("silver_processed_at"), F.lit(TRANSFORM_VERSION).alias("transform_version"),
        *[c for c in j.columns if c.startswith("_raw_")], "_proj_id", "_countrycode_size", "_file_name",
    )


def validation_only_columns(df: DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith("_")]
