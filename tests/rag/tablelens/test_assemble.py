"""TableLens assembly rules on small synthetic tables (offline)."""
from __future__ import annotations

from rag.tablelens.assemble import assemble, parse_number


def part(table_id, columns, page=1, group=None, cont_of=None, index=0):
    return {"table_id": table_id, "document_id": "D1", "page": page, "index_on_page": index, "columns": columns,
            "continuation_group": group or table_id, "continuation_of": cont_of, "section_path": "Annex 1 > Costs",
            "caption": None, "kind": "generic"}


def row(table_id, index, cells, page=1, header=False):
    return {"table_id": table_id, "row_index": index, "cells_clean": cells, "page": page, "is_header": header}


def test_continuations_are_joined_and_repeated_headers_dropped():
    columns = ["Component", "Cost (US$ m)"]
    tables = [part("D1:t1", columns, page=1, group="D1:t1"), part("D1:t2", columns, page=2, group="D1:t1", cont_of="D1:t1")]
    rows = {"D1:t1": [row("D1:t1", 0, columns, header=True), row("D1:t1", 1, ["Roads", "10.5"])],
            "D1:t2": [row("D1:t2", 0, columns, page=2), row("D1:t2", 1, ["Bridges", "4"], page=2)]}
    [table] = assemble(tables, rows)
    assert [r.cells for r in table.rows] == [["Roads", "10.5"], ["Bridges", "4"]] and table.pages == [1, 2]
    assert [(c.sql_name, c.type) for c in table.columns] == [("component", "text"), ("cost_us_m", "number")]
    assert table.structure == "structured"


def test_shifted_text_moves_into_the_named_column_and_empty_spacers_go():
    columns = ["Item", "col_2", "Description", "col_4", "Cost"]
    rows = {"D1:t1": [row("D1:t1", 1, ["1", "", "", "Supervision", "17"]), row("D1:t1", 2, ["2", "", "Training", "", "5"])]}
    [table] = assemble([part("D1:t1", columns)], rows)
    assert [c.header for c in table.columns] == ["Item", "Description", "Cost"]
    assert [r.cells for r in table.rows] == [["1", "Supervision", "17"], ["2", "Training", "5"]]


def test_section_rows_become_the_group_of_the_rows_below():
    columns = ["Package", "Method", "Amount"]
    rows = {"D1:t1": [row("D1:t1", 1, ["GOODS", "", ""]), row("D1:t1", 2, ["Laptops", "RFQ", "20000"])]}
    [table] = assemble([part("D1:t1", columns)], rows)
    assert len(table.rows) == 1 and table.rows[0].group == "GOODS"


def test_stacked_values_are_split_and_unmatched_labels_flagged():
    columns = ["Component", "Cost"]
    label = "1. Highway 2. Institutions 3. Road safety Total Costs Front-End Fees"
    rows = {"D1:t1": [row("D1:t1", 1, [label, "340\n18\n12\n370\n0"])]}
    [table] = assemble([part("D1:t1", columns)], rows)
    assert [(r.cells, r.quality) for r in table.rows[:3]] == [
        (["1. Highway", "340"], "ok"), (["2. Institutions", "18"], "ok"), (["3. Road safety", "12"], "ok")]
    assert {r.quality for r in table.rows[3:]} == {"stacked"}


def test_layout_tables_are_text_not_structured():
    columns = ["Annex 1: Results Framework", "col_2", "col_3"]
    rows = {"D1:t1": [row("D1:t1", i, [f"Line {i}", "", ""]) for i in range(1, 6)]}
    [table] = assemble([part("D1:t1", columns)], rows)
    assert table.structure == "text"


def test_numbers_parse_with_currency_commas_and_brackets():
    assert parse_number("US$ 1,250.5") == 1250.5 and parse_number("(12)") == -12 and parse_number("45%") == 45
    assert parse_number("IDA / 57880") is None and parse_number("") is None


def test_header_words_printed_over_several_rows_join_the_column_names():
    columns = ["Component", "Amount at Approval", "col_3"]
    rows = {"D1:t1": [row("D1:t1", 1, ["", "(US$M)", "Actual at"]), row("D1:t1", 2, ["", "", "Closing (US$M)"]),
                      row("D1:t1", 3, ["Roads", "22.9", "6.4"])]}
    [table] = assemble([part("D1:t1", columns)], rows)
    assert [c.header for c in table.columns] == ["Component", "Amount at Approval (US$M)", "Actual at Closing (US$M)"]
    assert [r.cells for r in table.rows] == [["Roads", "22.9", "6.4"]]


def test_values_printed_left_of_their_header_move_under_it():
    columns = ["Component", "Approved", "col_3", "Actual"]
    rows = {"D1:t1": [row("D1:t1", 1, ["Roads", "22.9", "6.4", ""]), row("D1:t1", 2, ["Total", "125", "", "68.1"])]}
    [table] = assemble([part("D1:t1", columns)], rows)
    assert [c.header for c in table.columns] == ["Component", "Approved", "Actual"]
    assert [r.cells for r in table.rows] == [["Roads", "22.9", "6.4"], ["Total", "125", "68.1"]]


def test_a_word_keeps_its_column_text_and_placeholders_keep_it_numeric():
    columns = ["Year", "Growth rate in %", "Note"]
    rows = {"D1:t1": [row("D1:t1", i, [y, g, "x"]) for i, (y, g) in enumerate(
        [("2021", "-8.49"), ("2022", "-"), ("2023", "43.16"), ("Average growth", "24.89")], start=1)]}
    [table] = assemble([part("D1:t1", columns)], rows)
    assert [c.type for c in table.columns] == ["text", "number", "text"]


def test_cover_pages_abbreviation_lists_and_data_headed_tables_are_not_loaded():
    from rag.tablelens.load import worth_loading
    abbreviations = assemble([part("D1:t1", ["ADB", "Asian Development Bank"])],
                             {"D1:t1": [row("D1:t1", i, [a, f"{a} long name"]) for i, a in enumerate(["AMP", "IDA", "WB"], 1)]})[0]
    roads = assemble([part("D1:t2", ["Road", "Length (in km)"])],
                     {"D1:t2": [row("D1:t2", i, [a, str(i)]) for i, a in enumerate(["A3-2", "A3-3", "A3-8"], 1)]})[0]
    dated = assemble([part("D1:t3", ["03", "31-Aug-2017", "Moderately Unsatisfactory"])],
                     {"D1:t3": [row("D1:t3", i, ["04", "1-Sep-2018", "Satisfactory"]) for i in range(1, 4)]})[0]
    assert (worth_loading(abbreviations), worth_loading(roads), worth_loading(dated)) == (False, True, False)
