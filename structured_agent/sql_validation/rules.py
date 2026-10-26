"""Data-policy and business-logic rules evaluated on the resolved query.

* Objects: registered and authorized for the principal (unregistered schemas such as Bronze or information_schema are
  unauthorized; unregistered names inside the registered schemas are unknown).
* Columns: prohibited columns are never usable; others need the right classification for how they are used
  ('output' if they reach the result, 'filter' otherwise).
* Required filters: e.g. KPI views whose grouping_level rows are subtotals must be filtered to one level.
* Measures: SUM over non-additive columns (percentiles, rates, scores, dates) and arithmetic that mixes measure
  families (contract values vs IBRD/IDA financing vs project commitments) are rejected.
"""
from __future__ import annotations

from sqlglot import exp

from structured_agent.contracts import ErrorCode, ToolError
from structured_agent.policy import AccessPolicy, Principal
from structured_agent.registry.models import RegistryBundle
from structured_agent.sql_validation.lineage import BaseColumn, LineageResolver, table_fqn

REGISTERED_SCHEMAS = {"workspace.wbg_gold", "workspace.wbg_silver"}
ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Coalesce)


def check_objects(bundle: RegistryBundle, policy: AccessPolicy, principal: Principal, fqns: list[str]) -> list[ToolError]:
    errors = []
    for fqn in fqns:
        obj = bundle.object(fqn)
        if obj is None:
            schema = fqn.rsplit(".", 1)[0]
            code = ErrorCode.UNKNOWN_TABLE if schema in REGISTERED_SCHEMAS else ErrorCode.UNAUTHORIZED_OBJECT
            errors.append(ToolError.of(code, f"'{fqn}' is not a registered object" +
                                       ("" if code == ErrorCode.UNKNOWN_TABLE else " and is outside the approved schemas")))
        elif not policy.can_use_object(principal, obj):
            errors.append(ToolError.of(ErrorCode.UNAUTHORIZED_OBJECT, f"Principal is not authorized to query '{fqn}'"))
    return errors


def check_columns(bundle: RegistryBundle, policy: AccessPolicy, principal: Principal,
                  used: set[BaseColumn], output: set[BaseColumn]) -> list[ToolError]:
    errors = []
    for fqn, name in sorted(used):
        obj = bundle.object(fqn)
        column = obj.column(name) if obj else None
        if column is None:
            errors.append(ToolError.of(ErrorCode.UNKNOWN_COLUMN, f"Unknown column {fqn}.{name}"))
        elif column.classification == "prohibited":
            errors.append(ToolError.of(ErrorCode.PROHIBITED_COLUMN,
                                       f"{fqn}.{name} is prohibited ({column.policy_source}) and can never be queried"))
        else:
            use = "output" if (fqn, name) in output else "filter"
            if not policy.can_use_column(principal, column, use):
                errors.append(ToolError.of(ErrorCode.UNAUTHORIZED_COLUMN,
                                           f"{fqn}.{name} ({column.classification}) may not be used as {use} by this principal"))
    return errors


def check_required_filters(bundle: RegistryBundle, resolver: LineageResolver) -> tuple[list[str], list[ToolError]]:
    """Each scope reading an object with required filters must fix that column with '= literal or :parameter'."""
    applied, errors = [], []
    for scope in resolver.scopes:
        where = scope.expression.args.get("where")
        conjuncts = list(where.this.flatten()) if where and isinstance(where.this, exp.And) else ([where.this] if where else [])
        fixed = {(c.this.table, c.this.name.lower()) for c in conjuncts
                 if isinstance(c, exp.EQ) and isinstance(c.this, exp.Column) and isinstance(c.expression, (exp.Literal, exp.Placeholder))}
        for alias, source in scope.sources.items():
            obj = bundle.object(table_fqn(source)) if isinstance(source, exp.Table) else None
            for required in (obj.required_filters if obj else []):
                if (alias, required.column) in fixed:
                    applied.append(f"{obj.name}.{required.column} fixed (required)")
                else:
                    errors.append(ToolError.of(ErrorCode.MISSING_REQUIRED_FILTER,
                                               f"{obj.name} requires WHERE {required.column} = <value>: {required.reason}"))
    return applied, errors


def check_measures(bundle: RegistryBundle, resolver: LineageResolver) -> list[ToolError]:
    errors = []
    for scope in resolver.scopes:
        for node in scope.expression.find_all(exp.Sum, *ARITHMETIC):
            if node.find_ancestor(exp.Select) is not scope.expression:
                continue
            if isinstance(node, exp.Sum):
                for fqn, name in resolver.expression_lineage(scope, node.this):
                    column = bundle.object(fqn).column(name)
                    if column is not None and not column.additive and column.data_type.startswith(("decimal", "double", "int", "bigint", "float", "date")):
                        errors.append(ToolError.of(ErrorCode.NON_ADDITIVE_AGGREGATE,
                                                   f"SUM over {name} is not meaningful: the column is not additive"))
            else:
                families = {bundle.object(f).column(n).measure_family for f, n in resolver.expression_lineage(scope, node)}
                families.discard(None)
                if len(families) > 1:
                    errors.append(ToolError.of(ErrorCode.MIXED_MEASURE_FAMILIES,
                                               f"Expression combines different measures {sorted(families)}; they are never combined"))
    return errors


def unit_for(bundle: RegistryBundle, lineage: set[BaseColumn], aggregate: str | None) -> str | None:
    if aggregate in ("Count", "CountIf", "ApproxDistinct"):
        return "count"
    units = {bundle.object(f).column(n).unit for f, n in lineage}
    return units.pop() if len(units) == 1 else None
