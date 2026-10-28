"""Thin adapter over the existing Structured Data Agent (no logic of the agent is re-implemented here).

Operations (read-only):
  answer_data_question  -> NlToSqlWorkflow.run(question, execute, request_id, conversation_context)
                           (guardrails, SQLGlot validator, MCP tool gates, gateway, breakers, deadline: all inside)
  describe_metric       -> get_metric_definition tool
  find_tables           -> search_table_registry tool
The workflow and tool caller are built per request for the application principal of the AppAuthorizationContext
(`InProcessToolCaller(service, principal, workspace)` in production). Result rows go to the request-scoped ResultCache;
the evidence ledger receives a bounded set of facts and the validation provenance only.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Protocol

import sqlglot
from sqlglot import exp

from conversation.redaction import redact_secrets
from structured_agent.guardrails.policies import contains_instruction_like_text
from structured_agent.nl2sql.result import FailureClass, WorkflowResult, WorkflowStatus

from ..authorization import AppAuthorizationContext
from ..contracts import (MAX_FACTS_PER_EVIDENCE, AgentTask, ErrorCategory, EvidenceKind, EvidenceRecord, ExecutionError, Fact,
                         NumericUse, SourceSystem, TaskState, Usage, ValidationStatus, WorkerName, new_id)
from .base import ResultCache, TicketAuthority, TransientWorkerError, WorkerContext, WorkerOutput

PROVENANCE_KEYS = ("credential_mode", "production_read_only", "registry_build_id", "executed_at")
FAILURE_CATEGORIES = {
    FailureClass.SQL_VALIDATION_DENIED: ErrorCategory.VALIDATION, FailureClass.AUTHORIZATION_DENIED: ErrorCategory.AUTHORIZATION,
    FailureClass.EXECUTION_REFUSED: ErrorCategory.AUTHORIZATION, FailureClass.EXECUTION_TIMEOUT: ErrorCategory.TIMEOUT,
    FailureClass.EXECUTION_FAILED: ErrorCategory.EXECUTION, FailureClass.RESULT_VALIDATION_FAILED: ErrorCategory.EXECUTION,
    FailureClass.GUARDRAIL_BLOCKED: ErrorCategory.GUARDRAIL, FailureClass.RATE_LIMITED: ErrorCategory.RATE_LIMITED,
    FailureClass.CIRCUIT_OPEN: ErrorCategory.CIRCUIT_OPEN, FailureClass.DEADLINE_EXCEEDED: ErrorCategory.DEADLINE,
}


class WorkflowLike(Protocol):
    def run(self, question: str, execute: bool = False, request_id: str | None = None,
            conversation_context: str | None = None) -> WorkflowResult: ...


class ToolCallerLike(Protocol):
    def call(self, tool: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]: ...


class StructuredDataWorker:
    name = WorkerName.STRUCTURED
    operations = frozenset({"answer_data_question", "describe_metric", "find_tables"})

    def __init__(self, workflow_for: Callable[[AppAuthorizationContext], WorkflowLike],
                 tools_for: Callable[[AppAuthorizationContext], ToolCallerLike], authority: TicketAuthority,
                 result_cache: ResultCache):
        self._workflow_for, self._tools_for = workflow_for, tools_for
        self._authority, self._cache = authority, result_cache

    def run(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        self._authority.verify(context.ticket, task)
        if task.operation == "answer_data_question":
            return self._answer(task, context)
        if task.operation == "describe_metric":
            return self._describe_metric(task, context)
        if task.operation == "find_tables":
            return self._find_tables(task, context)
        return WorkerOutput(TaskState.FAILED, error=_error(task, ErrorCategory.AUTHORIZATION, "unknown_operation",
                                                             "The operation is not offered by this worker."))

    # ------------------------------------------------------------------------------------------------- NL2SQL
    def _answer(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        execute = task.requires_execution and context.execution_approved
        memory = _memory_block(context)
        result = self._workflow_for(context.authorization).run(task.question, execute=execute, request_id=context.request_id,
                                                               conversation_context=memory)
        usage = Usage(llm_calls=result.llm_calls, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                      executions=1 if result.executed_sql else 0)
        status = result.status
        if status == WorkflowStatus.ANSWERED:
            return WorkerOutput(TaskState.SUCCEEDED, (self._result_evidence(task, result),), usage)
        if status == WorkflowStatus.VALIDATED_NOT_EXECUTED:
            notes = ("execution_not_approved",) if task.requires_execution else ()
            state = TaskState.PARTIAL if task.requires_execution else TaskState.SUCCEEDED
            return WorkerOutput(state, (_validated_query_evidence(task, result),), usage, notes=notes)
        if status == WorkflowStatus.NEEDS_CLARIFICATION:
            return WorkerOutput(TaskState.FAILED, usage=usage, clarification_options=tuple(result.clarification_options),
                                error=_error(task, ErrorCategory.CLARIFICATION, "needs_clarification",
                                             "The data question is ambiguous and needs clarification."))
        if status == WorkflowStatus.CANNOT_ANSWER:
            return WorkerOutput(TaskState.FAILED, usage=usage, error=_error(
                task, ErrorCategory.NO_EVIDENCE, "cannot_answer", "No approved table answers this data question."))
        if status == WorkflowStatus.BLOCKED_BY_GUARDRAIL:
            return WorkerOutput(TaskState.BLOCKED, usage=usage, error=_error(
                task, ErrorCategory.GUARDRAIL, result.guardrail_reason or "guardrail", "A guardrail stopped the data question."))
        failure = result.failure_class
        category = FAILURE_CATEGORIES.get(failure, ErrorCategory.PROVIDER) if failure else ErrorCategory.EXECUTION
        return WorkerOutput(TaskState.FAILED, usage=usage, error=_error(
            task, category, failure.value if failure else status.value, "The data question could not be completed.",
            retry_after=result.retry_after_seconds))

    def _result_evidence(self, task: AgentTask, result: WorkflowResult) -> EvidenceRecord:
        validation_id = result.validation.validation_id if result.validation else None
        result_ref = self._cache.put(new_id("rows"), list(result.rows))
        facts = _row_facts(result.rows)
        warnings = []
        if result.truncated:
            warnings.append("result_truncated")
        if len(facts) < sum(len(r) for r in result.rows):
            warnings.append("facts_truncated_to_limit")
        provenance = dict(result.provenance or {})
        if provenance.get("credential_mode") == "dev_owner_credentials":
            warnings.append("dev_owner_credentials")
        objects = tuple(result.source_objects)
        return EvidenceRecord(
            evidence_id=new_id("ev"), investigation_id=task.investigation_id, task_id=task.task_id,
            source_system=SourceSystem.DATABRICKS_PUBLIC, kind=EvidenceKind.STRUCTURED_RESULT, source_objects=objects,
            source_version=str(provenance.get("registry_build_id") or validation_id or ""),
            citation=f"Databricks {', '.join(objects)[:200]} (validation {validation_id})",
            provenance=tuple((k, v) for k, v in (
                ("validation_id", validation_id), ("sql_sha256", _sha(result.executed_sql or "")),
                ("params_sha256", _sha(json.dumps(result.parameters, sort_keys=True, default=str))), ("result_ref", result_ref),
                ("row_count", str(result.row_count)), *((k, str(provenance[k])) for k in PROVENANCE_KEYS if k in provenance))
                if v),
            validation_status=ValidationStatus.VERIFIED, numeric_use=NumericUse.APPROVED, quality_warnings=tuple(warnings),
            facts=facts, summary=_safe_text(_with_filters(result.message, result.executed_sql, result.parameters)))

    # ------------------------------------------------------------------------------------------- registry tools
    def _describe_metric(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        response = self._call(context, "get_metric_definition", {"query": task.question})
        if response.get("status") == "needs_clarification" or response.get("ambiguity"):
            options = tuple(o["name"] for o in (response.get("ambiguity") or {}).get("options", []))[:5]
            return WorkerOutput(TaskState.FAILED, clarification_options=options, error=_error(
                task, ErrorCategory.CLARIFICATION, "ambiguous_metric", "Several metrics match; please choose one."))
        problem = _tool_problem(task, response)
        if problem:
            return problem
        evidence = tuple(EvidenceRecord(
            evidence_id=new_id("ev"), investigation_id=task.investigation_id, task_id=task.task_id,
            source_system=SourceSystem.STRUCTURED_REGISTRY, kind=EvidenceKind.METRIC_DEFINITION,
            source_objects=(m["object_fqn"],), source_version=m.get("status"),
            citation=f"Metric registry: {m['metric_id']}", validation_status=ValidationStatus.VERIFIED,
            numeric_use=NumericUse.NOT_APPROVED, provenance=(("metric_id", m["metric_id"]),),
            facts=(Fact(name="metric_id", value=m["metric_id"]), Fact(name="unit", value=str(m.get("unit") or "")[:200]),
                   Fact(name="object_fqn", value=m["object_fqn"])),
            summary=_safe_text(f"{m['name']}: {m.get('description', '')}")) for m in response.get("metrics", [])[:3])
        return WorkerOutput(TaskState.SUCCEEDED if evidence else TaskState.FAILED, evidence,
                            error=None if evidence else _error(task, ErrorCategory.NO_EVIDENCE, "metric_not_found",
                                                               "No registered metric matches."))

    def _find_tables(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        response = self._call(context, "search_table_registry", {"query": task.question, "top_k": 3})
        problem = _tool_problem(task, response)
        if problem:
            return problem
        evidence = tuple(EvidenceRecord(
            evidence_id=new_id("ev"), investigation_id=task.investigation_id, task_id=task.task_id,
            source_system=SourceSystem.STRUCTURED_REGISTRY, kind=EvidenceKind.TABLE_CANDIDATE, source_objects=(c["object_fqn"],),
            citation=f"Table registry: {c['object_fqn']}", validation_status=ValidationStatus.VERIFIED,
            numeric_use=NumericUse.NOT_APPROVED, facts=(Fact(name="object_fqn", value=c["object_fqn"]),
                                                        Fact(name="grain", value=str(c.get("grain", ""))[:200])),
            summary=_safe_text(f"{c['logical_name']}: {c.get('short_description', '')}"))
            for c in response.get("candidates", [])[:3])
        return WorkerOutput(TaskState.SUCCEEDED if evidence else TaskState.FAILED, evidence,
                            error=None if evidence else _error(task, ErrorCategory.NO_EVIDENCE, "no_tables",
                                                               "No approved table matches."))

    def _call(self, context: WorkerContext, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._tools_for(context.authorization).call(tool, arguments, context.request_id)
        except TimeoutError as error:
            raise TransientWorkerError(f"{tool}_timeout") from error
        except ConnectionError as error:
            raise TransientWorkerError(f"{tool}_connection") from error


# ------------------------------------------------------------------------------------------------------ helpers
def _memory_block(context: WorkerContext) -> str | None:
    """Untrusted memory plus verified upstream evidence summaries (the workflow screens this block again)."""
    parts = [context.memory_block] if context.memory_block else []
    upstream = [f"- [{e.evidence_id}] {e.summary}" for e in context.upstream_evidence if e.summary]
    if upstream:
        parts.append("VERIFIED UPSTREAM EVIDENCE (from earlier tasks of this investigation):\n" + "\n".join(upstream))
    return "\n\n".join(parts) or None


def _row_facts(rows: list[dict[str, Any]]) -> tuple[Fact, ...]:
    facts = []
    for index, row in enumerate(rows):
        for column, value in row.items():
            if len(facts) >= MAX_FACTS_PER_EVIDENCE:
                return tuple(facts)
            name = column if len(rows) == 1 else f"row{index + 1}.{column}"
            facts.append(Fact(name=str(name)[:120], value=redact_secrets(str(value))[:200]))
    return tuple(facts)


def _validated_query_evidence(task: AgentTask, result: WorkflowResult) -> EvidenceRecord:
    validation_id = result.validation.validation_id if result.validation else None
    return EvidenceRecord(
        evidence_id=new_id("ev"), investigation_id=task.investigation_id, task_id=task.task_id,
        source_system=SourceSystem.DATABRICKS_PUBLIC, kind=EvidenceKind.VALIDATED_QUERY,
        source_objects=tuple(result.source_objects or result.context_tables), source_version=validation_id,
        citation=f"Validated SQL (validation {validation_id}); not executed",
        provenance=tuple((k, v) for k, v in (("validation_id", validation_id), ("sql_sha256", _sha(result.validated_sql or "")))
                         if v),
        validation_status=ValidationStatus.VERIFIED, numeric_use=NumericUse.NOT_APPROVED,
        quality_warnings=("not_executed",), summary=_safe_text(result.message))


def _tool_problem(task: AgentTask, response: dict[str, Any]) -> WorkerOutput | None:
    status = response.get("status")
    if status == "ok":
        return None
    codes = [e.get("code", "") for e in response.get("errors", [])]
    if any(code.startswith("UNAUTHORIZED") or code == "PROHIBITED_COLUMN" for code in codes):
        return WorkerOutput(TaskState.FAILED, error=_error(task, ErrorCategory.AUTHORIZATION, codes[0].lower(),
                                                           "The registry refused access for the application principal."))
    if status == "not_found":
        return WorkerOutput(TaskState.FAILED, error=_error(task, ErrorCategory.NO_EVIDENCE, "not_found", "Nothing registered matches."))
    return WorkerOutput(TaskState.FAILED, error=_error(task, ErrorCategory.EXECUTION, f"tool_{status}", "The registry tool failed."))


def _error(task: AgentTask, category: ErrorCategory, code: str, message: str, retry_after: float | None = None) -> ExecutionError:
    return ExecutionError(error_id=new_id("err"), investigation_id=task.investigation_id, task_id=task.task_id,
                          node="execute", category=category, code=str(code)[:80], retry_after_seconds=retry_after,
                          safe_message=message)


def _with_filters(message: str, sql: str | None, parameters: dict[str, Any]) -> str:
    """The result message plus the query's filter conditions, so an answer can state its scope (no full SQL text)."""
    filters = applied_filters(sql, parameters)
    return f"{message} Filters applied: {filters}." if filters else message


def applied_filters(sql: str | None, parameters: dict[str, Any]) -> str:
    """WHERE and HAVING conditions of an executed query with parameters filled in, e.g. "country_name = 'Ethiopia'"."""
    if not sql:
        return ""
    try:
        tree = sqlglot.parse_one(sql, read="databricks")
    except sqlglot.errors.ParseError:
        return ""
    conditions = []
    for clause in tree.find_all(exp.Where, exp.Having):
        text = clause.this.sql(dialect="databricks")
        for name, value in parameters.items():
            text = text.replace(f":{name}", f"'{value}'" if isinstance(value, str) else str(value))
        conditions.append(text)
    return "; ".join(conditions)[:300]


def _safe_text(text: str) -> str:
    """Bounded text without instruction-like content or secret-shaped values (it reaches state and checkpoints)."""
    text = redact_secrets((text or "")[:1500])
    return "[removed: instruction-like text]" if contains_instruction_like_text(text) else text


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest() if text else ""
