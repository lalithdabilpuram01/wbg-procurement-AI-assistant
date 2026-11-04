"""TableLens questions: description search -> schema registry -> validated single-table SQL -> labelled, cited rows.

    1. find_tables   question vector (bge-m3) against the registry descriptions in Lakebase, fused with keyword
                     scores over the description texts, within the guard's project scope; below MIN_SIMILARITY: none
    2. registry      the table's physical name and columns (SQL name, printed header, type, samples)
    3. write SQL     the caller's writer (Claude through the budgeted client) gets that one schema and the question
    4. validate      one SELECT over that one table, registry columns only, allow-listed functions, LIMIT <= MAX_ROWS;
                     aggregates exclude flagged rows (`_quality = 'ok'`) and also fetch the rows they used, for citation
    5. execute       read-only Lakebase connection with a statement timeout; a row lookup whose filter matches nothing
                     (PDF text splits words: "Mo djo-Hawass a") returns the table's rows unfiltered, marked `broadened`
Every table tried leaves a note (rows / broadened / no rows / unanswerable / rejected: reason) for the audit trail.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import psycopg
import sqlglot
from psycopg import sql as pgsql
from sqlglot import exp

from . import RELEASE
from rag.vector_store.index import VECTOR_TYPE, stored_release

from .store import REGISTRY, SCHEMA

MIN_SIMILARITY = 0.45          # description similarity below which no table is queried
TOP_TABLES = 2
MAX_ROWS = 50
RRF_K = 60
SYSTEM_COLUMNS = ("_row", "_page", "_group", "_quality")
FUNCTIONS = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Round, exp.Lower, exp.Upper, exp.Coalesce, exp.Abs,
             exp.Cast, exp.Trim, exp.Length)
AGGREGATES = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
WORD = re.compile(r"[a-z0-9]{3,}")

SqlWriter = Callable[[str, str], tuple[str | None, dict[str, int]]]   # (question, schema) -> (sql or None, usage)


class UnsafeSql(ValueError):
    """The SQL is not a single read-only query over the chosen table's registered columns."""


@dataclass(frozen=True)
class RegisteredTable:
    """One table in the TableLens registry: its Lakebase name, source document, pages, columns and card."""
    table_id: str
    physical_name: str
    document_id: str
    project_id: str
    doc_type: str
    project_title: str
    section: str
    pages: tuple[int, ...]
    columns: tuple[dict, ...]
    card: str

    def schema_text(self) -> str:
        """Schema text for the SQL writer: table name, card, registered columns with samples, system columns."""
        cols = "\n".join(f"  {c['sql_name']} ({c['type']}): printed header \"{c['header']}\"; e.g. "
                         f"{', '.join(repr(s) for s in c['samples'][:3])}" for c in self.columns)
        return (f"TABLE {SCHEMA}.{self.physical_name}\n{self.card}\nCOLUMNS:\n{cols}\n"
                f"  _row (int): row number; _page (int): PDF page; _group (text): section heading above the row; "
                f"_quality (text): 'ok' or 'stacked' (unreliable values)")


@dataclass
class TableResult:
    """Rows that one validated query returned from one table, with the cited (row, page) pairs and quality flags."""
    table: RegisteredTable
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    aggregate: bool
    cited_rows: list[tuple[int, int]] = field(default_factory=list)   # (row, page) behind the answer
    flagged: bool = False                                              # a returned row is not 'ok'
    broadened: bool = False                                            # the filter matched nothing: unfiltered rows


@dataclass
class TableAnswer:
    """Everything one question produced: the tables that returned rows, the writer's usage and a note per table."""
    results: list[TableResult]                                         # the tables that returned rows
    usage: dict[str, int]                                              # the writer's usage over every table tried
    notes: list[str]                                                   # per table: what happened (audit, debugging)


