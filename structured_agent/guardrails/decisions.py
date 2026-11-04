"""Typed guardrail decisions shared by the NeMo rails and the application policies.

Every decision says which layer enforced it, so reports and logs never present application code as a NeMo rail:
  * nemo_builtin_rail   a rail from the NeMo Guardrails library (here: the deterministic regex rail)
  * nemo_custom_action  a NeMo rail flow whose check is our deterministic Python action, executed by NeMo
  * application_policy  a deterministic check outside NeMo (retrieved-context, tool and execution gates)
  * nemo_rails          an allow decision after every NeMo rail of that stage passed (built-in and custom)
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from enum import Enum
from typing import Iterator, Literal

from pydantic import BaseModel

EnforcedBy = Literal["nemo_builtin_rail", "nemo_custom_action", "application_policy", "nemo_rails"]


# The point in the workflow where a guardrail decision is made.
class GuardStage(str, Enum):
    INPUT = "input"
    RETRIEVED_CONTEXT = "retrieved_context"
    MODEL_OUTPUT = "model_output"
    TOOL_AUTHORIZATION = "tool_authorization"
    EXECUTION = "execution"
    FINAL_RESPONSE = "final_response"


# Why a guardrail blocked (or, for a few values, adjusted) a request. Only these codes are logged, never text.
class ReasonCategory(str, Enum):
    INVALID_SIZE = "invalid_size"
    INVALID_FORMAT = "invalid_format"
    INSTRUCTION_OVERRIDE = "instruction_override"
    RESTRICTED_DATA_REQUEST = "restricted_data_request"
    CREDENTIAL_EXFILTRATION = "credential_exfiltration"
    CONTEXT_INSTRUCTION_REMOVED = "context_instruction_removed"   # allowed: untrusted text was dropped
    UNAPPROVED_METADATA = "unapproved_metadata"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    UNSUPPORTED_EXECUTION_CLAIM = "unsupported_execution_claim"
    FABRICATED_TABLE_REFERENCE = "fabricated_table_reference"
    SENSITIVE_VALUE_IN_OUTPUT = "sensitive_value_in_output"
    UNAPPROVED_TOOL = "unapproved_tool"
    IDENTITY_MISMATCH = "identity_mismatch"
    VALIDATION_NOT_APPROVED = "validation_not_approved"
    PRIOR_POLICY_FAILURE = "prior_policy_failure"
    INCONSISTENT_RESULT = "inconsistent_result"
    GUARDRAIL_ERROR = "guardrail_error"   # the guardrail itself failed: fail closed
    OFF_TOPIC = "off_topic"               # not about procurement: answered with what the assistant can do
    MISCONDUCT_ACCUSATION = "misconduct_accusation"   # asks to judge a party corrupt or fraudulent


class PolicyResult(BaseModel):
    """Outcome of one deterministic policy function."""

    allowed: bool
    reason: ReasonCategory | None = None

    @classmethod
    def allow(cls, reason: ReasonCategory | None = None) -> "PolicyResult":
        return cls(allowed=True, reason=reason)

    @classmethod
    def block(cls, reason: ReasonCategory) -> "PolicyResult":
        return cls(allowed=False, reason=reason)


class GuardDecision(BaseModel):
    """One guardrail decision. Contains no user text, retrieved text, SQL or result values."""

    stage: GuardStage
    policy_id: str
    allowed: bool
    reason_category: ReasonCategory | None = None
    enforced_by: EnforcedBy
    duration_ms: int = 0

    @property
    def decision(self) -> str:
        return "allow" if self.allowed else "block"


class GuardrailBlocked(Exception):
    """Raised when a guardrail blocks the request; carries only the safe decision."""

    def __init__(self, decision: GuardDecision):
        self.decision = decision
        super().__init__(f"{decision.stage.value}:{decision.policy_id}:{decision.reason_category}")


@contextmanager
def timed() -> Iterator[dict]:
    """Measures a block; the elapsed milliseconds are in the yielded dict's 'ms' after the block."""
    timing = {"ms": 0}
    started = time.perf_counter()
    try:
        yield timing
    finally:
        timing["ms"] = round(1000 * (time.perf_counter() - started))
