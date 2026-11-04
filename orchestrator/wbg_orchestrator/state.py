"""LangGraph state of one investigation and its reducers.

Nodes never mutate state: they return updates, and these reducers merge them.
  * record lists are append-only, keyed by the record id; re-sending an identical record is a no-op, sending a
    different record under an existing id raises (immutability);
  * task states only move forward along `ALLOWED_TRANSITIONS` (terminal states are final);
  * usage counters only add up.
Everything stored here is JSON (dicts of records), so any checkpointer can persist it.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Any, Callable, TypedDict

from .contracts import ALLOWED_TRANSITIONS, TaskState


class ImmutableRecordError(ValueError):
    """A record id was reused for different content."""


class IllegalTransitionError(ValueError):
    """A task state tried to move backwards or skip a step."""


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, default=str)


def append_records(*key_fields: str) -> Callable[[list[dict], list[dict]], list[dict]]:
    """Build an append-only list reducer keyed by `key_fields`; a changed record under a known key raises."""
    def reducer(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
        """Append new records, skip exact repeats, and raise ImmutableRecordError for a changed one."""
        merged = list(existing or [])
        index = {tuple(r[k] for k in key_fields): r for r in merged}
        for record in new or []:
            key = tuple(record[k] for k in key_fields)
            if key in index:
                if _canonical(index[key]) != _canonical(record):
                    raise ImmutableRecordError(f"record {key} already exists with different content")
                continue
            index[key] = record
            merged.append(record)
        return merged
    return reducer


def merge_task_states(existing: dict[str, str] | None, new: dict[str, str] | None) -> dict[str, str]:
    """Merge task states, allowing only forward transitions; a new task must enter as pending."""
    merged = dict(existing or {})
    for task_id, state in (new or {}).items():
        target = TaskState(state)
        if task_id in merged:
            current = TaskState(merged[task_id])
            if current == target:
                continue
            if target not in ALLOWED_TRANSITIONS[current]:
                raise IllegalTransitionError(f"task {task_id}: {current.value} -> {target.value} is not allowed")
        elif target not in (TaskState.PENDING,):
            raise IllegalTransitionError(f"task {task_id} must start as pending")
        merged[task_id] = target.value
    return merged


def add_counters(existing: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """Add usage counters together. Cost is summed as a Decimal string; an unknown (None) cost adds nothing."""
    merged = dict(existing or {})
    for key, value in (new or {}).items():
        if key == "cost_usd":
            if value is not None:
                merged[key] = str(Decimal(merged.get(key) or "0") + Decimal(str(value)))
            else:
                merged.setdefault(key, None)
        else:
            merged[key] = merged.get(key, 0) + value
    return merged


def last_value(existing: Any, new: Any) -> Any:
    return new


# The investigation graph's state; each Annotated field names the reducer that merges a node's update into it.
class InvestigationState(TypedDict, total=False):
    request: dict                                                        # InvestigationRequest (set once)
    phase: Annotated[str, last_value]
    supervisor: Annotated[dict, last_value]                              # fast path / plan / clarify decision
    plan: Annotated[dict | None, last_value]                             # current InvestigationPlan
    plans: Annotated[list[dict], append_records("plan_id")]              # every plan version (immutable)
    plan_errors: Annotated[list[dict], append_records("error_id")]       # validation errors of rejected plans
    replans: Annotated[int, lambda a, b: (a or 0) + (b or 0)]
    task_states: Annotated[dict[str, str], merge_task_states]
    next_batch: Annotated[list[str], last_value]
    results: Annotated[list[dict], append_records("task_id", "attempt")]
    evidence: Annotated[list[dict], append_records("evidence_id")]       # the evidence ledger
    errors: Annotated[list[dict], append_records("error_id")]
    approvals: Annotated[list[dict], append_records("approval_id")]
    decisions: Annotated[list[dict], append_records("approval_id")]
    usage: Annotated[dict, add_counters]
    verification: Annotated[dict | None, last_value]
    response: Annotated[dict | None, last_value]
