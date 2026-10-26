"""gold_country_context build logic on synthetic Silver-shaped frames over Databricks Connect (no table I/O)."""
from __future__ import annotations

from datetime import date

import pytest

from gold.country_context import transform as T

D = date(2026, 10, 8)
OBS = "country_code string, year int, {code} string, value double, is_value_missing boolean, source_lastupdated date, {extra}"


@pytest.fixture(scope="module")
def spark():
    try:
        from silver.common.session import get_spark
        return get_spark()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Databricks Connect session: {e}")


@pytest.fixture(scope="module")
def out(spark):
    mk = spark.createDataFrame
    wdi = [("GH", 2005, "NY.GDP.MKTP.KD.ZG", 6.2, False, D, "economy"), ("GH", 2005, "FP.CPI.TOTL.ZG", None, True, D, "economy"),
           ("GH", 2005, "NY.GDP.PCAP.CD", 0.0, False, D, "economy"), ("XM", 2005, "NY.GDP.MKTP.KD.ZG", 3.0, False, D, "aggregate")]
    wgi = [("GH", 2005, "CC.EST", -0.1, False, D, "matched", "economy"), ("GH", 2005, "GE.EST", None, True, D, "matched", "economy"),
           ("GH", 2006, "CC.EST", None, True, D, "matched", "economy"), ("", 2005, "CC.EST", 1.0, False, D, "unmatched_blank_source_code", None)]
    cpia = [("GH", 2005, "IQ.CPA.IRAI.XQ", 3.5, False, D, "economy"), ("GH", 2006, "IQ.CPA.IRAI.XQ", None, True, D, "economy"),
            ("SX", 2005, "IQ.CPA.IRAI.XQ", None, True, D, "economy")]
    inc = [("GHA", 2005, "L", "classified", "OGHIST.xlsx", "f" * 64), ("GHA", 2006, "LM", "classified", "OGHIST.xlsx", "f" * 64),
           ("SXM", 2006, None, "unclassified", "OGHIST.xlsx", "f" * 64)]
    dfs = {
        "wdi_country": mk([("GH", "GHA", "Ghana", "economy"), ("SX", "SXM", "Sint Maarten (Dutch part)", "economy"), ("XM", "LIC", "Low income", "aggregate")],
                          "country_code string, country_wb_id string, country_name string, entity_type string"),
        "wdi_indicator_observations": mk(wdi, OBS.format(code="indicator_code", extra="entity_type string")),
        "wgi_estimates": mk(wgi, OBS.format(code="indicator_short_code", extra="wdi_match_status string, wdi_entity_type string")),
        "cpia_ratings": mk(cpia, OBS.format(code="indicator_code", extra="wdi_entity_type string")),
        "income_classification_history": mk(inc, "iso3_code string, fiscal_year int, income_group_code string, classification_status string, "
                                                 "workbook_file_name string, bronze_file_sha256 string"),
    }
    return {(r.country_code, r.year): r for r in T.build(dfs, '{"x":1}', "t").collect()}


def test_grid_economies_only_no_duplicates(out):
    assert len(out) == 2 * 26 and {k[0] for k in out} == {"GH", "SX"}  # aggregate XM excluded


def test_values_as_published_and_null_not_zero(out):
    a = out[("GH", 2005)]
    assert (a.gdp_growth_annual_pct, a.inflation_consumer_prices_annual_pct, a.gdp_per_capita_current_usd, a.wdi_values_present) == (6.2, None, 0.0, 2)
    b = out[("GH", 2010)]
    assert b.gdp_growth_annual_pct is None and b.wdi_values_present == 0


def test_wgi_coverage_statuses(out):
    assert (out[("GH", 2005)].wgi_control_of_corruption_est, out[("GH", 2005)].wgi_values_present, out[("GH", 2005)].wgi_coverage_status) == (-0.1, 1, "published")
    assert out[("GH", 2006)].wgi_coverage_status == "all_values_missing"
    assert out[("GH", 2010)].wgi_coverage_status == "year_not_published"  # no WGI rows for that year anywhere
    assert out[("SX", 2005)].wgi_coverage_status == "economy_not_in_source"  # no name-based or successor match


def test_cpia_coverage_statuses(out):
    assert (out[("GH", 2005)].cpia_irai_overall, out[("GH", 2005)].cpia_coverage_status) == (3.5, "published")
    assert out[("GH", 2006)].cpia_coverage_status == "not_rated_in_year"
    assert out[("SX", 2005)].cpia_coverage_status == "economy_never_rated" and out[("SX", 2005)].cpia_irai_overall is None


def test_income_fiscal_years_explicit(out):
    a = out[("GH", 2005)]
    assert (a.income_fy_ending_in_year, a.income_group_fy_ending_in_year, a.income_fy_starting_in_year, a.income_group_fy_starting_in_year) == (2005, "L", 2006, "LM")
    s = out[("SX", 2005)]
    assert (s.income_group_fy_starting_in_year, s.income_status_fy_starting_in_year, s.income_status_fy_ending_in_year) == (None, "unclassified", "no_row_in_source")


def test_lineage(out):
    a = out[("GH", 2005)]
    assert (a.income_source_workbook, a.context_snapshot_semantics, a.silver_source_versions, a.gold_run_id) == ("OGHIST.xlsx", T.SEMANTICS, '{"x":1}', "t")
