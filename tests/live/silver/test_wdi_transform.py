"""Transformation-logic tests on small synthetic DataFrames over Databricks Connect (no table reads or writes).

Run from procurement-intelligence/:  python -m pytest silver/tests -q
Skipped automatically when no Databricks session can be created.
"""
from __future__ import annotations

import json

import pytest

from silver.common.dq import DQReport
from silver.wdi import transform as W


@pytest.fixture(scope="module")
def spark():
    try:
        from silver.common.session import get_spark
        return get_spark()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Databricks Connect session: {e}")


def rec(cid, iso3, name, year, value, ind="NY.GDP.MKTP.KD.ZG"):
    return json.dumps({"indicator": {"id": ind, "value": "GDP growth (annual %)"}, "country": {"id": cid, "value": name},
                       "countryiso3code": iso3, "date": str(year), "value": value, "unit": "", "obs_status": "", "decimal": 1})


@pytest.fixture(scope="module")
def frames(spark):
    from datetime import datetime
    ts = datetime(2026, 10, 22, 18, 0, 0)
    f = "/Volumes/x/observations_NY.GDP.MKTP.KD.ZG_2000-2025.json"
    obs = spark.createDataFrame([
        (rec("NZ", "NZL", "New Zealand", 2024, -0.87), "NY.GDP.MKTP.KD.ZG", f, ts),
        (rec("NA", "NAM", "Namibia", 2024, None), "NY.GDP.MKTP.KD.ZG", f, ts),        # 'NA' code + missing value
        (rec("XD", "", "High income", 2024, 1.97), "NY.GDP.MKTP.KD.ZG", f, ts),       # blank ISO3 aggregate
        (rec("XM", "", "Low income", 2024, 3.1), "NY.GDP.MKTP.KD.ZG", f, ts),         # second blank ISO3: must not collide
    ], "raw_record string, indicator_code string, source_file string, ingested_at timestamp")
    countries_raw = json.dumps([{"page": 1, "pages": 1, "per_page": "400", "total": 4}, [
        {"id": "NZL", "iso2Code": "NZ", "name": "New Zealand", "region": {"id": "EAS", "iso2code": "Z4", "value": "East Asia & Pacific"},
         "adminregion": {"id": "", "iso2code": "", "value": ""}, "incomeLevel": {"id": "HIC", "iso2code": "XD", "value": "High income"},
         "lendingType": {"id": "LNX", "iso2code": "XX", "value": "Not classified"}, "capitalCity": "Wellington", "longitude": "174.776", "latitude": "-41.2865"},
        {"id": "NAM", "iso2Code": "NA", "name": "Namibia", "region": {"id": "SSF", "iso2code": "ZG", "value": "Sub-Saharan Africa "},
         "adminregion": {"id": "SSA", "iso2code": "ZF", "value": "x"}, "incomeLevel": {"id": "UMC", "iso2code": "XT", "value": "Upper middle income"},
         "lendingType": {"id": "IBD", "iso2code": "XF", "value": "IBRD"}, "capitalCity": "Windhoek", "longitude": "17.0931", "latitude": "-22.5648"},
        {"id": "HIC", "iso2Code": "XD", "name": "High income", "region": {"id": "NA", "iso2code": "NA", "value": "Aggregates"},
         "adminregion": {"id": "", "iso2code": "", "value": ""}, "incomeLevel": {"id": "NA", "iso2code": "NA", "value": "Aggregates"},
         "lendingType": {"id": "", "iso2code": "", "value": "Aggregates"}, "capitalCity": "", "longitude": "", "latitude": ""},
        {"id": "LIC", "iso2Code": "XM", "name": "Low income", "region": {"id": "NA", "iso2code": "NA", "value": "Aggregates"},
         "adminregion": {"id": "", "iso2code": "", "value": ""}, "incomeLevel": {"id": "NA", "iso2code": "NA", "value": "Aggregates"},
         "lendingType": {"id": "", "iso2code": "", "value": "Aggregates"}, "capitalCity": "", "longitude": "", "latitude": ""},
    ]])
    meta = spark.createDataFrame([(countries_raw, "/Volumes/x/countries.json", ts)], "raw_response string, source_file string, ingested_at timestamp")
    api = spark.createDataFrame([("NY.GDP.MKTP.KD.ZG", json.dumps([{"page": 1, "pages": 1, "total": 4, "sourceid": "2", "lastupdated": "2026-10-08"}, []]), f)],
                                "indicator_code string, raw_response string, source_file string")
    ind = spark.createDataFrame([("NY.GDP.MKTP.KD.ZG", json.dumps([{"total": 1}, [{"name": "GDP growth (annual %)", "source": {"value": "World Development Indicators"}, "sourceOrganization": "x"}]]))],
                                "indicator_code string, raw_response string")
    countries = W.parse_country_metadata(meta)
    out = W.build_observations(obs, countries, W.parse_vintages(api), W.parse_indicator_metadata(ind), "test_run")
    return obs, countries, out


def test_rows_preserved_and_key_unique(frames):
    obs, _, out = frames
    assert out.count() == obs.count() == 4
    assert out.groupBy("country_code", "year", "indicator_code").count().filter("count > 1").count() == 0
    # grouping by ISO3 would wrongly merge the two blank-ISO3 aggregates
    assert out.groupBy("country_iso3_code", "year", "indicator_code").count().filter("count > 1").count() == 1


def test_blank_iso3_becomes_null_and_wb_id_filled(frames):
    rows = {r.country_code: r for r in frames[2].collect()}
    assert rows["XD"].country_iso3_code is None and rows["XD"].country_wb_id == "HIC"
    assert rows["NZ"].country_iso3_code == "NZL"


def test_missing_value_stays_null_and_namibia_code_kept(frames):
    rows = {r.country_code: r for r in frames[2].collect()}
    assert "NA" in rows, "Namibia's code 'NA' must not be read as null"
    assert rows["NA"].value is None and rows["NA"].is_value_missing is True
    assert rows["NZ"].value == pytest.approx(-0.87) and rows["NZ"].is_value_missing is False


def test_entity_classification_and_current_attributes(frames):
    c = {r.country_code: r for r in frames[1].collect()}
    assert c["XD"].entity_type == "aggregate" and c["XD"].region_code_current is None
    assert c["NZ"].entity_type == "economy" and c["NZ"].region_code_current == "EAS"
    assert c["NZ"].income_level_code_current == "HIC"


def test_vintage_and_lineage(frames):
    r = frames[2].filter("country_code = 'NZ'").collect()[0]
    assert str(r.source_lastupdated) == "2026-10-08"
    assert r.bronze_source_file.endswith("observations_NY.GDP.MKTP.KD.ZG_2000-2025.json")
    assert len(r.raw_record_sha256) == 64 and r.silver_run_id == "test_run"


def test_dq_report_blocks_on_error():
    rep = DQReport("t")
    rep.equals("ok", 1, 1)
    rep.add("soft", 2, 1, False, "warn")
    assert rep.passed
    rep.equals("hard", 0, 1)
    assert not rep.passed and len(rep.errors) == 1 and len(rep.warnings) == 1
