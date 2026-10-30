"""Logfire tracing of the NL2SQL workflow, offline: in-memory exporter, no export, safe metadata only.

Runs where the logfire package is installed (.venv-structured-mcp); skipped elsewhere.
"""
from __future__ import annotations

import json

import httpx
import pytest

logfire = pytest.importorskip("logfire")

from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from structured_agent.execution.executor import RawResult  # noqa: E402
from structured_agent.nl2sql import tracing as tracing_module  # noqa: E402
from structured_agent.nl2sql.result import FailureClass, WorkflowStatus  # noqa: E402
from structured_agent.nl2sql.settings import BreakerSettings, ConfigurationError, RuntimeSettings  # noqa: E402
from structured_agent.nl2sql.sql_generator import SYSTEM_PROMPT  # noqa: E402
from structured_agent.nl2sql.tools import InProcessToolCaller  # noqa: E402
from structured_agent.nl2sql.tracing import LogfireSettings, configure_logfire, load_logfire_settings  # noqa: E402
from structured_agent.nl2sql.workflow import NlToSqlWorkflow  # noqa: E402
from structured_agent.observability import Telemetry  # noqa: E402
from tests.structured_agent.nl2sql_fakes import (TEST_API_KEY, TEST_MODEL, EchoColumnsExecutor, FakePortkey,  # noqa: E402
                                                 offline_claude_client, sql_draft)
from tests.structured_agent.test_nl2sql_workflow import BID_QUESTION, BID_SQL, ListSink, make_service  # noqa: E402
from tests.structured_agent.test_registry_and_mcp import registry_available  # noqa: E402

FAKE_LOGFIRE_TOKEN = "lf-test-WRITE-TOKEN-never-sent-9999"
ROW_SECRET = "row-value-SECRET-77"


class DistinctiveRowsExecutor(EchoColumnsExecutor):
    """Fake Databricks returning a recognisable string in every cell, so tests can prove rows never reach spans."""

    def execute(self, sql, parameters, timeout_seconds):
        raw = super().execute(sql, parameters, timeout_seconds)
        return RawResult(raw.columns, [{name: ROW_SECRET for name in row} for row in raw.rows])


# ------------------------------------------------------------------------------------------------------- fixtures
@pytest.fixture
def network_attempts(monkeypatch) -> list[str]:
    """Records (and refuses) any outgoing HTTP request, so 'no export' can be asserted."""
    import requests

    attempts: list[str] = []

    def record(name):
        def refuse(*args, **kwargs):
            attempts.append(name)
            raise ConnectionError("network disabled in tests")
        return refuse
    monkeypatch.setattr(requests.Session, "send", record("requests"))
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", record("httpx"))
    return attempts


@pytest.fixture
def traced():
    """A local Logfire instance with export OFF (despite a token) and an in-memory exporter."""
    exporter = InMemorySpanExporter()
    tracer = configure_logfire(LogfireSettings(send_to_logfire=False, token=FAKE_LOGFIRE_TOKEN), [SimpleSpanProcessor(exporter)])
    return tracer, exporter


def spans(exporter: InMemorySpanExporter) -> list:
    return list(exporter.get_finished_spans())


def named(exporter, name: str) -> list:
    return [s for s in spans(exporter) if s.name == name]


def exported_text(exporter) -> str:
    """Everything exported: names, attributes and events of all spans, as one string."""
    return json.dumps([{"name": s.name, "attributes": dict(s.attributes),
                        "events": [{"name": e.name, "attributes": dict(e.attributes or {})} for e in s.events]}
                       for s in spans(exporter)], default=str)


# ------------------------------------------------------------------------------------------------------- settings
def test_export_is_off_by_default_even_with_a_token(monkeypatch):
    monkeypatch.setattr(tracing_module, "read_env_file", lambda: {"LOGFIRE_TOKEN": FAKE_LOGFIRE_TOKEN})
    for name in ("LOGFIRE_TOKEN", "LOGFIRE_SEND_TO_LOGFIRE", "LOGFIRE_SERVICE_NAME"):
        monkeypatch.delenv(name, raising=False)
    settings = load_logfire_settings()
    assert settings.send_to_logfire is False and settings.service_name == "wbg-procurement-intelligence"
    assert FAKE_LOGFIRE_TOKEN not in repr(settings)


@pytest.mark.parametrize("file_values, message", [
    ({"LOGFIRE_SEND_TO_LOGFIRE": "yes"}, "LOGFIRE_SEND_TO_LOGFIRE must be true or false"),
    ({"LOGFIRE_SEND_TO_LOGFIRE": "true"}, "needs LOGFIRE_TOKEN"),
])
def test_invalid_logfire_settings_are_named_not_shown(monkeypatch, file_values, message):
    monkeypatch.setattr(tracing_module, "read_env_file", lambda: file_values)
    for name in ("LOGFIRE_TOKEN", "LOGFIRE_SEND_TO_LOGFIRE"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigurationError, match=message):
        load_logfire_settings()


def test_no_export_when_send_to_logfire_is_false(network_attempts, traced):
    tracer, exporter = traced
    with tracer.stage("nl2sql.request", request_id="req_offline"):
        pass
    tracer.force_flush()
    assert tracer.export_enabled is False and network_attempts == []
    assert len(named(exporter, "nl2sql.request")) == 1                 # recorded locally only


def test_logfire_does_not_replace_the_global_opentelemetry_provider(traced):
    from opentelemetry import trace

    assert type(trace.get_tracer_provider()).__name__ == "ProxyTracerProvider"


