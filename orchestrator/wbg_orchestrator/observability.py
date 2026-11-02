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

Sessions: a trace is one job, never a whole session (traces that stay open for hours break most tools). Instead,
every span carries `session.id` (the sign-in session) and `user.hash` (the pseudonymous user), taken from the log
context the API opens, so Logfire can show all the traces of one session or one user with a filter.

Standard names (OpenTelemetry semantic conventions): code passes plain keyword names and `STANDARD_NAMES` records
them under their standard name where one exists, on spans and in the log alike: `user.hash`, `session.id`,
`error.type`, `http.response.status_code`, and for Claude calls `gen_ai.provider.name`, `gen_ai.operation.name`,
`gen_ai.request.model`, `gen_ai.usage.input_tokens` / `output_tokens` / `cache_read.input_tokens` /
`cache_creation.input_tokens` and `gen_ai.conversation.id`. Names with no standard (job_id, request_id, ...) stay.
The resource carries `service.name`, `service.version` and `deployment.environment.name` (logs.ServiceInfo).

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

from .logs import ServiceInfo, current_context, current_steps, describe_error, get_logger, redacted, step_scope

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
    # sessions and Claude calls (recorded under their standard names, see STANDARD_NAMES)
    "session_id", "user_hash", "http_request_id", "llm_provider", "llm_operation",
    "llm_input_tokens", "llm_output_tokens", "llm_cache_read_tokens", "llm_cache_write_tokens",
})
# Our keyword name -> the OpenTelemetry semantic-convention name it is recorded under (spans and log lines).
# Token counts take the gen_ai.usage names only on the Claude call itself (llm_*): the investigation's totals keep
# input_tokens / output_tokens, so tools that add up gen_ai.usage do not count the same tokens twice.
STANDARD_NAMES = {
    "session_id": "session.id", "user_hash": "user.hash", "owner_hash": "user.hash",
    "error_type": "error.type", "http_status": "http.response.status_code",
    "conversation_id": "gen_ai.conversation.id", "model": "gen_ai.request.model",
    "llm_provider": "gen_ai.provider.name", "llm_operation": "gen_ai.operation.name",
    "llm_input_tokens": "gen_ai.usage.input_tokens", "llm_output_tokens": "gen_ai.usage.output_tokens",
    "llm_cache_read_tokens": "gen_ai.usage.cache_read.input_tokens",
    "llm_cache_write_tokens": "gen_ai.usage.cache_creation.input_tokens",
}
IDENTITY_KEYS = ("session_id", "user_hash")          # from the log context, on every span
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


def standard_names(attributes: dict[str, Any]) -> dict[str, Any]:
    """The attributes under their OpenTelemetry names where one exists (Python keywords cannot contain dots)."""
    return {STANDARD_NAMES.get(key, key): value for key, value in attributes.items()}


def recordable(attributes: dict[str, Any]) -> dict[str, Any]:
    """What a span or a log line records: the safe attributes (masked free text included), under standard names."""
    return standard_names(safe_attributes(attributes, allow_text=True))


def identity_attributes() -> dict[str, Any]:
    """The session and pseudonymous user of the running work, from the log context (empty outside a session)."""
    ids = current_context()
    return standard_names(safe_attributes({key: ids.get(key) for key in IDENTITY_KEYS}))


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
    def local(cls, extra_span_processors: Sequence[Any] = (), service_name: str = "wbg-orchestrator",
              service: ServiceInfo | None = None) -> "OrchestratorTracer":
        """Local Logfire instance: never exports (explicit send_to_logfire=False, no token, no console). Spans still
        get trace ids, so log lines can be correlated."""
        import logfire
        instance = logfire.configure(local=True, send_to_logfire=False, token=None, console=False, service_name=service_name,
                                     additional_span_processors=list(extra_span_processors), inspect_arguments=False,
                                     metrics=False, scrubbing=_scrubbing(), **_resource(service))
        return cls(instance)

    @classmethod
    def exporting(cls, token: str, service_name: str, extra_span_processors: Sequence[Any] = (),
                  service: ServiceInfo | None = None) -> "OrchestratorTracer":
        """Local Logfire instance that ALSO exports to the existing Logfire project.

        The caller passes the existing write token explicitly; nothing is read from the environment here.
        """
        import logfire
        instance = logfire.configure(local=True, send_to_logfire=True, token=token, console=False, service_name=service_name,
                                     additional_span_processors=list(extra_span_processors), inspect_arguments=False,
                                     metrics=False, scrubbing=_scrubbing(), **_resource(service))
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
        started, recorded = time.perf_counter(), recordable(attributes)
        outcome = "failed"
        with step_scope(name):
            log.info("step started", extra={"attributes": recorded})
            try:
                with self._open(name, {**identity_attributes(), **recorded}) as setter:
                    def set_attributes(**values: Any) -> None:
                        nonlocal outcome
                        if isinstance(values.get("error"), BaseException):
                            outcome = "failed (handled)"
                        cleaned = recordable({k: v for k, v in values.items() if k not in ("error", "log_message")})
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
        clean = recordable(attributes)
        if self._logfire is not None:
            self._logfire.log(level, name, attributes={**identity_attributes(), **clean})
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
        for key, value in recordable(describe_error(error)).items():
            span.set_attribute(key, value)
        span.set_attribute("failed_step", step)
        try:
            error._wbg_failed_step = step                                     # type: ignore[attr-defined]
        except AttributeError:                                                 # exceptions with __slots__
            pass
        log.error(log_message, exc_info=error)
    else:
        span.set_attribute(STANDARD_NAMES["error_type"], type(error).__name__)
        span.set_attribute("failed_step", first_step)


def _resource(service: ServiceInfo | None) -> dict[str, str]:
    """service.version and deployment.environment.name for logfire.configure (nothing when not given)."""
    return {"service_version": service.version, "environment": service.environment} if service else {}


def _scrubbing() -> Any:
    """Logfire's scrubber, except for `session.id`: Logfire hides every attribute whose name contains "session", but
    the session id is not a secret (the token is, and it is never recorded), and without it no session can be found."""
    import logfire

    def keep_session_id(match: Any) -> Any:
        return match.value if match.path and match.path[-1] == STANDARD_NAMES["session_id"] else None
    return logfire.ScrubbingOptions(callback=keep_session_id)


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
