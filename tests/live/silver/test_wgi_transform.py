"""WGI transform logic on synthetic DataFrames over Databricks Connect (no table I/O). Skips without a session."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from silver.wgi import transform as W


@pytest.fixture(scope="module")
def spark():
    try:
        from silver.common.session import get_spark
        return get_spark()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Databricks Connect session: {e}")


def rec(cid, iso3, name, year, value, short="GE.EST"):
    return json.dumps({"indicator": {"id": f"GOV_WGI_{short}", "value": "Government Effectiveness - Governance estimate (approx. -2.5 to +2.5)"},
                       "country": {"id": cid, "value": name}, "countryiso3code": iso3, "date": str(year), "value": value,
                       "unit": "", "obs_status": "", "decimal": 1})


@pytest.fixture(scope="module")
def out(spark):
    ts = datetime(2026, 10, 22, 22, 58)
    f = "/Volumes/x/wgi_estimates/GOV_WGI_GE.EST.jsonl.gz"
    bronze = spark.createDataFrame([
        (rec("NZ", "NZL", "New Zealand", 2025, 1.87), "GE.EST", f, ts),
        (rec("SO", "SOM", "Somalia", 2025, 0.0), "GE.EST", f, ts),             # genuine zero
        (rec("TL", "TLS", "Timor-Leste", 1996, None), "GE.EST", f, ts),        # missing
        (rec("", "", "Anguilla", 2025, 0.64), "GE.EST", f, ts),                # blank code
        (rec("", "", "Jersey", 2025, None), "GE.EST", f, ts),                  # blank code + missing
        (rec("ZZ", "ZZZ", "Notland", 2025, -3.1), "GE.EST", f, ts),            # code not in WDI, beyond nominal range
    ], "raw_record string, indicator_code string, source_file string, ingested_at timestamp")
    wdi = spark.createDataFrame([("NZ", "NZL", "economy"), ("SO", "SOM", "economy"), ("TL", "TLS", "economy")],
                                "country_code string, country_wb_id string, entity_type string")
    vint = W.vintages_df(spark, [{"file_name": "GOV_WGI_GE.EST.jsonl.gz", "source_lastupdated": "2026-09-25",
                                  "source_retrieved_utc": "2026-10-21T01:20:10+00:00", "source_file_sha256": "a" * 64,
                                  "manifest_records": 6, "source_url": "u"}])
    return {r.entity_key: r for r in W.build(bronze, wdi, vint, "t").collect()}


def test_entity_keys(out):
    assert set(out) == {"NZ", "SO", "TL", "name:Anguilla", "name:Jersey", "ZZ"}
    a = out["name:Anguilla"]
    assert (a.entity_key_type, a.country_code, a.country_iso3_code, a.wdi_match_status) == ("country_name", None, None, "unmatched_blank_source_code")
    assert out["ZZ"].wdi_match_status == "unmatched_code_not_in_wdi" and out["ZZ"].wdi_entity_type is None
    assert out["NZ"].wdi_match_status == "matched" and out["NZ"].wdi_country_wb_id == "NZL"


def test_zero_is_not_missing_and_null_preserved(out):
    assert out["SO"].value == 0.0 and out["SO"].is_value_missing is False
    assert out["TL"].value is None and out["TL"].is_value_missing is True
    assert out["name:Jersey"].value is None and out["name:Jersey"].is_value_missing is True


def test_values_kept_as_published(out):
    assert out["ZZ"].value == pytest.approx(-3.1)                       # outside nominal range, not clipped
    assert out["NZ"].indicator_code == "GOV_WGI_GE.EST" and out["NZ"].indicator_short_code == "GE.EST"
    assert out["NZ"].governance_dimension == "Government Effectiveness"


def test_lineage(out):
    r = out["NZ"]
    assert str(r.source_lastupdated) == "2026-09-25" and r.source_file_sha256 == "a" * 64
    assert json.loads(r.bronze_raw_record)["country"]["id"] == "NZ" and len(r.raw_record_sha256) == 64


def test_load_vintages(tmp_path):
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"files": [
        {"bronze_table": "wgi_estimates", "staged_file": "x/wgi_estimates/GOV_WGI_GE.EST.jsonl.gz", "source_lastupdated": "2026-09-25",
         "retrieved_utc": "2026-10-21T01:20:10+00:00", "sha256": "b" * 64, "records": 5832, "source_url": "u"},
        {"bronze_table": "cpia_ratings", "staged_file": "x/cpia/IQ.CPA.PUBS.XQ.jsonl.gz", "source_lastupdated": "2026-10-08",
         "retrieved_utc": "t", "sha256": "c" * 64, "records": 1, "source_url": "u"}]}))
    v = W.load_vintages(m)
    assert [x["file_name"] for x in v] == ["GOV_WGI_GE.EST.jsonl.gz"] and v[0]["manifest_records"] == 5832
