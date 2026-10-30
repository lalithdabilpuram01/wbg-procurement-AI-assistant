"""TableLens SQL validation: one read-only SELECT over the chosen table and its registered columns (offline)."""
from __future__ import annotations

import pytest

from rag.tablelens.query import RegisteredTable, TableLens, TableResult, UnsafeSql, labelled_rows, unfiltered, validate

TABLE = RegisteredTable(
    "24440102:t02201", "t_24440102_t02201", "24440102", "P148850", "PAD", "Ethiopia Expressway", "B. Project Financing", (22,),
    ({"sql_name": "project_components", "header": "Project Components", "type": "text", "samples": ["1. Highway"]},
     {"sql_name": "ida_financing", "header": "IDA Financing", "type": "number", "samples": ["340.00"]}), "card")
T = "pdf_tables.t_24440102_t02201"


def test_a_lookup_is_kept_and_capped():
    safe, aggregate, _ = validate(f"SELECT project_components, ida_financing, _row, _page FROM {T} "
                                  "WHERE project_components ILIKE '%institutional%'", TABLE)
    assert not aggregate and "LIMIT 50" in safe and "ILIKE '%institutional%'" in safe


def test_a_total_excludes_flagged_rows_and_keeps_its_filter_for_citations():
    safe, aggregate, where = validate(f"SELECT SUM(ida_financing) AS total FROM {T} WHERE project_components ILIKE '%road%'", TABLE)
    assert aggregate and "_quality = 'ok'" in safe and "ILIKE" in where.sql()


@pytest.mark.parametrize("query", [
    f"SELECT * FROM {T}; DROP TABLE {T}",                                   # two statements
    f"DELETE FROM {T}",                                                     # not a SELECT
    "SELECT * FROM wbg_rag.units",                                          # another table
    f"SELECT * FROM {T} JOIN wbg_rag.units ON true",                        # a join
    f"SELECT (SELECT count(*) FROM wbg_rag.units) FROM {T}",                # a subquery
    f"SELECT secret_column FROM {T}",                                       # unregistered column
    f"SELECT pg_read_file('/etc/passwd') FROM {T}",                         # a function outside the allow-list
    f"WITH x AS (SELECT * FROM {T}) SELECT * FROM x",                       # a CTE
])
def test_anything_else_is_refused(query):
    with pytest.raises(UnsafeSql):
        validate(query, TABLE)


def test_rows_are_labelled_with_the_printed_headers():
    result = TableResult(TABLE, "", ["project_components", "ida_financing", "_row", "_page"],
                         [{"project_components": "2. Institutional development", "ida_financing": 18.0, "_row": 2, "_page": 22}],
                         aggregate=False)
    assert labelled_rows(result) == ["row 2, p. 22: Project Components: 2. Institutional development | IDA Financing: 18.0"]


class StubLens(TableLens):
    """TableLens with canned query results (no pool): the first lookup matches nothing, the unfiltered one matches."""

    def __init__(self, first_rows):
        self.first_rows, self.ran = first_rows, []

    def _run(self, table, safe, aggregate, where):
        self.ran.append(safe)
        rows = self.first_rows if len(self.ran) == 1 else [{"project_components": "2. Institutional development", "_row": 2, "_page": 22}]
        return TableResult(table, safe, list(rows[0]) if rows else [], rows, aggregate)


def test_a_filter_that_matches_nothing_returns_the_table_unfiltered_and_says_so():
    lens = StubLens([])
    result, note = lens._answer_from(TABLE, f"SELECT project_components FROM {T} WHERE project_components ILIKE '%regulatory framework%'")
    assert note.startswith("broadened") and result.broadened and "WHERE" not in lens.ran[1] and "_row" in lens.ran[1]


def test_every_outcome_leaves_a_note():
    assert StubLens([])._answer_from(TABLE, None) == (None, "unanswerable")
    assert StubLens([])._answer_from(TABLE, "DROP TABLE x")[1].startswith("rejected")
    assert StubLens([{"total": None}])._answer_from(TABLE, f"SELECT SUM(ida_financing) AS total FROM {T}") == (None, "no rows")


def test_the_unfiltered_lookup_keeps_the_chosen_columns_and_adds_citations():
    sql = unfiltered(f"SELECT project_components, ida_financing FROM {T} WHERE project_components ILIKE '%x%' LIMIT 50")
    assert sql == f"SELECT project_components, ida_financing, _row, _page, _group, _quality FROM {T} LIMIT 50"


def test_conditions_combined_with_and_or_are_allowed():
    query = (f"SELECT project_components, ida_financing, _row, _page FROM {T} WHERE (project_components ILIKE '%institutional%' "
             "OR project_components ILIKE '%regulatory%') AND _quality = 'ok' LIMIT 50")
    assert "OR" in validate(query, TABLE)[0]
