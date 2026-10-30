"""Projects v3 all-fields records: Bronze -> two Silver tables (approved 2026-10-23).

Source: workspace.wbg_bronze.bronze_projects_v3_allfields (raw_record JSON from search.worldbank.org/api/v3/projects
?id=<P>&fl=*), 184 cohort projects = the Phase 1 procurement-notice cluster (276 seeded, region-stratified projects)
intersected with the cohort. NON-RANDOM, partial coverage: absence of detail for the other 1,816 projects is a
collection limitation, never zero / "no risk". See silver/runs/projects_allfields_discovery_20261023/.

Tables:
  project_details_v3         one row per project (184). Only fields NOT already in wbg_silver.projects: product line,
                             E&S risk rating, parent project, P2A flag/date, the theme hierarchy (levels 1-3, nested,
                             original order), theme v2 level-2/3 labels, results indicators and geo-locations (typed
                             arrays in original order with their source position). wbg_silver.projects is unchanged.
  project_loan_transactions  one row per published loan_transactions element (16,643). Technical key =
                             project_id + source-array position (no source transaction id exists; identical rows are
                             kept and counted, never de-duplicated). Positions may change on an API refresh.

Excluded on purpose: the 39 keys already in `projects`, the *_exact / flattened duplicates, home-page URLs, and the
personal identifiers teamleadname / teamleaderupi. The raw JSON is NOT copied (it contains staff identifiers); lineage
is raw_record_sha256 + Bronze file references.

Semantics: every value is as of the single retrieval 2026-10-21T01:40:31Z (latest snapshot). Transaction dates are
dated records from that snapshot; they do not prove when a record became publicly available. Transaction amounts are
kept as published plus an exact decimal; their currency/unit is NOT independently verified (commitments agree with
IBRD/IDA original principal US$ for 223/224 loans, which is evidence, not a source statement).
"""
from __future__ import annotations

from app_config.paths import BRONZE_INPUTS_MANIFEST, SOURCE_PULLS_SAMPLES_MANIFEST
import json
from pathlib import Path

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F
from silver.common.pipeline import CATALOG, manifest

TRANSFORM_VERSION = "project_details_silver_v1.0.0"
BRONZE = f"{CATALOG}.wbg_bronze.bronze_projects_v3_allfields"
SOURCE_API = "search.worldbank.org/api/v3/projects (id=<P>, fl=*)"
REPO = Path(__file__).resolve().parents[2]
BRONZE_INPUTS_MANIFEST = manifest("WBG_BRONZE_INPUTS_MANIFEST", BRONZE_INPUTS_MANIFEST)
SAMPLES_MANIFEST = manifest("WBG_SOURCE_PULLS_MANIFEST", SOURCE_PULLS_SAMPLES_MANIFEST)
DETAIL_COVERAGE = "phase1_cluster_detail_pull"
RECORD_SEMANTICS = "current_values_as_of_source_retrieval"
TRANSACTION_SEMANTICS = "dated_records_from_snapshot_retrieved_2026_10_21"
PLACEHOLDER_PERIOD_PREFIX = "1900-01-01"
NUMERIC = r"^-?[0-9]+(\.[0-9]+)?([Ee][+-]?[0-9]+)?$"  # unambiguous plain or scientific notation
RISK_RATINGS = ["H", "S", "M", "L"]
TRANSACTION_TYPES = ["FEE_CHARGES", "INT_CHARGES", "LOAN_CANCEL", "LOAN_COMMITMENT", "LOAN_DISB", "LOAN_REPAY"]
PERSONAL_KEYS = ["teamleadname", "teamleaderupi"]
# The 76 top-level keys observed; an unknown key blocks the write so new API fields are reviewed first.
KNOWN_KEYS = sorted([
    "boardapprovaldate", "boardapprovaldate_exact", "borrower", "borrower_exact", "closingdate", "cons_serv_reqd_ind", "cons_serv_reqd_ind_exact",
    "countrycode", "countryhomepageurl", "countryname", "countryshortname", "countryshortname_exact", "curr_ibrd_commitment", "curr_ida_commitment",
    "curr_project_cost", "curr_total_commitment", "envassesmentcategorycode", "esrc_ovrl_risk_rate", "esrc_ovrl_risk_rate_exact", "financers",
    "fiscalyear", "geo_locations", "grantamt", "icr_ratings", "id", "idacommamt", "ieg_ratings", "impagency", "impagency_exact", "indicators",
    "isr_ratings", "last_stage_reached_name", "lendprojectcost", "loan_effective_date", "loan_transactions", "major_sector_code", "major_sector_name",
    "major_sectors", "milestones", "mjsector", "mjsector_exact", "p2a_flag", "p2a_updated_date", "parentprojid", "parentprojid_exact", "pdo",
    "prodline_exact", "proj_id", "proj_last_upd_date", "project_abstract", "project_name", "projectfinancialtype", "projectfinancialtype_exact",
    "projid_id_display", "public_disclosure_date", "regionhomepageurl", "regionname", "regionname_exact", "sector_exact", "sector_name",
    "sector_percent", "sectorcode", "sectorcode_exact", "status", "status_exact", "teamleaderupi", "teamleadname", "theme", "theme_exact",
    "theme_list", "themecode", "themecode_exact", "themev2_level1_exact", "themev2_level2_exact", "themev2_level3_exact", "totalamt"])