def validate(query: str, table: RegisteredTable) -> tuple[str, bool, exp.Expression | None]:
    """The safe SQL to run, whether it aggregates, and its WHERE clause (to fetch the rows an aggregate used)."""
    try:
        statements = sqlglot.parse(query, read="postgres")
    except sqlglot.errors.ParseError as error:
        raise UnsafeSql("the SQL does not parse") from error
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise UnsafeSql("exactly one SELECT is allowed")
    tree = statements[0]
    tables = {(t.db or "", t.name) for t in tree.find_all(exp.Table)}
    if tables != {(SCHEMA, table.physical_name)}:
        raise UnsafeSql("only the chosen table may be queried")
    if tree.find(exp.Subquery) or tree.find(exp.Join) or tree.find(exp.With):
        raise UnsafeSql("subqueries, joins and CTEs are not allowed")
    allowed = {c["sql_name"] for c in table.columns} | set(SYSTEM_COLUMNS)
    aliases = {a.alias for a in tree.find_all(exp.Alias)}
    unknown = {c.name for c in tree.find_all(exp.Column)} - allowed - aliases
    if unknown:
        raise UnsafeSql(f"unknown columns: {sorted(unknown)}")
    for node in tree.find_all(exp.Func):
        if not isinstance(node, FUNCTIONS + (exp.Connector, exp.Case)):   # sqlglot models AND / OR as functions
            raise UnsafeSql(f"function not allowed: {node.sql_name()}")
    aggregate = any(tree.find_all(*AGGREGATES))
    where = tree.args.get("where")
    if aggregate:
        tree = tree.where("_quality = 'ok'")                               # totals never include flagged rows
    limit = tree.args.get("limit")
    if limit is None or int(limit.expression.this) > MAX_ROWS:
        tree = tree.limit(MAX_ROWS)
    return tree.sql(dialect="postgres"), aggregate, (where.this if where is not None else None)


