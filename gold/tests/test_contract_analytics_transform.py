"""gold_contract_analytics build logic on synthetic Silver-shaped frames over Databricks Connect (no table I/O)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from gold.contract_analytics import transform as T

TS = datetime(2026, 10, 21, 1, 13)
A_SCHEMA = ("wb_contract_number string, project_id string, borrower_country_code string, borrower_country_name string, procurement_category string, "
            "procurement_method string, review_type string, global_practices array<string>, contract_signing_date date, fiscal_year int, "
            "contract_signed_calendar_year int, contract_amount_usd decimal(32,2), contract_amount_basis string, supplier_line_count bigint, is_multi_supplier boolean, "
            "supplier_lines_missing_amount bigint, contracts_sharing_reference_in_project bigint, source_dataset string, source_as_of_date date, "
            "source_retrieved_at timestamp, bronze_source_file string, bronze_input_file_sha256 string, source_population_sha256 string, silver_run_id string, "
            "supplier_names array<string>, supplier_ids array<string>, contract_description string, borrower_contract_reference string, line_keys array<string>")


def contract(no, pid, b, amount, lines, signed=date(2021, 3, 1), fy=2021, missing=0):
    return (no, pid, b, "Ghana", "Goods", "Request for Bids", "Post", ["Health"], signed, fy, signed.year, Decimal(amount), "sum_of_supplier_lines", lines,
            lines > 1, missing, 1, "DS00005", date(2026, 10, 20), TS, "/Volumes/x.csv", "a" * 64, "b" * 64, "r", ["SECRET NAME"], ["123"], "secret desc", "REF-1",
            [f"{no}:123"])


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
        "contract_awards": mk([contract("0000001", "P000001", "GH", "10.50", 2), contract("0000002", "P000001", "GH", "5.00", 1, signed=date(2019, 7, 1), fy=2020),
                               contract("0000003", "P000002", "3W", "7.25", 1), contract("0000004", "P000003", None, "1.00", 1)], A_SCHEMA),
        "contract_award_lines": mk([("0000001", "numeric", "GH", "h1"), ("0000001", "anonymized_individual_consultant", "FR", "h2"),
                                    ("0000002", "numeric", "GH", "h3"), ("0000003", "missing", "null", "h4"), ("0000004", "numeric", "GH", "h5")],
                                   "wb_contract_number string, supplier_id_type string, supplier_country_code string, source_row_sha256 string"),
        "projects": mk([("P000001", "Roads", "R1", "single_country", "Ghana", "GH", 2020, date(2019, 9, 1)),
                        ("P000002", "Regional", "R1", "regional_multicountry", "Western Africa", None, 2021, date(2020, 6, 1)),
                        ("P000003", "Somalia", "R2", "single_country", "Somalia", "SO", None, None)],
                       "project_id string, project_name string, region_name string, geography_type string, country_name string, wdi_country_code string, "
                       "approval_fiscal_year int, board_approval_date date"),
        "wdi_country": mk([("GH", "GHA"), ("SO", "SOM")], "country_code string, country_wb_id string"),
        "income_classification_history": mk([("GHA", 2021, "LM", "classified"), ("GHA", 2020, "LM", "classified")],
                                            "iso3_code string, fiscal_year int, income_group_code string, classification_status string"),
        "notice_contract_links": mk([("0000001", "ambiguous", date(2021, 6, 1)), ("0000001", "unambiguous", date(2021, 5, 1))],
                                    "wb_contract_number string, ambiguity_status string, notice_date date"),
        "procurement_notice_details": mk([("P000001",)], "project_id string"),
    }
    return {r.wb_contract_number: r for r in T.build(dfs, '{"x":1}', "t").collect()}


def test_one_row_per_contract_and_exact_amounts(out):
    assert sorted(out) == ["0000001", "0000002", "0000003", "0000004"]
    assert out["0000001"].contract_amount_usd == Decimal("10.50") and out["0000001"].supplier_line_count == 2


def test_no_supplier_identity_or_free_text(out):
    cols = next(iter(out.values())).asDict()
    assert not {"supplier_names", "supplier_ids", "contract_description", "borrower_contract_reference", "line_keys"} & set(cols)
    assert "SECRET" not in str([r.asDict() for r in out.values()]) and "secret desc" not in str([r.asDict() for r in out.values()])


def test_country_from_validated_project_code_only(out):
    a, r, s = out["0000001"], out["0000003"], out["0000004"]
    assert (a.wdi_country_code, a.wdi_country_iso3, a.borrower_country_code_status) == ("GH", "GHA", "equal_to_project_wdi_code")
    assert (r.wdi_country_code, r.income_group_code_at_signing_fy, r.borrower_country_code_status, r.borrower_country_code_published) == (None, None, "regional_project", "3W")
    assert (s.wdi_country_code, s.borrower_country_code_status, s.income_group_status_at_signing_fy) == ("SO", "missing_in_source", "no_row_in_source")


def test_income_group_at_signing_fiscal_year(out):
    assert (out["0000001"].income_group_code_at_signing_fy, out["0000002"].income_group_code_at_signing_fy) == ("LM", "LM")
    assert out["0000003"].income_group_status_at_signing_fy == "regional_not_applicable"


def test_supplier_aggregates_and_relation(out):
    a = out["0000001"]
    assert (a.has_anonymized_individual_consultant, a.has_supplier_with_numeric_id, a.has_supplier_with_missing_id) == (True, True, False)
    assert a.supplier_country_codes_published == ["FR", "GH"] and a.supplier_country_relation == "includes_other_country"
    assert out["0000002"].supplier_country_relation == "all_suppliers_same_country"
    r = out["0000003"]
    assert r.supplier_lines_missing_country == 1 and r.supplier_country_codes_published == [] and r.supplier_country_relation == "undetermined"


def test_approval_lag_and_signed_before_approval(out):
    assert out["0000002"].days_from_board_approval_to_signing == -62 and out["0000002"].signed_before_board_approval is True
    assert out["0000004"].days_from_board_approval_to_signing is None and out["0000004"].signed_before_board_approval is None


def test_link_semantics(out):
    a = out["0000001"]
    assert (a.award_notice_link_coverage, a.linked_award_notice_count, a.award_notice_link_status, a.earliest_linked_award_notice_date) == (
        True, 2, "ambiguous", date(2021, 5, 1))
    b = out["0000002"]
    assert (b.award_notice_link_coverage, b.linked_award_notice_count, b.award_notice_link_status) == (True, 0, "no_link_found")
    c = out["0000003"]
    assert (c.award_notice_link_coverage, c.linked_award_notice_count, c.award_notice_link_status, c.earliest_linked_award_notice_date) == (
        False, None, "not_covered", None)


def test_lineage(out):
    a = out["0000001"]
    import hashlib
    assert a.source_rows_sha256 == hashlib.sha256(b"h1,h2").hexdigest()
    assert (a.contract_snapshot_semantics, a.silver_source_versions, a.gold_run_id, a.transform_version) == (T.SNAPSHOT_SEMANTICS, '{"x":1}', "t", T.TRANSFORM_VERSION)
