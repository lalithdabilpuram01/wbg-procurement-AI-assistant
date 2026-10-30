from datetime import date

import pytest

from wbdisc.normalize import (clean_label, extract_project_ids, normalize_project_id, normalize_ref,
                              normalize_supplier_name, parse_amount, parse_date, strip_html, wb_fiscal_year)


@pytest.mark.parametrize("raw,expected", [
    ("19-May-2022", date(2022, 5, 19)),           # Finances One API
    ("10/20/2026", date(2026, 10, 20)),           # Finances One bulk CSV (US order)
    ("2024-12-20T00:00:00Z", date(2024, 12, 20)),  # search APIs
    ("2024-12-22 00:00:00.0", date(2024, 12, 22)),
    ("9/30/2021 12:00:00 AM", date(2021, 9, 30)),
    ("2021/05/31", date(2021, 5, 31)),            # award notice text
])
def test_parse_date_observed_formats(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "  ", "nan", "N/A", "31-Foo-2020", "2021-13-45"])
def test_parse_date_rejects_garbage(raw):
    assert parse_date(raw) is None


def test_mm_dd_preferred_over_dd_mm_for_ambiguous_dates():
    # Finances One bulk CSVs are MM/DD/YYYY; the parser must not silently read 03/11 as 3 Nov.
    assert parse_date("03/11/2022") == date(2022, 3, 11)


def test_fiscal_year_boundaries():
    assert wb_fiscal_year(date(2020, 6, 30)) == 2020
    assert wb_fiscal_year(date(2020, 7, 1)) == 2021
    assert wb_fiscal_year(None) is None


@pytest.mark.parametrize("raw,expected", [("P151416", "P151416"), (" p151416 ", "P151416"), ("P15141", None),
                                          ("P1514160", None), ("", None), (None, None), ("3E", None)])
def test_normalize_project_id(raw, expected):
    assert normalize_project_id(raw) == expected


def test_extract_project_ids_dedupes_in_order():
    txt = "GH-Ghana Accountability -- P165557; see also P165557 and p123456"
    assert extract_project_ids(txt) == ["P165557", "P123456"]
    assert extract_project_ids("P1234567 is too long") == []


@pytest.mark.parametrize("raw,expected", [("12,000,000", 12_000_000.0), ("161538.89", 161538.89), (12.0, 12.0),
                                          ("", None), (None, None), ("abc", None), ("0", 0.0)])
def test_parse_amount_never_maps_missing_to_zero(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("a,b", [
    ("MR-WARDIP-MR-538131-CS-INDV", " mr-wardip-mr-538131-cs-indv "),
    ("NG-SPIN/ CMP/4/4.1.4/06/2026", "NG-SPIN-CMP-4-4-1-4-06-2026"),
    ("IDA_UDP_SSS_21_1", "IDA-UDP-SSS-21-1"),
    ("KE–KISIP–123", "KE-KISIP-123"),  # en dash
])
def test_normalize_ref_equivalences(a, b):
    assert normalize_ref(a) == normalize_ref(b)


@pytest.mark.parametrize("raw", [None, "", "N/A", "-", "nan"])
def test_normalize_ref_missing(raw):
    assert normalize_ref(raw) is None


def test_normalize_ref_keeps_distinct_refs_distinct():
    assert normalize_ref("IDA-UDP-SSS-21-1") != normalize_ref("IDA-UDP-SSS-21-2")


@pytest.mark.parametrize("a,b", [
    ("M/S. AIRES INGEGNERIA", "Aires Ingegneria S.R.L."),
    ("HYDROPHIL GMBH", "Hydrophil GmbH"),
    ("Société Générale d'Études", "SOCIETE GENERALE D ETUDES"),
    ("Smith & Sons Ltd.", "SMITH AND SONS LIMITED"),
])
def test_supplier_name_key_groups_variants(a, b):
    assert normalize_supplier_name(a) == normalize_supplier_name(b)


def test_supplier_name_key_does_not_collapse_different_firms():
    assert normalize_supplier_name("ALL INGEGNERIA") != normalize_supplier_name("AIRES INGEGNERIA")


def test_clean_label_collapses_whitespace():
    assert clean_label("Project Information and Integrated\n            Safeguards Data Sheet") == \
        "Project Information and Integrated Safeguards Data Sheet"
    assert clean_label("Consultant Qualification  Selection") == "Consultant Qualification Selection"
    assert clean_label("   ") is None


def test_strip_html_preserves_table_cells_and_entities():
    html = "<p>Bidder&nbsp;A</p><table><tr><td>Name</td><td>Price</td></tr></table>&eacute;"
    out = strip_html(html)
    assert "Bidder A" in out and "Name | Price |" in out and "é" in out
