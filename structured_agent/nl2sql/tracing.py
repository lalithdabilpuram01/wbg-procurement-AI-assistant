"""Pydantic Logfire tracing for the NL2SQL workflow (safe metadata only).

Design decisions:
  * Logfire is configured as a LOCAL instance (`logfire.configure(local=True)`): it does not replace the global
    OpenTelemetry provider, so the service's existing OpenTelemetry spans are not duplicated into Logfire, and
    nothing else in the process starts exporting.
  * No automatic instrumentation (no instrument_httpx / instrument_psycopg / capture_all): HTTP requests, headers
    and bodies are never captured. Spans are created only by WorkflowTracer, with allow-listed attributes.
  * Exceptions are never recorded on spans (their messages may contain data); only the exception class and the
    workflow's own failure category are set.
  * Export is OFF unless LOGFIRE_SEND_TO_LOGFIRE=true. The token is passed to Logfire only when export is on.
  * The durable JSONL audit trail (observability.Telemetry) is separate and unchanged.
  * Logfire's default scrubbing stays on. Attribute names avoid its patterns (e.g. the Databricks credential mode is
    recorded as `databricks_access_mode`, because names containing "credential" are redacted).

Environment (process environment first, then .env, read without changing os.environ):
  LOGFIRE_SEND_TO_LOGFIRE  true | false (default false)
  LOGFIRE_TOKEN            write token, used only when export is on
  LOGFIRE_SERVICE_NAME     service name (default wbg-procurement-intelligence)
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Sequence

from app_config.check_env import read_env_file
from structured_agent.nl2sql.settings import ConfigurationError

DEFAULT_SERVICE_NAME = "wbg-procurement-intelligence"

# Only these attribute names ever reach a span or event; values must be simple scalars or lists of them.
SAFE_SPAN_ATTRIBUTES = {
    "request_id", "operation", "model", "transport", "execute_requested", "tool", "status", "duration_ms",
    "input_tokens", "output_tokens", "llm_call_index", "llm_calls", "validation_outcome", "error_codes",
    "provider_status_code", "provider_error_type", "error_category", "failure_class", "workflow_status",
    "retry_after_seconds", "limit", "breaker", "from_state", "to_state", "row_count", "truncated", "tables",
    "databricks_access_mode", "production_read_only", "retrieval_outcome", "attempts",
    "guard_stage", "policy_id", "decision", "reason_category", "enforced_by",
    "thread_id", "turn_seq", "resolution", "context_tokens", "context_budget", "items_included", "items_dropped",
    "summary_version", "summary_status", "degraded",
    # intent routing (route names, reasons and permissions only)
    "route", "route_reason", "confidence_level", "confidence_basis", "retrieval_allowed", "external_tools_allowed",
    "reset_topic", "inherited_route", "route_decided_by",
}

SpanSetter = Callable[..., None]


def safe_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Keep allow-listed keys with scalar (or list-of-scalar) values; drop everything else."""
    clean: dict[str, Any] = {}
    for key, value in attributes.items():
        if key not in SAFE_SPAN_ATTRIBUTES or value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value
        elif isinstance(value, (list, tuple)) and all(isinstance(v, (str, int, float, bool)) for v in value):
            clean[key] = list(value)
    return clean


# ----------------------------------------------------------------------------------------------------- settings
@dataclass(frozen=True)
class LogfireSettings:
    send_to_logfire: bool = False
    service_name: str = DEFAULT_SERVICE_NAME
    token: str | None = field(default=None, repr=False)   # never printed


def load_logfire_settings() -> LogfireSettings:
    """Read the Logfire settings; raise ConfigurationError naming (never showing) an invalid variable."""
    file_values = read_env_file()

    def value(name: str) -> str:
        return os.environ.get(name, file_values.get(name, "")).strip()

    send = value("LOGFIRE_SEND_TO_LOGFIRE").lower() or "false"
    if send not in ("true", "false"):
        raise ConfigurationError("LOGFIRE_SEND_TO_LOGFIRE must be true or false")
    if send == "true" and not value("LOGFIRE_TOKEN"):
        raise ConfigurationError("LOGFIRE_SEND_TO_LOGFIRE=true needs LOGFIRE_TOKEN")
    return LogfireSettings(send_to_logfire=send == "true", service_name=value("LOGFIRE_SERVICE_NAME") or DEFAULT_SERVICE_NAME,
                           token=value("LOGFIRE_TOKEN") or None)


# ------------------------------------------------------------------------------------------------------- tracer
class WorkflowTracer:
    """Safe span and event API used by the workflow. Without a Logfire instance every call is a no-op."""

    def __init__(self, logfire_instance: Any | None = None, export_enabled: bool = False):
        self._logfire = logfire_instance
        self.export_enabled = export_enabled

    @classmethod
    def disabled(cls) -> "WorkflowTracer":
        return cls(None)

    @property
    def enabled(self) -> bool:
        return self._logfire is not None

    @contextmanager
    def stage(self, name: str, **attributes: Any) -> Iterator[SpanSetter]:
        """A span for one workflow stage. Yields a setter for attributes known only at the end.

        An exception leaving the block is re-raised unchanged, but only its class name is recorded on the span.
        """
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
            except Exception as exc:   # keep the message out of Logfire, then re-raise outside the span
                error = exc
                span.set_attribute("error_category", type(exc).__name__)
                span.set_level("error")
        if error is not None:
            raise error

    def event(self, name: str, level: str = "info", **attributes: Any) -> None:
        """A point-in-time record (rate-limit rejection, breaker transition, final status)."""
        if self._logfire is not None:
            self._logfire.log(level, name, attributes=safe_attributes(attributes))

    def force_flush(self) -> None:
        if self._logfire is not None:
            self._logfire.force_flush()


def _ignore(**values: Any) -> None:
    return None


def configure_logfire(settings: LogfireSettings, extra_span_processors: Sequence[Any] = ()) -> WorkflowTracer:
    """A local Logfire instance; exports to Logfire only when settings.send_to_logfire is true.

    `extra_span_processors` lets tests attach an in-memory exporter. Raises ConfigurationError if the logfire
    package is not installed (it is installed in .venv-structured-mcp).
    """
    try:
        import logfire
    except ImportError:
        raise ConfigurationError("The logfire package is not installed in this environment (use .venv-structured-mcp)") from None
    instance = logfire.configure(
        local=True,                                   # do not replace the global OpenTelemetry provider
        send_to_logfire=settings.send_to_logfire,     # explicit: never the SDK's 'if-token-present' default
        token=settings.token if settings.send_to_logfire else None,
        service_name=settings.service_name,
        console=False,
        inspect_arguments=False,
        metrics=False,
        additional_span_processors=list(extra_span_processors),
    )
    return WorkflowTracer(instance, export_enabled=settings.send_to_logfire)


class TracedToolCaller:
    """Wraps a ToolCaller so each registry tool call gets its own span (tool name, status, latency only).

    Tool arguments and responses are never put on the span.
    """

    def __init__(self, tools: Any, tracer: WorkflowTracer):
        self._tools = tools
        self._tracer = tracer
        self.principal_id = tools.principal_id

    def call(self, tool: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        with self._tracer.stage("nl2sql.registry_tool", request_id=request_id, tool=tool) as span:
            response = self._tools.call(tool, arguments, request_id)
            span(status=response.get("status"), error_codes=[e["code"] for e in response.get("errors", [])])
        return response
