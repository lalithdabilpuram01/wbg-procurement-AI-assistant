"""Synthetic layouts (no PDF): stacked values, wraps, row splitting and lost values, independent of the pilot cases."""
from __future__ import annotations

import re

import pytest

from rag.ingestion.cell_clean import clean_cell
from rag.ingestion_p1.lost_values import check_row, column_ranges
from rag.ingestion_p1.stacked_values import CellLines, clean_cell_lines, is_wrap, split_stacked_row

VOCAB = {"total", "costs", "institutional", "development", "component"}


# ------------------------------------------------------------------------------------------------ stacked values
@pytest.mark.parametrize("raw, segments", [
    ("1,250.5\n300.25\n0.75", ["1,250.5", "300.25", "0.75"]),          # decimals with different precision
    ("45%\n55%\n100%", ["45%", "55%", "100%"]),                        # percentages
    ("(1.2)\n3.4\n-0.5", ["(1.2)", "3.4", "-0.5"]),                    # negatives in parentheses / with sign
    ("US$ 12\nUS$ 7", ["US$ 12", "US$ 7"]),                            # currency prefix
    ("2019\n2020\n2021", ["2019", "2020", "2021"]),                    # years
    ("12-Aug-2019\n23-Jun-2020", ["12-Aug-2019", "23-Jun-2020"]),      # stacked dates
    ("2020-11-20\n2021-01-18", ["2020-11-20", "2021-01-18"]),
    ("1,000\n2,500,000", ["1,000", "2,500,000"]),                      # thousands groups
])
def test_independent_stacked_values_keep_their_line_boundaries(raw, segments):
    cell = clean_cell_lines(raw, VOCAB)
    assert cell.stacked and cell.segments == segments and cell.text == "\n".join(segments)
    same_values = re.sub(r"\s+", "", cell.text) == re.sub(r"\s+", "", clean_cell(raw, VOCAB)[0])
    assert same_values                                                  # only boundaries differ from the active cleaner


@pytest.mark.parametrize("raw, expected", [
    ("140,348,30\n8.59", "140,348,308.59"),                            # thousands group cut by the column edge
    ("290,000,000.\n00", "290,000,000.00"),                            # decimal point at the line end
    ("9,890,912.0\n7", "9,890,912.07"),                                # last decimal digit wrapped
    ("1,\n234", "1,234"),
    ("2022-05-\n18", "2022-05-18"),                                    # date wrapped in a narrow column
    ("12-Aug-\n2019", "12-Aug-2019"),
])
def test_one_wrapped_value_is_still_joined(raw, expected):
    cell = clean_cell_lines(raw, VOCAB)
    assert not cell.stacked and cell.text == expected == clean_cell(raw, VOCAB)[0]


@pytest.mark.parametrize("raw", [
    "Institu\ntional Development",                                     # word break: behaviour of the active cleaner
    "Total\nCosts",
    "IDA-56250\nEffective",
    "LK-MIHAPCLG-136348-CW-\nRFB",
    "Component 1\n2. Institutional",
    "",
])
def test_cells_without_stacked_values_are_cleaned_exactly_like_the_active_pipeline(raw):
    assert clean_cell_lines(raw, VOCAB).text == clean_cell(raw, VOCAB)[0]


def test_values_mixed_with_text_lines_split_only_between_values():
    cell = clean_cell_lines("Total\n340.00\n18.00", VOCAB)
    assert cell.segments == ["Total 340.00", "18.00"] and cell.starts == [0, 2]


@pytest.mark.parametrize("left, right, wrap", [
    ("57", "37", False), ("340.00", "18.00", False), ("0.0", "100.0", False), ("126.0", "0.0", False),
    ("140,348,30", "8.59", True), ("9,890,912.0", "7", True), ("2020-11-20", "2021-01-18", False),
])
def test_wrap_versus_stacked(left, right, wrap):
    assert is_wrap(left, right) is wrap


# ---------------------------------------------------------------------------------------------------- splitting
def values(segments, tops):
    return CellLines("\n".join(segments), segments, tops, True)


def text(lines, tops):
    return CellLines(" ".join(lines), lines, tops, False)


IDENTITY = lambda s: s  # noqa: E731


