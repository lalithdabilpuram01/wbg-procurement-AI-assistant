"""Tracing (Logfire / OpenTelemetry) and audit events for the orchestrator.

What a trace looks like: one trace per user action (a chat message, a tender check, ...), with nested spans

    job.message                         the background job (job_id, kind, conversation_id, request_id)
      conversation.message              the masked question, the route it took and why
        orchestrator.node.intake        input guardrail (the question, personal data masked)
        orchestrator.node.supervise     fast path, fixed plan, Claude plan, clarify or refuse
        orchestrator.node.plan          the planner (Claude), only when a plan is needed
        orchestrator.node.validate_plan the controller's checks
        orchestrator.node.dispatch      which tasks are ready (repeats with execute until none are left)
        orchestrator.node.execute       one batch of ready tasks
          orchestrator.task             one task (worker, operation, final state)
            orchestrator.task_attempt   one span per attempt (retries are visible)
              worker.<name>             the worker itself, in its own thread (structured, document, prediction, ...)
                mcp.tool_call           a structured-data tool call (SQL over the Gold tables)
                orchestrator.llm_call   a Claude call (tokens, cost, provider errors)
        orchestrator.node.verify        evidence checks
        orchestrator.node.respond       the answer and how many claims were kept
        orchestrator.node.finalize      totals (tokens, cost, task count)

Graph steps also carry `state_changes` (what the step wrote) and `state` (the state after it), as JSON summaries
built by state_view.py: ids, statuses, counts and masked text, never rows or document excerpts.

When a step fails, its span is marked as an error and records `error_type`, `error_message` (personal data masked)
and `error_location` (file:line:function where it was raised); every enclosing span records `failed_step`, the name
of the innermost span that failed. The same failure is written once to the JSON log (logs.py) with its traceback
and the trace id, so the log line and the Logfire trace point at each other.

What may be recorded:
* attribute names come from an allow-list; string values must look like identifiers or codes (no spaces, bounded
  length), so rows, prompts, excerpts and tokens cannot be recorded by mistake;
* the only free-text attributes are TEXT_ATTRIBUTES (the question, generated SQL, an error message, the state
  summaries). They pass through the personal-data masker (e-mail addresses, phone numbers, card and IBAN numbers, IP addresses, secret-shaped
  values) and are truncated. The audit trail never records them;
* users appear only as the pseudonymous owner hash.

Export: Logfire runs as a LOCAL instance (console off, no auto-instrumentation, does not replace the global
provider). Spans leave the machine only through `exporting()`, which the runtime uses when LOGFIRE_SEND_TO_LOGFIRE is
true and a token is set. Tests attach an in-memory exporter.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol, Sequence

from .logs import current_steps, describe_error, get_logger, redacted, step_scope

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
TEXT_ATTRIBUTES = {"question": 1000, "sql": 4000, "error_message": 500,
                   "state_changes": 6000, "state": 12000}         # the graph state after each step (state_view.py)
# Built by state_view.py, which masks every free-text field it includes: only truncated here, not masked twice.
PREMASKED_ATTRIBUTES = frozenset({"state_changes", "state"})
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
        if allow_text and key in PREMASKED_ATTRIBUTES and isinstance(value, str):
            clean[key] = value[:TEXT_ATTRIBUTES[key]]
        elif allow_text and key in TEXT_ATTRIBUTES and isinstance(value, str):
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

        An exception that escapes the block is recorded on this span and re-raised unchanged (see record_failure).

        Every span is also a step in the log, with or without Logfire: a "step started" line, a "step finished" line
        (outcome done or failed, duration, the attributes set on the span), and every line written in between carries
        the step path (logs.step_scope)."""
        if self._logfire is None and not log.isEnabledFor(logging.INFO):     # nothing records it: keep the step path only
            with step_scope(name), self._open(name, {}) as setter:
                yield setter
            return
        started, recorded = time.perf_counter(), safe_attributes(attributes, allow_text=True)
        outcome = "failed"
        with step_scope(name):
            log.info("step started", extra={"attributes": recorded})
            try:
                with self._open(name, recorded) as setter:
                    def set_attributes(**values: Any) -> None:
                        nonlocal outcome
                        if isinstance(values.get("error"), BaseException):
                            outcome = "failed (handled)"
                        cleaned = safe_attributes({k: v for k, v in values.items() if k not in ("error", "log_message")},
                                                  allow_text=True)
                        recorded.update(cleaned)
                        setter(**cleaned, **{k: v for k, v in values.items() if k in ("error", "log_message")})
                    yield set_attributes
                if outcome == "failed":
                    outcome = "done"
            finally:
                log.log(logging.WARNING if outcome == "failed" else logging.INFO, "step finished",
                        extra={"outcome": outcome, "duration_ms": round(1000 * (time.perf_counter() - started)),
                               "attributes": recorded})

    @contextmanager
    def _open(self, name: str, attributes: dict[str, Any]) -> Iterator[SpanSetter]:
        """The Logfire span itself (or a no-op setter when tracing is off)."""
        if self._logfire is None:
            try:
                yield _ignore
            except Exception as exc:
                _ignore(error=exc)
                raise
            return
        error: BaseException | None = None
        with self._logfire.span(name, _span_name=name, **attributes) as span:
            def set_attributes(**values: Any) -> None:
                handled, message = values.pop("error", None), values.pop("log_message", "step failed")
                if isinstance(handled, BaseException):
                    record_failure(span, name, handled, message)
                for key, value in values.items():                         # already filtered and masked by span()
                    span.set_attribute(key, value)
            try:
                yield set_attributes
            except Exception as exc:
                error = exc
                record_failure(span, name, exc)
        if error is not None:
            raise error

    def event(self, name: str, level: str = "info", **attributes: Any) -> None:
        """A point-in-time event (a dispatch, a retry, a breaker transition), in the trace and in the log."""
        logged = log.isEnabledFor(logging.INFO)
        if self._logfire is None and not logged:
            return
        clean = safe_attributes(attributes, allow_text=True)
        if self._logfire is not None:
            self._logfire.log(level, name, attributes=clean)
        if logged:
            log.log(logging.WARNING if level in ("warn", "warning") else logging.INFO, name, extra={"attributes": clean})

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
        log.error(log_message, exc_info=error)
    else:
        span.set_attribute("error_type", type(error).__name__)
        span.set_attribute("failed_step", first_step)


def _ignore(**values: Any) -> None:
    """The setter when tracing is off: attributes are dropped, but a handled failure is still logged once."""
    error = values.get("error")
    if isinstance(error, BaseException) and getattr(error, "_wbg_failed_step", None) is None:
        try:
            error._wbg_failed_step = current_steps().get("step", "untraced")  # type: ignore[attr-defined]
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
