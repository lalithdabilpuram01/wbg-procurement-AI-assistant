"""CPIA transform logic on synthetic DataFrames over Databricks Connect (no table I/O), plus manifest loading."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from silver.common.sourcefiles import load_manifest_vintages, vintages_df
from silver.cpia import transform as C


@pytest.fixture(scope="module")
def spark():
    try:
        from silver.common.session import get_spark
        return get_spark()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Databricks Connect session: {e}")


def rec(cid, iso3, name, year, value, ind="IQ.CPA.TRAN.XQ"):
    return json.dumps({"indicator": {"id": ind, "value": "CPIA transparency, accountability, and corruption in the public sector rating (1=low to 6=high)"},
                       "country": {"id": cid, "value": name}, "countryiso3code": iso3, "date": str(year), "value": value,
                       "unit": "", "obs_status": "", "decimal": 1})


@pytest.fixture(scope="module")
def rows(spark):
    ts = datetime(2026, 10, 22, 23, 1)
    f = "/Volumes/x/cpia_ratings/IQ.CPA.TRAN.XQ.jsonl.gz"
    bronze = spark.createDataFrame([
        (rec("ET", "ETH", "Ethiopia", 2025, 3.0), "IQ.CPA.TRAN.XQ", f, ts),
        (rec("ET", "ETH", "Ethiopia", 1990, None), "IQ.CPA.TRAN.XQ", f, ts),          # pre-CPIA year kept, NULL
        (rec("XM", "", "Low income", 2025, 2.14583333333333), "IQ.CPA.TRAN.XQ", f, ts),   # aggregate, blank ISO3, average
        (rec("IN", "IND", "India", 2025, None), "IQ.CPA.TRAN.XQ", f, ts),             # no score (not IDA)
        (rec("QQ", "QQQ", "Elsewhere", 2025, 7.5), "IQ.CPA.TRAN.XQ", f, ts),          # not in WDI, outside scale: kept
    ], "raw_record string, indicator_code string, source_file string, ingested_at timestamp")
    wdi = spark.createDataFrame([("ET", "ETH", "economy"), ("XM", "LIC", "aggregate"), ("IN", "IND", "economy")],
                                "country_code string, country_wb_id string, entity_type string")
    vint = vintages_df(spark, [{"file_name": "IQ.CPA.TRAN.XQ.jsonl.gz", "source_lastupdated": "2026-10-08", "source_retrieved_utc": "2026-10-21T01:20:14+00:00",
                                "source_file_sha256": "d" * 64, "manifest_records": 5, "source_url": "https://api.worldbank.org/v2/country/all/indicator/IQ.CPA.TRAN.XQ"}])
    return {(r.country_code, r.year): r for r in C.build(bronze, wdi, vint, "t").collect()}


def test_all_records_kept_and_nulls_preserved(rows):
    assert len(rows) == 5
    assert rows[("ET", 1990)].value is None and rows[("ET", 1990)].is_value_missing
    assert rows[("IN", 2025)].value is None and rows[("IN", 2025)].is_value_missing


def test_aggregate_and_blank_iso3(rows):
    r = rows[("XM", 2025)]
    assert (r.country_iso3_code, r.wdi_country_wb_id, r.wdi_entity_type, r.wdi_match_status) == (None, "LIC", "aggregate", "matched")
    assert r.value == pytest.approx(2.14583333333333)


def test_unmatched_and_out_of_scale_kept(rows):
    r = rows[("QQ", 2025)]
    assert r.wdi_match_status == "unmatched_code_not_in_wdi" and r.value == 7.5


def test_scale_and_lineage(rows):
    r = rows[("ET", 2025)]
    assert r.rating_scale == "1=low to 6=high" and str(r.source_lastupdated) == "2026-10-08"
    assert r.source_url.endswith("IQ.CPA.TRAN.XQ") and json.loads(r.bronze_raw_record)["country"]["id"] == "ET"


def test_load_manifest_vintages(tmp_path):
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"files": [
        {"bronze_table": "cpia_ratings", "staged_file": "x/cpia_ratings/IQ.CPA.TRAN.XQ.jsonl.gz", "source_lastupdated": "2026-10-08",
         "retrieved_utc": "t", "sha256": "e" * 64, "records": 17556, "source_url": "u"},
        {"bronze_table": "wgi_estimates", "staged_file": "x/GOV_WGI_GE.EST.jsonl.gz", "source_lastupdated": "2026-09-25",
         "retrieved_utc": "t", "sha256": "f" * 64, "records": 5832, "source_url": "u"}]}))
    v = load_manifest_vintages("cpia_ratings", m)
    assert [x["file_name"] for x in v] == ["IQ.CPA.TRAN.XQ.jsonl.gz"] and v[0]["manifest_records"] == 17556
