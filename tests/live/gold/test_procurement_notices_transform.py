"""gold_procurement_notices build logic on synthetic Silver-shaped frames over Databricks Connect (no table I/O)."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from gold.procurement_notices import transform as T

TS = datetime(2026, 10, 21, 1, 26)
N_SCHEMA = ("notice_op_id string, notice_id string, project_id string, notice_type string, country_code string, country_name string, region_name_source string, "
            "procurement_category string, procurement_method string, sectors array<string>, publication_date date, publication_fiscal_year int, "
            "publication_calendar_year int, deadline_date date, identical_metadata_group_size bigint, has_api_detail boolean, source_dataset string, "
            "source_retrieved_at timestamp, source_row_sha256 string, bronze_source_file string, bronze_input_file_sha256 string, source_population_sha256 string, "
            "silver_run_id string, bid_description string, notice_url string")


def notice(op, ntype, pub, dl, pid="P000001", cc="GH", api=False, ident=1):
    fy = pub.year + 1 if pub.month >= 7 else pub.year
    return (op, op[2:], pid, ntype, cc, "Ghana", "AFRICA", "Goods", "Request for Bids", ["Health"], pub, fy, pub.year, dl, ident, api, "DS00979", TS, "h" + op,
            "/Volumes/x.csv", "a" * 64, "b" * 64, "r", "SECRET DESCRIPTION Tel 123456789", "https://x/" + op)


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
    d0 = date(2021, 3, 1)
    dfs = {
        "procurement_notices": mk([notice("OP00000001", "Invitation for Bids", d0, date(2021, 3, 31), api=True),
                                   notice("OP00000002", "Invitation for Bids", d0, d0),
                                   notice("OP00000003", "Request for Expression of Interest", d0, date(2021, 2, 1)),
                                   notice("OP00000004", "Contract Award", d0, d0, api=True, ident=2),
                                   notice("OP00000005", "Contract Award", d0, d0, api=True),
                                   notice("OP00000006", "General Procurement Notice", d0, d0, pid="P000002", cc="3W"),
                                   notice("OP00000007", "Invitation for Bids", d0, date(2021, 12, 31)),
                                   notice("OP00000008", "Invitation for Bids", date(2026, 10, 1), date(2026, 11, 1))], N_SCHEMA),
        "projects": mk([("P000001", "Roads", "Western and Central Africa", "single_country", "Ghana", "GH"),
                        ("P000002", "Regional", "Western and Central Africa", "regional_multicountry", "Western Africa", None)],
                       "project_id string, project_name string, region_name string, geography_type string, country_name string, wdi_country_code string"),
        "wdi_country": mk([("GH", "GHA")], "country_code string, country_wb_id string"),
        "income_classification_history": mk([("GHA", 2021, "LM", "classified")], "iso3_code string, fiscal_year int, income_group_code string, classification_status string"),
        "procurement_notice_details": mk([("OP00000001", "P000001", "Invitation for Bids", "Published", None, date(2021, 3, 31), "REF1"),
                                          ("OP00000004", "P000001", "Contract Award", "Revised", 2, None, "REF1"),
                                          ("OP00000005", "P000001", "Contract Award", "Published", 0, None, "REF1")],
                                         "notice_op_id string, project_id string, notice_type string, notice_status string, award_notice_version_no int, "
                                         "submission_deadline_date date, bid_reference_normalized string"),
        "notice_contract_links": mk([("OP00000004", "ambiguous"), ("OP00000004", "unambiguous")], "notice_op_id string, ambiguity_status string"),
    }
    return {r.notice_op_id: r for r in T.build(dfs, '{"x":1}', "t").collect()}


def test_one_row_per_notice_and_no_free_text(out):
    assert len(out) == 8
    cols = set(next(iter(out.values())).asDict())
    assert not {"bid_description", "notice_url", "notice_text"} & cols
    assert "SECRET" not in str([r.asDict() for r in out.values()])


def test_notice_classes_and_flags(out):
    assert (out["OP00000001"].notice_class, out["OP00000001"].is_tender_invitation, out["OP00000001"].is_solicitation_notice) == ("tender_invitation", True, True)
    assert (out["OP00000003"].notice_class, out["OP00000003"].is_tender_invitation, out["OP00000003"].is_solicitation_notice) == ("expression_of_interest", False, True)
    assert (out["OP00000004"].notice_class, out["OP00000004"].is_award_announcement, out["OP00000004"].is_solicitation_notice) == ("contract_award_announcement", True, False)
    assert out["OP00000006"].notice_class == "general_procurement_notice"


def test_deadline_status_and_windows(out):
    assert (out["OP00000001"].deadline_status, out["OP00000001"].published_response_window_days) == ("positive_window", 30)
    assert (out["OP00000002"].deadline_status, out["OP00000002"].published_response_window_days) == ("equals_publication_date_no_window", None)
    assert (out["OP00000003"].deadline_status, out["OP00000003"].published_response_window_days) == ("before_publication_date_invalid", None)
    assert out["OP00000003"].deadline_date_published == date(2021, 2, 1)  # kept as published
    assert (out["OP00000007"].deadline_status, out["OP00000007"].published_response_window_days) == ("positive_window_over_180_days_review", 305)
    assert out["OP00000008"].deadline_after_source_snapshot is True and out["OP00000001"].deadline_after_source_snapshot is False


def test_award_notices_never_get_a_window(out):
    for k in ("OP00000004", "OP00000005", "OP00000006"):
        assert out[k].deadline_status == "not_applicable_notice_type" and out[k].published_response_window_days is None and out[k].deadline_after_source_snapshot is None


def test_api_revision_metadata_and_reference_sharing(out):
    a = out["OP00000004"]
    assert (a.api_notice_status, a.api_award_notice_version_no, a.api_notices_sharing_bid_ref_count, a.api_same_type_notices_sharing_bid_ref_count) == ("Revised", 2, 3, 2)
    assert out["OP00000001"].api_same_type_notices_sharing_bid_ref_count == 1
    assert out["OP00000002"].api_notice_status is None and out["OP00000002"].api_notices_sharing_bid_ref_count is None


def test_link_semantics(out):
    assert (out["OP00000004"].linked_contract_count, out["OP00000004"].contract_link_status) == (2, "ambiguous")
    assert (out["OP00000005"].linked_contract_count, out["OP00000005"].contract_link_status) == (0, "no_link_found")
    assert (out["OP00000001"].linked_contract_count, out["OP00000001"].contract_link_status) == (None, "not_award_notice")


def test_country_and_region(out):
    a, r = out["OP00000001"], out["OP00000006"]
    assert (a.wdi_country_code, a.wdi_country_iso3, a.income_group_code_at_publication_fy, a.notice_country_code_status) == ("GH", "GHA", "LM", "equal_to_project_wdi_code")
    assert (a.region_name, a.notice_region_name_published) == ("Western and Central Africa", "AFRICA")
    assert (r.wdi_country_code, r.income_group_code_at_publication_fy, r.country_context_status, r.notice_country_code_status) == (
        None, None, "regional_not_applicable", "regional_project")