_T3 = "struct<code:string,name:string,seqno:string,percent:string>"
_T2 = f"struct<code:string,name:string,seqno:string,percent:string,theme3:array<{_T3}>>"
_T1 = f"struct<code:string,name:string,seqno:string,percent:string,theme2:array<{_T2}>>"
_MEASURE = "struct<Value:string,Date:string,Comment:string>"
RECORD_SCHEMA = (
    "id string, prodline_exact string, esrc_ovrl_risk_rate string, parentprojid string, p2a_flag string, p2a_updated_date string, "
    "themev2_level2_exact array<string>, themev2_level3_exact array<string>, "
    f"theme_list array<{_T1}>, "
    f"indicators array<struct<Name:string,Type:string,Subtype:string,Baseline:{_MEASURE},Current:{_MEASURE},Target:{_MEASURE}>>, "
    "geo_locations array<struct<geo_loc_id:string,place_id:string,wbg_cntry_key:string,geo_loc_name:string,geo_latitude_nbr:string,"
    "geo_longitude_nbr:string,admin_unit1_name:string,admin_unit2_name:string>>, "
    "loan_transactions array<struct<amount:string,period:string,transactiontype:string,financier_loan:string>>")


def load_provenance() -> dict:
    m = json.loads(BRONZE_INPUTS_MANIFEST.read_text())
    e = next(f for f in m["files"] if f["bronze_table"] == "bronze_projects_v3_allfields")
    src_name = Path(e["source_file"]).name
    pull = next(json.loads(ln) for ln in SAMPLES_MANIFEST.read_text().splitlines() if src_name in ln)
    return {"bronze_input_file_sha256": e["output_sha256"], "records": int(e["records_selected"]), "source_population_sha256": e["source_sha256"],
            "source_retrieved_at": pull["extracted_at_utc"], "source_population_records": int(e["source_records"]), "extraction_method": pull["extraction_method"]}


def _int(c: Column) -> Column:
    return F.when(c.rlike(r"^-?[0-9]+$"), c.cast("int"))


def _theme(x: Column, child: str | None, level_child=None) -> Column:
    fields = [x["code"].alias("code"), x["name"].alias("name"), _int(x["seqno"]).alias("seqno"), _int(x["percent"]).alias("percent")]
    if child:
        fields.append(F.transform(x[child], level_child).alias(child.replace("theme", "level")))
    return F.struct(*fields)


def _measure(m: Column) -> Column:
    return F.struct(m["Value"].alias("value"), F.to_date(F.substring(m["Date"], 1, 10), "yyyy-MM-dd").alias("date"), m["Comment"].alias("comment"))


def _lineage(prov: dict, run_id: str) -> list[Column]:
    return [F.lit(SOURCE_API).alias("source_api"), F.sha2("raw_record", 256).alias("raw_record_sha256"), F.lit(BRONZE).alias("bronze_table"),
            F.col("source_file").alias("bronze_source_file"), F.col("cohort_id").alias("bronze_cohort_id"), F.col("ingested_at").alias("bronze_ingested_at"),
            F.lit(prov["bronze_input_file_sha256"]).alias("bronze_input_file_sha256"), F.lit(prov["source_population_sha256"]).alias("source_population_sha256"),
            F.to_timestamp(F.lit(prov["source_retrieved_at"])).alias("source_retrieved_at"),
            F.lit(run_id).alias("silver_run_id"), F.current_timestamp().alias("silver_processed_at"), F.lit(TRANSFORM_VERSION).alias("transform_version")]


def parsed(bronze: DataFrame) -> DataFrame:
    return bronze.select(F.from_json("raw_record", RECORD_SCHEMA).alias("r"), "raw_record", "source_file", "cohort_id", "ingested_at")