# -------------------------------------------------------------------------------------------- workflow spans
registry = pytest.mark.skipif(not registry_available(), reason="registry database not reachable")


@pytest.fixture(scope="module")
def executor():
    return DistinctiveRowsExecutor(write_capable=False)


@pytest.fixture(scope="module")
def service(tmp_path_factory, executor):
    return make_service(tmp_path_factory, executor)


def run_workflow(service, tracer, portkey, execute=True, **runtime):
    sink = ListSink()
    flow = NlToSqlWorkflow(InProcessToolCaller(service, "analyst-demo", "wbg-procurement-poc"), offline_claude_client(portkey),
                           Telemetry(sink), RuntimeSettings(**runtime), tracer=tracer)
    return flow, sink, flow.run(BID_QUESTION, execute=execute, request_id="req_trace_test")


@registry
def test_every_stage_is_traced_once_with_safe_metadata(service, traced, network_attempts):
    tracer, exporter = traced
    _, sink, result = run_workflow(service, tracer, FakePortkey(sql_draft(BID_SQL, {"fiscal_year": 2024})))
    tracer.force_flush()
    assert result.status == WorkflowStatus.ANSWERED and network_attempts == []

    counts = {name: len(named(exporter, name)) for name in (
        "nl2sql.request", "nl2sql.metadata_retrieval", "nl2sql.sql_generation", "nl2sql.claude_request",
        "nl2sql.sql_validation", "nl2sql.databricks_execution", "nl2sql.final_status")}
    assert counts == dict.fromkeys(counts, 1)                            # one span per stage: no duplicates
    assert len(named(exporter, "nl2sql.registry_tool")) >= 5
    assert not named(exporter, "search_table_registry")                  # service OTel spans are not mirrored

    request = named(exporter, "nl2sql.request")[0].attributes
    assert request["request_id"] == "req_trace_test" and request["model"] == TEST_MODEL
    assert request["workflow_status"] == "answered" and request["llm_calls"] == 1
    claude = named(exporter, "nl2sql.claude_request")[0].attributes
    assert claude["status"] == "ok" and claude["input_tokens"] == 1200 and claude["output_tokens"] == 80
    assert named(exporter, "nl2sql.sql_validation")[0].attributes["validation_outcome"] == "approved"
    execution = named(exporter, "nl2sql.databricks_execution")[0].attributes
    assert execution["status"] == "ok" and execution["row_count"] == 2 and execution["databricks_access_mode"] == "read_only_principal"
    assert {e.event_type for e in sink.events} >= {"nl2sql.llm_call", "nl2sql.result"}   # durable audit unchanged


@registry
def test_no_sensitive_values_reach_logfire(service, traced):
    tracer, exporter = traced
    run_workflow(service, tracer, FakePortkey(sql_draft(BID_SQL, {"fiscal_year": 2024})))
    tracer.force_flush()
    text = exported_text(exporter)
    for secret in (TEST_API_KEY, FAKE_LOGFIRE_TOKEN, BID_QUESTION, "SELECT", "tender_invitation'", ROW_SECRET,
                   SYSTEM_PROMPT[:40], "x-portkey", "Counts rows."):
        assert secret not in text, f"exported telemetry contains {secret!r}"


@registry
def test_validation_only_runs_have_no_databricks_span(service, traced):
    tracer, exporter = traced
    run_workflow(service, tracer, FakePortkey(sql_draft(BID_SQL, {"fiscal_year": 2024})), execute=False)
    assert not named(exporter, "nl2sql.databricks_execution")


@registry
def test_provider_error_is_traced_by_status_and_category_only(service, traced, executor):
    tracer, exporter = traced
    echo = f"rejected: {BID_QUESTION}"
    reply = httpx.Response(400, json={"error": {"type": "invalid_request_error", "message": echo}})
    _, _, result = run_workflow(service, tracer, FakePortkey(reply))
    claude = named(exporter, "nl2sql.claude_request")[0]
    assert claude.attributes["provider_status_code"] == 400 and claude.attributes["provider_error_type"] == "invalid_request_error"
    assert claude.attributes["failure_class"] == "provider_bad_request" and claude.attributes["error_category"] == "LlmError"
    assert not [e for s in spans(exporter) for e in s.events if e.name == "exception"]   # no exception messages
    assert echo not in exported_text(exporter) and result.failure_class == FailureClass.PROVIDER_BAD_REQUEST


@registry
def test_rate_limit_rejection_is_traced(service, traced):
    tracer, exporter = traced
    flow, _, _ = run_workflow(service, tracer, FakePortkey(sql_draft(BID_SQL, {"fiscal_year": 2024})), execute=False,
                              requests_per_minute=1)
    flow.run(BID_QUESTION, request_id="req_second")
    event = named(exporter, "nl2sql.rate_limited")[0].attributes
    assert event["limit"] == "requests" and event["request_id"] == "req_second" and event["retry_after_seconds"] > 0


@registry
def test_circuit_breaker_transitions_are_traced(service, traced):
    tracer, exporter = traced
    portkey = FakePortkey(httpx.Response(503), httpx.Response(503))
    flow, _, _ = run_workflow(service, tracer, portkey, llm_breaker=BreakerSettings(2, 60, 30, 300))
    flow.run(BID_QUESTION, request_id="req_second")
    transitions = [s.attributes for s in named(exporter, "nl2sql.circuit_breaker_transition")]
    assert [(t["breaker"], t["from_state"], t["to_state"]) for t in transitions] == [("llm.claude_only", "closed", "open")]