def test_aligned_columns_with_one_label_per_value_are_split():
    result = split_stacked_row([text(["Roads", "Bridges", "Total"], [100, 120, 140]),
                                values(["10.0", "5.0", "15.0"], [100, 120, 140]),
                                values(["66%", "34%", "100%"], [100.8, 120.4, 139.6]),
                                CellLines("", [], [], False)], IDENTITY)
    assert result.status == "split_aligned"
    assert result.sub_rows == [["Roads", "10.0", "66%", ""], ["Bridges", "5.0", "34%", ""], ["Total", "15.0", "100%", ""]]


@pytest.mark.parametrize("label, reason", [
    (text(["Team leadership and", "leadership Support"], [100, 120]), "connector at line end"),
    (text(["Environment and social", "monitoring"], [100, 120]), "lower-case continuation"),
    (text(["Project construction"], [100]), "one label for several values"),
    (text(["A", "B", "C"], [100, 120, 140]), "more lines than values"),
    (text(["Component", "Total"], [100, 131]), "label not on the value baseline"),
])
def test_ambiguous_labels_block_the_split(label, reason):
    result = split_stacked_row([label, values(["12", "48"], [100, 120])], IDENTITY)
    assert result.status == "needs_review" and result.sub_rows == [], reason
    assert result.reasons == ["col1_alignment_ambiguous"]


def test_value_columns_with_different_counts_are_not_split():
    result = split_stacked_row([values(["1", "2", "3"], [100, 120, 140]), values(["4", "5"], [100, 120])], IDENTITY)
    assert result.status == "needs_review" and result.reasons == ["stacked_values_unaligned"]


def test_value_columns_offset_by_half_a_line_or_more_are_not_split():
    result = split_stacked_row([values(["1", "2"], [100, 120]), values(["3", "4"], [112, 132])], IDENTITY)
    assert result.status == "needs_review" and result.reasons == ["stacked_values_unaligned"]


# ------------------------------------------------------------------------------------------------- lost values
GRID = [(0, 0, 100, 20), (100, 0, 150, 20), (150, 0, 200, 20)]


def word(text_, x0, x1, top):
    return {"text": text_, "x0": x0, "x1": x1, "top": top, "bottom": top + 8}


def shifted(boxes, dy):
    return [(b[0], b[1] + dy, b[2], b[3] + dy) for b in boxes]


def test_lost_values_in_unique_empty_columns_are_recovered_from_source_words():
    partial = [(0, 40, 100, 60), None, None]
    rows = [GRID, partial]
    words = [word("Yes", 105, 120, 45), word("No", 155, 168, 45), word("Label", 5, 40, 45)]
    result = check_row(partial, GRID + [partial[0]], words, column_ranges(rows, 3), (0, 0, 200, 60))
    assert result.status == "values_recovered" and result.recovered == {1: "Yes", 2: "No"}


def test_a_word_straddling_two_columns_is_not_assigned():
    partial = [(0, 40, 100, 60), None, None]
    words = [word("2019-10-082020-01-24", 120, 175, 45)]
    result = check_row(partial, GRID + [partial[0]], words, column_ranges([GRID, partial], 3), (0, 0, 200, 60))
    assert result.status == "values_missing" and result.recovered == {}


def test_without_a_complete_row_nothing_is_recovered():
    partial = [(0, 40, 100, 60), None, None]
    result = check_row(partial, [partial[0]], [word("Yes", 105, 120, 45)], column_ranges([partial], 3), (0, 0, 200, 60))
    assert result.status == "values_missing" and result.recovered == {}


def test_words_inside_a_cell_spanning_from_another_row_are_not_lost():
    spanning = (100, 20, 200, 60)                                       # a merged cell from the row above
    partial = [(0, 40, 100, 60), None, None]
    result = check_row(partial, GRID + [spanning, partial[0]], [word("Comment", 110, 150, 45)],
                       column_ranges([GRID, partial], 3), (0, 0, 200, 60))
    assert result.status == "unchanged"


def test_complete_rows_and_rows_without_stray_words_are_unchanged():
    assert check_row(GRID, GRID, [word("x", 5, 9, 5)], column_ranges([GRID], 3), (0, 0, 200, 20)).status == "unchanged"
    partial = [(0, 40, 100, 60), None, None]
    assert check_row(partial, GRID + [partial[0]], [], column_ranges([GRID, partial], 3), (0, 0, 200, 60)).status == "unchanged"


def test_words_outside_the_table_are_ignored():
    partial = [(0, 40, 100, 60), None, None]
    result = check_row(partial, GRID + [partial[0]], [word("Footer", 230, 260, 45)], column_ranges([GRID, partial], 3), (0, 0, 200, 60))
    assert result.status == "unchanged"
