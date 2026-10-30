"""Debuggability: one trace per question with nested steps, and JSON log lines that point at the failing step."""
from __future__ import annotations

import io
import json
import logging

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from wbg_orchestrator.api import UserDirectory, create_app
from wbg_orchestrator.logs import LOGGER_NAME, JsonLineFormatter, log_context
from wbg_orchestrator.observability import OrchestratorTracer

from tests.orchestrator.test_api import DATA_QUESTION, Stack


@pytest.fixture
def traced_stack(tmp_path):
    exporter = InMemorySpanExporter()
    stack = Stack(tmp_path, tracer=OrchestratorTracer.local([SimpleSpanProcessor(exporter)]))
    return stack, exporter


@pytest.fixture
def json_log():
    """The app's JSON log lines, captured in memory for one test."""
    buffer, logger = io.StringIO(), logging.getLogger(LOGGER_NAME)
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonLineFormatter())
    logger.addHandler(handler)
    level, logger.level = logger.level, logging.INFO
    yield lambda: [json.loads(line) for line in buffer.getvalue().splitlines()]
    logger.removeHandler(handler)
    logger.setLevel(level)


def ancestors(span, by_id) -> list[str]:
    names = []
    while span.parent is not None and span.parent.span_id in by_id:
        span = by_id[span.parent.span_id]
        names.append(span.name)
    return names


def test_one_question_is_one_trace_with_every_step_nested_under_its_job(traced_stack):
    stack, exporter = traced_stack
    cid = stack.conversation()
    job = stack.wait(stack.send(cid, DATA_QUESTION, "msg-trace-1", execute=True).json()["job_id"])
    spans = exporter.get_finished_spans()
    (root,) = [s for s in spans if s.name == "job.message"]
    trace = [s for s in spans if s.context.trace_id == root.context.trace_id]
    by_id = {s.context.span_id: s for s in trace}
    names = {s.name for s in trace}
    assert {"conversation.message", "orchestrator.node.intake", "orchestrator.node.execute",
            "orchestrator.task_attempt", "orchestrator.node.respond"} <= names
    attempt = next(s for s in trace if s.name == "orchestrator.task_attempt")
    assert ancestors(attempt, by_id)[:2] == ["orchestrator.task", "orchestrator.node.execute"]
    assert ancestors(attempt, by_id)[-1] == "job.message"
    worker = next(s for s in trace if s.name.startswith("worker."))                  # ran in the task pool's thread
    assert ancestors(worker, by_id)[:3] == ["orchestrator.task_attempt", "orchestrator.task", "orchestrator.node.execute"]
    assert job["trace_id"] == format(root.context.trace_id, "032x")
    assert root.attributes["request_id"] == job["request_id"] and root.attributes["job_id"] == job["job_id"]


def test_a_failed_job_logs_its_traceback_with_the_ids_to_find_it(traced_stack, json_log, monkeypatch):
    stack, exporter = traced_stack

    def broken(*args, **kwargs):
        raise RuntimeError("routing broke for jane.doe@example.org")
    monkeypatch.setattr(stack.service._front_door, "handle_message", broken)
    cid = stack.conversation()
    job = stack.wait(stack.send(cid, DATA_QUESTION, "msg-trace-2").json()["job_id"])
    assert job["state"] == "failed" and job["error"] == "RuntimeError" and job["trace_id"]

    failed = [line for line in json_log() if line["message"] == "job failed"]
    assert len(failed) == 1
    line = failed[0]
    assert line["job_id"] == job["job_id"] and line["conversation_id"] == cid and line["trace_id"] == job["trace_id"]
    assert line["error_type"] == "RuntimeError" and line["error_location"].startswith("test_tracing_and_logs.py:")
    assert "broken" in line["traceback"] and "jane.doe" not in json.dumps(line)            # personal data masked
    root = next(s for s in exporter.get_finished_spans() if s.name == "job.message")
    assert root.attributes["failed_step"] == "job.message" and root.attributes["error_type"] == "RuntimeError"


def test_log_lines_carry_the_ids_of_the_surrounding_context(json_log):
    with log_context(job_id="job_1", request_id="req_1"):
        logging.getLogger(f"{LOGGER_NAME}.test").info("inside")
    logging.getLogger(f"{LOGGER_NAME}.test").info("outside")
    inside, outside = json_log()
    assert inside["job_id"] == "job_1" and inside["request_id"] == "req_1" and "job_id" not in outside


