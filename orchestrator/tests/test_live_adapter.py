"""Milestone B, offline: the budgeted live-Claude adapter over an in-memory Portkey transport (no network).

Covers the schema reduction for Claude structured outputs, the pre-call spend reservation (attempt and cost ceilings,
persisted across processes), per-schema token caps, settlement of actual usage, the gateway-retry check, provider
error mapping (timeout, 429, 529, 400, truncation) charged at worst case, and provider-call spans correlated with
the investigation.
"""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from structured_agent.nl2sql.llm_client import ClaudeRequest, LlmError, LlmFailure
from structured_agent.tests.nl2sql_fakes import FakePortkey, claude_settings, completion

from wbg_orchestrator.claude import UNSUPPORTED_KEYWORDS, PortkeyJsonClient, strict_output_schema
from wbg_orchestrator.contracts import InvestigationStatus
from wbg_orchestrator.live_llm import (FRAMING_TOKENS, GEO_MULTIPLIER, SONNET_5_5_PRICES, BudgetedClaudeClient, LedgerLimits,
                                       SpendLedger, SpendLimitReached, input_token_bound)
from wbg_orchestrator.observability import OrchestratorTracer
from wbg_orchestrator.planner import ClaudePlanner, DraftPlan
from wbg_orchestrator.response import DraftResponse

from tests.helpers import build_engine, plan, request, task

CAPS = {"investigation_plan": 3000, "investigation_answer": 2000}


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def with_retry_header(response: httpx.Response, value: str | None = "0") -> httpx.Response:
    headers = dict(response.headers)
    if value is not None:
        headers["x-portkey-retry-attempt-count"] = value
    return httpx.Response(response.status_code, headers=headers, content=response.content)


def client(tmp_path, *replies, limits=LedgerLimits(), tracer=None, max_output_tokens=16000):
    portkey = FakePortkey(*replies)
    ledger = SpendLedger(tmp_path / "ledger.json", limits)
    budgeted = BudgetedClaudeClient(claude_settings(max_output_tokens=max_output_tokens), ledger, output_caps=CAPS,
                                    tracer=tracer, transport=portkey.transport())
    return budgeted, ledger, portkey


def ask(budgeted, schema_name="investigation_plan", user="Which metric counts bid invitations?"):
    return budgeted.complete(ClaudeRequest(system="Return JSON.", user=user, trace_id="req_test",
                                           json_schema=strict_output_schema(DraftPlan.model_json_schema()), schema_name=schema_name))


# ------------------------------------------------------------------------------------------------ schemas
@pytest.mark.parametrize("model", [DraftPlan, DraftResponse])
def test_output_schemas_are_reduced_to_the_structured_output_subset(model):
    schema = strict_output_schema(model.model_json_schema())
    for node in walk(schema):                                       # no property of these models uses a keyword name
        assert not set(node) & UNSUPPORTED_KEYWORDS
        if "properties" in node:
            assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])
    if model is DraftPlan:
        assert "required" in schema["properties"]                   # a property NAMED "required" is kept
        DraftPlan.model_validate(plan(task("a")))                   # the full Pydantic model still validates replies


