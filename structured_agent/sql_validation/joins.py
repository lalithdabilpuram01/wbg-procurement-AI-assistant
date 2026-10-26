"""Join approval and fan-out (double-counting) analysis, scope by scope.

Rules
-----
1. A join needs key equality (ON a.k = b.k). Cross joins and joins on non-key conditions are rejected.
2. A join between two base objects must match a registered relationship, or join on conformed keys (columns linked
   through registered relationships, e.g. every Gold fact's project_id) with at least one side unique on the key.
3. A join involving a CTE/subquery must use conformed keys. A grouped derived relation is unique on its GROUP BY keys,
   which is how "pre-aggregate to the shared key, then join" is recognised.
4. Uniqueness comes from the verified grain of each object (plus columns fixed by an equality filter, e.g. the
   country-context year). Exactly one source (the root) may keep its rows; every other source must be reachable from
   it through many-to-one or one-to-one steps. Otherwise the query mixes incompatible grains and is rejected.
5. Rows of non-root sources repeat. Value aggregates (SUM, AVG, percentiles, ...) over their columns are rejected,
   in this scope or in any outer scope that reads them through a CTE or subquery.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from sqlglot import exp
from sqlglot.optimizer.scope import Scope

from structured_agent.contracts import ErrorCode, ToolError
from structured_agent.registry.models import RegistryBundle, Relationship
from structured_agent.sql_validation.lineage import BaseColumn, LineageResolver, table_fqn

VALUE_AGGREGATES = (exp.Sum, exp.Avg, exp.Stddev, exp.Variance, exp.PercentileCont, exp.PercentileDisc, exp.Median)


@dataclass
class Edge:
    left: str
    right: str
    pairs: list[tuple[str, str]] = field(default_factory=list)  # (left column, right column)
    left_unique: bool = False
    right_unique: bool = False
    relationship_id: str | None = None

    def describe(self) -> str:
        on = " AND ".join(f"{self.left}.{a} = {self.right}.{b}" for a, b in self.pairs)
        kind = {(True, True): "1:1", (False, True): "N:1", (True, False): "1:N", (False, False): "N:M"}[
            (self.left_unique, self.right_unique)]
        basis = f"registered {self.relationship_id}" if self.relationship_id else "conformed key"
        return f"{self.left} -> {self.right} ON {on} ({kind}, {basis})"


def _conjuncts(condition: exp.Expression | None) -> list[exp.Expression]:
    if condition is None:
        return []
    return list(condition.flatten()) if isinstance(condition, exp.And) else [condition]


def _in_scope(node: exp.Expression, scope: Scope) -> bool:
    return node.find_ancestor(exp.Select) is scope.expression


class KeyDomains:
    """Groups base columns that hold the same key, linked through registered relationships (union-find)."""

    def __init__(self, relationships: list[Relationship]):
        self._parent: dict[BaseColumn, BaseColumn] = {}
        for rel in relationships:
            for a, b in zip(rel.from_columns, rel.to_columns):
                self._union((rel.from_object, a), (rel.to_object, b))

    def _find(self, column: BaseColumn) -> BaseColumn:
        self._parent.setdefault(column, column)
        while self._parent[column] != column:
            column = self._parent[column]
        return column

    def _union(self, a: BaseColumn, b: BaseColumn) -> None:
        self._parent[self._find(a)] = self._find(b)

    def conformed(self, a: BaseColumn, b: BaseColumn) -> bool:
        return a == b or (a in self._parent and b in self._parent and self._find(a) == self._find(b))


class JoinAnalyzer:
    def __init__(self, bundle: RegistryBundle, resolver: LineageResolver):
        self._bundle = bundle
        self._resolver = resolver
        self._domains = KeyDomains(bundle.relationships)
        self._repeated_outputs: dict[int, set[str]] = {}   # derived scope -> outputs whose rows repeat
        self.descriptions: list[str] = []

    # ------------------------------------------------------------------------------------------------ public entry point
    def analyze(self) -> list[ToolError]:
        errors: list[ToolError] = []
        for scope in self._resolver.scopes:  # children first, so derived facts are known before their parents
            errors += self._analyze_scope(scope)
        return errors

    # ---------------------------------------------------------------------------------------------------- one scope
    def _analyze_scope(self, scope: Scope) -> list[ToolError]:
        select = scope.expression
        joins = select.args.get("joins") or []
        repeated_aliases: set[str] = set()
        errors: list[ToolError] = []

        if joins:
            edges, errors = self._edges(scope, joins)
            if not errors:
                repeated_aliases, errors = self._root_and_repeated(scope, edges)
                self.descriptions += [e.describe() for e in edges]

        errors += self._check_aggregates(scope, repeated_aliases)
        self._record_repeated_outputs(scope, repeated_aliases)
        return errors

    def _edges(self, scope: Scope, joins: list[exp.Join]) -> tuple[list[Edge], list[ToolError]]:
        fixed = self._fixed_columns(scope)
        edges: dict[frozenset[str], Edge] = {}
        errors: list[ToolError] = []
        for join in joins:
            on = join.args.get("on")
            if on is None or (join.args.get("kind") or "").upper() == "CROSS":
                errors.append(ToolError.of(ErrorCode.UNAPPROVED_JOIN, f"Join without key equality: {join.sql()[:120]}"))
                continue
            for conjunct in _conjuncts(on):
                pair = self._column_pair(conjunct)
                if pair is None:
                    continue  # a filter inside ON (e.g. a fixed year); collected by _fixed_columns
                (la, lc), (ra, rc) = pair
                key = frozenset((la, ra))
                edge = edges.setdefault(key, Edge(left=la, right=ra))
                edge.pairs.append((lc, rc) if edge.left == la else (rc, lc))

        joined = {join.alias_or_name for join in joins}
        for alias in joined:
            if not any(alias in (e.left, e.right) for e in edges.values()):
                errors.append(ToolError.of(ErrorCode.UNAPPROVED_JOIN, f"'{alias}' is joined without a key equality"))

        for edge in edges.values():
            errors += self._approve(scope, edge, fixed)
        return list(edges.values()), errors

    @staticmethod
    def _column_pair(conjunct: exp.Expression):
        if isinstance(conjunct, (exp.EQ, exp.NullSafeEQ)) and isinstance(conjunct.this, exp.Column) \
                and isinstance(conjunct.expression, exp.Column) and conjunct.this.table != conjunct.expression.table:
            return ((conjunct.this.table, conjunct.this.name.lower()), (conjunct.expression.table, conjunct.expression.name.lower()))
        return None

    def _fixed_columns(self, scope: Scope) -> dict[str, set[str]]:
        """Columns fixed to one value by an equality with a literal or :parameter (in WHERE or any ON)."""
        conditions = [scope.expression.args.get("where")] + [j.args.get("on") for j in scope.expression.args.get("joins") or []]
        fixed: dict[str, set[str]] = {}
        for condition in conditions:
            node = condition.this if isinstance(condition, exp.Where) else condition
            for conjunct in _conjuncts(node):
                if isinstance(conjunct, exp.EQ):
                    column, value = conjunct.this, conjunct.expression
                    if isinstance(value, exp.Column):
                        column, value = value, column
                    if isinstance(column, exp.Column) and isinstance(value, (exp.Literal, exp.Placeholder)):
                        fixed.setdefault(column.table, set()).add(column.name.lower())
        return fixed

    # ---------------------------------------------------------------------------------------------------- approval
    def _approve(self, scope: Scope, edge: Edge, fixed: dict[str, set[str]]) -> list[ToolError]:
        left_source, right_source = scope.sources.get(edge.left), scope.sources.get(edge.right)
        left_cols = [a for a, _ in edge.pairs]
        right_cols = [b for _, b in edge.pairs]
        edge.left_unique = self._unique(left_source, set(left_cols) | fixed.get(edge.left, set()))
        edge.right_unique = self._unique(right_source, set(right_cols) | fixed.get(edge.right, set()))

        if isinstance(left_source, exp.Table) and isinstance(right_source, exp.Table):
            relationship = self._registered(table_fqn(left_source), left_cols, table_fqn(right_source), right_cols)
            if relationship is not None:
                edge.relationship_id = relationship.relationship_id
                return self._required_conditions(relationship, edge, scope, fixed)

        for left_col, right_col in edge.pairs:
            left_base = self._single_base(scope, edge.left, left_col)
            right_base = self._single_base(scope, edge.right, right_col)
            if left_base is None or right_base is None or not self._domains.conformed(left_base, right_base):
                return [ToolError.of(ErrorCode.UNAPPROVED_JOIN,
                                     f"No approved relationship for {edge.left}.{left_col} = {edge.right}.{right_col}")]
        if not (edge.left_unique or edge.right_unique):
            return [ToolError.of(ErrorCode.FAN_OUT_RISK,
                                 f"Many-to-many join {edge.describe()}: rows of both sides repeat. Pre-aggregate one side "
                                 f"to the join key (GROUP BY {', '.join(right_cols)}) before joining.")]
        return []

    def _registered(self, left_fqn: str, left_cols: list[str], right_fqn: str, right_cols: list[str]) -> Relationship | None:
        pairs = set(zip(left_cols, right_cols))
        for rel in self._bundle.relationships:
            forward = {(a, b) for a, b in zip(rel.from_columns, rel.to_columns)}
            if rel.from_object == left_fqn and rel.to_object == right_fqn and forward <= pairs:
                return rel
            if rel.from_object == right_fqn and rel.to_object == left_fqn and {(b, a) for a, b in forward} <= pairs:
                return rel
        return None

    def _required_conditions(self, rel: Relationship, edge: Edge, scope: Scope, fixed: dict[str, set[str]]) -> list[ToolError]:
        errors = []
        for condition in rel.required_conditions:
            alias = edge.left if self._alias_object(scope, edge.left) == condition.object else edge.right
            joined_cols = {a for a, _ in edge.pairs} if alias == edge.left else {b for _, b in edge.pairs}
            if condition.column not in fixed.get(alias, set()) | joined_cols:
                errors.append(ToolError.of(ErrorCode.MISSING_REQUIRED_FILTER,
                                           f"Join {rel.relationship_id} requires {alias}.{condition.column} = <value>: {condition.reason}"))
        return errors

    def _alias_object(self, scope: Scope, alias: str) -> str | None:
        source = scope.sources.get(alias)
        return table_fqn(source) if isinstance(source, exp.Table) else None

    def _single_base(self, scope: Scope, alias: str, column: str) -> BaseColumn | None:
        lineage = self._resolver.base_columns(scope, exp.column(column, table=alias))
        return next(iter(lineage)) if len(lineage) == 1 else None

    # --------------------------------------------------------------------------------------------------- uniqueness
    def _unique(self, source, columns: set[str]) -> bool:
        if isinstance(source, exp.Table):
            obj = self._bundle.object(table_fqn(source))
            return bool(obj and obj.grain_status == "verified" and set(obj.grain_columns) <= columns)
        if isinstance(source, Scope):
            return any(key <= columns for key in self._derived_unique_keys(source))
        return False

    def _derived_unique_keys(self, scope: Scope) -> list[set[str]]:
        """Sets of output names on which a CTE/subquery has at most one row."""
        select = scope.expression
        projections = {self._plain_column(p): p.alias_or_name.lower() for p in select.expressions if self._plain_column(p)}
        group = select.args.get("group")
        if group:
            names = [projections.get(g.name.lower()) if isinstance(g, exp.Column) else None for g in group.expressions]
            return [set(names)] if all(names) else []
        if any(isinstance(p.find(exp.AggFunc), exp.AggFunc) for p in select.expressions):
            return [set()]  # aggregate without GROUP BY: a single row
        if select.args.get("distinct"):
            return [{p.alias_or_name.lower() for p in select.expressions}]
        sources = list(scope.sources.values())
        if len(sources) == 1 and isinstance(sources[0], exp.Table) and not select.args.get("joins"):
            obj = self._bundle.object(table_fqn(sources[0]))
            if obj and obj.grain_status == "verified" and set(obj.grain_columns) <= projections.keys():
                return [{projections[c] for c in obj.grain_columns}]
        return []

    @staticmethod
    def _plain_column(projection: exp.Expression) -> str | None:
        node = projection.this if isinstance(projection, exp.Alias) else projection
        return node.name.lower() if isinstance(node, exp.Column) else None

    # ------------------------------------------------------------------------------------------------ root / repeat
    def _root_and_repeated(self, scope: Scope, edges: list[Edge]) -> tuple[set[str], list[ToolError]]:
        """Find a root source whose rows are kept; return the aliases whose rows repeat."""
        aliases = list(scope.selected_sources.keys())  # sources in FROM / JOIN (not every visible CTE)
        steps: dict[str, list[tuple[str, bool]]] = {a: [] for a in aliases}   # alias -> [(neighbour, repeats)]
        for e in edges:
            if e.right_unique:
                steps[e.left].append((e.right, not e.left_unique))
            if e.left_unique:
                steps[e.right].append((e.left, not e.right_unique))

        for root in aliases:
            repeated = self._reach(root, steps)
            if repeated is not None and len(repeated) == len(aliases):
                return {a for a, r in repeated.items() if r}, []
        return set(), [ToolError.of(
            ErrorCode.INCOMPATIBLE_GRAIN,
            "The joined sources have incompatible grains (two or more sources are 'many' sides of the same key). "
            "Aggregate each source to the shared key in a CTE before joining. Joins: " + "; ".join(e.describe() for e in edges))]

    @staticmethod
    def _reach(root: str, steps: dict[str, list[tuple[str, bool]]]) -> dict[str, bool] | None:
        repeated = {root: False}
        queue = deque([root])
        while queue:
            current = queue.popleft()
            for neighbour, repeats in steps[current]:
                if neighbour not in repeated:
                    repeated[neighbour] = repeated[current] or repeats
                    queue.append(neighbour)
        return repeated

    # -------------------------------------------------------------------------------------------------- aggregates
    def _check_aggregates(self, scope: Scope, repeated_aliases: set[str]) -> list[ToolError]:
        errors = []
        for aggregate in scope.expression.find_all(*VALUE_AGGREGATES):
            if not _in_scope(aggregate, scope):
                continue
            for column in aggregate.find_all(exp.Column):
                source = scope.sources.get(column.table)
                derived_repeat = isinstance(source, Scope) and column.name.lower() in self._repeated_outputs.get(id(source), set())
                if column.table in repeated_aliases or derived_repeat:
                    errors.append(ToolError.of(
                        ErrorCode.FAN_OUT_RISK,
                        f"{type(aggregate).__name__.upper()} over {column.table}.{column.name} would count repeated rows "
                        f"(one-to-many join). Aggregate it at its own grain first."))
        return errors

    def _record_repeated_outputs(self, scope: Scope, repeated_aliases: set[str]) -> None:
        select = scope.expression
        if select.args.get("group") or any(_in_scope(a, scope) for a in select.find_all(exp.AggFunc)):
            return  # aggregated output: grain resolved here (fan-out inside was already checked)
        repeated = set()
        for projection in select.expressions:
            for column in projection.find_all(exp.Column):
                source = scope.sources.get(column.table)
                inner = isinstance(source, Scope) and column.name.lower() in self._repeated_outputs.get(id(source), set())
                if column.table in repeated_aliases or inner:
                    repeated.add(projection.alias_or_name.lower())
        self._repeated_outputs[id(scope)] = repeated
