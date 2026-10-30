"""Streaming responses (offline): the Claude client's streamed replies, the spend ledger around them, the per-job event
store, and the API's event stream (live steps, conversational text, the checked investigation answer)."""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from structured_agent.nl2sql.llm_client import ClaudeRequest, LlmError, LlmFailure, PortkeyClaudeClient
from tests.structured_agent.nl2sql_fakes import claude_settings

from wbg_orchestrator.live_llm import BudgetedClaudeClient, LedgerLimits, SpendLedger
from wbg_orchestrator.progress import JobStreams

from tests.orchestrator.test_api import DATA_QUESTION, Stack


def sse(*chunks: dict) -> bytes:
    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"


def chunk(text: str | None = None, model: str = "claude-test-model", finish: str | None = None, usage: dict | None = None) -> dict:
    out = {"model": model, "provider": "anthropic", "choices": [{"delta": {"content": text} if text else {}, "finish_reason": finish}]}
    return {**out, "usage": usage} if usage else out


def client(body: bytes, status: int = 200) -> tuple[PortkeyClaudeClient, list[dict]]:
    sent: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(status, content=body, headers={"content-type": "text/event-stream"})
    return PortkeyClaudeClient(claude_settings(), transport=httpx.MockTransport(handle)), sent


ASK = ClaudeRequest(system="s", user="u", trace_id="t", schema_name="conversation_answer")


def test_a_streamed_reply_arrives_piece_by_piece_and_whole():
    portkey, sent = client(sse(chunk("In short, "), chunk("Kenya had 412 notices."),
                               chunk(finish="stop", usage={"prompt_tokens": 120, "completion_tokens": 9})))
    pieces: list[str] = []
    reply = portkey.stream(ASK, pieces.append)
    assert pieces == ["In short, ", "Kenya had 412 notices."] and reply.content == "In short, Kenya had 412 notices."
    assert (reply.input_tokens, reply.output_tokens) == (120, 9) and sent[0]["stream"] is True


def test_a_reply_from_another_provider_stops_before_any_text_is_shown():
    portkey, _ = client(sse(chunk("hello", model="grok-4")))
    pieces: list[str] = []
    with pytest.raises(LlmError) as refused:
        portkey.stream(ASK, pieces.append)
    assert refused.value.failure == LlmFailure.UNEXPECTED_PROVIDER and pieces == []


def test_provider_errors_keep_their_types_and_a_streamed_json_reply_is_parsed():
    portkey, _ = client(b'{"error": {"type": "rate_limit_error"}}', status=429)
    with pytest.raises(LlmError) as limited:
        portkey.stream(ASK, lambda piece: None)
    assert limited.value.failure == LlmFailure.RATE_LIMITED
    portkey, sent = client(sse(chunk('{"answer": "o'), chunk('k"}'), chunk(finish="stop")))
    reply = portkey.stream(ClaudeRequest(system="s", user="u", trace_id="t", json_schema={"type": "object"}), lambda piece: None)
    assert reply.parsed == {"answer": "ok"} and sent[0]["response_format"]["type"] == "json_schema"


def test_a_streamed_reply_is_reserved_and_settled_in_the_spend_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("WBG_ALLOW_LIVE_LLM_CALLS", "true")
    body = sse(chunk("ok"), chunk(finish="stop", usage={"prompt_tokens": 50, "completion_tokens": 2}))
    ledger = SpendLedger(tmp_path / "ledger.json", LedgerLimits(max_attempts=5, max_usd=Decimal("1")))
    budgeted = BudgetedClaudeClient(claude_settings(), ledger, output_caps={"conversation_answer": 600},
                                    transport=httpx.MockTransport(lambda r: httpx.Response(
                                        200, content=body, headers={"content-type": "text/event-stream"})))
    assert budgeted.stream(ASK, lambda piece: None).content == "ok"
    assert [a["status"] for a in ledger.attempts] == ["ok"] and ledger.attempts[0]["output_tokens"] == 2


# ---------------------------------------------------------------------------------------------- job streams
def test_a_reader_gets_every_event_from_where_it_left_off():
    streams = JobStreams()
    streams.open("job-1")
    streams.bind("inv-1", "job-1")
    streams.publish_investigation("inv-1", {"type": "step", "key": "plan", "label": "Planning the steps", "state": "running"})
    streams.publish("job-1", {"type": "text", "text": "Hello"})
    first, ended = streams.read("job-1", 0, timeout=0.1)
    assert [e["type"] for e in first] == ["step", "text"] and not ended
    streams.close("job-1", {"state": "succeeded", "status": "responded"})
    rest, ended = streams.read("job-1", 2, timeout=0.1)
    assert rest == [{"type": "done", "state": "succeeded", "status": "responded"}] and ended
    streams.publish_investigation("inv-1", {"type": "text", "text": "late"})              # nothing after the end
    assert streams.read("job-1", 3, timeout=0.05) == ([], True)
    assert streams.read("job-unknown", 0, timeout=0.05) == ([], True)


