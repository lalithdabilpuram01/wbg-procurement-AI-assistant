"""Tracing (Logfire / OpenTelemetry) and audit events for the orchestrator.

What a trace looks like: one trace per user action (a chat message, a tender check, ...), with nested spans

    job.message                         the background job (job_id, kind, conversation_id, request_id)
      conversation.route                which route the message took (conversation, investigation, ...)
        orchestrator.node.intake        input guardrail (the question, personal data masked)
        orchestrator.node.plan          the planner (Claude or a fixed plan)
        orchestrator.node.execute       one span per task
          orchestrator.task_attempt     one span per attempt (retries are visible)
            worker.<name>               the worker itself, in its own thread (structured, document, prediction, ...)
              mcp.tool_call             a structured-data tool call (SQL over the Gold tables)
              orchestrator.llm_call     a Claude call (tokens, cost, provider errors)
        orchestrator.node.verify        evidence checks
        orchestrator.node.respond       the answer and how many claims were kept

When a step fails, its span is marked as an error and records `error_type`, `error_message` (personal data masked)
and `error_location` (file:line:function where it was raised); every enclosing span records `failed_step`, the name
of the innermost span that failed. The same failure is written once to the JSON log (logs.py) with its traceback
and the trace id, so the log line and the Logfire trace point at each other.

What may be recorded:
* attribute names come from an allow-list; string values must look like identifiers or codes (no spaces, bounded
  length), so rows, prompts, excerpts and tokens cannot be recorded by mistake;
* the only free-text attributes are TEXT_ATTRIBUTES (the question, generated SQL, an error message). They pass through
  the personal-data masker (e-mail addresses, phone numbers, card and IBAN numbers, IP addresses, secret-shaped
  values) and are truncated. The audit trail never records them;
* users appear only as the pseudonymous owner hash.

Export: Logfire runs as a LOCAL instance (console off, no auto-instrumentation, does not replace the global
provider). Spans leave the machine only through `exporting()`, which the runtime uses when LOGFIRE_SEND_TO_LOGFIRE is
true and a token is set. Tests attach an in-memory exporter.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol, Sequence

from .logs import describe_error, get_logger, redacted

log = get_logger("trace")

SAFE_ATTRIBUTES = frozenset({
    "investigation_id", "request_id", "plan_id", "plan_version", "produced_by", "task_id", "worker", "operation", "node",
    "mode", "route", "status", "execution_state", "attempt", "max_attempts", "retries", "retry_delay_ms", "duration_ms",
    "latency_ms", "llm_calls", "input_tokens", "output_tokens", "cost_usd", "executions", "error_category", "error_code",
    "breaker", "breaker_from", "breaker_to", "approval_id", "approval_kind", "approval_decision", "evidence_count",
    "task_count", "ready_count", "cancelled_count", "claims_kept", "claims_removed", "rag_release", "replans",
    "validation_errors", "conflict_count", "unmet_count", "owner_match",
    # correlation, provider calls and checkpoints
    "conversation_id", "owner_hash", "model", "worst_case_usd", "committed_usd", "attempts_used", "http_status",
    "provider_error_type", "gateway_retry_count", "checkpoint_id", "checkpoint_thread", "checkpointer", "step",
    # audit timeline: trace link, guardrail decisions, error lists
    "trace_id", "error_codes", "guard_policy", "guard_reason", "cache_read_tokens", "cache_write_tokens",
    # jobs, routing and failures
    "job_id", "job_kind", "reason", "error_type", "error_location", "failed_step",
})
# Free text allowed on spans only (never in the audit trail), masked and cut to this many characters.
TEXT_ATTRIBUTES = {"question": 1000, "sql": 4000, "error_message": 500}
SAFE_STRING = re.compile(r"^[A-Za-z0-9_.:@,\-]{0,200}$")


def current_trace_id() -> str | None:
    """The OpenTelemetry (Logfire) trace id of the running span, to link an audit line to its trace."""
    from opentelemetry import trace
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


def safe_attributes(attributes: dict[str, Any], allow_text: bool = False) -> dict[str, Any]:
    """The attributes that may be recorded: allow-listed names with identifier-like values, plus (on spans only,
    allow_text=True) the masked free-text attributes."""
    clean: dict[str, Any] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if allow_text and key in TEXT_ATTRIBUTES and isinstance(value, str):
            clean[key] = redacted(value, TEXT_ATTRIBUTES[key])
        elif key not in SAFE_ATTRIBUTES:
            continue
        elif isinstance(value, bool | int | float):
            clean[key] = value
        elif isinstance(value, str) and SAFE_STRING.match(value):
            clean[key] = value
    return clean


SpanSetter = Callable[..., None]


class OrchestratorTracer:
    def __init__(self, logfire_instance: Any | None = None):
        self._logfire = logfire_instance

    @property
    def enabled(self) -> bool:
        return self._logfire is not None

    @classmethod
    def disabled(cls) -> "OrchestratorTracer":
        return cls(None)

    @classmethod
    def local(cls, extra_span_processors: Sequence[Any] = (), service_name: str = "wbg-orchestrator") -> "OrchestratorTracer":
        """Local Logfire instance: never exports (explicit send_to_logfire=False, no token, no console). Spans still
        get trace ids, so log lines can be correlated."""
        import logfire
        instance = logfire.configure(local=True, send_to_logfire=False, token=None, console=False, service_name=service_name,
                                     additional_span_processors=list(extra_span_processors), inspect_arguments=False,
                                     metrics=False)
        return cls(instance)

    @classmethod
    def exporting(cls, token: str, service_name: str, extra_span_processors: Sequence[Any] = ()) -> "OrchestratorTracer":
        """Local Logfire instance that ALSO exports to the existing Logfire project.

        The caller passes the existing write token explicitly; nothing is read from the environment here.
        """
        import logfire
        instance = logfire.configure(local=True, send_to_logfire=True, token=token, console=False, service_name=service_name,
                                     additional_span_processors=list(extra_span_processors), inspect_arguments=False,
                                     metrics=False)
        return cls(instance)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[SpanSetter]:
        """A nested span. The yielded setter adds attributes later: `span(status="done")`, or `span(error=exc)` to
        record a failure that the code handles itself (the span is marked as an error, the block continues;
        `log_message=` names the log line, default "step failed").

        An exception that escapes the block is recorded on this span and re-raised unchanged (see record_failure)."""
        if self._logfire is None:
            yield _ignore
            return
        error: BaseException | None = None
        with self._logfire.span(name, _span_name=name, **safe_attributes(attributes, allow_text=True)) as span:
            def set_attributes(**values: Any) -> None:
                handled, message = values.pop("error", None), values.pop("log_message", "step failed")
                if isinstance(handled, BaseException):
                    record_failure(span, name, handled, message)
                for key, value in safe_attributes(values, allow_text=True).items():
                    span.set_attribute(key, value)
            try:
                yield set_attributes
            except Exception as exc:
                error = exc
                record_failure(span, name, exc)
        if error is not None:
            raise error

    def event(self, name: str, level: str = "info", **attributes: Any) -> None:
        if self._logfire is not None:
            self._logfire.log(level, name, attributes=safe_attributes(attributes, allow_text=True))

    def force_flush(self) -> None:
        if self._logfire is not None:
            self._logfire.force_flush()


def record_failure(span: Any, step: str, error: BaseException, log_message: str = "step failed") -> None:
    """Mark a span as failed. The innermost span that sees an exception records its type, masked message and location,
    and writes it once to the log with the traceback; outer spans record which step failed. So each failure gives
    exactly one ERROR line with a traceback, carrying the ids of the job it happened in."""
    span.set_level("error")
    first_step = getattr(error, "_wbg_failed_step", None)
    if first_step is None:
        details = describe_error(error)
        for key, value in safe_attributes(details, allow_text=True).items():
            span.set_attribute(key, value)
        span.set_attribute("failed_step", step)
        try:
            error._wbg_failed_step = step                                     # type: ignore[attr-defined]
        except AttributeError:                                                 # exceptions with __slots__
            pass
        log.error(log_message, extra={"step": step}, exc_info=error)
    else:
        span.set_attribute("error_type", type(error).__name__)
        span.set_attribute("failed_step", first_step)


def _ignore(**values: Any) -> None:
    """The setter when tracing is off: attributes are dropped, but a handled failure is still logged once."""
    error = values.get("error")
    if isinstance(error, BaseException) and getattr(error, "_wbg_failed_step", None) is None:
        try:
            error._wbg_failed_step = "untraced"                               # type: ignore[attr-defined]
        except AttributeError:
            pass
        log.error(values.get("log_message", "step failed"), exc_info=error)


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
