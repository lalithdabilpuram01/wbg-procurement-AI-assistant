"""An app started without --live: Claude is off, nothing is ever sent, and the user is told plainly why an answer that
needs Claude was not written. Features without Claude (fixed plans, deterministic answers) keep working."""
from __future__ import annotations

import pytest

from structured_agent.nl2sql.llm_client import ClaudeRequest, LlmError, LlmFailure

from wbg_orchestrator.claude import PortkeyJsonClient
from wbg_orchestrator.contracts import InvestigationStatus
from wbg_orchestrator.graph import CLAUDE_OFF
from wbg_orchestrator.live_llm import ClaudeOff
from wbg_orchestrator.planner import ClaudePlanner
from wbg_orchestrator.response import ClaudeResponseGenerator

from tests.helpers import build_engine, request


def test_claude_off_refuses_before_any_request():
    with pytest.raises(LlmError) as refused:
        ClaudeOff().complete(ClaudeRequest(system="s", user="u", trace_id="t"))
    assert refused.value.failure == LlmFailure.DISABLED and not refused.value.is_transient


def test_a_question_that_needs_the_planner_says_claude_is_off():
    engine, parts = build_engine(planner=ClaudePlanner(PortkeyJsonClient(ClaudeOff())))
    out = engine.start(request("Investigate the metric definition step by step"))
    assert out.status == InvestigationStatus.FAILED and out.response.message == CLAUDE_OFF
    assert out.response.error_codes == ("claude_disabled",) and parts["tools"].calls == []


def test_an_answer_that_needs_claude_says_claude_is_off():
    engine, _ = build_engine(responder=ClaudeResponseGenerator(PortkeyJsonClient(ClaudeOff())))
    out = engine.start(request("How many bid invitations were published in fiscal year 2024?", execute=True))
    assert out.response.message == CLAUDE_OFF and "claude_disabled" in out.response.error_codes