# ----------------------------------------------------------------------------------------------------- API
def events_of(stack: Stack, job_id: str, user: str = "alice") -> list[dict]:
    response = stack.call("GET", f"/jobs/{job_id}/events", user)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_an_investigation_streams_its_steps_and_then_its_checked_answer(tmp_path):
    stack = Stack(tmp_path)
    cid = stack.conversation()
    job = stack.send(cid, DATA_QUESTION, "msg-0001", execute=True).json()
    stack.wait(job["job_id"])
    events = events_of(stack, job["job_id"])
    steps = [e for e in events if e["type"] == "step"]
    assert {"Querying procurement data", "Writing the answer"} <= {s["label"] for s in steps}
    assert all(s["state"] in ("running", "done", "failed", "no_result") for s in steps)
    [answer] = [e for e in events if e["type"] == "answer"]
    final = stack.call("GET", f"/conversations/{cid}/investigations/{stack.wait(job['job_id'])['request_id']}").json()
    expected = "\n\n".join(c["text"] for c in final["claims"]) or final["message"]
    assert answer["text"] == expected and events[-1]["type"] == "done"                  # the checked answer, as paragraphs


def test_a_greeting_streams_its_text_and_another_user_cannot_listen(tmp_path):
    stack = Stack(tmp_path)
    cid = stack.conversation()
    job = stack.send(cid, "hi", "msg-0001").json()
    stack.wait(job["job_id"])
    events = events_of(stack, job["job_id"])
    assert [e["type"] for e in events] == ["text", "done"] and "Hello" in events[0]["text"]
    assert stack.call("GET", f"/jobs/{job['job_id']}/events", "bob").status_code == 404


# ------------------------------------------------------------------------------------- the final answer, streamed
def test_only_the_claim_sentences_are_streamed_out_of_the_json():
    from wbg_orchestrator.claim_stream import ClaimTextStream
    draft = {"claims": [{"evidence_ids": ["ev_1"], "text": 'P148850 signed 214 "contracts".'},
                        {"text": "Most were Request for Bids \u2014 caf\u00e9.", "evidence_ids": ["ev_2"]}],
             "limitations": ["text", "Figures are estimates."]}
    raw, out = json.dumps(draft), []
    stream = ClaimTextStream(out.append)
    for i in range(0, len(raw), 2):
        stream.feed(raw[i:i + 2])
    assert "".join(out) == "\n\n".join(c["text"] for c in draft["claims"])          # one paragraph per claim


class StreamingWriter:
    """Stands in for the Claude JSON client: cites the first evidence record it is given, streamed a few characters at a time."""

    def __init__(self):
        self.calls = 0

    def stream_json(self, *, user, on_text, **kwargs):
        import re
        from wbg_orchestrator.contracts import Usage
        self.calls += 1
        evidence = re.search(r"^\[([^\]]+)\]", user.split("EVIDENCE:\n", 1)[1]).group(1)
        draft = {"claims": [{"text": "Answered from the verified data.", "evidence_ids": [evidence]}], "limitations": []}
        raw = json.dumps(draft)
        for i in range(0, len(raw), 4):
            on_text(raw[i:i + 4])
        return draft, raw, Usage(llm_calls=1)


def test_the_final_answer_streams_as_it_is_written_and_matches_the_checked_text():
    from tests.orchestrator.helpers import build_engine, request
    from wbg_orchestrator.response import ClaudeResponseGenerator
    engine, _ = build_engine(responder=ClaudeResponseGenerator(StreamingWriter()))
    events: list[dict] = []
    engine.deps.progress = lambda investigation_id, event: events.append(event)
    out = engine.start(request(DATA_QUESTION, execute=True))
    pieces = [e["text"] for e in events if e["type"] == "text"]
    assert len(pieces) > 1 and "".join(pieces) == out.response.message == "Answered from the verified data."
    assert [e["type"] for e in events if e["type"].startswith("text")][0] == "text_reset"


def test_a_gateway_that_does_not_stream_still_gives_the_whole_reply():
    body = json.dumps({"model": "claude-test-model", "provider": "anthropic",
                       "choices": [{"message": {"content": "whole reply"}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 10, "completion_tokens": 2}}).encode()
    portkey = PortkeyClaudeClient(claude_settings(), transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=body, headers={"content-type": "application/json"})))
    pieces: list[str] = []
    assert portkey.stream(ASK, pieces.append).content == "whole reply" and pieces == ["whole reply"]


def test_if_the_gateway_refuses_streaming_the_answer_comes_whole():
    from wbg_orchestrator.contracts import Usage
    from wbg_orchestrator.response import ClaudeResponseGenerator

    class RefusesStreaming:
        def stream_json(self, **kwargs):
            raise LlmError(LlmFailure.BAD_REQUEST, 400)

        def complete(self, **kwargs):
            return {"claims": [{"text": "Whole answer.", "evidence_ids": ["ev_1"]}], "limitations": []}, "", Usage(llm_calls=1)

    from tests.orchestrator.helpers import request
    pieces: list[str] = []
    draft, _ = ClaudeResponseGenerator(RefusesStreaming()).compose(request(DATA_QUESTION), [], None, on_text=pieces.append)
    assert draft.claims[0].text == "Whole answer." and "".join(pieces) == "Whole answer."


def test_the_checked_answer_keeps_the_no_evidence_opening_as_its_own_paragraph():
    from wbg_orchestrator.app_service import answer_text
    from wbg_orchestrator.contracts import InvestigationResponse, InvestigationStatus, ResponseClaim
    response = InvestigationResponse(
        investigation_id="inv-1", status=InvestigationStatus.PARTIAL,
        message="No evidence found for part of the question: bid counts. What the evidence shows: A. B.",
        claims=(ResponseClaim(text="A.", evidence_ids=("ev_1",)), ResponseClaim(text="B.", evidence_ids=("ev_2",))))
    assert answer_text(response) == ("No evidence found for part of the question: bid counts. What the evidence shows:\n\n"
                                     "A.\n\nB.")
