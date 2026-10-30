"""project_details_v3 / project_loan_transactions transform logic on synthetic Bronze-shaped records over Databricks Connect (no table I/O)."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from silver.project_details import transform as T

PROV = {"bronze_input_file_sha256": "a" * 64, "records": 2, "source_population_sha256": "b" * 64,
        "source_retrieved_at": "2026-10-21T01:40:31+00:00", "source_population_records": 2, "extraction_method": "x"}
M = lambda v, d: {"Value": v, "Date": d, "Comment": None}  # noqa: E731
FULL = {
    "id": "P000001", "prodline_exact": "PE", "esrc_ovrl_risk_rate": "S", "parentprojid": "P000009", "p2a_flag": "Y", "p2a_updated_date": "2024-05-06",
    "teamleadname": ["Synthetic Person"], "teamleaderupi": "00000000001", "project_name": "Synthetic",
    "themev2_level2_exact": ["Gender", "Jobs"], "themev2_level3_exact": ["Job Creation"],
    "theme_list": [{"code": "60", "name": "Social", "seqno": "2", "percent": "40", "theme2": [
        {"code": "62", "name": "Jobs", "seqno": "1", "percent": "20", "theme3": [{"code": "621", "name": "Job Creation", "seqno": "1", "percent": "20"}]},
        {"code": "61", "name": "Gender", "seqno": "0", "percent": "x"}]},
        {"code": "10", "name": "Economic", "seqno": "1", "theme2": []}],
    "indicators": [{"Name": "People reached", "Type": "PDO", "Subtype": None, "Baseline": M("0.00", "2020-01-31T00:00:00Z"),
                    "Current": M(None, None), "Target": M("1000.00", "2026-12-31T00:00:00Z")},
                   {"Name": "People reached", "Type": "PDO", "Subtype": "Breakdown", "Baseline": M("1", None), "Current": M("2", None), "Target": M("3", None)}],
    "geo_locations": [{"geo_loc_id": "G1", "place_id": None, "wbg_cntry_key": "6000001", "geo_loc_name": "Town", "geo_latitude_nbr": "5.1234567",
                       "geo_longitude_nbr": "-0.1", "admin_unit1_name": None, "admin_unit2_name": None},
                      {"geo_loc_id": "G1", "place_id": "2", "wbg_cntry_key": None, "geo_loc_name": None, "geo_latitude_nbr": "0",
                       "geo_longitude_nbr": "0", "admin_unit1_name": "Region", "admin_unit2_name": None}],
    "loan_transactions": [
        {"amount": "1.5E7", "period": "2019-06-27T00:00:00.000Z", "transactiontype": "LOAN_COMMITMENT", "financier_loan": "IDA-61410"},
        {"amount": "-2500.25", "period": "2021-01-15T00:00:00.000Z", "transactiontype": "LOAN_DISB", "financier_loan": "IDA-61410"},
        {"amount": "100", "period": "1900-01-01T00:00:00.000Z", "transactiontype": "LOAN_CANCEL", "financier_loan": "TF-A9875"},
        {"amount": "100", "period": "2022-03-01T00:00:00.000Z", "transactiontype": "FEE_CHARGES", "financier_loan": "IBRD-99510"},
        {"amount": "100", "period": "2022-03-01T00:00:00.000Z", "transactiontype": "FEE_CHARGES", "financier_loan": "IBRD-99510"},
        {"amount": "12,5", "period": "2022-03-02T00:00:00.000Z", "transactiontype": "INT_CHARGES", "financier_loan": "IBRD-99510"}],
}
MINIMAL = {"id": "P000002", "prodline_exact": "RE", "teamleadname": ["Another Person"], "teamleaderupi": "00000000002",
           "themev2_level2_exact": [], "theme_list": []}


@pytest.fixture(scope="module")
def spark():
    try:
        from silver.common.session import get_spark
        return get_spark()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Databricks Connect session: {e}")


@pytest.fixture(scope="module")
def bronze(spark):
    rows = [(json.dumps(r), "/Volumes/x/f.jsonl", "wbg_poc_2000", datetime(2026, 10, 22)) for r in (FULL, MINIMAL)]
    return spark.createDataFrame(rows, "raw_record string, source_file string, cohort_id string, ingested_at timestamp")


@pytest.fixture(scope="module")
def details(bronze):
    return {r.project_id: r for r in T.drop_validation(T.build_details(bronze, PROV, "t")).collect()}


@pytest.fixture(scope="module")
def tx(bronze):
    return {r.transaction_key: r for r in T.drop_validation(T.build_transactions(bronze, PROV, "t")).collect()}


def test_no_personal_or_raw_columns(bronze):
    cols = T.drop_validation(T.build_details(bronze, PROV, "t")).columns + T.drop_validation(T.build_transactions(bronze, PROV, "t")).columns
    assert not [c for c in cols if "team" in c or "upi" in c or "raw_record" == c or c.startswith("_")]


def test_personal_values_absent_from_every_output_value(bronze):
    for df in (T.drop_validation(T.build_details(bronze, PROV, "t")), T.drop_validation(T.build_transactions(bronze, PROV, "t"))):
        for row in df.collect():
            text = json.dumps(row.asDict(recursive=True), default=str)
            assert "Person" not in text and "0000000000" not in text


def test_scalar_fields_and_dates(details):
    d = details["P000001"]
    assert (d.product_line_code, d.environmental_social_risk_rating, d.parent_project_id, d.p2a_flag) == ("PE", "S", "P000009", "Y")
    assert d.p2a_updated_date == date(2024, 5, 6) and d.themes_level2 == ["Gender", "Jobs"] and d.themes_level3 == ["Job Creation"]


def test_theme_hierarchy_order_and_integers(details):
    t = details["P000001"].asDict(recursive=True)["themes"]
    assert [x["code"] for x in t] == ["60", "10"]  # source order, not seqno order
    assert t[0]["seqno"] == 2 and t[0]["percent"] == 40 and t[1]["percent"] is None and t[1]["level2"] == []
    assert [x["code"] for x in t[0]["level2"]] == ["62", "61"]
    assert t[0]["level2"][0]["level3"] == [{"code": "621", "name": "Job Creation", "seqno": 1, "percent": 20}]
    assert t[0]["level2"][1]["percent"] is None and t[0]["level2"][1]["level3"] is None  # non-integer -> NULL; absent theme3 stays NULL


def test_indicators_positions_and_measures(details):
    ind = details["P000001"].asDict(recursive=True)["results_indicators"]
    assert [x["indicator_index"] for x in ind] == [0, 1] and ind[1]["subtype"] == "Breakdown"
    assert ind[0]["baseline"] == {"value": "0.00", "date": date(2020, 1, 31), "comment": None}
    assert ind[0]["current"] == {"value": None, "date": None, "comment": None}


def test_geo_positions_exact_coordinates_and_duplicates_kept(details):
    g = details["P000001"].asDict(recursive=True)["geo_locations"]
    assert [x["location_index"] for x in g] == [0, 1] and [x["geo_loc_id"] for x in g] == ["G1", "G1"]
    assert g[0]["latitude"] == Decimal("5.1234567") and g[0]["longitude"] == Decimal("-0.1000000") and g[0]["country_key"] == "6000001"
    assert g[1]["latitude"] == Decimal("0E-7") and g[1]["name"] is None


def test_counts_and_absent_arrays(details):
    a, b = details["P000001"], details["P000002"]
    assert (a.theme_level1_count, a.indicator_count, a.geo_location_count, a.loan_transaction_count) == (2, 2, 2, 6)
    assert (b.theme_level1_count, b.indicator_count, b.geo_location_count, b.loan_transaction_count) == (0, 0, 0, 0)
    assert b.results_indicators is None and b.geo_locations is None and b.environmental_social_risk_rating is None and b.themes_level3 is None


def test_detail_lineage(details):
    d = details["P000001"]
    assert d.raw_record_sha256 == hashlib.sha256(json.dumps(FULL).encode("utf-8")).hexdigest()
    assert (d.detail_coverage, d.record_snapshot_semantics, d.transform_version) == (T.DETAIL_COVERAGE, T.RECORD_SEMANTICS, T.TRANSFORM_VERSION)
    assert d.bronze_cohort_id == "wbg_poc_2000" and d.bronze_input_file_sha256 == "a" * 64 and d.silver_run_id == "t"


def test_transaction_keys_follow_source_positions(tx):
    assert sorted(tx) == [f"P000001:{i}" for i in range(6)]
    assert [tx[f"P000001:{i}"].transaction_index for i in range(6)] == list(range(6))


def test_amounts_exact_signed_and_scientific(tx):
    assert tx["P000001:0"].amount == Decimal("15000000.00") and tx["P000001:0"].amount_published == "1.5E7"
    assert tx["P000001:1"].amount == Decimal("-2500.25")


def test_ambiguous_amount_kept_as_published_only(tx):
    assert tx["P000001:5"].amount is None and tx["P000001:5"].amount_published == "12,5"


def test_placeholder_date_null_with_flag(tx):
    p = tx["P000001:2"]
    assert p.transaction_date is None and p.transaction_date_is_placeholder is True and p.transaction_period_published.startswith("1900-01-01")
    assert tx["P000001:1"].transaction_date == date(2021, 1, 15) and tx["P000001:1"].transaction_date_is_placeholder is False


def test_identical_rows_kept_and_counted(tx):
    assert tx["P000001:3"].identical_transaction_group_size == 2 and tx["P000001:4"].identical_transaction_group_size == 2
    assert tx["P000001:3"].raw_record_sha256 == tx["P000001:4"].raw_record_sha256
    assert tx["P000001:2"].identical_transaction_group_size == 1  # same amount but different loan/type/period


def test_references_preserved_not_forced(tx):
    assert (tx["P000001:0"].financier_loan, tx["P000001:0"].financing_number, tx["P000001:0"].financing_number_prefix) == ("IDA-61410", "IDA61410", "IDA")
    assert (tx["P000001:2"].financier_loan, tx["P000001:2"].financing_number, tx["P000001:2"].financing_number_prefix) == ("TF-A9875", "TFA9875", "TF")
    assert tx["P000001:2"].transaction_snapshot_semantics == T.TRANSACTION_SEMANTICS
