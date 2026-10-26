"""Projects transform logic on synthetic records over Databricks Connect (no table I/O). Skips without a session."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from silver.projects import transform as P

PROV = {"file_name": "projects_v3_population__cohort2000.jsonl", "source_file_sha256": "a" * 64, "manifest_records": 4,
        "source_population_sha256": "b" * 64, "cohort_subset_extracted_at": "2026-10-21T22:40:38+00:00",
        "source_retrieved_at": "2026-10-21T01:41:22+00:00", "source_extraction_method": "x", "cohort_manifest_sha256": "c" * 64}


@pytest.fixture(scope="module")
def spark():
    try:
        from silver.common.session import get_spark
        return get_spark()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Databricks Connect session: {e}")


def proj(pid, cc, **kw):
    base = {"id": pid, "proj_id": pid, "project_name": f"Project {pid}", "countrycode": [cc], "countryname": "n", "countryshortname": "n",
            "regionname": "r", "status": "Active", "last_stage_reached_name": "Board Approved", "fiscalyear": "2020",
            "boardapprovaldate": "2019-07-02T00:00:00Z", "closingdate": "2025-06-30", "public_disclosure_date": "2019-01-01",
            "milestones": [{"apprvl_date": "2019-07-02T00:00:00Z", "effctvnss_date": None}],
            "financers": [{"fincrname": "IDA", "fincr_id": "IDA", "fincr_curr_id": "USD  ", "fincr_curr_amt": None, "fincr_usd_amt": "5000000",
                           "projectfinancialtype": "IDA"}],
            "major_sectors": [{"major_sector": {"major_sector_code": "TX", "major_sector_name": "Transportation",
                                                "sectors": [{"sector_code": "TA", "sector_name": "Roads", "sector_percent": "100.0"}]}}],
            "isr_ratings": [{"name": "Procurement", "code": "14", "isr_date": "2024-01-01", "value": "Satisfactory"}]}
    base.update(kw)
    return json.dumps(base)


@pytest.fixture(scope="module")
def rows(spark):
    ts = datetime(2026, 10, 22, 1, 45)
    f = "/Volumes/x/projects_v3/projects_v3_population__cohort2000.jsonl"
    bronze = spark.createDataFrame([
        (proj("P000001", "GH", curr_total_commitment="0", grantamt="0"), f, "wbg_poc_2000", ts),       # published zeros
        (proj("P000002", "ZR"), f, "wbg_poc_2000", ts),                                                 # curated alias ZR -> CD
        (proj("P000003", "4E", regionname="East Asia and Pacific"), f, "wbg_poc_2000", ts),            # regional, collides with WDI 4E
        (proj("P000004", "MX", fiscalyear="0", boardapprovaldate=None), f, "wbg_poc_2000", ts),         # no approval date
    ], "raw_record string, source_file string, cohort_id string, ingested_at timestamp")
    wdi = spark.createDataFrame([("GH", "GHA", "economy"), ("CD", "COD", "economy"), ("MX", "MEX", "economy"), ("4E", "EAP", "aggregate")],
                                "country_code string, country_wb_id string, entity_type string")
    return {r.project_id: r for r in P.build(bronze, wdi, PROV, "t").collect()}


def test_geography_and_wdi_matching(rows):
    assert (rows["P000001"].geography_type, rows["P000001"].wdi_country_code, rows["P000001"].wdi_match_method) == ("single_country", "GH", "exact")
    assert (rows["P000002"].country_code, rows["P000002"].wdi_country_code, rows["P000002"].wdi_match_method) == ("ZR", "CD", "curated_alias")
    r = rows["P000003"]
    assert (r.geography_type, r.wdi_country_code, r.wdi_match_method, r.regional_code_collides_with_wdi_code) == \
        ("regional_multicountry", None, "not_applicable_regional", True)


def test_zero_vs_missing_amounts(rows):
    r = rows["P000001"]
    assert r.current_total_commitment_usd == Decimal("0.00") and r.grant_amount_usd == Decimal("0.00")
    assert r.total_amount_usd is None and r.current_project_cost_usd is None          # key absent -> NULL


def test_fiscal_year_zero_and_missing_date(rows):
    r = rows["P000004"]
    assert r.approval_fiscal_year is None and r.approval_fiscal_year_raw == "0" and r.board_approval_date is None
    assert rows["P000001"].approval_fiscal_year == 2020 and rows["P000001"].board_approval_date == date(2019, 7, 2)


def test_nested_typed(rows):
    r = rows["P000001"]
    assert r.financers[0].amount_usd == Decimal("5000000.00") and r.financers[0].currency_code == "USD"
    assert r.financers[0].amount_original_currency is None
    assert r.major_sectors[0].sectors[0].sector_percent == 100.0
    assert r.isr_ratings_snapshot[0].rating_name == "Procurement" and r.isr_ratings_snapshot[0].isr_date == date(2024, 1, 1)
    assert r.milestone_approval_date == date(2019, 7, 2) and r.effectiveness_date is None


def test_lineage(rows):
    r = rows["P000002"]
    assert json.loads(r.bronze_raw_record)["countrycode"] == ["ZR"] and len(r.raw_record_sha256) == 64
    assert r.bronze_cohort_id == "wbg_poc_2000" and r.bronze_input_file_sha256 == "a" * 64
