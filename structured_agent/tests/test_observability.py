"""Spans and audit events carry only allow-listed, sanitised attributes (OpenTelemetry SDK in-memory exporter)."""
from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from structured_agent.observability import AuditEvent, Telemetry

UNSAFE = {"sql": "SELECT supplier_name FROM t WHERE email = 'a@b.org'", "rows": [{"x": 1}], "principal_id": "analyst-demo",
          "parameters": {"p": "P148850"}, "password": "secret"}


class ListSink:
    def __init__(self):
        self.events: list[AuditEvent] = []

    def write(self, event: AuditEvent) -> None:
        self.events.append(event)


def test_spans_and_audit_events_drop_unsafe_attributes():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    sink = ListSink()
    telemetry = Telemetry(sink, tracer_provider=provider)

    with telemetry.span("validate_sql", "req_1", "inv_1") as span:
        telemetry.annotate(span, status="rejected", error_codes=["FORBIDDEN_OPERATION"], **UNSAFE)
    telemetry.audit("req_1", "inv_1", "validate_sql", status="rejected", **UNSAFE)

    exported = dict(exporter.get_finished_spans()[0].attributes)
    assert exported == {"wbg.request_id": "req_1", "wbg.invocation_id": "inv_1", "wbg.status": "rejected",
                        "wbg.error_codes": '["FORBIDDEN_OPERATION"]'}
    assert sink.events[0].attributes == {"status": "rejected"}
