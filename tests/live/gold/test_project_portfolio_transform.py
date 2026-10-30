"""gold_project_portfolio build logic on synthetic Silver-shaped frames over Databricks Connect (no table I/O)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from gold.project_portfolio import transform as T

TS = datetime(2026, 10, 21, 1, 40)
P_SCHEMA = ("project_id string, project_name string, region_name string, geography_type string, country_code string, country_name string, "
            "wdi_country_code string, wdi_match_method string, approval_fiscal_year int, board_approval_date date, project_status string, closing_date date, "
            "current_total_commitment_usd decimal(22,2), source_retrieved_at timestamp, raw_record_sha256 string")


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
    dfs = {
        "projects": mk([("P000001", "Roads", "R1", "single_country", "GH", "Ghana", "GH", "exact", 2020, date(2019, 6, 1), "Active", date(2027, 1, 1), Decimal("100.00"), TS, "h1"),
                        ("P000002", "Regional", "R1", "regional_multicountry", "3W", "Western Africa", None, "not_applicable_regional", 2021, date(2020, 6, 1), "Closed",
                         date(2025, 1, 1), Decimal("50.00"), TS, "h2"),
                        ("P000003", "No FY", "R2", "single_country", "KE", "Kenya", "KE", "exact", None, None, "Active", None, None, TS, "h3")], P_SCHEMA),
        "contract_awards": mk([("P000001", Decimal("10.50"), True, 0, date(2021, 1, 1), date(2026, 10, 20)),
                               ("P000001", Decimal("0.25"), False, 1, date(2022, 2, 2), date(2026, 10, 20)),
                               ("P000002", Decimal("7.00"), False, 0, date(2023, 3, 3), date(2026, 10, 20))],
                              "project_id string, contract_amount_usd decimal(32,2), is_multi_supplier boolean, supplier_lines_missing_amount bigint, "
                              "contract_signing_date date, source_as_of_date date"),
        "procurement_notices": mk([("P000001", "Contract Award", date(2021, 5, 1), 1, TS), ("P000001", "Invitation for Bids", date(2020, 5, 1), 2, TS),
                                   ("P000001", "Invitation for Bids", date(2020, 5, 1), 2, TS), ("P000003", "General Procurement Notice", date(2019, 1, 1), 1, TS)],
                                  "project_id string, notice_type string, publication_date date, identical_metadata_group_size bigint, source_retrieved_at timestamp"),
        "procurement_notice_details": mk([("P000001",), ("P000001",)], "project_id string"),
        "ibrd_loans": mk([("P000001", date(2026, 9, 30)), ("P000001", date(2026, 9, 30))], "project_id string, source_end_of_period date"),
        "ida_credits": mk([("P000001", date(2026, 9, 30)), ("P000003", date(2026, 9, 30))], "project_id string, source_end_of_period date"),
        "ieg_project_ratings": mk([("P000002", 2025, "Unsatisfactory", 2, date(2026, 10, 18))],
                                  "project_id string, evaluation_fiscal_year int, outcome_rating string, outcome_score int, source_as_of_date date"),
        "project_details_v3": mk([("P000001", "PE", None, None, 0, ["g"], 1, 0, TS)],
                                 "project_id string, product_line_code string, environmental_social_risk_rating string, results_indicators array<string>, "
                                 "indicator_count int, geo_locations array<string>, geo_location_count int, loan_transaction_count int, source_retrieved_at timestamp"),
        "wdi_country": mk([("GH", "GHA"), ("KE", "KEN")], "country_code string, country_wb_id string"),
        "income_classification_history": mk([("GHA", 2020, "LM", "classified"), ("KEN", 2020, "LM", "classified")],
                                            "iso3_code string, fiscal_year int, income_group_code string, classification_status string"),
    }
    return {r.project_id: r for r in T.build(dfs, '{"x":1}', "t").collect()}


def test_one_row_per_project_despite_multiple_loans_contracts_notices(out):
    assert sorted(out) == ["P000001", "P000002", "P000003"]


def test_contract_measures_at_contract_grain_exact(out):
    a = out["P000001"]
    assert (a.contract_award_count, a.contract_award_amount_usd) == (2, Decimal("10.75"))
    assert (a.multi_supplier_contract_count, a.contracts_with_missing_line_amount_count) == (1, 1)
    assert (a.first_contract_signing_date, a.last_contract_signing_date) == (date(2021, 1, 1), date(2022, 2, 2))
    c = out["P000003"]
    assert c.contract_award_count == 0 and c.contract_award_amount_usd is None and c.first_contract_signing_date is None


def test_notice_counts_by_type_and_zero_from_complete_source(out):
    a = out["P000001"]
    assert (a.notice_count, a.contract_award_notice_count, a.invitation_for_bids_notice_count, a.notices_in_identical_metadata_groups) == (3, 1, 2, 2)
    assert out["P000002"].notice_count == 0 and out["P000002"].invitation_for_bids_notice_count == 0


def test_api_details_null_outside_coverage(out):
    assert out["P000001"].has_api_notice_details is True and out["P000001"].api_notice_detail_count == 2
    assert out["P000003"].has_api_notice_details is False and out["P000003"].api_notice_detail_count is None


def test_financing_counts_separate_and_flag(out):
    assert (out["P000001"].ibrd_loan_count, out["P000001"].ida_credit_count, out["P000001"].has_ibrd_ida_financing) == (2, 1, True)
    assert (out["P000002"].ibrd_loan_count, out["P000002"].ida_credit_count, out["P000002"].has_ibrd_ida_financing) == (0, 0, False)


def test_regional_project_has_no_country_context(out):
    r = out["P000002"]
    assert r.wdi_country_code is None and r.wdi_country_iso3 is None and r.income_group_code_at_approval is None
    assert (r.country_context_status, r.income_group_status_at_approval, r.source_country_code) == ("regional_not_applicable", "regional_not_applicable", "3W")


def test_income_group_point_in_time_and_missing_fy(out):
    assert (out["P000001"].wdi_country_iso3, out["P000001"].income_group_code_at_approval, out["P000001"].income_group_status_at_approval) == ("GHA", "LM", "classified")
    assert out["P000003"].income_group_code_at_approval is None and out["P000003"].income_group_status_at_approval == "approval_fiscal_year_missing"


def test_ieg_null_when_unrated_not_poor(out):
    assert out["P000002"].has_ieg_rating is True and out["P000002"].ieg_outcome_score == 2
    assert out["P000001"].has_ieg_rating is False and out["P000001"].ieg_outcome_rating is None and out["P000001"].ieg_outcome_score is None


def test_details_null_semantics(out):
    a = out["P000001"]
    assert a.has_project_details_v3 is True and a.product_line_code == "PE"
    assert a.results_indicator_count is None and a.geo_location_count == 1 and a.loan_transaction_count is None  # unpublished arrays -> NULL
    b = out["P000002"]
    assert b.has_project_details_v3 is False and b.product_line_code is None and b.geo_location_count is None


def test_snapshot_and_lineage(out):
    a = out["P000001"]
    assert a.portfolio_snapshot_semantics == T.SNAPSHOT_SEMANTICS and a.contract_awards_coverage == T.CONTRACT_COVERAGE
    assert a.financing_source_end_of_period == date(2026, 9, 30) and a.contract_awards_source_as_of_date == date(2026, 10, 20)
    assert (a.projects_raw_record_sha256, a.silver_source_versions, a.gold_run_id, a.transform_version) == ("h1", '{"x":1}', "t", T.TRANSFORM_VERSION)
    assert a.project_status_snapshot == "Active" and a.current_total_commitment_usd_snapshot == Decimal("100.00")


def test_no_prohibited_columns(out):
    from gold.project_portfolio.checks import PROHIBITED_NAME
    assert not [c for c in next(iter(out.values())).asDict() if PROHIBITED_NAME.search(c)]
