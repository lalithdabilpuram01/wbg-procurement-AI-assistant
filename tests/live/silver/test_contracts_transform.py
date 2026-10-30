"""Contract-award transform logic on synthetic Bronze-shaped rows over Databricks Connect (no table I/O)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from silver.contracts import transform as T

PROV = {"bronze_input_file_sha256": "a" * 64, "records": 5, "source_population_sha256": "b" * 64,
        "source_retrieved_at": "2026-10-21T01:13:10+00:00", "source_url_note": "x"}
DS5_COLS = ["As of Date", "Fiscal Year", "Region", "Borrower Country / Economy", "Borrower Country / Economy Code", "Project ID", "Project Name",
            "Project Global Practice", "Procurement Category", "Procurement Method", "WB Contract Number", "Contract Description",
            "Borrower Contract Reference Number", "Contract Signing Date", "Supplier ID", "Supplier", "Supplier Country / Economy",
            "Supplier Country / Economy Code", "Supplier Contract Amount (USD)", "Review type", "Contract signed - Calendar year"]


@pytest.fixture(scope="module")
def spark():
    try:
        from silver.common.session import get_spark
        return get_spark()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Databricks Connect session: {e}")


def ds5_row(cn, sid, sup, amt, method="Request for Bids", ref="REF-1", proj="P000001"):
    vals = {"As of Date": "10/20/2026", "Fiscal Year": "2025", "Region": "R", "Borrower Country / Economy": "Ghana", "Borrower Country / Economy Code": "GH",
            "Project ID": proj, "Project Name": "Proj", "Project Global Practice": "Energy;Water", "Procurement Category": "Works",
            "Procurement Method": method, "WB Contract Number": cn, "Contract Description": "Build", "Borrower Contract Reference Number": ref,
            "Contract Signing Date": "08/15/2024", "Supplier ID": sid, "Supplier": sup, "Supplier Country / Economy": "Ghana",
            "Supplier Country / Economy Code": "GH", "Supplier Contract Amount (USD)": amt, "Review type": "Post", "Contract signed - Calendar year": "2024"}
    return tuple(vals[c] for c in DS5_COLS) + ("/Volumes/x/ds5.csv", "wbg_poc_2000", datetime(2026, 10, 22))


@pytest.fixture(scope="module")
def built(spark):
    schema = ", ".join(f"`{c}` string" for c in DS5_COLS) + ", source_file string, cohort_id string, ingested_at timestamp"
    ds5 = spark.createDataFrame([
        ds5_row("100", "11", "JV A", "500.00"), ds5_row("100", "12", "JV B", "500.00"),          # JV: equal shares, sum = 1000
        ds5_row("101", "aaaaaaaaaaaaaaaa", "INDIVIDUAL CONSULTANT", "0.00", method="Individual Consultant Selection", ref="REF-1"),  # zero; same ref
        ds5_row("102", None, "CONTINUATION)", None),                                              # blank supplier id + blank amount
        ds5_row("102", "13", "MAIN SUPPLIER (", "250.50"),
    ], schema)
    lines = T.build_lines(ds5, PROV, "t")
    return {r.line_key: r for r in lines.collect()}, {r.wb_contract_number: r for r in T.build_contracts(lines, "t").collect()}


def test_line_keys_and_types(built):
    lines, _ = built
    assert set(lines) == {"100:11", "100:12", "101:aaaaaaaaaaaaaaaa", "102:13"} | {k for k in lines if k.startswith("102:NO_SUPPLIER_ID-")}
    assert len(lines) == 5
    assert lines["101:aaaaaaaaaaaaaaaa"].supplier_id_type == "anonymized_individual_consultant"
    nok = next(v for k, v in lines.items() if k.startswith("102:NO_SUPPLIER_ID-"))
    assert nok.supplier_id_type == "missing" and nok.supplier_amount_usd is None and nok.is_amount_missing


def test_zero_kept_and_dates(built):
    lines, _ = built
    z = lines["101:aaaaaaaaaaaaaaaa"]
    assert z.supplier_amount_usd == Decimal("0.00") and not z.is_amount_missing
    assert z.contract_signing_date == date(2024, 8, 15) and z.fiscal_year == 2025 and z.global_practices == ["Energy", "Water"]


def test_contract_amount_is_sum_of_lines_no_double_count(built):
    _, contracts = built
    assert contracts["100"].contract_amount_usd == Decimal("1000.00") and contracts["100"].supplier_line_count == 2 and contracts["100"].is_multi_supplier
    assert contracts["102"].contract_amount_usd == Decimal("250.50") and contracts["102"].supplier_lines_missing_amount == 1
    assert contracts["101"].contract_amount_usd == Decimal("0.00")
    assert contracts["100"].contracts_sharing_reference_in_project == 3          # 100, 101, 102 share REF-1 in P000001


def test_lineage_hash_is_row_level(built):
    lines, _ = built
    assert len({v.source_row_sha256 for v in lines.values()}) == 5 and lines["100:11"].source_dataset == "DS00005"