def build_details(bronze: DataFrame, prov: dict, run_id: str) -> DataFrame:
    p = parsed(bronze)
    r = lambda k: F.col(f"r.{k}")  # noqa: E731
    themes = F.transform(r("theme_list"), lambda t1: _theme(t1, "theme2", lambda t2: _theme(t2, "theme3", lambda t3: _theme(t3, None))))
    indicators = F.transform(r("indicators"), lambda x, i: F.struct(
        i.alias("indicator_index"), x["Name"].alias("name"), x["Type"].alias("indicator_type"), x["Subtype"].alias("subtype"),
        _measure(x["Baseline"]).alias("baseline"), _measure(x["Current"]).alias("current"), _measure(x["Target"]).alias("target")))
    geo = F.transform(r("geo_locations"), lambda g, i: F.struct(
        i.alias("location_index"), g["geo_loc_id"].alias("geo_loc_id"), g["place_id"].alias("place_id"), g["wbg_cntry_key"].alias("country_key"),
        g["geo_loc_name"].alias("name"), g["geo_latitude_nbr"].cast("decimal(10,7)").alias("latitude"), g["geo_longitude_nbr"].cast("decimal(10,7)").alias("longitude"),
        g["admin_unit1_name"].alias("admin_unit1_name"), g["admin_unit2_name"].alias("admin_unit2_name")))
    size = lambda c: F.coalesce(F.size(c), F.lit(0))  # noqa: E731  (absent array -> 0 elements; size(NULL) is NULL/-1)
    return p.select(
        r("id").alias("project_id"), r("prodline_exact").alias("product_line_code"), r("esrc_ovrl_risk_rate").alias("environmental_social_risk_rating"),
        r("parentprojid").alias("parent_project_id"), r("p2a_flag").alias("p2a_flag"),
        F.to_date(F.substring(r("p2a_updated_date"), 1, 10), "yyyy-MM-dd").alias("p2a_updated_date"),
        themes.alias("themes"), r("themev2_level2_exact").alias("themes_level2"), r("themev2_level3_exact").alias("themes_level3"),
        indicators.alias("results_indicators"), geo.alias("geo_locations"),
        size(r("theme_list")).alias("theme_level1_count"), size(r("indicators")).alias("indicator_count"),
        size(r("geo_locations")).alias("geo_location_count"), size(r("loan_transactions")).alias("loan_transaction_count"),
        F.lit(DETAIL_COVERAGE).alias("detail_coverage"), F.lit(RECORD_SEMANTICS).alias("record_snapshot_semantics"),
        *_lineage(prov, run_id),
        F.json_object_keys("raw_record").alias("_json_keys"), r("p2a_updated_date").alias("_raw_p2a_date"), F.col("raw_record").alias("_raw_record"),
    )


def build_transactions(bronze: DataFrame, prov: dict, run_id: str) -> DataFrame:
    p = parsed(bronze)
    e = p.select(F.col("r.id").alias("project_id"), F.posexplode("r.loan_transactions").alias("transaction_index", "t"),
                 "raw_record", "source_file", "cohort_id", "ingested_at")
    t = lambda k: F.col(f"t.{k}")  # noqa: E731
    amount_ok = t("amount").rlike(NUMERIC)
    placeholder = t("period").startswith(PLACEHOLDER_PERIOD_PREFIX)
    ident = Window.partitionBy("project_id", t("financier_loan"), t("period"), t("transactiontype"), t("amount"))
    return e.select(
        F.concat_ws(":", "project_id", F.col("transaction_index").cast("string")).alias("transaction_key"), "project_id", "transaction_index",
        t("financier_loan").alias("financier_loan"), F.regexp_replace(t("financier_loan"), "-", "").alias("financing_number"),
        F.regexp_extract(t("financier_loan"), r"^([A-Z]+)", 1).alias("financing_number_prefix"),
        t("transactiontype").alias("transaction_type"), t("period").alias("transaction_period_published"),
        F.when(placeholder, F.lit(None).cast("date")).otherwise(F.to_date(F.substring(t("period"), 1, 10), "yyyy-MM-dd")).alias("transaction_date"),
        F.coalesce(placeholder, F.lit(False)).alias("transaction_date_is_placeholder"),
        t("amount").alias("amount_published"), F.when(amount_ok, t("amount").cast("decimal(22,2)")).alias("amount"),
        F.count(F.lit(1)).over(ident).cast("int").alias("identical_transaction_group_size"),
        F.lit(TRANSACTION_SEMANTICS).alias("transaction_snapshot_semantics"),
        *_lineage(prov, run_id),
        amount_ok.alias("_amount_unambiguous"),
    )


def drop_validation(df: DataFrame) -> DataFrame:
    return df.drop(*[c for c in df.columns if c.startswith("_")])
