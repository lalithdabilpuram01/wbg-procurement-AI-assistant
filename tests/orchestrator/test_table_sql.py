"""TableLens SQL writer (offline): Claude's reply becomes SQL or None, usage is always reported, TableLens validates it."""
from __future__ import annotations

from rag.tablelens.query import validate
from tests.rag.tablelens.test_query import TABLE, T
from wbg_orchestrator.contracts import Usage
from wbg_orchestrator.table_sql import TableSqlWriter


class FakeJsonClient:
    def __init__(self, parsed):
        self.parsed, self.calls = parsed, []

    def complete(self, **request):
        self.calls.append(request)
        return self.parsed, "", Usage(llm_calls=1, input_tokens=400, output_tokens=60)


def write(parsed, question="How much IDA financing does institutional development get?"):
    client = FakeJsonClient(parsed)
    sql, usage = TableSqlWriter(client).writer("trace-1")(question, TABLE.schema_text())
    return sql, usage, client.calls[0]


def test_an_answerable_question_returns_sql_that_tablelens_accepts():
    query = f"SELECT project_components, ida_financing, _row, _page FROM {T} WHERE project_components ILIKE '%institutional%'"
    sql, usage, request = write({"answerable": True, "sql": query})
    assert validate(sql, TABLE)[0].startswith("SELECT")
    assert usage == {"llm_calls": 1, "input_tokens": 400, "output_tokens": 60}
    assert request["schema_name"] == "table_sql" and request["trace_id"] == "trace-1" and T in request["user"]


def test_an_unanswerable_table_gives_no_sql_but_still_reports_usage():
    sql, usage, _ = write({"answerable": False, "sql": ""})
    assert sql is None and usage["llm_calls"] == 1


def test_a_malformed_reply_gives_no_sql():
    assert write({"sql": 42})[0] is None
