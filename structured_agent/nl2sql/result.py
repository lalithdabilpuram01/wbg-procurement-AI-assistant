"""Typed result of the natural-language-to-SQL workflow and its safe user-facing message.

Failure classes keep the different reasons apart: provider problems, SQL validation denials, authorization denials,
execution refusals/failures and the workflow's own resilience limits. The model's description of its query is
returned separately and labelled unverified; the user-facing message is built only from validated facts.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# Final outcome of one workflow run, as shown to the caller.
class WorkflowStatus(str, Enum):
    ANSWERED = "answered"                              # validated SQL executed through the gateway
    VALIDATED_NOT_EXECUTED = "validated_not_executed"  # approved by the validator; execution not requested
    NEEDS_CLARIFICATION = "needs_clarification"        # ambiguous metric wording
    CANNOT_ANSWER = "cannot_answer"                    # no authorized table fits, or the model declined
    REJECTED = "rejected"                              # SQL validation or authorization denied
    EXECUTION_REFUSED = "execution_refused"            # gateway credential gate (default deny)
    FAILED = "failed"                                  # provider, execution, deadline, rate limit or circuit open
    BLOCKED_BY_GUARDRAIL = "blocked_by_guardrail"      # an input, context, output, tool or execution guardrail


# Why a run did not answer; one value per distinct cause, so dashboards and tests can tell them apart.
class FailureClass(str, Enum):
    # LLM provider (values match llm_client.LlmFailure)
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_CONNECTION_ERROR = "provider_connection_error"
    PROVIDER_AUTH_ERROR = "provider_auth_error"
    PROVIDER_BAD_REQUEST = "provider_bad_request"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    MODEL_OUTPUT_TRUNCATED = "model_output_truncated"
    UNEXPECTED_PROVIDER = "unexpected_provider"
    # Policy and validation
    SQL_VALIDATION_DENIED = "sql_validation_denied"
    AUTHORIZATION_DENIED = "authorization_denied"
    # Execution
    EXECUTION_REFUSED = "execution_refused"
    EXECUTION_TIMEOUT = "execution_timeout"
    EXECUTION_FAILED = "execution_failed"
    RESULT_VALIDATION_FAILED = "result_validation_failed"
    # Workflow resilience controls
    GUARDRAIL_BLOCKED = "guardrail_blocked"
    RATE_LIMITED = "rate_limited"
    CIRCUIT_OPEN = "circuit_open"
    DEADLINE_EXCEEDED = "deadline_exceeded"


# The parts of the validator's report that the workflow passes on (no SQL text beyond the validated one).
class ValidationSummary(BaseModel):
    approved: bool
    validation_id: str | None = None
    validated_sql: str | None = None                   # the validator's normalised SQL (what the gateway may run)
    error_codes: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    checks_failed: list[str] = Field(default_factory=list)


# Everything one workflow run returns. Fields marked unverified come from the model and are never facts.
class WorkflowResult(BaseModel):
    request_id: str
    question: str
    status: WorkflowStatus = WorkflowStatus.FAILED
    failure_class: FailureClass | None = None
    message: str = ""                                   # safe, deterministic user-facing explanation
    retry_after_seconds: float | None = None
    provider_status_code: int | None = None             # HTTP status of a failed LLM call (non-sensitive)
    provider_error_type: str | None = None              # provider's short error code, e.g. invalid_request_error
    clarification_options: list[str] = Field(default_factory=list)
    context_tables: list[str] = Field(default_factory=list)
    proposed_sql: str | None = None                     # Claude's proposal: unvalidated
    validated_sql: str | None = None                    # approved by the existing SQLGlot validator
    executed_sql: str | None = None                     # actually executed through the gateway (set only on success)
    parameters: dict[str, Any] = Field(default_factory=dict)
    model_description_unverified: str | None = None     # Claude's description of its query: NOT a verified claim
    validation: ValidationSummary | None = None
    columns: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    source_objects: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] | None = None
    guardrail_reason: str | None = None                 # reason category of a blocking guardrail decision
    guardrail_decisions: list[dict[str, Any]] = Field(default_factory=list)   # stage, policy, decision, reason only
    llm_calls: int = 0
    input_tokens: int = 0                                # summed over every Claude call (for cost accounting)
    output_tokens: int = 0
    generator: str = "Claude via Portkey (Claude-only route, no Config)"
    timings_ms: dict[str, int] = Field(default_factory=dict)


def user_message(result: WorkflowResult) -> str:
    """A short explanation built only from validated facts (never from model claims)."""
    status = result.status
    if status == WorkflowStatus.ANSWERED:
        objects = ", ".join(o.split(".")[-1] for o in result.source_objects)
        truncated = " The row limit was reached, so more rows may exist." if result.truncated else ""
        return (f"The query passed every validation check and was executed read-only on {objects}; it returned "
                f"{result.row_count} row(s).{truncated} The values come from Databricks, not from the language model.")
    if status == WorkflowStatus.VALIDATED_NOT_EXECUTED:
        return "The generated query passed every validation check. It was not executed because execution was not requested."
    if status == WorkflowStatus.NEEDS_CLARIFICATION:
        return "The question can refer to different measures; please choose one: " + ", ".join(result.clarification_options) + "."
    if status == WorkflowStatus.CANNOT_ANSWER:
        return "The approved tables available to you cannot answer this question, so no query was run."
    if status == WorkflowStatus.REJECTED:
        codes = ", ".join(result.validation.error_codes) if result.validation else ""
        reason = "access rules" if result.failure_class == FailureClass.AUTHORIZATION_DENIED else "SQL validation rules"
        return f"The generated query was not run because it broke the {reason} ({codes})."
    if status == WorkflowStatus.BLOCKED_BY_GUARDRAIL:
        reason = result.guardrail_reason or "policy"
        if result.executed_sql:
            return f"The response was withheld by a safety policy ({reason})."
        return f"The request was stopped by a safety policy ({reason}); nothing was executed."
    if status == WorkflowStatus.EXECUTION_REFUSED:
        return "The query was valid but was not executed: the Databricks credential is not approved for execution."
    retry = f" Try again in {int(result.retry_after_seconds)} s." if result.retry_after_seconds else ""
    return f"The request could not be completed ({result.failure_class.value if result.failure_class else 'error'}).{retry}"
