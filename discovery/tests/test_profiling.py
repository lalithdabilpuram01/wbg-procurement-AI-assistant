from wbdisc.profiling import markdown_table, profile_records, value_kind


def test_value_kind_distinguishes_missing_buckets():
    assert value_kind(None) == "null"
    assert value_kind("  ") == "empty"
    assert value_kind("NIL") == "sentinel"
    assert value_kind("1900-01-01T00:00:00Z") == "sentinel"
    assert value_kind("0") == "str:int"  # zero is a value, not missing


def test_value_kind_dates_and_numbers():
    assert value_kind("19-May-2022") == "str:date[DD-Mon-YYYY]"
    assert value_kind("10/20/2026") == "str:date[MM/DD/YYYY]"
    assert value_kind("12,000,000") == "str:thousands-separated-number"
    assert value_kind(3.5) == "float"
    assert value_kind(["a"]) == "list"


def test_profile_counts_and_uniqueness():
    recs = [{"id": "1", "d": "19-May-2022", "x": ""}, {"id": "2", "d": "01-Jan-2020", "x": "NIL"},
            {"id": "3", "d": None}]
    prof = profile_records(recs)
    assert prof["row_count"] == 3
    idc = prof["columns"]["id"]
    assert idc["populated_pct"] == 100.0 and idc.get("unique_when_present")
    d = prof["columns"]["d"]
    assert d["date_min"] == "2020-01-01" and d["date_max"] == "2022-05-19"
    x = prof["columns"]["x"]
    assert x["empty_pct"] == round(100 / 3, 2) and x["sentinel_pct"] == round(100 / 3, 2) and x["null_pct"] == round(100 / 3, 2)


def test_profile_ignores_private_extract_keys():
    prof = profile_records([{"a": 1, "_extract": {"os": 0}}])
    assert "_extract" not in prof["columns"]


def test_markdown_table_escapes_pipes():
    prof = profile_records([{"a": "x|y"}])
    assert "x\\|y" in markdown_table(prof)


def test_date_range_only_for_date_typed_columns():
    recs = [{"ref": "2016-04-12"}] + [{"ref": f"UDP-QCBS-{i}"} for i in range(9)]
    assert "date_min" not in profile_records(recs)["columns"]["ref"]


def test_capped_distinct_is_flagged_in_markdown():
    prof = profile_records([{"a": str(i)} for i in range(20)], max_distinct_tracked=5)
    assert prof["columns"]["a"]["distinct_capped"]
    assert "≥5 (capped)" in markdown_table(prof)
