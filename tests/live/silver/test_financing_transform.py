"""IBRD/IDA financing transform logic on synthetic Bronze-shaped rows over Databricks Connect (no table I/O)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from silver.financing import transform as T

PROV = {"bronze_input_file_sha256": "a" * 64, "records": 3, "source_population_sha256": "b" * 64,
        "source_retrieved_at": "2026-10-21T01:19:56+00:00", "source_url_note": "x", "source_population_records": 3}
COMMON = ["End of Period", "Region", "Country / Economy Code", "Country / Economy", "Borrower", "Currency of Commitment", "Project ID", "Project Name",
          "Original Principal Amount (US$)", "Cancelled Amount (US$)", "Undisbursed Amount (US$)", "Disbursed Amount (US$)", "Exchange Adjustment (US$)",
          "Sold 3rd Party (US$)", "Repaid 3rd Party (US$)", "Due 3rd Party (US$)", "First Repayment Date", "Last Repayment Date", "Agreement Signing Date",
          "Board Approval Date", "Effective Date (Most Recent)", "Closed Date (Most Recent)", "Last Disbursement Date", "Board approval - Fiscal year",
          "Board approval - Calendar year"]
IBRD = COMMON + ["Loan Number", "Guarantor Country / Economy Code", "Guarantor", "Loan Type", "Loan Status", "Interest Rate", "Repaid to IBRD (US$)",
                 "Due to IBRD (US$)", "Borrower's Obligation (US$)", "Loans Held (US$)"]
IDA = COMMON + ["Credit Number", "Credit Status", "Service Charge Rate", "Repaid to IDA (US$)", "Due to IDA (US$)", "Borrowers Obligation (US$)", "Credits Held (US$)"]


@pytest.fixture(scope="module")
def spark():
    try:
        from silver.common.session import get_spark
        return get_spark()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Databricks Connect session: {e}")


def base(**kw) -> dict:
    v = {"End of Period": "09/30/2026", "Region": "R", "Country / Economy Code": "GH", "Country / Economy": "Ghana", "Borrower": "Ministry", "Currency of Commitment": None,
         "Project ID": "P000001", "Project Name": "Synthetic", "Original Principal Amount (US$)": "100000000.0", "Cancelled Amount (US$)": "0.0",
         "Undisbursed Amount (US$)": "-0.01", "Disbursed Amount (US$)": "99999999.99", "Exchange Adjustment (US$)": "-5473.8", "Sold 3rd Party (US$)": "0.0",
         "Repaid 3rd Party (US$)": "0.0", "Due 3rd Party (US$)": "0.0", "First Repayment Date": "03/15/2025", "Last Repayment Date": "09/15/2045",
         "Agreement Signing Date": "08/20/2019", "Board Approval Date": "06/27/2019", "Effective Date (Most Recent)": "11/01/2019",
         "Closed Date (Most Recent)": "12/31/2025", "Last Disbursement Date": "04/30/2026", "Board approval - Fiscal year": "2019",
         "Board approval - Calendar year": "2019"}
    v.update(kw)
    return v


def frame(spark, cols, rows):
    schema = ", ".join(f"`{c}` string" for c in cols) + ", source_file string, cohort_id string, ingested_at timestamp"
    return spark.createDataFrame([tuple(r.get(c) for c in cols) + ("/Volumes/x/f.csv", "wbg_poc_2000", datetime(2026, 10, 22)) for r in rows], schema)


@pytest.fixture(scope="module")
def ibrd(spark):
    rows = [base(**{"Loan Number": "IBRD89580", "Guarantor Country / Economy Code": "GH", "Guarantor": "Ghana", "Loan Type": "FSL", "Loan Status": "Repaying",
                    "Interest Rate": "0.0", "Repaid to IBRD (US$)": "20366308.5", "Due to IBRD (US$)": "-3064753.96", "Borrower's Obligation (US$)": "1.0",
                    "Loans Held (US$)": "-3064753.96"}),
            base(**{"Loan Number": "IBRDK1234", "Guarantor Country / Economy Code": "GH", "Guarantor": "Ghana", "Loan Type": "SCL", "Loan Status": "Approved",
                    "Interest Rate": "3.65", "Repaid to IBRD (US$)": "0.0", "Due to IBRD (US$)": "0.0", "Borrower's Obligation (US$)": "0.0",
                    "Loans Held (US$)": "0.0", "Agreement Signing Date": "01/01/1900", "Effective Date (Most Recent)": "01/01/1900",
                    "Last Disbursement Date": None, "Board Approval Date": "09/10/2026", "Board approval - Fiscal year": "2027",
                    "Board approval - Calendar year": "2026"})]
    return {r.loan_number: r for r in T.build(frame(spark, IBRD, rows), "ibrd", PROV, "t").collect()}


@pytest.fixture(scope="module")
def ida(spark):
    rows = [base(**{"Credit Number": "IDA61410", "Credit Status": "Repaying", "Service Charge Rate": "0.75", "Currency of Commitment": "XDR",
                    "Repaid to IDA (US$)": "5638547.25", "Due to IDA (US$)": "1.0", "Borrowers Obligation (US$)": "1.0", "Credits Held (US$)": "1.0"}),
            base(**{"Credit Number": "IDAD1234", "Credit Status": "Disbursing", "Service Charge Rate": None, "Currency of Commitment": "XDR",
                    "Repaid to IDA (US$)": "0.0", "Due to IDA (US$)": "0.0", "Borrowers Obligation (US$)": "0.0", "Credits Held (US$)": "0.0",
                    "First Repayment Date": None, "Last Repayment Date": None}),
            base(**{"Credit Number": "IDA70001", "Credit Status": "Repaying", "Service Charge Rate": "1.06123", "Currency of Commitment": "EUR",
                    "Repaid to IDA (US$)": "0.0", "Due to IDA (US$)": "0.0", "Borrowers Obligation (US$)": "0.0", "Credits Held (US$)": "0.0"})]
    return {r.credit_number: r for r in T.build(frame(spark, IDA, rows), "ida", PROV, "t").collect()}


def test_amounts_exact_with_signs_and_no_currency_assumed(ibrd):
    a = ibrd["IBRD89580"]
    assert a.undisbursed_amount_usd == Decimal("-0.01") and a.due_to_lender_usd == Decimal("-3064753.96") and a.held_amount_usd == Decimal("-3064753.96")
    assert a.exchange_adjustment_usd == Decimal("-5473.80") and a.disbursed_amount_usd == Decimal("99999999.99")
    assert a.currency_of_commitment is None and a.lender == "IBRD" and a.interest_rate == Decimal("0.000000")


def test_placeholder_dates_null_with_flags(ibrd):
    b = ibrd["IBRDK1234"]
    assert b.agreement_signing_date is None and b.agreement_signing_date_is_placeholder is True
    assert b.effective_date_most_recent is None and b.effective_date_is_placeholder is True
    assert b.last_disbursement_date is None and b.financing_number_prefix == "IBRDK" and b.interest_rate == Decimal("3.650000")
    a = ibrd["IBRD89580"]
    assert a.agreement_signing_date == date(2019, 8, 20) and a.agreement_signing_date_is_placeholder is False
    assert (b.board_approval_fiscal_year, b.board_approval_calendar_year) == (2027, 2026)


def test_ida_prefix_and_repayment_schedule_not_grant_classification(ida):
    assert ida["IDAD1234"].financing_number_prefix == "IDAD" and ida["IDAD1234"].has_repayment_schedule is False
    assert ida["IDA61410"].has_repayment_schedule is True and ida["IDA61410"].financing_number_prefix == "IDA"
    assert ida["IDAD1234"].service_charge_rate is None and ida["IDA70001"].service_charge_rate == Decimal("1.061230")
    assert ida["IDA70001"].currency_of_commitment == "EUR" and not any("grant" in c for c in ida["IDAD1234"].asDict())


def test_snapshot_and_lineage(ida, ibrd):
    r = ida["IDA61410"]
    assert r.source_end_of_period == date(2026, 9, 30) and r.financing_snapshot_semantics == "latest_monthly_snapshot_as_of_end_of_period"
    assert r.source_dataset == "DS00001" and ibrd["IBRD89580"].source_dataset == "DS00047"
    assert len({x.source_row_sha256 for x in ida.values()}) == 3


def test_constraint_fields_are_valid_published_negatives_excluded():
    assert "undisbursed_amount_usd" not in T.NON_NEGATIVE and "due_to_lender_usd" not in T.NON_NEGATIVE and "held_amount_usd" not in T.NON_NEGATIVE
    assert "exchange_adjustment_usd" not in T.NON_NEGATIVE and "borrower_obligation_usd" not in T.NON_NEGATIVE
    assert "original_principal_amount_usd" in T.NON_NEGATIVE and "disbursed_amount_usd" in T.NON_NEGATIVE
