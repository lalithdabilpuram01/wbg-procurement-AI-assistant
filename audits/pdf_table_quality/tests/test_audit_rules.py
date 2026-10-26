"""Offline tests of the audit's own comparison rules (no PDF, no network). Cases are taken from the audit findings."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_tables as A  # noqa: E402


@pytest.mark.parametrize("clean, raw, expected", [
    ("140,348,308.59", "140,348,30\n8.59", "wrap_joined"),                 # one amount wrapped in its cell (plan p4)
    ("9,890,912.07", "9,890,912.0\n7", "wrap_joined"),
    ("57375752", "57\n37\n57\n52", "line_merge"),                           # four stacked values glued (PAD p104)
    ("340.0018.0012.00370.000.00370.00", "340.00\n18.00\n12.00\n370.00\n0.00\n370.00", "line_merge"),
    ("3,591,022", "3,591,022", "exact"),
    ("1,234", "9,999", "not_in_source"),
])
def test_cell_comparison_separates_wraps_from_glued_values(clean, raw, expected):
    assert {verdict for _, verdict in A.compare_cell(clean, raw)} == {expected}


def test_identifier_numbers_are_not_treated_as_values():
    assert A.quantity_tokens("MIHAPCLG-108999-CW-RFB") == []
    assert A.quantity_tokens("IDA-56250 Effective 370.00") == ["370.00"]


@pytest.mark.parametrize("label, real", [
    ("Project cost", True), ("col_3", False), ("Date 19-Sep-2021 -- 06-Jun-2022 21-Sep-2026", False),
    ("Under the original PDO, indicators, and targets, objective 1 was partially achieved and is rated modest", False),
])
def test_column_label_quality(label, real):
    assert A.is_real_label(label) is real


def test_reversed_rotated_header_words_are_detected():
    assert all(A.REVERSED_WORD.match(w) for w in ("eganiarD", "sdaoR", "sllifdnaL"))
    assert not any(A.REVERSED_WORD.match(w) for w in ("Drainage", "Roads", "Total", "US$"))
