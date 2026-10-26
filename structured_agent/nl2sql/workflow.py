"""Natural-language question -> Claude (Portkey, Claude-only) -> validated SQL -> gateway execution -> typed result.

Flow for one request:
  1. per-principal request rate limit; total deadline starts
  2. grounded schema context from the existing registry tools (context.py)
  3. Claude proposes SQL (structured reply)                      [LLM rate limit, slot, circuit breaker]
  4. the existing validate_sql tool decides; at most one correction, only for recoverable validation errors
  5. only approved SQL goes to the existing execute_readonly_sql tool  [Databricks rate limit, slot, breaker]
     (the gateway validates again and applies its own credential gate and timeout)
  6. typed WorkflowResult with a safe message built from validated facts

The model never executes anything and never chooses its identity. Nothing is retried automatically: an LLM error
ends the request (no nested retries), and Databricks execution is attempted at most once.

Guardrails (optional; structured_agent/guardrails): input rails -> retrieval with tool gate -> retrieved-context
sanitising -> Claude -> model-output rails -> existing validator -> execution gate -> execution -> final-response
rails. They add independent checks; MCP authorization and the SQLGlot validator remain authoritative.

Observability: the JSONL audit trail (Telemetry) is the durable record; Logfire spans (WorkflowTracer) mirror the
stages with safe metadata only and are disabled unless a configured tracer is passed in.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from structured_agent.guardrails.decisions import GuardDecision, GuardrailBlocked, GuardStage
from structured_agent.guardrails.pipeline import EXECUTION_TOOLS, RETRIEVAL_TOOLS, VALIDATION_TOOLS, Guardrails
from structured_agent.guardrails.policies import contains_instruction_like_text
from structured_agent.nl2sql.context import SchemaContext, build_schema_context
from structured_agent.nl2sql.llm_client import LlmError, LlmFailure, PortkeyClaudeClient
from structured_agent.nl2sql.resilience import (CircuitBreaker, CircuitOpenError, CircuitState, ConcurrencyLimiter,
                                                Deadline, DeadlineExceeded, RateLimitExceeded, TokenBucketRateLimiter)
from structured_agent.nl2sql.result import FailureClass, ValidationSummary, WorkflowResult, WorkflowStatus, user_message
from structured_agent.nl2sql.settings import RuntimeSettings
from structured_agent.nl2sql.sql_generator import SqlDraft, generate_sql
from structured_agent.nl2sql.tools import ToolCaller
from structured_agent.nl2sql.tracing import TracedToolCaller, WorkflowTracer
from structured_agent.observability import Telemetry

AUTHORIZATION_CODES = {"UNAUTHORIZED_PRINCIPAL", "UNAUTHORIZED_OBJECT", "UNAUTHORIZED_COLUMN", "PROHIBITED_COLUMN"}
# Execution outcomes that indicate an unhealthy Databricks upstream (counted by the breaker).
DATABRICKS_FAILURE_CODES = {"EXECUTION_TIMEOUT", "EXECUTION_FAILED"}
MINIMUM_SECONDS_FOR_A_CALL = 1.0


class RuntimeControls:
    """Process-wide limiters and breakers shared by all requests of one workflow instance."""

    def __init__(self, settings: RuntimeSettings, telemetry: Telemetry, clock: Callable[[], float] = time.monotonic,
                 tracer: WorkflowTracer | None = None):
        tracer = tracer or WorkflowTracer.disabled()

        def audit_transition(name: str, old: CircuitState, new: CircuitState) -> None:
            telemetry.audit("breaker", "inv_" + uuid.uuid4().hex[:16], "circuit_breaker.transition",
                            breaker=name, from_state=old.value, to_state=new.value)
            tracer.event("nl2sql.circuit_breaker_transition", level="warn", breaker=name, from_state=old.value,
                         to_state=new.value)

        self.request_rate = TokenBucketRateLimiter("requests", settings.requests_per_minute, clock=clock)
        self.llm_rate = TokenBucketRateLimiter("llm_calls", settings.llm_calls_per_minute, clock=clock)
        self.databricks_rate = TokenBucketRateLimiter("databricks_executions", settings.databricks_executions_per_minute, clock=clock)
        self.llm_slots = ConcurrencyLimiter("llm_concurrency", settings.max_concurrent_llm_calls, settings.concurrency_wait_seconds)
        self.databricks_slots = ConcurrencyLimiter("databricks_concurrency", settings.max_concurrent_databricks_calls,
                                                   settings.concurrency_wait_seconds)
        self.llm_breaker = CircuitBreaker("llm.claude_only", settings.llm_breaker, clock, audit_transition)
        self.databricks_breaker = CircuitBreaker("databricks.execution", settings.databricks_breaker, clock, audit_transition)


class NlToSqlWorkflow:
    def __init__(self, tools: ToolCaller, claude: PortkeyClaudeClient, telemetry: Telemetry,
                 settings: RuntimeSettings = RuntimeSettings(), clock: Callable[[], float] = time.monotonic,
                 tracer: WorkflowTracer | None = None, guardrails: Guardrails | None = None):
        self._tools = tools
        self._guardrails = guardrails
        self._claude = claude
        self._telemetry = telemetry
        self._settings = settings
        self._clock = clock
        self._tracer = tracer or WorkflowTracer.disabled()
        self.controls = RuntimeControls(settings, telemetry, clock, self._tracer)

    # ------------------------------------------------------------------------------------------------------- entry
    def run(self, question: str, execute: bool = False, request_id: str | None = None,
            conversation_context: str | None = None) -> WorkflowResult:
        """Answer one question. `execute=False` stops after validation (no Databricks call).
        `conversation_context`: optional memory block from the conversation service (untrusted; prompt context only)."""
        result = WorkflowResult(request_id=request_id or "req_" + uuid.uuid4().hex[:16], question=question)
        started = time.perf_counter()
        with self._tracer.stage("nl2sql.request", request_id=result.request_id, operation="nl2sql_question",
                                model=self._claude.model, execute_requested=execute) as span:
            try:
                self._run(result, execute, conversation_context)
            except RateLimitExceeded as error:
                self._fail(result, FailureClass.RATE_LIMITED, error.retry_after_seconds)
                self._tracer.event("nl2sql.rate_limited", level="warn", request_id=result.request_id, limit=error.limit,
                                   retry_after_seconds=error.retry_after_seconds)
            except CircuitOpenError as error:
                self._fail(result, FailureClass.CIRCUIT_OPEN, error.retry_after_seconds)
            except DeadlineExceeded:
                self._fail(result, FailureClass.DEADLINE_EXCEEDED)
            except LlmError as error:
                self._fail(result, FailureClass(error.failure.value), error.retry_after_seconds)
                result.provider_status_code, result.provider_error_type = error.status_code, error.provider_error_type
            except GuardrailBlocked as blocked:
                self._block(result, blocked.decision)
            self._check_final_response(result)
            result.message = user_message(result)
            result.timings_ms["total"] = round(1000 * (time.perf_counter() - started))
            final = {"workflow_status": result.status.value, "failure_class": result.failure_class.value if result.failure_class else None,
                     "llm_calls": result.llm_calls, "provider_status_code": result.provider_status_code,
                     "row_count": result.row_count, "duration_ms": result.timings_ms["total"]}
            span(**final)
            self._tracer.event("nl2sql.final_status", request_id=result.request_id, **final)
        self._audit(result.request_id, "nl2sql.result", workflow_status=result.status.value,
                    failure_class=result.failure_class.value if result.failure_class else None, llm_calls=result.llm_calls,
                    provider_status_code=result.provider_status_code,
                    objects=result.source_objects or result.context_tables, row_count=result.row_count,
                    duration_ms=result.timings_ms["total"], query_length=len(question))
        return result

    def _run(self, result: WorkflowResult, execute: bool, conversation_context: str | None) -> None:
        trusted_principal = self._tools.principal_id   # fixed for the whole request
        self.controls.request_rate.acquire(trusted_principal)
        deadline = Deadline(self._settings.total_deadline_seconds, self._clock)
        if self._guardrails:
            self._guard(result, self._guardrails.check_input(result.question))

        context = self._retrieve_context(result)
        if context is None:
            return
        memory = self._screen_memory(result, conversation_context)
        deadline.check(MINIMUM_SECONDS_FOR_A_CALL)

        draft = self._generate_validated_sql(result, context, deadline, memory)
        if draft is None:
            return  # status already set (rejected, cannot answer)
        if not execute:
            result.status = WorkflowStatus.VALIDATED_NOT_EXECUTED
            return
        if self._guardrails:
            self._guard(result, self._guardrails.check_execution(
                trusted_principal, self._tools.principal_id, result.validation.approved, result.validation.validation_id,
                [GuardDecision.model_validate(d) for d in result.guardrail_decisions]))
        self._execute(result, draft, deadline)

    # ------------------------------------------------------------------------------------------------- retrieval
    def _retrieve_context(self, result: WorkflowResult) -> SchemaContext | None:
        started = time.perf_counter()
        with self._tracer.stage("nl2sql.metadata_retrieval", request_id=result.request_id) as span:
            context, outcome = build_schema_context(TracedToolCaller(self._tools_for(RETRIEVAL_TOOLS, "retrieval"), self._tracer),
                                                    result.question,
                                                    result.request_id, self._settings.max_tables_in_context,
                                                    self._settings.max_examples_in_context)
            span(retrieval_outcome=outcome.status, tables=[t.object_fqn for t in context.tables] if context else [])
        result.timings_ms["retrieval"] = round(1000 * (time.perf_counter() - started))
        if outcome.status == "needs_clarification":
            result.status = WorkflowStatus.NEEDS_CLARIFICATION
            result.clarification_options = outcome.clarification_options
            return None
        if context is None:
            result.status = WorkflowStatus.CANNOT_ANSWER
            return None
        if self._guardrails:
            context, decision = self._guardrails.guard_context(context)   # registry text is untrusted data
            self._guard(result, decision)
        result.context_tables = [t.object_fqn for t in context.tables]
        return context

    # ------------------------------------------------------------------------------------- generation + validation
    def _generate_validated_sql(self, result: WorkflowResult, context: SchemaContext, deadline: Deadline,
                                memory: str | None = None) -> SqlDraft | None:
        """Up to max_llm_calls_per_request Claude calls; returns an approved draft or None (result already set)."""
        with self._tracer.stage("nl2sql.sql_generation", request_id=result.request_id) as span:
            feedback: list[str] | None = None
            for attempt in range(1, self._settings.max_llm_calls_per_request + 1):
                span(attempts=attempt)
                draft = self._call_claude(result, context, deadline, attempt, feedback, memory)
                if self._guardrails:
                    self._guard(result, self._guardrails.check_model_output(draft, context))
                if not draft.answerable:
                    result.status = WorkflowStatus.CANNOT_ANSWER
                    return None
                if not draft.sql.strip():
                    raise LlmError(LlmFailure.INVALID_OUTPUT)

                result.proposed_sql, result.parameters = draft.sql, draft.typed_parameters()
                result.model_description_unverified = draft.explanation
                response = self._validate(result, draft, attempt)
                if result.validation.approved:
                    return draft

                errors = response.get("errors", [])
                recoverable = bool(errors) and all(e["recoverable"] for e in errors)
                if not recoverable or attempt == self._settings.max_llm_calls_per_request:
                    self._reject(result)
                    return None
                feedback = [f"{e['code']}: {e['message']}" for e in errors]
        return None

    def _validate(self, result: WorkflowResult, draft: SqlDraft, attempt: int) -> dict[str, Any]:
        """The existing validate_sql tool decides; the span records only the outcome and error codes."""
        with self._tracer.stage("nl2sql.sql_validation", request_id=result.request_id, llm_call_index=attempt) as span:
            response = self._tools_for(VALIDATION_TOOLS, "validation").call(
                "validate_sql", {"sql": draft.sql, "parameters": result.parameters}, result.request_id)
            result.validation = _validation_summary(response)
            result.validated_sql = result.validation.validated_sql if result.validation.approved else None
            span(validation_outcome="approved" if result.validation.approved else "rejected",
                 error_codes=result.validation.error_codes)
        return response

    def _call_claude(self, result: WorkflowResult, context: SchemaContext, deadline: Deadline, attempt: int,
                     feedback: list[str] | None, memory: str | None = None) -> SqlDraft:
        self.controls.llm_rate.acquire(self._tools.principal_id)
        with self.controls.llm_slots.slot():
            timeout = min(self._claude.timeout_seconds, deadline.check(MINIMUM_SECONDS_FOR_A_CALL))
            breaker = self.controls.llm_breaker
            breaker.before_call()
            result.llm_calls += 1
            with self._tracer.stage("nl2sql.claude_request", request_id=result.request_id, model=self._claude.model,
                                    llm_call_index=attempt) as span:
                try:
                    draft, usage = generate_sql(self._claude, context, result.request_id, timeout, feedback, result.proposed_sql,
                                                memory)
                except LlmError as error:
                    if error.is_transient:
                        breaker.record_failure()
                    else:
                        breaker.record_ignored()   # auth, bad request, invalid output: not upstream health
                    details = {"failure_class": error.failure.value, "provider_status_code": error.status_code,
                               "provider_error_type": error.provider_error_type, "retry_after_seconds": error.retry_after_seconds}
                    span(status="error", **details)
                    self._audit(result.request_id, "nl2sql.llm_call", status="error", llm_call_index=attempt, **details)
                    raise
                span(status="ok", input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                     duration_ms=usage["duration_ms"])
            breaker.record_success()
        result.timings_ms[f"llm_call_{attempt}"] = usage["duration_ms"]
        self._audit(result.request_id, "nl2sql.llm_call", status="ok", llm_call_index=attempt,
                    input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"], duration_ms=usage["duration_ms"])
        return draft

    def _reject(self, result: WorkflowResult) -> None:
        result.status = WorkflowStatus.REJECTED
        codes = set(result.validation.error_codes) if result.validation else set()
        result.failure_class = FailureClass.AUTHORIZATION_DENIED if codes & AUTHORIZATION_CODES else FailureClass.SQL_VALIDATION_DENIED

    # -------------------------------------------------------------------------------------------------- execution
    def _execute(self, result: WorkflowResult, draft: SqlDraft, deadline: Deadline) -> None:
        self.controls.databricks_rate.acquire(self._tools.principal_id)
        with self.controls.databricks_slots.slot():
            deadline.check(MINIMUM_SECONDS_FOR_A_CALL)
            breaker = self.controls.databricks_breaker
            breaker.before_call()
            started = time.perf_counter()
            with self._tracer.stage("nl2sql.databricks_execution", request_id=result.request_id) as span:
                response = self._tools_for(EXECUTION_TOOLS, "execution").call(
                    "execute_readonly_sql", {"sql": draft.sql, "parameters": result.parameters},
                    result.request_id)   # exactly one attempt: never retried
                codes = {e["code"] for e in response.get("errors", [])}
                provenance = response.get("provenance") or {}
                span(status=response["status"], error_codes=sorted(codes), row_count=response.get("row_count"),
                     truncated=response.get("truncated"), databricks_access_mode=provenance.get("credential_mode"),
                     production_read_only=provenance.get("production_read_only"))
            if response["status"] == "ok":
                breaker.record_success()
            elif codes & DATABRICKS_FAILURE_CODES:
                breaker.record_failure()
            else:
                breaker.record_ignored()   # credential refusal, re-validation rejection, result checks: not upstream health
        result.timings_ms["execution"] = round(1000 * (time.perf_counter() - started))
        self._apply_execution(result, response, codes)

    @staticmethod
    def _apply_execution(result: WorkflowResult, response: dict[str, Any], codes: set[str]) -> None:
        if response["status"] == "ok":
            result.status = WorkflowStatus.ANSWERED
            result.executed_sql = result.validated_sql   # the gateway re-validates and runs this normalised SQL
            result.columns, result.rows = response["columns"], response["rows"]
            result.row_count, result.truncated = response["row_count"], response["truncated"]
            result.source_objects = response["source_objects"]
            result.metric_ids = [m["metric_id"] for m in response["metric_definitions"]]
            result.limitations, result.provenance = response["limitations"], response["provenance"]
        elif "CREDENTIAL_NOT_READ_ONLY" in codes:
            result.status, result.failure_class = WorkflowStatus.EXECUTION_REFUSED, FailureClass.EXECUTION_REFUSED
        elif response["status"] == "rejected":
            result.validation = ValidationSummary(approved=False, error_codes=sorted(codes))
            result.status = WorkflowStatus.REJECTED
            result.failure_class = (FailureClass.AUTHORIZATION_DENIED if codes & AUTHORIZATION_CODES
                                    else FailureClass.SQL_VALIDATION_DENIED)
        elif "EXECUTION_TIMEOUT" in codes:
            result.status, result.failure_class = WorkflowStatus.FAILED, FailureClass.EXECUTION_TIMEOUT
        elif "RESULT_VALIDATION_FAILED" in codes:
            result.status, result.failure_class = WorkflowStatus.FAILED, FailureClass.RESULT_VALIDATION_FAILED
        else:
            result.status, result.failure_class = WorkflowStatus.FAILED, FailureClass.EXECUTION_FAILED

    # ------------------------------------------------------------------------------------------------- guardrails
    def _tools_for(self, allowed_tools: frozenset[str], stage_name: str) -> ToolCaller:
        """With guardrails, each stage may call only its approved MCP tools."""
        return self._guardrails.tool_gate(self._tools, allowed_tools, stage_name) if self._guardrails else self._tools

    def _guard(self, result: WorkflowResult, decision: GuardDecision) -> None:
        """Record a guardrail decision; stop the request if it blocks."""
        self._record_guard(result, decision)
        if not decision.allowed:
            raise GuardrailBlocked(decision)

    def _record_guard(self, result: WorkflowResult, decision: GuardDecision) -> None:
        safe = {"guard_stage": decision.stage.value, "policy_id": decision.policy_id, "decision": decision.decision,
                "reason_category": decision.reason_category.value if decision.reason_category else None,
                "enforced_by": decision.enforced_by, "duration_ms": decision.duration_ms}
        if not any(d == decision.model_dump(mode="json") for d in result.guardrail_decisions):
            result.guardrail_decisions.append(decision.model_dump(mode="json"))
        self._audit(result.request_id, "guardrail.decision", **safe)
        self._tracer.event("nl2sql.guardrail_decision", level="info" if decision.allowed else "warn",
                           request_id=result.request_id, **safe)

    def _block(self, result: WorkflowResult, decision: GuardDecision) -> None:
        if decision.stage == GuardStage.TOOL_AUTHORIZATION:
            self._record_guard(result, decision)   # raised by the tool gate, not yet recorded
        result.status, result.failure_class = WorkflowStatus.BLOCKED_BY_GUARDRAIL, FailureClass.GUARDRAIL_BLOCKED
        result.guardrail_reason = decision.reason_category.value if decision.reason_category else None
        result.rows, result.columns, result.row_count = [], [], 0

    def _check_final_response(self, result: WorkflowResult) -> None:
        """Final-response rails on every completed, non-blocked result; a block withholds rows and values."""
        if not self._guardrails or result.status == WorkflowStatus.BLOCKED_BY_GUARDRAIL:
            return
        decision = self._guardrails.check_final_response(result.model_dump_json())
        self._record_guard(result, decision)
        if not decision.allowed:
            self._block(result, decision)

    # ---------------------------------------------------------------------------------------------------- helpers
    @staticmethod
    def _fail(result: WorkflowResult, failure: FailureClass, retry_after_seconds: float | None = None) -> None:
        result.status, result.failure_class, result.retry_after_seconds = WorkflowStatus.FAILED, failure, retry_after_seconds
        result.rows, result.row_count = [], 0

    def _screen_memory(self, result: WorkflowResult, conversation_context: str | None) -> str | None:
        """Second line of defence for conversation memory (the service already sanitises it): memory that still holds
        instruction-like text is dropped entirely and the request continues without it."""
        if not conversation_context:
            return None
        if not contains_instruction_like_text(conversation_context):
            return conversation_context
        self._audit(result.request_id, "guardrail.decision", guard_stage=GuardStage.RETRIEVED_CONTEXT.value,
                    policy_id="app.conversation_memory", decision="block", reason_category="context_instruction_removed",
                    enforced_by="application_policy")
        return None

    def _audit(self, request_id: str, event: str, **attributes: Any) -> None:
        self._telemetry.audit(request_id, "inv_" + uuid.uuid4().hex[:16], event, **attributes)


def _validation_summary(response: dict[str, Any]) -> ValidationSummary:
    report = response.get("report") or {}
    return ValidationSummary(approved=bool(report.get("approved")), validation_id=report.get("validation_id"),
                             validated_sql=report.get("validated_sql"),
                             error_codes=[e["code"] for e in response.get("errors", [])],
                             messages=[e["message"] for e in response.get("errors", [])][:5],
                             checks_failed=[c["name"] for c in report.get("checks", []) if not c["passed"]])
