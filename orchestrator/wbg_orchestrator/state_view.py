"""Readable summaries of the investigation state, for the log and the trace after every graph step.

The full state holds evidence rows, document excerpts and generated text, too much (and too sensitive) to log. These
summaries keep what an engineer needs to follow a run: ids, phases, task states, counts, error codes, and the question,
task questions and answer message with personal data masked and cut short.

    summarize_state(state)      the whole state after a step
    describe_update(update)     what one step (or one task) wrote, with list fields shown as the records it added
    apply_update(state, update) the state after the update, merged with the same reducers LangGraph uses
"""
from __future__ import annotations

import typing
from collections import Counter
from functools import lru_cache
from typing import Any

from .logs import redacted
from .state import InvestigationState

QUESTION_CHARS = 300
MESSAGE_CHARS = 300
MAX_LISTED = 20                       # longer lists are cut, with their full count kept


@lru_cache(maxsize=512)
def _masked(text: str, limit: int) -> str:
    """The same question is summarized after every step: mask it once."""
    return redacted(text, limit)


def _text(value: Any, limit: int) -> str:
    return _masked(str(value), limit) if value else ""


def _local(task_id: str | None) -> str | None:
    """`plan_x.t1` -> `t1`: the task's name inside its plan, short enough to read."""
    return task_id.rsplit(".", 1)[-1] if task_id else task_id


def _listed(items: list[Any]) -> list[Any]:
    return items[:MAX_LISTED] + ([f"... {len(items) - MAX_LISTED} more"] if len(items) > MAX_LISTED else [])


# ------------------------------------------------------------------------------------------------- records
# One small view per record type: ids, statuses and counts, with any text masked and cut; never facts or excerpts.
def _request(request: dict) -> dict:
    return {"investigation_id": request.get("investigation_id"), "request_id": request.get("request_id"),
            "mode": request.get("mode"), "execute_requested": request.get("execute_requested"),
            "rag_release": request.get("rag_release"), "question": _text(request.get("question"), QUESTION_CHARS)}


def _plan(plan: dict | None) -> dict | None:
    """The plan's id, version and author, and its tasks (local ids, worker, operation, dependencies, short question)."""
    if not plan:
        return None
    tasks = [{"task": t.get("local_id"), "worker": t.get("worker"), "operation": t.get("operation"),
              "depends_on": [_local(d) for d in t.get("depends_on", [])], "question": _text(t.get("question"), QUESTION_CHARS)}
             for t in plan.get("tasks", [])]
    return {"plan_id": plan.get("plan_id"), "version": plan.get("version"), "produced_by": plan.get("produced_by"),
            "tasks": _listed(tasks)}


def _result(result: dict) -> dict:
    return {"task": _local(result.get("task_id")), "attempt": result.get("attempt"), "status": result.get("status"),
            "evidence": len(result.get("evidence_ids", [])), "duration_ms": result.get("duration_ms"),
            "error_id": result.get("error_id")}


def _evidence(record: dict) -> dict:
    """One evidence record's ids, kind, source, validation status, document pages and projects (no content)."""
    pages = record.get("pages")
    return {"evidence_id": record.get("evidence_id"), "task": _local(record.get("task_id")), "kind": record.get("kind"),
            "source": record.get("source_system"), "validation": record.get("validation_status"),
            "document_id": record.get("document_id"), "pages": list(pages) if pages else None,
            "projects": _listed(list(record.get("project_ids", [])))}


def _error(error: dict) -> dict:
    return {"node": error.get("node"), "task": _local(error.get("task_id")), "category": error.get("category"),
            "code": error.get("code"), "message": error.get("safe_message")}


def _verification(report: dict | None) -> dict | None:
    """Counts from the verification report: verified evidence, conflicts and unmet requirements."""
    if not report:
        return None
    return {"verified_evidence": report.get("verified_evidence_count"), "conflicts": len(report.get("conflicts", [])),
            "unmet": len(report.get("unmet", []))}


