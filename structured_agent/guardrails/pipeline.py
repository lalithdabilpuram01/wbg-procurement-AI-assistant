"""Guardrail pipeline used by the NL2SQL workflow.

Stage                 Enforced by
-------------------   ----------------------------------------------------------------------------------------
input                 NeMo: built-in regex rail (credential requests) + custom action rail (size, format,
                      instruction override, restricted data)
retrieved_context     application policy: drop unapproved tables and instruction-like text from registry metadata
model_output          NeMo: built-in regex rail (secret values) + custom action rail (draft schema, execution
                      claims, tables outside the context)
tool_authorization    application policy: each workflow stage may call only its approved MCP tools
execution             application policy: trusted identity unchanged, validation approved, no earlier block
final_response        NeMo: built-in regex rail (secret values) + custom action rail (status/SQL/rows consistency)

The existing MCP authorization and SQLGlot validator remain the authoritative controls; these guardrails add
earlier, independent checks and never replace or relax them.
"""
from __future__ import annotations

from typing import Any

from structured_agent.guardrails import policies
from structured_agent.guardrails.decisions import (GuardDecision, GuardrailBlocked, GuardStage, PolicyResult, ReasonCategory,
                                                   timed)
from structured_agent.nl2sql.context import SchemaContext
from structured_agent.nl2sql.sql_generator import SqlDraft

# Which MCP tools each workflow stage may call.
RETRIEVAL_TOOLS = frozenset({"search_table_registry", "get_table_schema", "get_table_relationships",
                             "get_metric_definition", "get_verified_sql_examples"})
VALIDATION_TOOLS = frozenset({"validate_sql"})
EXECUTION_TOOLS = frozenset({"execute_readonly_sql"})


def _application_decision(stage: GuardStage, policy_id: str, result: PolicyResult, duration_ms: int) -> GuardDecision:
    return GuardDecision(stage=stage, policy_id=policy_id, allowed=result.allowed, reason_category=result.reason,
                         enforced_by="application_policy", duration_ms=duration_ms)


class ToolGate:
    """Wraps a ToolCaller so one workflow stage can only call its approved tools (application policy)."""

    def __init__(self, tools: Any, allowed_tools: frozenset[str], stage_name: str):
        """Keep the wrapped tool caller, the tools this stage may call, and the stage name used in refusals."""
        self._tools = tools
        self._allowed = allowed_tools
        self._stage_name = stage_name
        # Exposed so the gate can stand in for the ToolCaller it wraps.
        self.principal_id = tools.principal_id

    def call(self, tool: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        """Forward the call when the tool is approved for this stage; otherwise raise GuardrailBlocked."""
        if tool not in self._allowed:
            raise GuardrailBlocked(GuardDecision(
                stage=GuardStage.TOOL_AUTHORIZATION, policy_id=f"app.tool_gate.{self._stage_name}", allowed=False,
                reason_category=ReasonCategory.UNAPPROVED_TOOL, enforced_by="application_policy"))
        return self._tools.call(tool, arguments, request_id)


class Guardrails:
    """All guardrail checks of one workflow. `nemo` is a NemoGuardrails instance (input/output rails)."""

    def __init__(self, nemo: Any):
        self._nemo = nemo

    # ------------------------------------------------------------------------------------------------- NeMo rails
    def check_input(self, question: str) -> GuardDecision:
        return self._nemo.check_input(question)

    def check_model_output(self, draft: SqlDraft, context: SchemaContext) -> GuardDecision:
        return self._nemo.check_output(GuardStage.MODEL_OUTPUT, draft.model_dump_json(), [t.object_fqn for t in context.tables])

    def check_final_response(self, response_json: str) -> GuardDecision:
        return self._nemo.check_output(GuardStage.FINAL_RESPONSE, response_json)

    # ------------------------------------------------------------------------------------- application policies
    @staticmethod
    def guard_context(context: SchemaContext) -> tuple[SchemaContext, GuardDecision]:
        """Sanitise the retrieved schema context; returns the clean context and the decision."""
        with timed() as timing:
            clean, result, _removed = policies.sanitize_retrieved_context(context)
        return clean, _application_decision(GuardStage.RETRIEVED_CONTEXT, "app.retrieved_context", result, timing["ms"])

    @staticmethod
    def check_execution(trusted_principal: str, current_principal: str, validation_approved: bool,
                        validation_id: str | None, prior_decisions: list[GuardDecision]) -> GuardDecision:
        """Execution gate: same identity as at request start, an approved validation, and no earlier block."""
        with timed() as timing:
            result = policies.evaluate_execution(trusted_principal, current_principal, validation_approved, validation_id,
                                                 prior_decisions)
        return _application_decision(GuardStage.EXECUTION, "app.execution_gate", result, timing["ms"])

    @staticmethod
    def tool_gate(tools: Any, allowed_tools: frozenset[str], stage_name: str) -> ToolGate:
        return ToolGate(tools, allowed_tools, stage_name)


def build_guardrails() -> Guardrails:
    """The production pipeline. Raises ConfigurationError (or ImportError) when NeMo is unavailable: fail closed."""
    from structured_agent.guardrails.nemo_rails import NemoGuardrails

    return Guardrails(NemoGuardrails())
