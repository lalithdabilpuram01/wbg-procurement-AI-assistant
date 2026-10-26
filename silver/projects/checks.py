"""Data-quality rules for workspace.wbg_silver.projects. "error" blocks writes; "warn" is reported."""
from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.projects.transform import AMOUNTS


def cohort_df(spark: SparkSession, cohort_pd) -> DataFrame:
    c = cohort_pd[["project_id", "project_name", "country_code", "country_is_regional", "region", "status", "approval_fy", "board_approval_date"]].copy()
    return spark.createDataFrame(c.astype(str))


def check(spark: SparkSession, s: DataFrame, bronze: DataFrame, cohort: DataFrame, prov: dict, report: DQReport) -> None:
    n = s.count()
    # --- identity and cohort boundary
    report.equals("bronze_rows", bronze.count(), 2000)
    report.equals("silver_rows_equal_bronze", n, bronze.count())
    report.equals("project_id_null_or_blank (incl. JSON parse failures)", s.filter(F.col("project_id").isNull() | (F.trim("project_id") == "")).count(), 0)
    report.equals("project_id_unique", s.select("project_id").distinct().count(), n)
    report.equals("project_id_format_P######", s.filter(~F.col("project_id").rlike(r"^P\d{6}$")).count(), 0)
    report.equals("id_equals_proj_id", s.filter(F.col("project_id") != F.col("_proj_id")).count(), 0)
    report.equals("ids_not_in_cohort_manifest", s.join(cohort, "project_id", "left_anti").count(), 0)
    report.equals("cohort_ids_missing_from_silver", cohort.join(s, "project_id", "left_anti").count(), 0)
    report.equals("cohort_rows", cohort.count(), 2000)
    report.equals("bronze_cohort_id_values", sorted(r[0] for r in s.select("bronze_cohort_id").distinct().collect()), ["wbg_poc_2000"])
    # --- agreement with the approved manifest (no value changed)
    j = s.join(cohort.select(*[F.col(c).alias(f"m_{c}") for c in cohort.columns]), s.project_id == F.col("m_project_id"))
    dis = {
        "project_name": j.filter(F.col("project_name") != F.col("m_project_name")).count(),
        "country_code": j.filter(F.col("country_code") != F.col("m_country_code")).count(),
        "region": j.filter(F.col("region_name") != F.col("m_region")).count(),
        "status": j.filter(F.col("project_status") != F.col("m_status")).count(),
        "geography_vs_manifest_regional_flag": j.filter((F.col("geography_type") == "regional_multicountry") != (F.col("m_country_is_regional") == "True")).count(),
        "approval_fy": j.filter(~F.coalesce(F.col("approval_fiscal_year").cast("string"), F.lit("")).eqNullSafe(F.regexp_replace("m_approval_fy", r"\.0$", ""))).count(),
        "board_approval_date": j.filter(~F.coalesce(F.col("board_approval_date").cast("string"), F.lit("")).eqNullSafe(F.col("m_board_approval_date"))).count(),
    }
    report.equals("disagreements_with_cohort_manifest", dis, {k: 0 for k in dis})
    # --- geography
    report.equals("countrycode_array_size_1", s.filter(F.col("_countrycode_size") != 1).count(), 0)
    geo = {r[0]: r[1] for r in s.groupBy("geography_type").count().collect()}
    report.equals("geography_counts", geo, {"single_country": 1861, "regional_multicountry": 139})
    report.equals("regional_projects_never_mapped_to_wdi", s.filter("geography_type = 'regional_multicountry' AND wdi_country_code IS NOT NULL").count(), 0)
    report.equals("single_country_projects_unmatched_to_wdi", s.filter("geography_type = 'single_country' AND wdi_match_method = 'unmatched'").count(), 0)
    report.equals("single_country_matched_to_non_economy",
                  s.filter("geography_type = 'single_country'").join(spark.table("workspace.wbg_silver.wdi_country").select(F.col("country_code").alias("w"), "entity_type"),
                                                                   F.col("wdi_country_code") == F.col("w")).filter("entity_type != 'economy'").count(), 0)
    meth = {r[0]: r[1] for r in s.groupBy("wdi_match_method").count().collect()}
    report.add("wdi_match_methods (projects)", meth, "exact + curated_alias = 1,861; not_applicable_regional = 139", True, "warn")
    report.add("curated_aliases_applied", sorted({(r[0], r[1]) for r in s.filter("wdi_match_method = 'curated_alias'").select("country_code", "wdi_country_code").collect()}),
               "approved D-07/D-31 aliases only", True, "warn")
    col = sorted({(r[0], r[1]) for r in s.filter("regional_code_collides_with_wdi_code").select("country_code", "region_name").collect()})
    report.add("regional_codes_that_collide_with_wdi_codes (deliberately not matched)", col, "reported", True, "warn")
    # --- dates: every published string parses; none invented
    for raw, col_ in (("_raw_boardapprovaldate", "board_approval_date"), ("_raw_closingdate", "closing_date"),
                      ("_raw_loan_effective_date", "loan_effective_date"), ("_raw_public_disclosure_date", "public_disclosure_date"),
                      ("_raw_proj_last_upd_date", "project_last_updated_at")):
        report.equals(f"date_parse_failures:{col_}", s.filter(F.col(raw).isNotNull() & F.col(col_).isNull()).count(), 0)
        report.equals(f"date_present_without_source:{col_}", s.filter(F.col(raw).isNull() & F.col(col_).isNotNull()).count(), 0)
    report.equals("fiscal_year_null_iff_raw_zero", s.filter(F.col("approval_fiscal_year").isNull() != (F.col("approval_fiscal_year_raw") == "0")).count(), 0)
    no_appr = sorted(r[0] for r in s.filter("board_approval_date IS NULL").select("project_id").collect())
    report.add("projects_without_board_approval_date", no_appr, ["P120417", "P124073"], no_appr == ["P120417", "P124073"], "warn")
    fy_bad = s.filter("board_approval_date IS NOT NULL AND approval_fiscal_year != year(board_approval_date) + CASE WHEN month(board_approval_date) >= 7 THEN 1 ELSE 0 END").count()
    report.equals("fiscal_year_consistent_with_approval_date (WB FY Jul-Jun)", fy_bad, 0)
    for name, cond in (("closing_before_approval", "closing_date < board_approval_date"), ("effective_before_approval", "loan_effective_date < board_approval_date")):
        rows = s.filter(cond).select("project_id", "board_approval_date", "closing_date", "loan_effective_date").orderBy("project_id").collect()
        report.add(f"{name} (kept as published)", len(rows), "reported only", True, "warn",
                   "; ".join(f"{r.project_id} appr={r.board_approval_date} close={r.closing_date} eff={r.loan_effective_date}" for r in rows[:6]))
    rng = s.agg(*[F.min(c).alias(f"min_{c}") for c in ("board_approval_date", "closing_date", "loan_effective_date")],
                *[F.max(c).alias(f"max_{c}") for c in ("board_approval_date", "closing_date", "loan_effective_date")]).collect()[0].asDict()
    report.add("date_ranges", {k: str(v) for k, v in rng.items()}, "reported", True, "warn")
    # --- amounts: missing vs zero preserved exactly
    for src, col_ in AMOUNTS.items():
        raw_amt = F.col(f"_raw_{src}")
        report.equals(f"amount_parse_failures:{col_}", s.filter(raw_amt.isNotNull() & F.col(col_).isNull()).count(), 0)
        report.equals(f"amount_zero_preserved:{col_}", s.filter(F.col(col_) == 0).count(), s.filter(raw_amt.rlike(r"^0+(\.0+)?$")).count())
    nulls = {v: s.filter(F.col(v).isNull()).count() for v in AMOUNTS.values()}
    zeros = {v: s.filter(F.col(v) == 0).count() for v in AMOUNTS.values()}
    report.add("amount_null_counts (key absent in source)", nulls, "reported", True, "warn")
    report.add("amount_zero_counts (published 0)", zeros, "reported", True, "warn")
    report.equals("negative_amounts", sum(s.filter(F.col(v) < 0).count() for v in AMOUNTS.values()), 0)
    # --- nested arrays: no element lost
    report.equals("financer_elements_preserved", s.agg(F.sum(F.size(F.coalesce("financers", F.array())))).collect()[0][0],
                  s.agg(F.sum(F.size(F.coalesce("_raw_financers", F.array())))).collect()[0][0])
    report.equals("financer_usd_parse_failures", s.select(F.explode("financers").alias("f")).filter("f.amount_usd IS NULL").count(), 0)
    report.equals("isr_elements_preserved", s.agg(F.sum(F.size(F.coalesce("isr_ratings_snapshot", F.array())))).collect()[0][0],
                  s.agg(F.sum(F.size(F.coalesce("_raw_isr", F.array())))).collect()[0][0])
    sec = s.select(F.explode("major_sectors").alias("m")).select(F.explode("m.sectors").alias("x"))
    report.equals("sector_percent_parse_failures", sec.filter("x.sector_percent IS NULL").count(), 0)
    report.add("nested_element_counts", {"financers": s.agg(F.sum(F.size(F.coalesce("financers", F.array())))).collect()[0][0],
                                         "major_sectors": s.agg(F.sum(F.size(F.coalesce("major_sectors", F.array())))).collect()[0][0],
                                         "sectors": sec.count(),
                                         "isr_ratings": s.agg(F.sum(F.size(F.coalesce("isr_ratings_snapshot", F.array())))).collect()[0][0],
                                         "icr_ratings": s.agg(F.sum(F.size(F.coalesce("icr_ratings_snapshot", F.array())))).collect()[0][0],
                                         "ieg_ratings": s.agg(F.sum(F.size(F.coalesce("ieg_ratings_snapshot", F.array())))).collect()[0][0]},
               "reported", True, "warn")
    # --- lineage and provenance
    lineage = ["bronze_raw_record", "raw_record_sha256", "bronze_source_file", "bronze_ingested_at", "source_retrieved_at",
               "bronze_input_file_sha256", "cohort_manifest_sha256", "silver_run_id"]
    report.equals("lineage_nulls", {c: s.filter(F.col(c).isNull()).count() for c in lineage}, {c: 0 for c in lineage})
    report.equals("raw_record_sha256_unique", s.select("raw_record_sha256").distinct().count(), n)
    report.equals("rows_equal_extraction_manifest_records", n, prov["manifest_records"])
    report.equals("single_bronze_source_file", s.select("bronze_source_file").distinct().count(), 1)