def test_an_unexpected_server_error_returns_a_reference_that_is_in_the_log(tmp_path, json_log, monkeypatch):
    stack = Stack(tmp_path)
    monkeypatch.setattr(stack.service, "list_conversations", lambda *a, **k: 1 / 0)
    client = TestClient(create_app(stack.service, UserDirectory.load(tmp_path / "users.json")), raise_server_exceptions=False)
    response = client.get("/conversations", headers={"Authorization": f"Bearer {stack.tokens['alice']}"})
    assert response.status_code == 500 and response.json()["detail"] == "Something went wrong on the server."
    reference = response.json()["reference"]
    assert response.headers["X-Request-ID"] == reference
    (line,) = [line for line in json_log() if line["message"] == "request failed"]
    assert line["reference"] == reference and line["error_type"] == "ZeroDivisionError" and "Traceback" in line["traceback"]


def test_every_response_echoes_a_request_id(tmp_path):
    stack = Stack(tmp_path)
    response = stack.client.get("/health", headers={"X-Request-ID": "support-ticket-42"})
    assert response.headers["X-Request-ID"] == "support-ticket-42"
    assert stack.client.get("/health").headers["X-Request-ID"].startswith("http_")


def test_every_graph_step_logs_what_it_wrote_and_the_state_after_it(traced_stack, json_log):
    stack, exporter = traced_stack
    cid = stack.conversation()
    job = stack.wait(stack.send(cid, DATA_QUESTION, "msg-trace-3", execute=True).json()["job_id"])
    lines = [line for line in json_log() if line.get("job_id") == job["job_id"]]

    updates = [line for line in lines if line["message"] == "state updated"]
    steps = [line["step"] for line in updates]
    assert steps[:2] == ["orchestrator.node.intake", "orchestrator.node.supervise"] and steps[-1] == "orchestrator.node.finalize"
    assert {"orchestrator.node.dispatch", "orchestrator.node.execute", "orchestrator.node.respond"} <= set(steps)
    supervise = updates[1]
    assert supervise["changes"]["phase"] == "planned" and supervise["state"]["plan"]["tasks"]
    assert supervise["state"]["request"]["question"]                                     # the masked question
    assert updates[-1]["state"]["response"]["status"] == job["status"]

    (task,) = [line for line in lines if line["message"] == "task state updated"]
    assert task["step_path"].endswith("orchestrator.node.execute > orchestrator.task")
    assert list(task["changes"]["task_states"].values()) == ["running -> succeeded"] and task["changes"]["evidence"]

    hops = [(line["from_step"], line["to_step"]) for line in lines if line["message"] == "next step"]
    assert hops[0] == ("intake", "supervise") and ("dispatch", "verify") in hops
    starts = [line for line in lines if line["message"] == "step started"]
    assert starts[0]["step"] == "job.message" and starts[0]["depth"] == 0
    worker = next(line for line in starts if line["step"].startswith("worker."))
    assert worker["step_path"].startswith("job.message > conversation.message > orchestrator.node.execute")
    spans = {s.name: s.attributes for s in exporter.get_finished_spans() if s.context.trace_id == int(job["trace_id"], 16)}
    assert json.loads(spans["orchestrator.node.respond"]["state"])["response"]["status"] == job["status"]


def test_a_failing_worker_is_named_by_its_full_step_path(traced_stack, json_log, monkeypatch):
    stack, _ = traced_stack
    workers = stack.engine.deps.controller._workers

    def broken(task, context):
        raise KeyError("column missing")
    for worker in workers.values():
        monkeypatch.setattr(worker, "run", broken)
    cid = stack.conversation()
    job = stack.wait(stack.send(cid, DATA_QUESTION, "msg-trace-4", execute=True).json()["job_id"])
    (error,) = [line for line in json_log() if line["level"] == "ERROR" and line.get("job_id") == job["job_id"]]
    assert error["step"].startswith("worker.") and error["error_type"] == "KeyError"
    assert error["step_path"].startswith("job.message > conversation.message > orchestrator.node.execute > orchestrator.task")
    finished = [line for line in json_log() if line["message"] == "step finished" and line["step"] == error["step"]]
    assert finished[0]["outcome"] == "failed" and finished[0]["level"] == "WARNING"