def _response(response: dict | None) -> dict | None:
    """The answer's status, claim counts, error codes and shortened message."""
    if not response:
        return None
    return {"status": response.get("status"), "claims": len(response.get("claims", [])),
            "claims_removed": len(response.get("removed_claims", [])), "error_codes": list(response.get("error_codes", [])),
            "message": _text(response.get("message"), MESSAGE_CHARS)}


def _supervisor(decision: dict | None) -> dict | None:
    """The supervisor's decision: mode, worker, operation and its first five reasons."""
    if not decision:
        return None
    return {"mode": decision.get("mode"), "worker": decision.get("worker"), "operation": decision.get("operation"),
            "reasons": list(decision.get("reasons", []))[:5]}


# --------------------------------------------------------------------------------------------------- views
def summarize_state(state: dict) -> dict[str, Any]:
    """The state after a step: request, phase, plan, task states, counts of results and evidence, errors, answer."""
    evidence = state.get("evidence", [])
    summary = {
        "phase": state.get("phase"),
        "request": _request(state.get("request") or {}),
        "supervisor": _supervisor(state.get("supervisor")),
        "plan": _plan(state.get("plan")),
        "plan_versions": len(state.get("plans", [])),
        "replans": state.get("replans", 0),
        "task_states": {_local(k): v for k, v in state.get("task_states", {}).items()},
        "next_batch": [_local(t) for t in state.get("next_batch", [])],
        "results": dict(Counter(r.get("status") for r in state.get("results", []))),
        "evidence": {"count": len(evidence), "verified": sum(1 for e in evidence if e.get("validation_status") == "verified"),
                     "by_kind": dict(Counter(e.get("kind") for e in evidence))},
        "errors": _listed([_error(e) for e in state.get("errors", [])]),
        "plan_errors": _listed([e.get("code") for e in state.get("plan_errors", [])]),
        "usage": dict(state.get("usage", {})),
        "verification": _verification(state.get("verification")),
        "response": _response(state.get("response")),
    }
    return {k: v for k, v in summary.items() if v not in (None, {}, [], "")}


# How each state field is shown when a step writes it (fields not listed are shown as written).
_CHANGE_VIEWS = {
    "request": _request, "supervisor": _supervisor, "plan": _plan, "verification": _verification, "response": _response,
    "plans": lambda rows: [p.get("plan_id") for p in rows],
    "plan_errors": lambda rows: [_error(e) for e in rows],
    "results": lambda rows: [_result(r) for r in rows],
    "evidence": lambda rows: [_evidence(e) for e in rows],
    "errors": lambda rows: [_error(e) for e in rows],
    "approvals": lambda rows: [a.get("approval_id") for a in rows],
    "decisions": lambda rows: [d.get("approval_id") for d in rows],
    "next_batch": lambda ids: [_local(t) for t in ids],
}


def describe_update(update: dict | None, before: dict | None = None) -> dict[str, Any]:
    """What a step wrote: each changed field, with task states as `old -> new` and lists as the records added."""
    before = before or {}
    changes: dict[str, Any] = {}
    for key, value in (update or {}).items():
        if value in (None, [], {}) and key not in ("plan", "response"):
            continue
        if key == "task_states":
            old = before.get("task_states", {})
            changes[key] = {_local(t): f"{old[t]} -> {s}" if t in old and old[t] != s else s for t, s in value.items()}
        elif key in _CHANGE_VIEWS:
            view = _CHANGE_VIEWS[key](value)
            changes[key] = _listed(view) if isinstance(view, list) else view
        else:
            changes[key] = value
    return changes


@lru_cache(maxsize=1)
def _reducers() -> dict[str, Any]:
    """The reducer of each annotated state field (the same functions LangGraph merges updates with)."""
    hints = typing.get_type_hints(InvestigationState, include_extras=True)
    return {k: h.__metadata__[0] for k, h in hints.items() if getattr(h, "__metadata__", None)}


def apply_update(state: dict, update: dict | None) -> dict:
    """The state after `update`, without touching `state`. A reducer that refuses the update is LangGraph's error to
    raise, not the log's: the field is then shown as it was."""
    merged, reducers = dict(state), _reducers()
    for key, value in (update or {}).items():
        try:
            merged[key] = reducers[key](merged.get(key), value) if key in reducers else value
        except Exception:
            pass
    return merged
