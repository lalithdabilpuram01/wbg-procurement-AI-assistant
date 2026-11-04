"""Pydantic request/response contracts for the seven Structured Data Agent operations.

Every response carries the request id, a tool-invocation id, a status and, on failure, a typed error.
"""
from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from structured_agent.registry.models import Metric, RegisteredColumn, Relationship, RequiredFilter, VerifiedSqlExample

ParameterValue = str | int | float | bool | dt.date


# ----------------------------------------------------------------------------------------------------------------- errors
# Typed error codes returned to callers. The value is the stable string the client and the audit log see.
class ErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED_PRINCIPAL = "UNAUTHORIZED_PRINCIPAL"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS_METRIC = "AMBIGUOUS_METRIC"
    # SQL structure and safety
    PARSE_ERROR = "PARSE_ERROR"
    MULTIPLE_STATEMENTS = "MULTIPLE_STATEMENTS"
    NOT_SELECT = "NOT_SELECT"
    FORBIDDEN_OPERATION = "FORBIDDEN_OPERATION"
    FORBIDDEN_FUNCTION = "FORBIDDEN_FUNCTION"
    UNSUPPORTED_CONSTRUCT = "UNSUPPORTED_CONSTRUCT"
    # Registry and authorization
    UNKNOWN_TABLE = "UNKNOWN_TABLE"
    UNKNOWN_COLUMN = "UNKNOWN_COLUMN"
    UNAUTHORIZED_OBJECT = "UNAUTHORIZED_OBJECT"
    UNAUTHORIZED_COLUMN = "UNAUTHORIZED_COLUMN"
    PROHIBITED_COLUMN = "PROHIBITED_COLUMN"
    # Business logic
    UNAPPROVED_JOIN = "UNAPPROVED_JOIN"
    FAN_OUT_RISK = "FAN_OUT_RISK"
    INCOMPATIBLE_GRAIN = "INCOMPATIBLE_GRAIN"
    MISSING_REQUIRED_FILTER = "MISSING_REQUIRED_FILTER"
    NON_ADDITIVE_AGGREGATE = "NON_ADDITIVE_AGGREGATE"
    MIXED_MEASURE_FAMILIES = "MIXED_MEASURE_FAMILIES"
    # Budget
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    COMPLEXITY_EXCEEDED = "COMPLEXITY_EXCEEDED"
    SCAN_BUDGET_EXCEEDED = "SCAN_BUDGET_EXCEEDED"
    PARAMETER_ERROR = "PARAMETER_ERROR"
    # Execution
    CREDENTIAL_NOT_READ_ONLY = "CREDENTIAL_NOT_READ_ONLY"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    RESULT_VALIDATION_FAILED = "RESULT_VALIDATION_FAILED"
    GENERATION_UNAVAILABLE = "GENERATION_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# A generator may attempt one bounded correction for these; security and authorization errors are never retried.
RECOVERABLE_CODES = {
    ErrorCode.PARSE_ERROR, ErrorCode.UNKNOWN_COLUMN, ErrorCode.UNKNOWN_TABLE, ErrorCode.UNAPPROVED_JOIN,
    ErrorCode.FAN_OUT_RISK, ErrorCode.INCOMPATIBLE_GRAIN, ErrorCode.MISSING_REQUIRED_FILTER,
    ErrorCode.NON_ADDITIVE_AGGREGATE, ErrorCode.MIXED_MEASURE_FAMILIES, ErrorCode.LIMIT_EXCEEDED,
    ErrorCode.COMPLEXITY_EXCEEDED, ErrorCode.PARAMETER_ERROR,
}


# One typed failure. 'recoverable' is derived from the code (RECOVERABLE_CODES), never set by hand.
class ToolError(BaseModel):
    code: ErrorCode
    message: str
    recoverable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, code: ErrorCode, message: str, **details: Any) -> "ToolError":
        return cls(code=code, message=message, recoverable=code in RECOVERABLE_CODES, details=details)


Status = Literal["ok", "needs_clarification", "rejected", "error", "not_found"]


# Common envelope of every operation's response; each response class below adds its own payload fields.
class ToolResponse(BaseModel):
    request_id: str
    invocation_id: str
    tool: str
    status: Status
    errors: list[ToolError] = Field(default_factory=list)
    duration_ms: int = 0


# --------------------------------------------------------------------------------------------------------------- requests
class SearchTableRegistryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)


class ObjectRequest(BaseModel):
    object_fqn: str = Field(min_length=3, max_length=200)


class MetricDefinitionRequest(BaseModel):
    metric_id: str | None = Field(default=None, max_length=100)
    query: str | None = Field(default=None, max_length=500)


class VerifiedSqlExamplesRequest(BaseModel):
    query: str | None = Field(default=None, max_length=500)
    object_fqn: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=3, ge=1, le=10)


class SqlRequest(BaseModel):
    sql: str = Field(min_length=6, max_length=20_000)
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)


