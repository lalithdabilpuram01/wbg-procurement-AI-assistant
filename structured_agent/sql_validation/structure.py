"""Structural safety checks on the parsed SQL AST (sqlglot, Databricks dialect).

Only one plain SELECT (optionally with CTEs and subqueries) is accepted. The whole tree is walked, so operations
hidden in CTEs, subqueries or expressions are found. Functions are allow-listed: anything not on the list (including
unknown/UDF functions such as reflect, read_files, ai_query, http_request) is rejected.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from structured_agent.config import QueryLimits
from structured_agent.contracts import ErrorCode, ParameterValue, ToolError

DIALECT = "databricks"

# Statements or clauses that change data, schema, permissions or session state, or write results.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.TruncateTable, exp.Command,
    exp.Set, exp.Use, exp.Grant, exp.Revoke, exp.Copy, exp.LoadData, exp.Cache, exp.Uncache, exp.Refresh,
    exp.Transaction, exp.Commit, exp.Rollback, exp.Pragma, exp.Into, exp.Analyze, exp.Kill, exp.Execute,
    exp.Describe, exp.Show,
)

# Read-only constructs that are outside the supported, reviewable query shape.
UNSUPPORTED_NODES: dict[type[exp.Expression], str] = {
    exp.SetOperation: "UNION / INTERSECT / EXCEPT",
    exp.Version: "time travel (VERSION/TIMESTAMP AS OF)",
    exp.TableSample: "TABLESAMPLE",
    exp.Lateral: "LATERAL VIEW",
    exp.Unnest: "UNNEST",
    exp.Explode: "EXPLODE",
    exp.Values: "inline VALUES",
    exp.Pivot: "PIVOT / UNPIVOT",
    exp.Parameter: "${variable} substitution",
    exp.SessionParameter: "session parameters",
}

ALLOWED_FUNCTIONS: tuple[type[exp.Expression], ...] = (
    exp.And, exp.Or, exp.Case,  # sqlglot models these as functions
    exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max, exp.CountIf, exp.ApproxDistinct,
    exp.PercentileDisc, exp.PercentileCont, exp.Median, exp.Stddev, exp.Variance,
    exp.Round, exp.Floor, exp.Ceil, exp.Abs, exp.Div, exp.IntDiv, exp.SafeDivide,
    exp.Coalesce, exp.Nullif, exp.If, exp.Greatest, exp.Least, exp.Cast, exp.TryCast,
    exp.Year, exp.Month, exp.Quarter, exp.DateDiff, exp.DateTrunc, exp.TimestampTrunc, exp.TsOrDsToDate, exp.CurrentDate,
    exp.Extract, exp.Lower, exp.Upper, exp.Trim, exp.Length, exp.Substring, exp.Concat,
    exp.RowNumber, exp.Rank, exp.DenseRank,
)


def parse_single_select(sql: str) -> tuple[exp.Select | None, list[ToolError]]:
    """Parse the text and require exactly one SELECT statement."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except ParseError as error:
        return None, [ToolError.of(ErrorCode.PARSE_ERROR, f"SQL could not be parsed: {str(error).splitlines()[0][:200]}")]
    if len(statements) != 1:
        return None, [ToolError.of(ErrorCode.MULTIPLE_STATEMENTS, f"Exactly one statement is allowed, found {len(statements)}")]
    statement = statements[0]
    forbidden = find_forbidden(statement)
    if forbidden:
        return None, forbidden
    if not isinstance(statement, exp.Select):
        return None, [ToolError.of(ErrorCode.NOT_SELECT, f"Only SELECT queries are allowed, found {type(statement).__name__}")]
    return statement, []


def find_forbidden(tree: exp.Expression) -> list[ToolError]:
    errors = []
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            errors.append(ToolError.of(ErrorCode.FORBIDDEN_OPERATION, f"Forbidden operation: {type(node).__name__.upper()}"))
    return errors


