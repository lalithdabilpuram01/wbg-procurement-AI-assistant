"""gold_financing_analytics build logic on synthetic Silver-shaped frames over Databricks Connect (no table I/O)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from gold.financing_analytics import transform as T

TS = datetime(2026, 10, 21, 1, 19)
COMMON_SCHEMA = ("financing_number_prefix string, lender string, project_id string, country_code string, country_name string, financing_status string, "
                 "currency_of_commitment string, " + ", ".join(f"{a} decimal(22,2)" for a in T.AMOUNTS) + ", first_repayment_date date, last_repayment_date date, "
                 "agreement_signing_date date, agreement_signing_date_is_placeholder boolean, board_approval_date date, effective_date_most_recent date, "
                 "effective_date_is_placeholder boolean, closed_date_most_recent date, last_disbursement_date date, board_approval_fiscal_year int, "
                 "board_approval_calendar_year int, source_end_of_period date, financing_snapshot_semantics string, source_dataset string, source_row_sha256 string, "
                 "bronze_source_file string, bronze_input_file_sha256 string, source_population_sha256 string, source_retrieved_at timestamp, silver_run_id string, "
                 "borrower_name string")


def common(prefix, lender, pid, cc, cur, amounts, last_disb=date(2026, 6, 1), closed=date(2025, 6, 30), placeholder=False, approval=date(2019, 6, 1)):
    return (prefix, lender, pid, cc, "Ghana", "Repaying", cur, *[Decimal(a) for a in amounts], date(2025, 3, 15), date(2045, 9, 15),
            None if placeholder else date(2019, 8, 20), placeholder, approval, date(2019, 11, 1), False, closed, last_disb, 2019, 2019, date(2026, 9, 30),
            "latest_monthly_snapshot_as_of_end_of_period", "DS00047" if lender == "IBRD" else "DS00001", "h", "/Volumes/x.csv", "a" * 64, "b" * 64, TS, "r",
            "Ministry of Finance, contact@example.org")


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
    base = ["100.00"] + ["0.00"] * 11
    neg = ["100.00", "0.00", "-0.01"] + ["0.00"] * 9
    ib = mk([("IBRD89580", *common("IBRD", "IBRD", "P000001", "GH", None, neg), "FSL", Decimal("0.000000"), "GH")],
            "loan_number string, " + COMMON_SCHEMA + ", loan_type string, interest_rate decimal(12,6), guarantor_country_code string")
    ida = mk([("IDA61410", *common("IDA", "IDA", "P000001", "GH", "XDR", base, last_disb=date(2026, 10, 15)), Decimal("0.750000"), True),
              ("IDAD1234", *common("IDAD", "IDA", "P000002", "3W", "EUR", base, placeholder=True, approval=date(2018, 1, 1)), None, False)],
             "credit_number string, " + COMMON_SCHEMA + ", service_charge_rate decimal(12,6), has_repayment_schedule boolean")
    dfs = {
        "ibrd_loans": ib, "ida_credits": ida,
        "projects": mk([("P000001", "Roads", "R1", "single_country", "GH", date(2019, 5, 1)), ("P000002", "Regional", "R1", "regional_multicountry", None, date(2019, 1, 1))],
                       "project_id string, project_name string, region_name string, geography_type string, wdi_country_code string, board_approval_date date"),
        "wdi_country": mk([("GH", "GHA")], "country_code string, country_wb_id string"),
        "project_loan_transactions": mk([("IBRD89580",), ("IBRD89580",), ("TFA9875",)], "financing_number string"),
        "project_details_v3": mk([("P000001",)], "project_id string"),
    }
    return {r.financing_number: r for r in T.build(dfs, '{"x":1}', "t").collect()}


def test_one_row_per_instrument_with_separate_source_ids(out):
    assert sorted(out) == ["IBRD89580", "IDA61410", "IDAD1234"]  # unmatched transaction reference adds no row
    a, b = out["IBRD89580"], out["IDA61410"]
    assert (a.lender, a.ibrd_loan_number, a.ida_credit_number) == ("IBRD", "IBRD89580", None)
    assert (b.lender, b.ibrd_loan_number, b.ida_credit_number) == ("IDA", None, "IDA61410")


def test_currency_never_assumed_and_terms_by_lender(out):
    a, b = out["IBRD89580"], out["IDA61410"]
    assert (a.currency_of_commitment, a.currency_status, a.interest_rate, a.service_charge_rate, a.has_repayment_schedule) == (
        None, "not_published_by_source", Decimal("0E-6"), None, None)
    assert (b.currency_of_commitment, b.currency_status, b.loan_type, b.service_charge_rate) == ("XDR", "published", None, Decimal("0.750000"))


def test_amounts_exact_with_negatives_flagged(out):
    a = out["IBRD89580"]
    assert a.undisbursed_amount_usd == Decimal("-0.01") and a.original_principal_amount_usd == Decimal("100.00") and a.has_negative_published_amount is True
    assert out["IDA61410"].has_negative_published_amount is False


def test_dates_placeholders_and_anomaly_flags(out):
    c = out["IDAD1234"]
    assert c.agreement_signing_date is None and c.agreement_signing_date_is_placeholder is True and c.approved_before_project_approval is True
    b = out["IDA61410"]
    assert b.last_disbursement_after_end_of_period is True and b.last_disbursement_over_183_days_after_closing is True
    assert out["IBRD89580"].approved_before_project_approval is False


def test_country_and_regional(out):
    a, c = out["IBRD89580"], out["IDAD1234"]
    assert (a.wdi_country_code, a.wdi_country_iso3, a.financing_country_code_status) == ("GH", "GHA", "equal_to_project_wdi_code")
    assert (c.wdi_country_code, c.country_context_status, c.financing_country_code_published, c.financing_country_code_status) == (
        None, "regional_not_applicable", "3W", "regional_project")


def test_transaction_counts_without_multiplication(out):
    assert (out["IBRD89580"].loan_transaction_coverage, out["IBRD89580"].published_loan_transaction_count) == (True, 2)
    assert out["IDA61410"].published_loan_transaction_count == 0
    assert (out["IDAD1234"].loan_transaction_coverage, out["IDAD1234"].published_loan_transaction_count) == (False, None)


def test_no_free_text_names(out):
    cols = set(next(iter(out.values())).asDict())
    assert "borrower_name" not in cols and "guarantor_name" not in cols
    assert "contact@example.org" not in str([r.asDict() for r in out.values()])