# ------------------------------------------------------------------------------------------------- ledger
def test_worst_case_is_reserved_before_the_request_and_settled_after(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ledger"] = json.loads((tmp_path / "ledger.json").read_text())
        seen["body"] = json.loads(request.content)
        return with_retry_header(completion(plan(task("a"))))

    portkey = FakePortkey()
    portkey.transport = lambda: httpx.MockTransport(handler)
    ledger = SpendLedger(tmp_path / "ledger.json")
    budgeted = BudgetedClaudeClient(claude_settings(), ledger, output_caps=CAPS, transport=portkey.transport())
    response = ask(budgeted)
    reserved = seen["ledger"]["attempts"][0]
    assert reserved["status"] == "reserved" and seen["body"]["max_tokens"] == 3000
    expected_bound = len(("Return JSON." + "Which metric counts bid invitations?" +
                          json.dumps(strict_output_schema(DraftPlan.model_json_schema()))).encode()) + FRAMING_TOKENS
    assert reserved["input_bound"] == expected_bound
    worst = (SONNET_5_5_PRICES.cost(expected_bound, 3000) * GEO_MULTIPLIER).quantize(Decimal("0.000001"))
    assert Decimal(reserved["worst_case_usd"]) == worst
    settled = ledger.attempts[0]
    assert settled["status"] == "ok" and settled["gateway_retry_count"] == "0"
    assert Decimal(settled["estimated_usd"]) == SONNET_5_5_PRICES.cost(response.input_tokens, response.output_tokens)
    assert ledger.committed_usd() == Decimal(settled["conservative_usd"]) < worst


def test_the_attempt_limit_is_enforced_before_any_request_and_survives_a_restart(tmp_path):
    budgeted, ledger, portkey = client(tmp_path, *[with_retry_header(completion(plan(task("a"))))] * 3,
                                       limits=LedgerLimits(max_attempts=2))
    ask(budgeted)
    ask(budgeted)
    with pytest.raises(SpendLimitReached, match="attempt_limit"):
        ask(budgeted)
    assert len(portkey.requests) == 2
    reloaded = SpendLedger(tmp_path / "ledger.json", LedgerLimits(max_attempts=50))   # stored limits win
    assert reloaded.limits.max_attempts == 2 and reloaded.remaining_attempts() == 0


def test_a_request_that_could_exceed_the_spend_ceiling_is_refused(tmp_path):
    budgeted, ledger, portkey = client(tmp_path, completion(plan(task("a"))), limits=LedgerLimits(max_usd=Decimal("0.01")))
    with pytest.raises(SpendLimitReached, match="cost_limit"):
        ask(budgeted)
    assert portkey.requests == [] and ledger.attempts == []


@pytest.mark.parametrize("value", ["1", "-1"])
def test_gateway_retries_lock_the_ledger(tmp_path, value):
    budgeted, ledger, portkey = client(tmp_path, with_retry_header(completion(plan(task("a"))), value),
                                       completion(plan(task("a"))))
    ask(budgeted)
    assert ledger.locked == "gateway_retries_detected"
    with pytest.raises(SpendLimitReached, match="ledger_locked"):
        ask(budgeted)
    assert len(portkey.requests) == 1


def test_input_above_the_pre_call_bound_locks_the_ledger(tmp_path):
    reply = completion(plan(task("a")))
    body = json.loads(reply.content)
    body["usage"]["prompt_tokens"] = 10**6
    budgeted, ledger, _ = client(tmp_path, httpx.Response(200, json=body))
    ask(budgeted)
    assert ledger.locked == "input_bound_exceeded"


# ---------------------------------------------------------------------------------------------- failures
@pytest.mark.parametrize("reply, failure", [
    (httpx.ReadTimeout("slow"), LlmFailure.TIMEOUT),
    (httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"type": "rate_limit_error"}}), LlmFailure.RATE_LIMITED),
    (httpx.Response(529, json={"error": {"type": "overloaded_error"}}), LlmFailure.UNAVAILABLE),
    (httpx.Response(400, json={"error": {"type": "invalid_request_error", "message": "echo of the prompt"}}), LlmFailure.BAD_REQUEST),
    (completion(plan(task("a")), finish_reason="length"), LlmFailure.OUTPUT_TRUNCATED),
    (completion(plan(task("a")), model="grok-4", provider="x-ai"), LlmFailure.UNEXPECTED_PROVIDER),
])
def test_provider_failures_are_typed_and_charged_at_worst_case(tmp_path, reply, failure):
    budgeted, ledger, portkey = client(tmp_path, reply)
    with pytest.raises(LlmError) as raised:
        ask(budgeted)
    assert raised.value.failure == failure and len(portkey.requests) == 1             # one attempt, no hidden retry
    attempt = ledger.attempts[0]
    assert attempt["status"] == failure.value and ledger.committed_usd() == Decimal(attempt["worst_case_usd"])
    assert "echo of the prompt" not in json.dumps(ledger.attempts)


def test_the_cap_never_exceeds_the_configured_maximum(tmp_path):
    budgeted, _, portkey = client(tmp_path, completion(plan(task("a"))), max_output_tokens=1000)
    ask(budgeted)
    assert portkey.bodies()[0]["max_tokens"] == 1000


# --------------------------------------------------------------------------------------- through the graph
def test_planner_and_responder_calls_are_budgeted_priced_and_traced(tmp_path):
    exporter = InMemorySpanExporter()
    tracer = OrchestratorTracer.local([SimpleSpanProcessor(exporter)])
    budgeted, ledger, portkey = client(tmp_path, with_retry_header(completion(plan(task("m", operation="describe_metric",
                                                                                         question="Which metric?")))),
                                       with_retry_header(completion({"claims": [], "limitations": []})), tracer=tracer)
    json_client = PortkeyJsonClient(budgeted)
    from wbg_orchestrator.response import ClaudeResponseGenerator
    engine, _ = build_engine(planner=ClaudePlanner(json_client), responder=ClaudeResponseGenerator(json_client), tracer=tracer,
                             prices=SONNET_5_5_PRICES)
    out = engine.start(request("Investigate the metric definition step by step"))
    assert out.status == InvestigationStatus.INSUFFICIENT_EVIDENCE and len(ledger.attempts) == 2
    assert [b["max_tokens"] for b in portkey.bodies()] == [3000, 2000]
    assert Decimal(out.values["usage"]["cost_usd"]) == SONNET_5_5_PRICES.cost(2400, 160)
    tracer.force_flush()
    spans = exporter.get_finished_spans()
    calls = [s for s in spans if s.name == "orchestrator.llm_call"]
    assert len(calls) == 2 and all(s.attributes["model"] == "test-claude-model" for s in calls)
    by_id = {s.context.span_id: s for s in spans}
    for call in calls:                                                # each provider call sits inside a node span
        parent = by_id[call.parent.span_id]
        assert parent.name in ("orchestrator.node.plan", "orchestrator.node.respond")
        assert parent.attributes["investigation_id"] == out.values["request"]["investigation_id"]
        assert parent.attributes["conversation_id"] == "conv-1"
