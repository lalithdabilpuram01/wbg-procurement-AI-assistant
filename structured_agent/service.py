"""StructuredDataService: the seven approved operations behind one invocation path.

Every operation goes through `_invoke`, which
  * assigns a tool-invocation id (the caller's request id is kept, or a new one is created),
  * validates the request against its Pydantic contract,
  * resolves and authorizes the principal (unknown principal or workspace -> refused),
  * runs the handler inside a tracing span and converts any exception into a typed, safe error,
  * writes one sanitised audit event.
SQL execution is retried zero times; generation may be corrected once (generation.py).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from structured_agent.config import Settings, load_settings
from structured_agent.contracts import (ErrorCode, ExecuteSqlRequest, MetricDefinitionRequest, MetricDefinitionResponse,
                                        ObjectRequest, QueryResult, SearchTableRegistryRequest, SearchTableRegistryResponse,
                                        SqlRequest, TableRelationshipsResponse, TableSchemaResponse, ToolError, ToolResponse,
                                        ValidateSqlResponse, VerifiedSqlExamplesRequest, VerifiedSqlExamplesResponse)
from structured_agent.execution.executor import DatabricksSparkExecutor, SqlExecutor
from structured_agent.execution.gateway import ExecutionGateway
from structured_agent.observability import JsonlAuditSink, Telemetry, log_failure, principal_hash
from structured_agent.policy import AccessPolicy, AuthorizationError, Principal
from structured_agent.registry.models import RegisteredObject, RegistryBundle
from structured_agent.registry.store import RegistryStore
from structured_agent.registry.vector_index import EmbeddingModel
from structured_agent.search import RegistrySearch, tokenize
from structured_agent.sql_validation.validator import SqlValidator


# Error codes that mean the principal was refused access to the request, an object or a column.
AUTHORIZATION_DENIALS = {ErrorCode.UNAUTHORIZED_PRINCIPAL, ErrorCode.UNAUTHORIZED_OBJECT, ErrorCode.UNAUTHORIZED_COLUMN,
                         ErrorCode.PROHIBITED_COLUMN}
# The validator stage after which object and column authorization have both been evaluated.
LAST_AUTHORIZATION_STAGE = "column_authorization_and_classification"


@dataclass(frozen=True)
class RequestContext:
    principal_id: str
    workspace: str
    request_id: str = field(default_factory=lambda: "req_" + uuid.uuid4().hex[:16])


class StructuredDataService:
    def __init__(self, settings: Settings, bundle: RegistryBundle, search: RegistrySearch, policy: AccessPolicy,
                 validator: SqlValidator, gateway: ExecutionGateway, telemetry: Telemetry):
        self.settings = settings
        self.bundle = bundle
        self._search = search
        self._policy = policy
        self._validator = validator
        self._gateway = gateway
        self._telemetry = telemetry

    @classmethod
    def from_environment(cls, settings: Settings | None = None, executor: SqlExecutor | None = None) -> "StructuredDataService":
        """Load the registry from PostgreSQL (read-only role) and wire every component."""
        settings = settings or load_settings()
        store = RegistryStore(settings.registry_dsn)
        bundle = store.load_bundle()
        documents, index = store.load_documents_and_index()
        policy = AccessPolicy()
        search = RegistrySearch(bundle, documents, index, EmbeddingModel(index.model_id), policy)
        validator = SqlValidator(bundle, policy, settings.limits)
        gateway = ExecutionGateway(settings, bundle, validator, executor or DatabricksSparkExecutor(settings.databricks_profile))
        return cls(settings, bundle, search, policy, validator, gateway, Telemetry(JsonlAuditSink(settings.audit_log_path)))

    # -------------------------------------------------------------------------------------------- common invocation
    def _invoke(self, tool: str, ctx: RequestContext, request_model: type[BaseModel], raw: dict[str, Any],
                response_cls: type[ToolResponse], handler: Callable[[Any, Principal, dict], ToolResponse]) -> ToolResponse:
        invocation_id = "inv_" + uuid.uuid4().hex[:16]
        base = {"request_id": ctx.request_id, "invocation_id": invocation_id, "tool": tool}
        started = time.perf_counter()
        with self._telemetry.span(tool, ctx.request_id, invocation_id) as span:
            response = self._handle(ctx, request_model, raw, response_cls, handler, base)
            response.duration_ms = round(1000 * (time.perf_counter() - started))
            attributes = self._audit_attributes(response)
            attributes["principal_hash"] = principal_hash(ctx.principal_id)
            self._telemetry.annotate(span, **attributes)
            self._telemetry.audit(ctx.request_id, invocation_id, tool, **attributes)
        return response

    def _handle(self, ctx, request_model, raw, response_cls, handler, base) -> ToolResponse:
        try:
            request = request_model.model_validate(raw)
        except ValidationError as error:
            fields = sorted({".".join(map(str, e["loc"])) for e in error.errors()})
            return response_cls(**base, status="error", errors=[ToolError.of(ErrorCode.INVALID_REQUEST, f"Invalid request fields: {fields}")])
        try:
            principal = self._policy.resolve(ctx.principal_id, ctx.workspace)
        except AuthorizationError as error:
            return response_cls(**base, status="error", errors=[ToolError.of(ErrorCode.UNAUTHORIZED_PRINCIPAL, str(error))])
        try:
            return handler(request, principal, base)
        except Exception as error:  # never leak internals or data in the message
            log_failure("structured tool failed", error, tool=getattr(request, "tool", None))
            return response_cls(**base, status="error", errors=[ToolError.of(ErrorCode.INTERNAL_ERROR, f"Internal error ({type(error).__name__})")])

    def audit_protocol_rejection(self, tool: str, principal_id: str, request_id: str | None, duration_ms: int) -> None:
        """Audit a tool call that the MCP layer rejected before it reached this service (e.g. invalid arguments)."""
        request_id = request_id or "req_" + uuid.uuid4().hex[:16]
        invocation_id = "inv_" + uuid.uuid4().hex[:16]
        self._telemetry.audit(request_id, invocation_id, tool, tool=tool, status="error", duration_ms=duration_ms,
                              error_codes=["INVALID_TOOL_ARGUMENTS"], authorization="not_evaluated",
                              validation_status="not_run", principal_hash=principal_hash(principal_id))

    @staticmethod
    def _authorization_result(response: ToolResponse) -> str:
        """granted | denied | not_evaluated (request invalid, or SQL rejected before the authorization stages ran)."""
        codes = {e.code for e in response.errors}
        if codes & AUTHORIZATION_DENIALS:
            return "denied"
        if ErrorCode.INVALID_REQUEST in codes:
            return "not_evaluated"
        checks = response.report.checks if isinstance(response, ValidateSqlResponse) and response.report else \
            response.validation_checks if isinstance(response, QueryResult) else None
        if checks is not None and not any(c.name == LAST_AUTHORIZATION_STAGE and c.passed for c in checks):
            return "not_evaluated"
        return "granted"

    @staticmethod
    def _validation_status(response: ToolResponse) -> str | None:
        """approved | rejected | not_run for SQL tools; None for registry tools."""
        if isinstance(response, ValidateSqlResponse):
            checks = response.report.checks if response.report else []
        elif isinstance(response, QueryResult):
            checks = response.validation_checks
        else:
            return None
        if not checks:
            return "not_run"
        return "approved" if all(c.passed for c in checks) else "rejected"

    @classmethod
    def _audit_attributes(cls, response: ToolResponse) -> dict[str, Any]:
        attributes: dict[str, Any] = {"tool": response.tool, "status": response.status, "duration_ms": response.duration_ms,
                                      "error_codes": [e.code.value for e in response.errors],
                                      "authorization": cls._authorization_result(response),
                                      "validation_status": cls._validation_status(response)}
        if isinstance(response, SearchTableRegistryResponse):
            attributes |= {"candidate_objects": [c.object_fqn for c in response.candidates],
                           "metric_ids": [m.metric_id for m in response.metrics],
                           "ambiguity_group": response.ambiguity.group if response.ambiguity else None}
        if isinstance(response, ValidateSqlResponse) and response.report:
            attributes |= {"approved": response.report.approved, "validation_id": response.report.validation_id,
                           "objects": response.report.objects, "sql_sha256": response.report.sql_sha256,
                           "checks_failed": [c.name for c in response.report.checks if not c.passed]}
        if isinstance(response, QueryResult):
            attributes |= {"validation_id": response.validation_id, "query_id": response.query_id, "row_count": response.row_count,
                           "objects": response.source_objects, "truncated": response.truncated,
                           "result_checks_passed": all(c.passed for c in response.result_checks) if response.result_checks else None}
            if response.provenance:
                attributes |= {"credential_mode": response.provenance.credential_mode,
                               "production_read_only": response.provenance.production_read_only,
                               "registry_build_id": response.provenance.registry_build_id}
        if isinstance(response, TableSchemaResponse):
            attributes["objects"] = [response.object_fqn] if response.object_fqn else []
        return attributes

    # ------------------------------------------------------------------------------------------- object authorization
    def _authorized_object(self, fqn: str, principal: Principal) -> tuple[RegisteredObject | None, ToolError | None]:
        obj = self.bundle.object(fqn.lower())
        if obj is None:
            return None, ToolError.of(ErrorCode.NOT_FOUND, f"'{fqn}' is not a registered object")
        if not self._policy.can_use_object(principal, obj):
            return None, ToolError.of(ErrorCode.UNAUTHORIZED_OBJECT, f"Principal is not authorized for '{fqn}'")
        return obj, None

    def _usable(self, principal: Principal, column) -> bool:
        return self._policy.can_use_column(principal, column, "output") or self._policy.can_use_column(principal, column, "filter")

    # ------------------------------------------------------------------------------------------------ the operations
    def search_table_registry(self, ctx: RequestContext, **raw: Any) -> SearchTableRegistryResponse:
        def handler(request: SearchTableRegistryRequest, principal: Principal, base: dict):
            candidates, metrics, ambiguity = self._search.search(request.query, principal, request.top_k)
            status = "needs_clarification" if ambiguity else ("ok" if candidates else "not_found")
            return SearchTableRegistryResponse(**base, status=status, candidates=candidates, metrics=metrics, ambiguity=ambiguity)
        return self._invoke("search_table_registry", ctx, SearchTableRegistryRequest, raw, SearchTableRegistryResponse, handler)

    def get_table_schema(self, ctx: RequestContext, **raw: Any) -> TableSchemaResponse:
        def handler(request: ObjectRequest, principal: Principal, base: dict):
            obj, error = self._authorized_object(request.object_fqn, principal)
            if error:
                return TableSchemaResponse(**base, status="not_found" if error.code == ErrorCode.NOT_FOUND else "rejected", errors=[error])
            visible = [c for c in obj.columns if self._usable(principal, c)]
            return TableSchemaResponse(
                **base, status="ok", object_fqn=obj.fqn, logical_name=obj.logical_name, short_description=obj.short_description,
                grain=obj.grain, grain_columns=obj.grain_columns, grain_status=obj.grain_status, row_count=obj.row_count,
                certified_aggregate=obj.certified_aggregate, required_filters=obj.required_filters, columns=visible,
                hidden_column_count=len(obj.columns) - len(visible), limitations=obj.limitations,
                provenance={"description_source": obj.description_source, "grain_source": obj.grain_source,
                            "check_constraints": obj.provenance.get("check_constraints", []),
                            "registry_build_id": self.bundle.build.build_id})
        return self._invoke("get_table_schema", ctx, ObjectRequest, raw, TableSchemaResponse, handler)

    def get_table_relationships(self, ctx: RequestContext, **raw: Any) -> TableRelationshipsResponse:
        def handler(request: ObjectRequest, principal: Principal, base: dict):
            obj, error = self._authorized_object(request.object_fqn, principal)
            if error:
                return TableRelationshipsResponse(**base, status="not_found" if error.code == ErrorCode.NOT_FOUND else "rejected", errors=[error])
            relationships = [r for r in self.bundle.relationships if obj.fqn in (r.from_object, r.to_object)
                             and all(self._policy.can_use_object(principal, self.bundle.object(o)) for o in (r.from_object, r.to_object))]
            return TableRelationshipsResponse(**base, status="ok", object_fqn=obj.fqn, relationships=relationships)
        return self._invoke("get_table_relationships", ctx, ObjectRequest, raw, TableRelationshipsResponse, handler)

    def get_metric_definition(self, ctx: RequestContext, **raw: Any) -> MetricDefinitionResponse:
        def handler(request: MetricDefinitionRequest, principal: Principal, base: dict):
            authorized = [m for m in self.bundle.metrics if self._policy.can_use_object(principal, self.bundle.object(m.object_fqn))]
            if request.metric_id:
                metrics = [m for m in authorized if m.metric_id == request.metric_id]
                return MetricDefinitionResponse(**base, status="ok" if metrics else "not_found", metrics=metrics,
                                                errors=[] if metrics else [ToolError.of(ErrorCode.NOT_FOUND, f"No authorized metric '{request.metric_id}'")])
            if not request.query:
                return MetricDefinitionResponse(**base, status="error", errors=[ToolError.of(ErrorCode.INVALID_REQUEST, "Give metric_id or query")])
            _, matches, ambiguity = self._search.search(request.query, principal, top_k=3)
            ids = [m.metric_id for m in matches]
            metrics = sorted((m for m in authorized if m.metric_id in ids), key=lambda m: ids.index(m.metric_id))
            if ambiguity:
                return MetricDefinitionResponse(**base, status="needs_clarification", metrics=metrics, ambiguity=ambiguity,
                                                errors=[ToolError.of(ErrorCode.AMBIGUOUS_METRIC, ambiguity.message)])
            return MetricDefinitionResponse(**base, status="ok" if metrics else "not_found", metrics=metrics)
        return self._invoke("get_metric_definition", ctx, MetricDefinitionRequest, raw, MetricDefinitionResponse, handler)

    def get_verified_sql_examples(self, ctx: RequestContext, **raw: Any) -> VerifiedSqlExamplesResponse:
        def handler(request: VerifiedSqlExamplesRequest, principal: Principal, base: dict):
            examples = [e for e in self.bundle.examples
                        if all(self._policy.can_use_object(principal, self.bundle.object(o)) for o in e.objects)
                        and (request.object_fqn is None or request.object_fqn.lower() in e.objects)]
            if request.query:
                words = set(tokenize(request.query))
                examples.sort(key=lambda e: -len(words & set(tokenize(e.question + " " + " ".join(e.metrics)))))
            selected = examples[:request.limit]
            return VerifiedSqlExamplesResponse(**base, status="ok" if selected else "not_found", examples=selected)
        return self._invoke("get_verified_sql_examples", ctx, VerifiedSqlExamplesRequest, raw, VerifiedSqlExamplesResponse, handler)

    def validate_sql(self, ctx: RequestContext, **raw: Any) -> ValidateSqlResponse:
        def handler(request: SqlRequest, principal: Principal, base: dict):
            report = self._validator.validate(request.sql, request.parameters, principal)
            return ValidateSqlResponse(**base, status="ok" if report.approved else "rejected", report=report, errors=report.errors)
        return self._invoke("validate_sql", ctx, SqlRequest, raw, ValidateSqlResponse, handler)

    def execute_readonly_sql(self, ctx: RequestContext, **raw: Any) -> QueryResult:
        def handler(request: ExecuteSqlRequest, principal: Principal, base: dict):
            result = self._gateway.execute(request.sql, request.parameters, principal, request.max_rows)
            report = result.report
            return QueryResult(
                **base, status=result.status, errors=result.errors,
                validation_id=report.validation_id if report else None, query_id=result.query_id,
                source_objects=report.objects if report else [], columns=result.columns, rows=result.rows,
                row_count=len(result.rows), truncated=result.truncated, metric_definitions=result.metrics,
                applied_filters=report.applied_filters if report else [], limitations=result.limitations,
                validation_checks=report.checks if report else [], result_checks=result.result_checks,
                provenance=result.provenance)
        return self._invoke("execute_readonly_sql", ctx, ExecuteSqlRequest, raw, QueryResult, handler)