def check_constructs(tree: exp.Select) -> list[ToolError]:
    """Reject unsupported constructs, non-allow-listed functions, SELECT * and positional placeholders."""
    errors = []
    for node in tree.walk():
        for node_type, label in UNSUPPORTED_NODES.items():
            if isinstance(node, node_type):
                errors.append(ToolError.of(ErrorCode.UNSUPPORTED_CONSTRUCT, f"Unsupported construct: {label}"))
        if isinstance(node, exp.Func) and not isinstance(node, ALLOWED_FUNCTIONS):
            name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
            errors.append(ToolError.of(ErrorCode.FORBIDDEN_FUNCTION, f"Function not allowed: {name}"))
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
            errors.append(ToolError.of(ErrorCode.UNSUPPORTED_CONSTRUCT, "SELECT * is not allowed: name the columns explicitly"))
        if isinstance(node, exp.Placeholder) and not node.name:
            errors.append(ToolError.of(ErrorCode.PARAMETER_ERROR, "Positional '?' parameters are not allowed; use :name"))
    return errors


def table_references(tree: exp.Select) -> tuple[list[str], list[ToolError]]:
    """Fully qualified names of every physical table reference (CTE references excluded)."""
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    names, errors = [], []
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            errors.append(ToolError.of(ErrorCode.FORBIDDEN_FUNCTION, f"Table-valued functions are not allowed: {table.sql(DIALECT)[:80]}"))
            continue
        if not table.db and not table.catalog and table.name.lower() in cte_names:
            continue
        if not (table.catalog and table.db):
            errors.append(ToolError.of(ErrorCode.UNKNOWN_TABLE, f"Use the fully qualified name catalog.schema.table: {table.sql(DIALECT)[:80]}"))
            continue
        names.append(f"{table.catalog}.{table.db}.{table.name}".lower())
    return sorted(set(names)), errors


def check_parameters(tree: exp.Select, parameters: dict[str, ParameterValue]) -> list[ToolError]:
    """Every :name placeholder needs a value and every value needs a placeholder; values are simple scalars."""
    used = {p.name for p in tree.find_all(exp.Placeholder) if p.name}
    errors = []
    if missing := used - parameters.keys():
        errors.append(ToolError.of(ErrorCode.PARAMETER_ERROR, f"Missing parameter values: {sorted(missing)}"))
    if unused := parameters.keys() - used:
        errors.append(ToolError.of(ErrorCode.PARAMETER_ERROR, f"Parameters not used in the query: {sorted(unused)}"))
    for name, value in parameters.items():
        if isinstance(value, str) and len(value) > 200:
            errors.append(ToolError.of(ErrorCode.PARAMETER_ERROR, f"Parameter '{name}' is longer than 200 characters"))
    return errors


def enforce_limit(tree: exp.Select, max_rows: int) -> tuple[list[str], list[ToolError]]:
    """Require a literal LIMIT no larger than max_rows; add LIMIT max_rows when missing (a tightening only)."""
    limit = tree.args.get("limit")
    if limit is None:
        tree.limit(max_rows, copy=False)
        return [f"Added LIMIT {max_rows}"], []
    value = limit.expression
    if not (isinstance(value, exp.Literal) and value.is_int):
        return [], [ToolError.of(ErrorCode.LIMIT_EXCEEDED, "LIMIT must be an integer literal")]
    if int(value.this) > max_rows:
        return [], [ToolError.of(ErrorCode.LIMIT_EXCEEDED, f"LIMIT {value.this} exceeds the maximum of {max_rows} rows",
                                 requested=int(value.this), maximum=max_rows)]
    return [], []


def subquery_depth(tree: exp.Expression) -> int:
    return max((sum(isinstance(a, exp.Select) for a in _ancestors(node)) for node in tree.find_all(exp.Select)), default=0)


def _ancestors(node: exp.Expression):
    parent = node.parent
    while parent is not None:
        yield parent
        parent = parent.parent


def check_complexity(tree: exp.Select, limits: QueryLimits) -> tuple[dict[str, int], list[ToolError]]:
    measures = {
        "joins": len(list(tree.find_all(exp.Join))),
        "ctes": len(list(tree.find_all(exp.CTE))),
        "subquery_depth": subquery_depth(tree),
        "ast_nodes": sum(1 for _ in tree.walk()),
    }
    maxima = {"joins": limits.max_joins, "ctes": limits.max_ctes, "subquery_depth": limits.max_subquery_depth,
              "ast_nodes": limits.max_ast_nodes}
    errors = [ToolError.of(ErrorCode.COMPLEXITY_EXCEEDED, f"{name} = {measures[name]} exceeds the maximum of {maxima[name]}")
              for name in measures if measures[name] > maxima[name]]
    return measures, errors