class TableLens:
    """Built with an open read-only pool and the question encoder; the SQL writer is given per question."""

    def __init__(self, pool, encode_query: Callable[[str], np.ndarray], release: str = RELEASE):
        """Load the release's registry from Lakebase; raises LookupError when the release is not loaded."""
        self._pool, self._encode, self.release = pool, encode_query, release
        with pool.connection() as conn:
            if stored_release(conn, release) is None:
                raise LookupError(f"TableLens release {release!r} is not loaded")
            rows = conn.execute(f"SELECT table_id, physical_name, document_id, project_id, doc_type, project_title, section, "
                                f"pages, columns, card_text, part_ids FROM wbg_rag.{REGISTRY} WHERE release = %s",
                                (release,)).fetchall()
        self.tables = {r[0]: RegisteredTable(r[0], r[1], r[2], r[3], r[4], r[5], r[6] or "", tuple(r[7]), tuple(r[8]), r[9])
                       for r in rows}
        self.part_ids = frozenset(p for r in rows for p in r[10])         # extraction tables now served by SQL
        self._words = {tid: set(WORD.findall(t.card.lower())) for tid, t in self.tables.items()}

    def find_tables(self, question: str, scope_projects: set[str] | None = None) -> list[tuple[RegisteredTable, float]]:
        """Rank registered tables for a question: dense similarity fused with keyword overlap, within the project scope.
        Returns at most TOP_TABLES tables whose similarity is at least MIN_SIMILARITY."""
        where = "release = %s" + (" AND project_id = ANY(%s)" if scope_projects else "")
        vector = self._encode(question)
        params: list = [vector, self.release, *([sorted(scope_projects)] if scope_projects else []), vector]
        with self._pool.connection() as conn:
            dense = conn.execute(f"SELECT table_id, 1 - (embedding <=> %s::{VECTOR_TYPE}) FROM wbg_rag.{REGISTRY} WHERE {where} "
                                 f"ORDER BY embedding <=> %s::{VECTOR_TYPE} LIMIT 20", params).fetchall()
        similarity = {tid: float(s) for tid, s in dense}
        terms = set(WORD.findall(question.lower()))
        keyword = sorted(similarity, key=lambda tid: -len(terms & self._words[tid]))
        by_dense = sorted(similarity, key=lambda tid: -similarity[tid])
        fused = {tid: 1 / (RRF_K + by_dense.index(tid) + 1) + 1 / (RRF_K + keyword.index(tid) + 1) for tid in similarity}
        ranked = sorted(fused, key=lambda tid: -fused[tid])
        return [(self.tables[tid], similarity[tid]) for tid in ranked if similarity[tid] >= MIN_SIMILARITY][:TOP_TABLES]

    def ask(self, question: str, write_sql: SqlWriter, scope_projects: set[str] | None = None) -> TableAnswer:
        """Answer a question from the best-matching tables: the writer drafts SQL for each; it is validated and run."""
        answer = TableAnswer([], {}, [])
        usage: Counter = Counter()
        for table, _ in self.find_tables(question, scope_projects):
            query, used = write_sql(question, table.schema_text())
            usage.update(used)
            result, note = self._answer_from(table, query)
            answer.notes.append(f"{table.table_id}: {note}")
            if result is not None:
                answer.results.append(result)
        answer.usage = dict(usage)
        return answer

    def _answer_from(self, table: RegisteredTable, query: str | None) -> tuple[TableResult | None, str]:
        """Validate and run the writer's SQL on one table; return (result or None, a note for the audit trail).
        A row lookup whose filter matches nothing is re-run without the filter and marked broadened."""
        if not query:
            return None, "unanswerable"
        try:
            safe, aggregate, where = validate(query, table)
        except UnsafeSql as error:
            return None, f"rejected: {error}"[:120]
        try:
            result = self._run(table, safe, aggregate, where)
        except psycopg.Error as error:                           # e.g. comparing a text column with a number
            return None, f"failed: {type(error).__name__}"
        if aggregate:
            found = any(v is not None for row in result.rows for v in row.values())
            return (result, "total") if found else (None, "no rows")
        if result.rows:
            return result, "rows"
        if where is None:
            return None, "no rows"
        broadened = self._run(table, unfiltered(safe), False, None)
        broadened.broadened = True
        return (broadened, "broadened: filter matched nothing") if broadened.rows else (None, "no rows")

    def _run(self, table: RegisteredTable, safe: str, aggregate: bool, where) -> TableResult:
        """Run validated SQL in a transaction; for an aggregate also fetch the (row, page) pairs it used, to cite."""
        with self._pool.connection() as conn, conn.transaction():
            cursor = conn.execute(safe)
            columns = [d.name for d in cursor.description]
            rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
            if aggregate:
                condition = f" AND ({where.sql(dialect='postgres')})" if where is not None else ""   # validated above
                rows_used = (pgsql.SQL("SELECT _row, _page FROM {}.{} WHERE _quality = 'ok'").format(
                    pgsql.Identifier(SCHEMA), pgsql.Identifier(table.physical_name)) + pgsql.SQL(condition + f" LIMIT {MAX_ROWS}"))
                cited = conn.execute(rows_used).fetchall()
            else:
                cited = [(r["_row"], r["_page"]) for r in rows if "_row" in r and "_page" in r]
        flagged = any(r.get("_quality") not in (None, "ok") for r in rows)
        return TableResult(table, safe, columns, rows, aggregate, [tuple(c) for c in cited], flagged)


def unfiltered(safe: str) -> str:
    """The same validated lookup without its WHERE clause, with row and page added for citations."""
    tree = sqlglot.parse_one(safe, read="postgres")
    tree.set("where", None)
    selected = {c.alias_or_name for c in tree.expressions}
    missing = [c for c in ("_row", "_page", "_group", "_quality") if c not in selected and "*" not in selected]
    return (tree.select(*missing, append=True) if missing else tree).sql(dialect="postgres")


def labelled_rows(result: TableResult) -> list[str]:
    """Each returned row as 'row N, p. P, group G: Printed header: value | ...' (what Claude reads and the citation shows)."""
    header = {c["sql_name"]: c["header"] for c in result.table.columns}
    lines = []
    for row in result.rows:
        place = [f"row {row['_row']}" if row.get("_row") is not None else "", f"p. {row['_page']}" if row.get("_page") else "",
                 f"group {row['_group']}" if row.get("_group") else ""]
        cells = [f"{header.get(k, k)}: {v}" for k, v in row.items() if k not in SYSTEM_COLUMNS and v not in (None, "")]
        prefix = ", ".join(p for p in place if p)
        lines.append((prefix + ": " if prefix else "") + " | ".join(cells))
    return lines