class ExecuteSqlRequest(SqlRequest):
    max_rows: int | None = Field(default=None, ge=1)


# ---------------------------------------------------------------------------------------------------------- registry info
class JoinSummary(BaseModel):
    relationship_id: str
    from_object: str
    from_columns: list[str]
    to_object: str
    to_columns: list[str]
    cardinality: str
    aggregation_rule: str
    required_conditions: list[dict[str, Any]] = Field(default_factory=list)
    status: str


# One search hit: a registered object with its score, why it matched, and the columns and joins the
# principal is allowed to use.
class TableCandidate(BaseModel):
    object_fqn: str
    logical_name: str
    layer: str
    object_type: str
    certified_aggregate: bool
    short_description: str
    grain: str
    score: float
    reasons: list[str]
    matched_columns: list[str]
    authorized_columns: list[str]
    related_metrics: list[str]
    approved_joins: list[JoinSummary]
    limitations: list[str]
    provenance: dict[str, Any]


# A metric found by registry search, with its score and why it matched.
class MetricMatch(BaseModel):
    metric_id: str
    name: str
    object_fqn: str
    score: float
    reasons: list[str]


# Returned when a question matches several metrics of one group and the caller must choose.
class Ambiguity(BaseModel):
    group: str
    message: str
    options: list[MetricMatch]


class SearchTableRegistryResponse(ToolResponse):
    candidates: list[TableCandidate] = Field(default_factory=list)
    metrics: list[MetricMatch] = Field(default_factory=list)
    ambiguity: Ambiguity | None = None


# get_table_schema result: description, grain, required filters and only the columns the principal may use.
class TableSchemaResponse(ToolResponse):
    object_fqn: str | None = None
    logical_name: str | None = None
    short_description: str | None = None
    grain: str | None = None
    grain_columns: list[str] = Field(default_factory=list)
    grain_status: str | None = None
    row_count: int | None = None
    certified_aggregate: bool | None = None
    required_filters: list[RequiredFilter] = Field(default_factory=list)
    columns: list[RegisteredColumn] = Field(default_factory=list)
    hidden_column_count: int = 0          # columns the principal may not see (names are not disclosed)
    limitations: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class TableRelationshipsResponse(ToolResponse):
    object_fqn: str | None = None
    relationships: list[Relationship] = Field(default_factory=list)


class MetricDefinitionResponse(ToolResponse):
    metrics: list[Metric] = Field(default_factory=list)
    ambiguity: Ambiguity | None = None


class VerifiedSqlExamplesResponse(ToolResponse):
    examples: list[VerifiedSqlExample] = Field(default_factory=list)


# ------------------------------------------------------------------------------------------------------------- validation
class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


# One output column of a validated query and the source columns it is computed from.
class OutputColumn(BaseModel):
    name: str
    source_columns: list[str]          # fully qualified source columns this output is computed from
    aggregate: str | None = None       # outermost aggregate function, if any
    unit: str | None = None
    classification: str = "public"


# Full result of validating one statement: every check that ran, the errors, and on approval the exact
# SQL the gateway is allowed to execute.
class ValidationReport(BaseModel):
    validation_id: str
    approved: bool
    sql_sha256: str
    validated_sql: str | None = None   # the exact SQL the gateway may run (normalised, LIMIT enforced)
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)
    objects: list[str] = Field(default_factory=list)
    output_columns: list[OutputColumn] = Field(default_factory=list)
    joins: list[str] = Field(default_factory=list)
    applied_filters: list[str] = Field(default_factory=list)
    applied_rewrites: list[str] = Field(default_factory=list)
    estimated_scanned_rows: int = 0
    checks: list[CheckResult] = Field(default_factory=list)
    errors: list[ToolError] = Field(default_factory=list)


class ValidateSqlResponse(ToolResponse):
    report: ValidationReport | None = None


# -------------------------------------------------------------------------------------------------------------- execution
class ResultColumn(BaseModel):
    name: str
    data_type: str
    unit: str | None = None
    source_columns: list[str] = Field(default_factory=list)


# Which credential ran the query, whether it was read-only, and which registry build approved it.
class ExecutionProvenance(BaseModel):
    credential_mode: Literal["read_only_principal", "dev_owner_credentials"]
    production_read_only: bool
    registry_build_id: str
    executed_at: str
    engine: str = "Databricks Serverless (Spark Connect)"


# Result of one executed query, with the checks that approved it, the checks on the returned rows,
# and where it ran.
class QueryResult(ToolResponse):
    validation_id: str | None = None
    query_id: str | None = None
    source_objects: list[str] = Field(default_factory=list)
    columns: list[ResultColumn] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    metric_definitions: list[Metric] = Field(default_factory=list)
    applied_filters: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    validation_checks: list[CheckResult] = Field(default_factory=list)
    result_checks: list[CheckResult] = Field(default_factory=list)
    provenance: ExecutionProvenance | None = None
