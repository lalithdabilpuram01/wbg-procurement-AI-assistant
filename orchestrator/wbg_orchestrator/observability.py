"""Metadata-only tracing (Logfire / OpenTelemetry) and audit events for the orchestrator.

* Allow-list of attribute names; string values must look like identifiers or codes (no spaces, bounded length), so
  questions, prompts, SQL, excerpts, rows, e-mail addresses and tokens cannot be recorded even by mistake.
* Logfire is a LOCAL instance (console off, no auto-instrumentation, does not replace the global provider). Tests
  attach an in-memory span exporter. Export to Logfire happens only through `exporting()` with the existing
  LOGFIRE_* settings explicitly switched on by the caller; the same allow-list applies to exported spans.
* Identifiers only: investigation, conversation (thread id), plan, task, approval and checkpoint ids; users appear
  only as the pseudonymous owner hash.
* The audit trail writes allow-listed events to an explicitly supplied sink only (never a default project file).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol, Sequence

SAFE_ATTRIBUTES = frozenset({
    "investigation_id", "request_id", "plan_id", "plan_version", "produced_by", "task_id", "worker", "operation", "node",
    "mode", "route", "status", "execution_state", "attempt", "max_attempts", "retries", "retry_delay_ms", "duration_ms",
    "latency_ms", "llm_calls", "input_tokens", "output_tokens", "cost_usd", "executions", "error_category", "error_code",
    "breaker", "breaker_from", "breaker_to", "approval_id", "approval_kind", "approval_decision", "evidence_count",
    "task_count", "ready_count", "cancelled_count", "claims_kept", "claims_removed", "rag_release", "replans",
    "validation_errors", "conflict_count", "unmet_count", "owner_match",
    # Milestone B: correlation, provider calls and checkpoints
    "conversation_id", "owner_hash", "model", "worst_case_usd", "committed_usd", "attempts_used", "http_status",
    "provider_error_type", "gateway_retry_count", "checkpoint_id", "checkpoint_thread", "checkpointer", "step",
    # audit timeline: trace link, guardrail decisions, error lists
    "trace_id", "error_codes", "guard_policy", "guard_reason", "cache_read_tokens", "cache_write_tokens",
})
SAFE_STRING = re.compile(r"^[A-Za-z0-9_.:@,\-]{0,200}$")


def current_trace_id() -> str | None:
    """The OpenTelemetry (Logfire) trace id of the running span, to link an audit line to its trace."""
    from opentelemetry import trace
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


def safe_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in attributes.items():
        if key not in SAFE_ATTRIBUTES or value is None:
            continue
        if isinstance(value, bool | int | float):
            clean[key] = value
        elif isinstance(value, str) and SAFE_STRING.match(value):
            clean[key] = value
    return clean


SpanSetter = Callable[..., None]


class OrchestratorTracer:
    def __init__(self, logfire_instance: Any | None = None):
        self._logfire = logfire_instance

    @classmethod
    def disabled(cls) -> "OrchestratorTracer":
        return cls(None)

    @classmethod
    def local(cls, extra_span_processors: Sequence[Any] = (), service_name: str = "wbg-orchestrator") -> "OrchestratorTracer":
        """Local Logfire instance: never exports (explicit send_to_logfire=False, no token, no console)."""
        import logfire
        instance = logfire.configure(local=True, send_to_logfire=False, token=None, console=False, service_name=service_name,
                                     additional_span_processors=list(extra_span_processors), inspect_arguments=False,
                                     metrics=False)
        return cls(instance)

    @classmethod
    def exporting(cls, token: str, service_name: str, extra_span_processors: Sequence[Any] = ()) -> "OrchestratorTracer":
        """Local Logfire instance that ALSO exports to the existing Logfire project (bounded, metadata-only tests).

        The caller passes the existing write token explicitly; nothing is read from the environment here.
        """
        import logfire
        instance = logfire.configure(local=True, send_to_logfire=True, token=token, console=False, service_name=service_name,
                                     additional_span_processors=list(extra_span_processors), inspect_arguments=False,
                                     metrics=False)
        return cls(instance)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[SpanSetter]:
        if self._logfire is None:
            yield _ignore
            return
        error: BaseException | None = None
        with self._logfire.span(name, _span_name=name, **safe_attributes(attributes)) as span:
            def set_attributes(**values: Any) -> None:
                for key, value in safe_attributes(values).items():
                    span.set_attribute(key, value)
            try:
                yield set_attributes
            except Exception as exc:        # never record the message: it may contain data
                error = exc
                span.set_attribute("error_category", type(exc).__name__)
                span.set_level("error")
        if error is not None:
            raise error

    def event(self, name: str, level: str = "info", **attributes: Any) -> None:
        if self._logfire is not None:
            self._logfire.log(level, name, attributes=safe_attributes(attributes))

    def force_flush(self) -> None:
        if self._logfire is not None:
            self._logfire.force_flush()


def _ignore(**values: Any) -> None:
    return None


# -------------------------------------------------------------------------------------------------------- audit
class AuditSink(Protocol):
    def write(self, event: dict[str, Any]) -> None: ...


class MemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def write(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class JsonlAuditSink:
    """Appends to an explicitly chosen file (tests pass a temporary path; there is no default path)."""

    def __init__(self, path: Path):
        self._path = Path(path)

    def write(self, event: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


class AuditTrail:
    def __init__(self, sink: AuditSink | None = None):
        self._sink = sink

    def record(self, event: str, **attributes: Any) -> None:
        if self._sink is not None:
            self._sink.write({"event": event, "occurred_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
                              **safe_attributes(attributes)})
