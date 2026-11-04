"""Blocking and reported checks for gold_country_context (Spark side)."""
from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from gold.country_context import transform as T
from silver.common.dq import DQReport

ROWS = 217 * 26


def _n(df: DataFrame, cond) -> int:
    return df.filter(cond).count()


def check(g: DataFrame, dfs: dict[str, DataFrame], gold: dict[str, DataFrame], rep: DQReport) -> dict:
    """Run every data-quality rule for gold_country_context and add the results to `rep`; returns summary figures."""
    wc, w, gi, cp, ic = (dfs[k] for k in ("wdi_country", "wdi_indicator_observations", "wgi_estimates", "cpia_ratings", "income_classification_history"))
    rows = g.count()
    rep.equals("rows_217_economies_x_26_years", (rows, g.select("country_code", "year").distinct().count()), (ROWS, ROWS))
    eco = {r[0] for r in wc.filter("entity_type = 'economy'").select("country_code").collect()}
    got = {r[0] for r in g.select("country_code").distinct().collect()}
    rep.equals("economies_equal_wdi_economies_no_aggregates", (len(got), sorted(got ^ eco)[:5]), (217, []))
    rep.equals("years_2000_2025", g.agg(F.min("year"), F.max("year"), F.countDistinct("year")).collect()[0][:], (2000, 2025, 26))
    for name, df, code, mapping in (("wdi", w.filter("entity_type = 'economy'"), "indicator_code", T.WDI),
                                    ("wgi", gi.filter("wdi_match_status = 'matched' AND wdi_entity_type = 'economy'"), "indicator_short_code", T.WGI),
                                    ("cpia", cp.filter("wdi_entity_type = 'economy'"), "indicator_code", T.CPIA)):
        y = df.filter(F.col("year").between(*T.YEARS))
        rep.equals(f"{name}:source_key_unique_in_window", y.count(), y.select("country_code", code, "year").distinct().count())
        src = {r[0]: r[1] for r in y.filter("NOT is_value_missing").groupBy(code).count().collect()}
        rep.equals(f"{name}:non_null_values_equal_silver_per_indicator", {k: _n(g, f"{v} IS NOT NULL") for k, v in mapping.items()}, {k: src.get(k, 0) for k in mapping})
        rep.equals(f"{name}:source_economies_outside_grid", y.join(g.select("country_code").distinct(), "country_code", "left_anti").count(), 0)
        cells = y.filter("NOT is_value_missing").select("country_code", "year", code, "value").join(g, ["country_code", "year"])
        pick = F.coalesce(*[F.when(F.col(code) == k, F.col(v)) for k, v in mapping.items()])
        rep.equals(f"{name}:every_published_value_copied_exactly", _n(cells, ~(pick == F.col("value"))), 0)
    rep.add("wgi:coverage_status (reported)", {r[0]: r[1] for r in g.groupBy("wgi_coverage_status").count().collect()}, "reported", True, "warn")
    rep.equals("wgi:year_2001_not_published_for_covered_economies", _n(g, "year = 2001 AND wgi_coverage_status NOT IN ('year_not_published', 'economy_not_in_source')"), 0)
    rep.equals("wgi:economies_not_in_source", g.filter("wgi_coverage_status = 'economy_not_in_source'").select("country_code").distinct().count(), 10)
    rep.add("cpia:coverage_status (reported)", {r[0]: r[1] for r in g.groupBy("cpia_coverage_status").count().collect()}, "reported", True, "warn")
    rep.equals("cpia:values_within_1_to_6", _n(g, " OR ".join(f"({c} < 1 OR {c} > 6)" for c in T.CPIA.values())), 0)
    rep.equals("missing:never_zero_filled", _n(g, "(wdi_values_present = 0 AND (gdp_growth_annual_pct IS NOT NULL OR gdp_per_capita_current_usd IS NOT NULL)) "
                                                  "OR (wgi_coverage_status <> 'published' AND wgi_values_present > 0)"), 0)

    end = ic.select(F.col("iso3_code").alias("country_iso3"), F.col("fiscal_year").alias("year"), F.col("income_group_code").alias("e"))
    rep.equals("income:fy_ending_in_year_equals_silver", _n(g.join(end, ["country_iso3", "year"], "left"), "NOT (income_group_fy_ending_in_year <=> e)"), 0)
    start = ic.select(F.col("iso3_code").alias("country_iso3"), (F.col("fiscal_year") - 1).alias("year"), F.col("income_group_code").alias("s"))
    rep.equals("income:fy_starting_in_year_equals_silver", _n(g.join(start, ["country_iso3", "year"], "left"), "NOT (income_group_fy_starting_in_year <=> s)"), 0)
    rep.equals("income:fiscal_year_numbers_explicit", _n(g, "income_fy_ending_in_year <> year OR income_fy_starting_in_year <> year + 1"), 0)
    rep.add("income:status (reported)", {"ending": {r[0]: r[1] for r in g.groupBy("income_status_fy_ending_in_year").count().collect()},
                                         "starting": {r[0]: r[1] for r in g.groupBy("income_status_fy_starting_in_year").count().collect()}}, "reported", True, "warn")
    rep.equals("income:no_iso3_without_rows_for_wdi_economies", _n(g, "income_status_fy_ending_in_year = 'no_row_in_source'"), 0)

    p = gold["gold_project_portfolio"].filter("geography_type = 'single_country' AND approval_fiscal_year IS NOT NULL")
    pj = p.join(g, (p.wdi_country_code == g.country_code) & (p.approval_fiscal_year - 2 == g.year), "left")
    rep.equals("join:projects_by_validated_code_and_approval_fy_minus_2_one_to_one", (pj.count(), _n(pj, g.country_code.isNull())), (p.count(), 0))
    rp = gold["gold_project_portfolio"].filter("geography_type <> 'single_country'")
    rep.equals("join:regional_projects_have_no_country_code_to_join", _n(rp, "wdi_country_code IS NOT NULL"), 0)
    for t, ycol in (("gold_contract_analytics", "contract_signed_calendar_year"), ("gold_procurement_notices", "publication_calendar_year")):
        x = gold[t].filter("wdi_country_code IS NOT NULL").select("wdi_country_code", ycol)
        joined = x.join(g, (x.wdi_country_code == g.country_code) & (x[ycol] == g.year), "left")
        rep.equals(f"join:{t}_by_code_and_calendar_year_no_multiplication", joined.count(), x.count())
        rep.add(f"join:{t}_rows_with_year_outside_grid (reported)", _n(joined, g.country_code.isNull()), "reported", True, "warn")
    return {"rows": rows}
