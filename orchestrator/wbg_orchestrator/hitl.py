"""Human-in-the-loop approval: when a plan needs approval, how a request is built, and how a decision is checked.

The MVP workers are read-only; approval gates read-only SQL execution in planned investigations and large plans. No
database modification or external action can be approved, because no such operation exists in the allow-list.
A decision is valid only if it answers the pending request (same approval id and payload hash), comes from the same
user who owns the investigation, and arrives before the request expires. Each approval can be used once.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import ApprovalDecision, ApprovalRequest, InvestigationPlan, InvestigationRequest, stable_id


class ApprovalPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    execution_requires_approval: Literal["always", "planned", "never"] = "planned"   # fast path: the user's own request
    llm_plans_require_approval: bool = False
    large_plan_tasks: int = Field(default=4, ge=1)
    ttl_minutes: int = Field(default=30, ge=1, le=1440)


def approval_needed(plan: InvestigationPlan, policy: ApprovalPolicy) -> tuple[str, tuple[str, ...]] | None:
    reasons = []
    executes = [t.task_id for t in plan.tasks if t.requires_execution]
    planned = plan.produced_by != "fast_path"
    if executes and (policy.execution_requires_approval == "always" or (policy.execution_requires_approval == "planned" and planned)):
        reasons.append("read_only_sql_execution")
    if planned and policy.llm_plans_require_approval:
        reasons.append("llm_generated_plan")
    if planned and len(plan.tasks) >= policy.large_plan_tasks:              # fixed system plans (brief) are reviewed in code
        reasons.append("large_plan")
    if not reasons:
        return None
    kind = "execute_sql" if "read_only_sql_execution" in reasons else ("llm_plan" if "llm_generated_plan" in reasons else "large_plan")
    return kind, tuple(reasons)


def payload_sha256(plan: InvestigationPlan) -> str:
    """Hash of exactly what runs: task ids, workers, operations, questions, execution flags and dependencies."""
    payload = [(t.task_id, t.worker.value, t.operation, t.question, t.requires_execution, t.depends_on) for t in plan.tasks]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def build_approval(plan: InvestigationPlan, request: InvestigationRequest, kind: str, reasons: tuple[str, ...],
                   policy: ApprovalPolicy, now: dt.datetime) -> ApprovalRequest:
    tasks = "; ".join(f"{t.local_id}: {t.worker.value}.{t.operation}{' (runs SQL, read-only)' if t.requires_execution else ''}"
                      for t in plan.tasks)
    return ApprovalRequest(
        approval_id=stable_id("apr", plan.plan_id), investigation_id=request.investigation_id, plan_id=plan.plan_id, kind=kind,
        reasons=reasons, summary=f"{len(plan.tasks)} read-only task(s): {tasks}"[:600], task_ids=tuple(t.task_id for t in plan.tasks),
        payload_sha256=payload_sha256(plan), requested_for=request.owner, created_at=now,
        expires_at=now + dt.timedelta(minutes=policy.ttl_minutes))


def decision_problem(decision: ApprovalDecision, approval: ApprovalRequest, now: dt.datetime, used: set[str]) -> str | None:
    """None when the decision may be applied, else a reason code (nothing runs in that case)."""
    if decision.approval_id != approval.approval_id:
        return "approval_id_mismatch"
    if decision.approval_id in used:
        return "approval_already_used"
    if decision.decided_by != approval.requested_for:
        return "not_the_requesting_user"
    if decision.payload_sha256 != approval.payload_sha256:
        return "payload_mismatch"
    if now > approval.expires_at:
        return "approval_expired"
    return None
