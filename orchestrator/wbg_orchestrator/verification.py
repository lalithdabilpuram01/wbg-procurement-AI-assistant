"""Deterministic evidence verification over the append-only evidence ledger.

* Requirements: every task's evidence requirements are checked against the evidence its results produced (including
  results reused by idempotency); only VERIFIED records count when a requirement demands verification.
* Completion: required tasks usable and their requirements met.
* Conflicts: the same query (same source objects, SQL and parameters) reporting different values for the same fact
  (e.g. data changed between two executions), and document answers the RAG validator marked as conflicting.
  Different queries on the same view (other filters) are never compared.
* Numeric support: only APPROVED numbers (validated dataset results) and DOCUMENT_STATEMENT numbers (narrative text
  with a verified page) may support figures in an answer; PDF table values never do.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict

from .contracts import (USABLE_TASK_STATES, EvidenceRecord, InvestigationPlan, NumericUse, TaskState, ValidationStatus)


class Conflict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    subject: str                                   # fact name and source objects, or a document conflict marker
    values: tuple[str, ...]
    evidence_ids: tuple[str, ...]


class VerificationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    task_evidence: dict[str, tuple[str, ...]]      # task id -> evidence ids (own or reused)
    unmet: tuple[str, ...]                         # "<task id>:<kind>:<have>/<need>"
    satisfied_required: tuple[str, ...]
    missing_required: tuple[str, ...]
    required_complete: bool
    verified_evidence_count: int
    conflicts: tuple[Conflict, ...]
    numeric_support_ids: tuple[str, ...]           # evidence allowed to support figures
    quality_warnings: tuple[str, ...]


def ledger(state: Mapping[str, Any]) -> dict[str, EvidenceRecord]:
    return {e["evidence_id"]: EvidenceRecord.from_state(e) for e in state.get("evidence", [])}


def task_evidence(state: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = defaultdict(list)
    for result in state.get("results", []):
        if TaskState(result["status"]) in USABLE_TASK_STATES | {TaskState.SKIPPED}:
            out[result["task_id"]].extend(result["evidence_ids"])
    return {k: tuple(dict.fromkeys(v)) for k, v in out.items()}


def verify(plan: InvestigationPlan, state: Mapping[str, Any]) -> VerificationReport:
    records = ledger(state)
    states = state.get("task_states", {})
    by_task = task_evidence(state)
    unmet, satisfied, missing = [], [], []
    for task in plan.tasks:
        evidence = [records[i] for i in by_task.get(task.task_id, ()) if i in records]
        task_ok = TaskState(states.get(task.task_id, "pending")) in USABLE_TASK_STATES | {TaskState.SKIPPED}
        for requirement in task.evidence_requirements:
            have = sum(1 for e in evidence if e.kind in requirement.accepted_kinds
                       and (not requirement.must_be_verified or e.validation_status == ValidationStatus.VERIFIED))
            if have < requirement.min_count:
                unmet.append(f"{task.task_id}:{requirement.kind.value}:{have}/{requirement.min_count}")
                task_ok = False
        if task.task_id in plan.completion.required_task_ids:
            (satisfied if task_ok else missing).append(task.task_id)
    used = [records[i] for ids in by_task.values() for i in ids if i in records]
    verified = sum(1 for e in used if e.validation_status == ValidationStatus.VERIFIED)
    complete = not missing and verified >= plan.completion.min_verified_evidence
    warnings = sorted({w for e in used for w in e.quality_warnings})
    numeric = tuple(e.evidence_id for e in used if e.numeric_use != NumericUse.NOT_APPROVED
                    and e.validation_status == ValidationStatus.VERIFIED)
    return VerificationReport(task_evidence=by_task, unmet=tuple(unmet), satisfied_required=tuple(satisfied),
                              missing_required=tuple(missing), required_complete=complete, verified_evidence_count=verified,
                              conflicts=_conflicts(used), numeric_support_ids=numeric, quality_warnings=tuple(warnings))


def _conflicts(evidence: list[EvidenceRecord]) -> tuple[Conflict, ...]:
    seen: dict[tuple[str, ...], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for record in evidence:
        if record.numeric_use != NumericUse.APPROVED or record.validation_status != ValidationStatus.VERIFIED:
            continue
        query = (",".join(sorted(record.source_objects)), record.provenance_value("sql_sha256") or "",
                 record.provenance_value("params_sha256") or "")
        for fact in record.facts:
            seen[(fact.name, *query)][fact.value.strip()].add(record.evidence_id)
    conflicts = [Conflict(subject=f"{key[0]} @ {key[1]}"[:200], values=tuple(sorted(values)),
                          evidence_ids=tuple(sorted({i for ids in values.values() for i in ids})))
                 for key, values in seen.items() if len(values) > 1]
    flagged = tuple(sorted(e.evidence_id for e in evidence if "conflicting_evidence" in e.quality_warnings))
    if flagged:
        conflicts.append(Conflict(subject="document evidence reported as conflicting", values=(), evidence_ids=flagged))
    return tuple(conflicts)
