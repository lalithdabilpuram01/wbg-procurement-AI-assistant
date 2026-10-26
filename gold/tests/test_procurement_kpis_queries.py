"""Proposed KPI view SQL on synthetic Gold-shaped frames over Databricks Connect (no table I/O; inputs bound as DataFrames)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from gold.procurement_kpis import kpi_queries as Q

TS = datetime(2026, 10, 21, 1, 0)


@pytest.fixture(scope="module")
def spark():
    try:
        from silver.common.session import get_spark
        return get_spark()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Databricks Connect session: {e}")


@pytest.fixture(scope="module")
def frames(spark):
    mk = spark.createDataFrame
    pf = mk([("P1", "single_country", "GH", True, True, False, TS, "g1"), ("P2", "regional_multicountry", None, False, False, True, TS, "g1")],
            "project_id string, geography_type string, wdi_country_code string, has_api_notice_details boolean, has_project_details_v3 boolean, "
            "has_ieg_rating boolean, projects_source_retrieved_at timestamp, gold_run_id string")
    ca = mk([("C1", "P1", 2021, "R", "single_country", "GH", "Ghana", "Goods", "Request for Bids", Decimal("10.00"), "Prior", True, False,
              "includes_other_country", False, date(2021, 1, 1), date(2026, 10, 20), "g2"),
             ("C2", "P1", 2021, "R", "single_country", "GH", "Ghana", "Goods", "Request for Bids", Decimal("30.50"), None, False, False, "undetermined", True,
              date(2021, 2, 1), date(2026, 10, 20), "g2"),
             ("C3", "P2", 2022, "R", "regional_multicountry", None, "Western Africa", "Works", "Direct Selection", Decimal("5.25"), "Post", False, True,
              "undetermined", False, date(2022, 1, 1), date(2026, 10, 20), "g2")],
            "wb_contract_number string, project_id string, contract_fiscal_year int, region_name string, geography_type string, wdi_country_code string, "
            "project_country_name string, procurement_category string, procurement_method string, contract_amount_usd decimal(32,2), review_type string, "
            "is_multi_supplier boolean, has_anonymized_individual_consultant boolean, supplier_country_relation string, signed_before_board_approval boolean, "
            "contract_signing_date date, source_as_of_date date, gold_run_id string")
    n = lambda ntype, cls, sol, ifb, status, w, pid="P1", geo="single_country", cc="GH": (pid, 2021, ntype, cls, "R", geo, cc, "Ghana", 1, False, sol, ifb,  # noqa: E731
                                                                                          "Request for Bids", status, w, TS, "g3")
    pn = mk([n("Invitation for Bids", "tender_invitation", True, True, "positive_window", 10),
             n("Invitation for Bids", "tender_invitation", True, True, "positive_window", 30),
             n("Invitation for Bids", "tender_invitation", True, True, "positive_window_over_180_days_review", 400),
             n("Invitation for Bids", "tender_invitation", True, True, "equals_publication_date_no_window", None),
             n("Contract Award", "contract_award_announcement", False, False, "not_applicable_notice_type", None),
             n("General Procurement Notice", "general_procurement_notice", False, False, "not_applicable_notice_type", None, "P2", "regional_multicountry", None)],
            "project_id string, publication_fiscal_year int, notice_type string, notice_class string, region_name string, geography_type string, "
            "wdi_country_code string, project_country_name string, identical_metadata_group_size bigint, has_api_detail boolean, is_solicitation_notice boolean, "
            "is_tender_invitation boolean, procurement_method string, deadline_status string, published_response_window_days int, source_retrieved_at timestamp, "
            "gold_run_id string")
    fa = mk([("P1", "IBRD", 2020, "R", "single_country", "GH", "Repaying", False, True, True, date(2026, 9, 30), "g4"),
             ("P2", "IDA", 2021, "R", "regional_multicountry", None, "Disbursing", True, False, False, date(2026, 9, 30), "g4")],
            "project_id string, lender string, board_approval_fiscal_year int, region_name string, geography_type string, wdi_country_code string, "
            "financing_status string, approved_before_project_approval boolean, has_negative_published_amount boolean, loan_transaction_coverage boolean, "
            "source_end_of_period date, gold_run_id string")
    cc = mk([("GH", "GHA", "Ghana", 2025, 2026, "LM", "g5"), ("GH", "GHA", "Ghana", 2024, 2025, "LM", "g5")],
            "country_code string, country_iso3 string, country_name string, year int, income_fy_starting_in_year int, income_group_fy_starting_in_year string, "
            "gold_run_id string")
    return {"pf": pf, "ca": ca, "pn": pn, "fa": fa, "cc": cc}


def run(spark, frames, name):
    return [r.asDict() for r in spark.sql(Q.QUERIES[name], **frames).collect()]


def test_overview_totals_exact_and_separate(spark, frames):
    o = run(spark, frames, "vw_kpi_overview")[0]
    assert (o["total_projects"], o["contract_awards"], o["total_contract_award_value_usd"], o["procurement_notices"]) == (2, 3, Decimal("45.75"), 6)
    assert (o["ibrd_loan_instruments"], o["ida_credit_instruments"], o["tender_invitation_notices"]) == (1, 1, 4)
    assert not [k for k in o if "principal" in k or "disbursed" in k]  # financing amounts never in KPIs


def test_contract_awards_partitions_and_regional_country_null(spark, frames):
    rows = run(spark, frames, "vw_kpi_contract_awards")
    gh = next(r for r in rows if r["wdi_country_code"] == "GH")
    assert (gh["contract_awards"], gh["contract_award_value_usd"], gh["prior_review_contracts"], gh["review_type_unknown_contracts"]) == (2, Decimal("40.50"), 1, 1)
    assert (gh["other_country_supplier_contracts"], gh["supplier_country_undetermined_contracts"], gh["contracts_signed_before_board_approval"]) == (1, 1, 1)
    reg = next(r for r in rows if r["geography_type"] != "single_country")
    assert reg["wdi_country_code"] is None and reg["country_name"] is None


def test_value_distribution_discrete_percentiles(spark, frames):
    rows = {r["grouping_level"]: r for r in run(spark, frames, "vw_kpi_contract_value_distribution") if r["grouping_level"] == "all_contracts"}
    a = rows["all_contracts"]
    assert (a["contract_awards"], a["min_contract_value_usd"], a["p50_contract_value_usd"], a["max_contract_value_usd"]) == (3, Decimal("5.25"), Decimal("10.00"), Decimal("30.50"))


def test_response_windows_eligible_only_and_review_excluded_from_stats(spark, frames):
    rows = [r for r in run(spark, frames, "vw_kpi_response_windows") if r["grouping_level"] == "type"]
    assert [r["notice_type"] for r in rows] == ["Invitation for Bids"]  # award notices and GPNs never appear
    r = rows[0]
    assert (r["eligible_solicitation_notices"], r["positive_window_notices"], r["over_180_day_review_notices"], r["no_window_notices"]) == (4, 2, 1, 1)
    assert (r["min_window_days"], r["p50_window_days"], r["max_window_days"]) == (10, 10, 30)  # 400-day review notice excluded


def test_financing_counts_only(spark, frames):
    rows = run(spark, frames, "vw_kpi_financing_instruments")
    assert sorted((r["lender"], r["financing_instruments"]) for r in rows) == [("IBRD", 1), ("IDA", 1)]
    assert not [k for k in rows[0] if "usd" in k]


def test_country_comparison_no_multiplication(spark, frames):
    rows = {r["geography_type"]: r for r in run(spark, frames, "vw_kpi_country_comparison")}
    gh, reg = rows["single_country"], rows["regional_multicountry"]
    assert (gh["projects"], gh["contract_awards"], gh["contract_award_value_usd"], gh["procurement_notices"], gh["ibrd_loan_instruments"]) == (1, 2, Decimal("40.50"), 5, 1)
    assert (gh["country_name"], gh["latest_income_fiscal_year"], gh["latest_income_group"], gh["tender_invitations_with_positive_window"]) == ("Ghana", 2026, "LM", 2)
    assert (reg["wdi_country_code"], reg["country_name"], reg["contract_awards"], reg["ida_credit_instruments"]) == (None, None, 1, 1)


def test_view_ddl_is_create_only_and_not_executed():
    ddl = Q.view_ddl("vw_kpi_overview")
    assert ddl.startswith("CREATE VIEW workspace.wbg_gold.vw_kpi_overview") and "OR REPLACE" not in ddl and "{pf}" not in ddl
