"""SQL Execution Validator: the only way SQL can be approved for execution.

Stages run in order and stop at the first stage that fails (later stages depend on earlier ones). Parsing alone never
approves a query: a query is approved only when every stage passes. Any unexpected exception rejects the query.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from sqlglot import exp

from structured_agent.config import QueryLimits
from structured_agent.contracts import CheckResult, ErrorCode, OutputColumn, ParameterValue, ToolError, ValidationReport
from structured_agent.policy import AccessPolicy, Principal
from structured_agent.observability import log_failure
from structured_agent.registry.models import RegistryBundle
from structured_agent.sql_validation import rules, structure
from structured_agent.sql_validation.joins import JoinAnalyzer
from structured_agent.sql_validation.lineage import LineageResolver, qualify_columns, registry_schema


def fingerprint(sql: str, parameters: dict[str, ParameterValue]) -> str:
    payload = sql + "\n" + json.dumps(parameters, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class _Stages:
    """Records one CheckResult per stage on the report."""

    def __init__(self, report: ValidationReport):
        self.report = report

    def passed(self, name: str, errors: list[ToolError], detail: str = "") -> bool:
        ok = not errors
        message = detail if ok else "; ".join(e.message for e in errors)[:500]
        self.report.checks.append(CheckResult(name=name, passed=ok, detail=message))
        self.report.errors.extend(errors)
        return ok


class SqlValidator:
    def __init__(self, bundle: RegistryBundle, policy: AccessPolicy, limits: QueryLimits):
        self._bundle = bundle
        self._policy = policy
        self._limits = limits
        self._schema = registry_schema(bundle.objects)

    def validate(self, sql: str, parameters: dict[str, ParameterValue], principal: Principal,
                 max_rows: int | None = None) -> ValidationReport:
        report = ValidationReport(validation_id="val_" + uuid.uuid4().hex[:16], approved=False,
                                  sql_sha256=fingerprint(sql, parameters), parameters=parameters)
        try:
            self._run(sql, parameters, principal, max_rows, report)
        except Exception as error:  # fail closed
            log_failure("sql validator failed", error)
            report.approved = False
            report.validated_sql = None
            report.errors.append(ToolError.of(ErrorCode.INTERNAL_ERROR, f"Validator error: {type(error).__name__}"))
            report.checks.append(CheckResult(name="validator_internal", passed=False, detail=type(error).__name__))
        return report

    def _run(self, sql: str, parameters: dict[str, ParameterValue], principal: Principal, max_rows: int | None,
             report: ValidationReport) -> None:
        stage = _Stages(report)

        tree, errors = structure.parse_single_select(sql)
        if not stage.passed("single_select_statement", errors, "one SELECT statement; no DDL/DML/commands"):
            return
        if not stage.passed("allowed_constructs_and_functions", structure.check_constructs(tree)):
            return
        if not stage.passed("parameters", structure.check_parameters(tree, parameters)):
            return

        fqns, errors = structure.table_references(tree)
        errors += rules.check_objects(self._bundle, self._policy, principal, fqns)
        if not stage.passed("registered_and_authorized_objects", errors, ", ".join(fqns)):
            return
        report.objects = fqns

        qualified, errors = qualify_columns(tree, self._schema)
        if not stage.passed("column_resolution", errors):
            return
        resolver = LineageResolver(qualified)
        outputs = resolver.root_outputs()
        output_columns = set().union(*outputs.values()) if outputs else set()
        errors = rules.check_columns(self._bundle, self._policy, principal, resolver.all_base_columns(), output_columns)
        if not stage.passed("column_authorization_and_classification", errors):
            return

        joins = JoinAnalyzer(self._bundle, resolver)
        errors = joins.analyze()
        report.joins = joins.descriptions
        if not stage.passed("approved_joins_and_grain", errors, f"{len(joins.descriptions)} join(s) approved"):
            return

        applied, errors = rules.check_required_filters(self._bundle, resolver)
        report.applied_filters = applied + self._where_filters(qualified)
        if not stage.passed("required_filters", errors):
            return
        if not stage.passed("measure_rules", rules.check_measures(self._bundle, resolver)):
            return

        limit = min(x for x in (self._limits.max_rows, self._policy.max_rows(principal, self._limits.max_rows), max_rows) if x)
        rewrites, errors = structure.enforce_limit(tree, limit)
        report.applied_rewrites = rewrites
        if not stage.passed("row_limit", errors, f"LIMIT <= {limit}"):
            return

        measures, errors = structure.check_complexity(tree, self._limits)
        if not stage.passed("complexity_budget", errors, json.dumps(measures)):
            return

        report.estimated_scanned_rows = sum(self._bundle.object(f).row_count for f in fqns)
        budget_errors = [] if report.estimated_scanned_rows <= self._limits.max_scanned_rows else [ToolError.of(
            ErrorCode.SCAN_BUDGET_EXCEEDED,
            f"Reads {report.estimated_scanned_rows:,} registered rows; the budget is {self._limits.max_scanned_rows:,}")]
        if not stage.passed("scan_budget", budget_errors, f"{report.estimated_scanned_rows:,} rows"):
            return

        report.output_columns = self._output_columns(resolver, outputs)
        report.validated_sql = tree.sql(dialect=structure.DIALECT)
        report.sql_sha256 = fingerprint(report.validated_sql, parameters)
        report.approved = True

    def _output_columns(self, resolver: LineageResolver, outputs: dict) -> list[OutputColumn]:
        columns = []
        root = resolver.root_scope()
        for projection in root.expression.expressions:
            lineage = outputs[projection.alias_or_name]
            aggregate = resolver.output_aggregate(root, projection)
            columns.append(OutputColumn(
                name=projection.alias_or_name, source_columns=sorted(f"{f}.{c}" for f, c in lineage), aggregate=aggregate,
                unit=rules.unit_for(self._bundle, lineage, aggregate),
                classification=max((self._bundle.object(f).column(c).classification for f, c in lineage), default="public",
                                   key=["public", "lineage", "restricted", "prohibited"].index)))
        return columns

    @staticmethod
    def _where_filters(qualified: exp.Select) -> list[str]:
        """Human-readable WHERE conditions of every scope (for the result contract)."""
        return [where.this.sql(dialect=structure.DIALECT) for where in qualified.find_all(exp.Where)]
