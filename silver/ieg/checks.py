"""Data-quality rules for the IEG ratings Silver table. "error" blocks writes; "warn" is reported.

Known anomalies are reported (and listed by project ID in known_anomalies()), never deleted or changed.
"""
from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.ieg import transform as T


def _cnt(df: DataFrame, cond) -> int:
    return df.filter(cond).count()


def check(ieg: DataFrame, bronze: DataFrame, projects: DataFrame, cohort_ids: set[str], prov: dict, rep: DQReport, expected_rows: int = 750) -> None:
    n = ieg.count()
    # rows and keys
    rep.equals("rows_equal_bronze", n, bronze.count())
    rep.equals("rows_equal_manifest", n, prov["records"])
    rep.equals("rows_equal_expected", n, expected_rows)
    rep.equals("project_id_unique", ieg.select("project_id").distinct().count(), n)
    rep.equals("project_id_null_or_bad_format", _cnt(ieg, F.col("project_id").isNull() | ~F.col("project_id").rlike(r"^P[0-9]{6}$")), 0)
    ids = {r[0] for r in ieg.select("project_id").collect()}
    rep.equals("cohort_membership (cohort manifest)", sorted(ids - cohort_ids)[:10], [])
    rep.equals("project_id_not_in_silver_projects", ieg.join(projects, "project_id", "left_anti").count(), 0)
    rep.equals("bronze_cohort_id_values", [r[0] for r in ieg.select("bronze_cohort_id").distinct().collect()], ["wbg_poc_2000"])
    rep.equals("source_row_sha256_unique", ieg.select("source_row_sha256").distinct().count(), n)
    # dates and fiscal years
    rep.equals("as_of_date_parse_failures", _cnt(ieg, F.col("_raw_as_of").isNotNull() & F.col("source_as_of_date").isNull()), 0)
    rep.equals("as_of_date_values", [str(r[0]) for r in ieg.select("source_as_of_date").distinct().collect()], ["2026-10-18"])
    rep.equals("fiscal_year_parse_failures", {k: _cnt(ieg, F.col(raw).isNotNull() & F.col(col).isNull()) for k, raw, col in
                                              (("evaluation", "_raw_eval_fy", "evaluation_fiscal_year"), ("approval", "_raw_approval_fy", "approval_fiscal_year"),
                                               ("final_closing", "_raw_closing_fy", "final_closing_fiscal_year"))},
               {"evaluation": 0, "approval": 0, "final_closing": 0})
    rep.equals("final_closing_before_approval", _cnt(ieg, F.col("final_closing_fiscal_year") < F.col("approval_fiscal_year")), 0)
    rep.equals("evaluation_before_approval", _cnt(ieg, F.col("evaluation_fiscal_year") < F.col("approval_fiscal_year")), 0)
    rep.equals("evaluation_after_source_as_of_fiscal_year", _cnt(ieg, F.col("evaluation_fiscal_year") > F.year("source_as_of_date") + F.when(F.month("source_as_of_date") >= 7, 1).otherwise(0)), 0)
    rep.equals("rating_available_from_date_populated (no verified availability date in DS00053)", _cnt(ieg, F.col("rating_available_from_date").isNotNull()), 0)
    # vocabularies and score mappings
    rep.equals("rating_vocabulary_violations", {stem: _cnt(ieg, ~F.col(f"_vocab_ok_{stem}")) for stem, _ in T.RATING_FIELDS.values()},
               {stem: 0 for stem, _ in T.RATING_FIELDS.values()})
    for src, (stem, scale) in T.RATING_FIELDS.items():
        r, s = F.col(f"{stem}_rating"), F.col(f"{stem}_score")
        bad = (r.isin(list(scale)) & ~s.eqNullSafe(F.create_map(*[F.lit(x) for kv in scale.items() for x in kv])[r])) | \
              ((r.isNull() | r.isin(T.UNRATED)) & s.isNotNull()) | (s.isNotNull() & ~s.between(1, max(scale.values())))
        rep.equals(f"score_mapping:{stem}", _cnt(ieg, bad), 0)
    rep.equals("outcome_satisfactory_consistent", _cnt(ieg, ~F.col("outcome_satisfactory").eqNullSafe(
        F.when(F.col("outcome_score").isNotNull(), F.col("outcome_score") >= T.SATISFACTORY_MIN_SCORE))), 0)
    rep.equals("evaluation_type_known", sorted(r[0] for r in ieg.select("evaluation_type").distinct().collect() if r[0] not in T.EVALUATION_TYPES), [])
    rep.equals("lending_instrument_known", sorted(str(r[0]) for r in ieg.select("lending_instrument_type").distinct().collect() if r[0] not in T.LENDING_INSTRUMENTS), [])
    unnamed = _cnt(ieg, F.col("evaluation_type_name").isNull())
    rep.add("evaluation_type_without_documented_name (name left NULL, not guessed)", unnamed, 0, unnamed == 0, "warn")
    # NULLs exactly as Bronze blanks
    pairs = {f"{stem}_rating": src for src, (stem, _) in T.RATING_FIELDS.items()} | {
        "project_name_source": "Project Name", "wb_region_name": "WB Region", "country_name_source": "Country / Economy",
        "practice_group": "Practice Group", "global_practice": "Global Practice", "agreement_type": "Agreement Type",
        "final_closing_fiscal_year": "Final Closing FY", "evaluation_fiscal_year": "Evaluation FY", "approval_fiscal_year": "Approval FY"}
    rep.equals("null_counts_equal_bronze", {k: _cnt(ieg, F.col(k).isNull()) for k in pairs}, {k: _cnt(bronze, F.col(f"`{v}`").isNull()) for k, v in pairs.items()})
    rep.equals("source_row_hashes_equal_bronze", ieg.select("source_row_sha256").exceptAll(bronze.select(T.row_hash_expr(T.source_columns(bronze)))).count(), 0)
    # distributions (reported)
    for stem, _ in T.RATING_FIELDS.values():
        rep.add(f"distribution:{stem}", {f"{r[0]}={r[1]}": r[2] for r in ieg.groupBy(f"{stem}_rating", f"{stem}_score").count().orderBy(f"{stem}_score").collect()},
                "reported", True, "warn")
    rep.add("distribution:evaluation_lag_fiscal_years", {str(r[0]): r[1] for r in ieg.groupBy("evaluation_lag_fiscal_years").count().orderBy("evaluation_lag_fiscal_years").collect()},
            "reported", True, "warn")
    # consistency with Silver projects (validation only; reported)
    j = ieg.join(projects.select("project_id", "approval_fiscal_year", "closing_date", "region_name", "country_name", "project_status",
                                 F.element_at("ieg_ratings_snapshot", 1).alias("v3")).withColumnRenamed("approval_fiscal_year", "p_afy"), "project_id")
    close_fy = F.year("closing_date") + F.when(F.month("closing_date") >= 7, 1).otherwise(0)
    rep.add("projects_consistency (validation only)",
            {"approval_fy_differs": _cnt(j, ~F.col("approval_fiscal_year").eqNullSafe(F.col("p_afy"))),
             "final_closing_fy_differs_from_closing_date_fy": _cnt(j, ~F.col("final_closing_fiscal_year").eqNullSafe(close_fy)),
             "region_differs": _cnt(j, F.col("wb_region_name") != F.col("region_name")),
             "country_name_differs": _cnt(j, F.col("country_name_source") != F.col("country_name")),
             "outcome_differs_from_v3_snapshot": _cnt(j, F.col("v3").isNotNull() & (F.lower("outcome_rating") != F.lower("v3.outcome"))),
             "no_v3_snapshot": _cnt(j, F.col("v3").isNull())},
            "reported", True, "warn", "discovery: all 0 except no_v3_snapshot = 1")


def known_anomalies(ieg: DataFrame, projects: DataFrame) -> dict:
    """Project IDs per known anomaly class (reported separately; source records unchanged)."""
    ids = lambda df: sorted(r[0] for r in df.select("project_id").collect())  # noqa: E731
    p = projects.select("project_id", "project_status", "ieg_ratings_snapshot")
    j = ieg.join(p, "project_id")
    rated = ieg.select("project_id")
    return {
        "evaluation_precedes_final_closing": ids(ieg.filter("evaluation_precedes_final_closing")),
        "rated_but_project_active": ids(j.filter("project_status = 'Active'")),
        "cohort_rated_in_v3_snapshot_but_absent_from_ds00053": ids(p.filter(F.size("ieg_ratings_snapshot") > 0).join(rated, "project_id", "left_anti")),
        "rated_but_no_v3_snapshot": ids(j.filter(F.col("ieg_ratings_snapshot").isNull() | (F.size("ieg_ratings_snapshot") == 0))),
        "cohort_projects_without_ds00053_rating_count": p.join(rated, "project_id", "left_anti").count(),
        "cohort_projects_without_ds00053_rating_by_status": {r[0]: r[1] for r in p.join(rated, "project_id", "left_anti").groupBy("project_status").count().collect()},
    }
