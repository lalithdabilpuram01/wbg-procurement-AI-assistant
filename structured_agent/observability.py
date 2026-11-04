"""Sanitised audit events and OpenTelemetry-compatible tracing.

* Audit events go to an AuditSink. The active sink writes local JSONL; registry.audit_events in PostgreSQL is prepared
  for a durable sink later.
* Spans use the OpenTelemetry API. Logfire is OpenTelemetry-based, so once the Logfire SDK is installed and
  configured it can export these spans; it is not installed here, so no hosted export happens.
* Only allow-listed attributes are recorded: SQL text, parameter values, result rows, credentials and principal ids
  never leave the process (the principal id is hashed).
"""
from __future__ import annotations

import hashlib
import json
import logging
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

from opentelemetry import trace
from pydantic import BaseModel

log = logging.getLogger("wbg.structured_agent")

# The only attribute names that may be written to audit events and spans; anything else is dropped.
SAFE_ATTRIBUTES = {
    "tool", "status", "duration_ms", "error_codes", "objects", "candidate_objects", "approved", "validation_id",
    "query_id", "row_count", "truncated", "credential_mode", "production_read_only", "checks_failed", "sql_sha256",
    "generator", "is_llm", "attempts", "result_checks_passed", "registry_build_id", "principal_hash", "top_k",
    "query_length", "metric_ids", "ambiguity_group", "authorization", "validation_status",
    # natural-language-to-SQL workflow
    "failure_class", "llm_call_index", "llm_calls", "input_tokens", "output_tokens", "breaker", "from_state", "to_state",
    "retry_after_seconds", "limit", "workflow_status", "provider_status_code", "provider_error_type",
    # guardrail decisions
    "guard_stage", "policy_id", "decision", "reason_category", "enforced_by",
    # conversation service (ids, counts and statuses only)
    "thread_id", "turn_seq", "resolution", "context_tokens", "context_budget", "items_included", "items_dropped",
    "summary_version", "summary_status", "degraded",
    # intent routing (route names, reasons and permissions only)
    "route", "route_reason", "confidence_level", "confidence_basis", "retrieval_allowed", "external_tools_allowed",
    "reset_topic", "inherited_route", "route_decided_by",
}


def log_failure(message: str, error: BaseException, **ids: Any) -> None:
    """Log a failure that is turned into a typed error result: its type and where it was raised (file:line:function),
    never its message, which may contain SQL or data. Enough to find the failing code; the caller returns the error."""
    frames = traceback.extract_tb(error.__traceback__)
    location = f"{Path(frames[-1].filename).name}:{frames[-1].lineno}:{frames[-1].name}" if frames else ""
    log.error(message, extra={"error_type": type(error).__name__, "error_location": location, **sanitize(ids)})


def principal_hash(principal_id: str) -> str:
    """Short, stable hash of a principal id, so audit events can be grouped by caller without naming them."""
    return hashlib.sha256(principal_id.encode()).hexdigest()[:12]


def sanitize(attributes: dict[str, Any]) -> dict[str, Any]:
    """Keep allow-listed keys only, with simple JSON values."""
    clean = {}
    for key, value in attributes.items():
        if key not in SAFE_ATTRIBUTES or value is None:
            continue
        clean[key] = value if isinstance(value, (str, int, float, bool)) else json.loads(json.dumps(value, default=str))
    return clean


# One line of the audit log. 'attributes' holds only sanitised, allow-listed values.
class AuditEvent(BaseModel):
    event_id: str
    occurred_at: str
    request_id: str
    invocation_id: str
    event_type: str
    attributes: dict[str, Any]


class AuditSink(Protocol):
    def write(self, event: AuditEvent) -> None: ...


class JsonlAuditSink:
    """Audit sink that appends one JSON line per event to a local file."""

    def __init__(self, path: Path):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: AuditEvent) -> None:
        with self._path.open("a") as handle:
            handle.write(event.model_dump_json() + "\n")


class Telemetry:
    """Audit events plus OpenTelemetry spans. Without a configured provider the OpenTelemetry API is a no-op."""

    def __init__(self, sink: AuditSink, tracer_provider: trace.TracerProvider | None = None):
        self._sink = sink
        self._tracer = (tracer_provider or trace.get_tracer_provider()).get_tracer("wbg.structured_agent")

    def audit(self, request_id: str, invocation_id: str, event_type: str, **attributes: Any) -> None:
        """Write one sanitised audit event to the sink."""
        self._sink.write(AuditEvent(event_id="evt_" + uuid.uuid4().hex[:16],
                                    occurred_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                                    request_id=request_id, invocation_id=invocation_id, event_type=event_type,
                                    attributes=sanitize(attributes)))

    @contextmanager
    def span(self, name: str, request_id: str, invocation_id: str) -> Iterator[trace.Span]:
        """Open a tracing span tagged with the request and invocation ids."""
        with self._tracer.start_as_current_span(name) as span:
            span.set_attribute("wbg.request_id", request_id)
            span.set_attribute("wbg.invocation_id", invocation_id)
            yield span

    @staticmethod
    def annotate(span: trace.Span, **attributes: Any) -> None:
        for key, value in sanitize(attributes).items():
            span.set_attribute(f"wbg.{key}", value if isinstance(value, (str, int, float, bool)) else json.dumps(value))
