"""Column resolution and lineage across CTEs and subqueries.

The parsed query is qualified against the registry schema (sqlglot `qualify`), which fails on unknown or ambiguous
columns. Each column is then traced through derived relations (CTEs, subqueries) to the base columns it comes from.
"""
from __future__ import annotations

from sqlglot import exp
from sqlglot.errors import OptimizeError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from structured_agent.contracts import ErrorCode, ToolError
from structured_agent.registry.models import RegisteredObject
from structured_agent.sql_validation.structure import DIALECT

BaseColumn = tuple[str, str]  # (object fqn, column name)


def registry_schema(objects: list[RegisteredObject]) -> dict:
    """Nested {catalog: {schema: {table: {column: type}}}} mapping for sqlglot (types are not needed here)."""
    schema: dict = {}
    for obj in objects:
        catalog, db, name = obj.fqn.split(".")
        schema.setdefault(catalog, {}).setdefault(db, {})[name] = {c.name: "string" for c in obj.columns}
    return schema


def qualify_columns(tree: exp.Select, schema: dict) -> tuple[exp.Select | None, list[ToolError]]:
    """Qualify every column against the registry schema; an unknown or ambiguous column becomes UNKNOWN_COLUMN."""
    try:
        qualified = qualify(tree.copy(), schema=schema, dialect=DIALECT, validate_qualify_columns=True,
                            quote_identifiers=False, identify=False)
    except OptimizeError as error:
        return None, [ToolError.of(ErrorCode.UNKNOWN_COLUMN, f"Column resolution failed: {str(error)[:200]}")]
    return qualified, []


def table_fqn(table: exp.Table) -> str:
    return f"{table.catalog}.{table.db}.{table.name}".lower()


class LineageResolver:
    """Traces columns of a qualified query back to registered base columns."""

    def __init__(self, qualified: exp.Select):
        self.root = qualified
        self.scopes = traverse_scope(qualified)  # children before parents

    def base_columns(self, scope: Scope, column: exp.Column) -> set[BaseColumn]:
        """Registered base columns behind one column reference, followed through CTEs and subqueries."""
        source = self._source(scope, column.table)
        if isinstance(source, exp.Table):
            return {(table_fqn(source), column.name.lower())}
        if isinstance(source, Scope):
            return self.output_lineage(source, column.name)
        return set()

    def output_lineage(self, scope: Scope, output_name: str) -> set[BaseColumn]:
        """Base columns behind one output column of a derived scope."""
        for projection in scope.expression.expressions:
            if projection.alias_or_name.lower() == output_name.lower():
                return self.expression_lineage(scope, projection)
        return set()

    def expression_lineage(self, scope: Scope, expression: exp.Expression) -> set[BaseColumn]:
        """Base columns behind every column used in an expression."""
        lineage: set[BaseColumn] = set()
        for column in expression.find_all(exp.Column):
            lineage |= self.base_columns(scope, column)
        return lineage

    def _source(self, scope: Scope, alias: str):
        """Find what an alias refers to (table or derived scope), looking outward through enclosing scopes."""
        current = scope
        while current is not None:  # correlated references resolve in an enclosing scope
            if alias in current.sources:
                return current.sources[alias]
            current = current.parent
        return None

    def root_scope(self) -> Scope:
        return self.scopes[-1]

    def all_base_columns(self) -> set[BaseColumn]:
        """Every base column the query touches, in any scope or clause."""
        used: set[BaseColumn] = set()
        for scope in self.scopes:
            for column in scope.columns:
                used |= self.base_columns(scope, column)
        return used

    def root_outputs(self) -> dict[str, set[BaseColumn]]:
        root = self.root_scope()
        return {p.alias_or_name: self.expression_lineage(root, p) for p in root.expression.expressions}


    def output_aggregate(self, scope: Scope, expression: exp.Expression) -> str | None:
        """Aggregate that produces an output, following plain column references into CTEs and subqueries."""
        node = expression.this if isinstance(expression, exp.Alias) else expression
        if isinstance(node, exp.Coalesce):
            node = node.this
        if isinstance(node, exp.AggFunc):
            return type(node).__name__
        if isinstance(node, exp.Column):
            source = self._source(scope, node.table)
            if isinstance(source, Scope):
                for projection in source.expression.expressions:
                    if projection.alias_or_name.lower() == node.name.lower():
                        return self.output_aggregate(source, projection)
        return None
