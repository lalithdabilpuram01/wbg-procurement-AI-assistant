"""The TableLens SQL writer: the SQL agent's Claude path, pointed at one PDF table from the TableLens registry.

Same budgeted client as every other Claude call (Portkey route, spend ledger, output cap `table_sql`, audit). Claude
sees only the question and the chosen table's schema (SQL names, printed headers, types, sample values) and returns
one PostgreSQL SELECT or says the table cannot answer. The SQL is never trusted: TableLens validates it (one SELECT,
that table, registered columns, allow-listed functions, LIMIT) before it runs on a read-only Lakebase connection.
"""
from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, ValidationError

from .claude import PortkeyJsonClient

SqlWriter = Callable[[str, str], tuple[str | None, dict[str, int]]]   # (question, table schema) -> (sql or None, usage)

SYSTEM = ("You write ONE read-only PostgreSQL SELECT that answers the question from the single table described. "
          "Use only that table (schema-qualified, exactly as given) and its listed columns. Filter rows with ILIKE on "
          "ONE or TWO short distinctive words taken from the row labels shown (e.g. ILIKE '%institutional%'), never a "
          "long phrase, punctuation or a code: PDF text splits words and spacing varies. Prefer returning the matching "
          "rows with their _row and _page columns; aggregate (SUM, COUNT, AVG, MIN, MAX) only when the question asks for a total or count. "
          "Never use rows whose _quality is not 'ok' in a total. No joins, subqueries, CTEs or writes; LIMIT 50 at most. "
          "If the table cannot answer the question, set answerable to false and sql to an empty string. "
          "Return only JSON matching the schema.")


class TableSql(BaseModel):
    answerable: bool
    sql: str


class TableSqlWriter:
    """`writer(trace_id)` gives TableLens a per-request writer whose calls are traced and charged to that request."""

    def __init__(self, client: PortkeyJsonClient):
        self._client = client

    def writer(self, trace_id: str) -> SqlWriter:
        """Return a SQL writer bound to `trace_id`; it gives (None, usage) when Claude says the table cannot answer."""
        def write(question: str, schema: str) -> tuple[str | None, dict[str, int]]:
            """Ask Claude for SQL over one table; returns (the SQL or None, the usage)."""
            parsed, content, usage = self._client.complete(
                system=SYSTEM, user=f"QUESTION:\n{question}\n\nTABLE:\n{schema}", schema=TableSql.model_json_schema(),
                schema_name="table_sql", trace_id=trace_id)
            used = {"llm_calls": usage.llm_calls, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
            # A reply that does not match the schema is treated like "cannot answer", never as SQL to run.
            try:
                draft = TableSql.model_validate(parsed if parsed is not None else content)
            except ValidationError:
                return None, used
            return (draft.sql.strip() or None) if draft.answerable else None, used
        return write
