"""Test doubles for the natural-language-to-SQL workflow: a fake Portkey endpoint and a fake Databricks executor.

Neither makes a network or Databricks call. The fake Portkey records every HTTP request so tests can check headers,
bodies and how many upstream calls were made.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import sqlglot

from structured_agent.execution.executor import CredentialCheck, QueryTimeout, RawResult
from structured_agent.nl2sql.llm_client import PortkeyClaudeClient
from structured_agent.nl2sql.settings import ClaudeSettings

TEST_API_KEY = "pk-test-SECRET-do-not-leak-4242"
TEST_MODEL = "@wbg-anthropic/test-claude-model"


def claude_settings(**overrides: Any) -> ClaudeSettings:
    return ClaudeSettings(api_key=TEST_API_KEY, model=TEST_MODEL, **overrides)


def offline_claude_client(portkey: "FakePortkey", **settings_overrides: Any) -> PortkeyClaudeClient:
    """A client wired to the in-memory fake Portkey (no network, no live-access grant needed)."""
    return PortkeyClaudeClient(claude_settings(**settings_overrides), transport=portkey.transport())


def sql_draft(sql: str, parameters: dict[str, Any] | None = None, answerable: bool = True,
              explanation: str = "Counts rows.") -> dict[str, Any]:
    """The structured reply the model is asked to produce."""
    typed = []
    for name, value in (parameters or {}).items():
        value_type = "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else \
            "number" if isinstance(value, float) else "string"
        typed.append({"name": name, "value": str(value).lower() if isinstance(value, bool) else str(value), "value_type": value_type})
    return {"answerable": answerable, "sql": sql, "parameters": typed, "tables_used": [], "explanation": explanation}


def completion(content: dict[str, Any] | str, model: str = "claude-test-model", provider: str = "anthropic",
               finish_reason: str = "stop") -> httpx.Response:
    """A Portkey (OpenAI-compatible) chat-completion response."""
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={"id": "chatcmpl-test", "model": model, "provider": provider,
                                     "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                                  "finish_reason": finish_reason}],
                                     "usage": {"prompt_tokens": 1200, "completion_tokens": 80}})


class FakePortkey:
    """Scripted replies: each item is an httpx.Response, an exception to raise, or a SQL-draft dict."""

    def __init__(self, *replies: Any):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, httpx.Response) else completion(reply)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


class EchoColumnsExecutor:
    """Fake Databricks: returns two rows whose columns are the validated query's output names."""

    def __init__(self, write_capable: bool = False, failure: Exception | None = None):
        self.write_capable = write_capable
        self.failure = failure
        self.calls: list[str] = []

    def check_credential(self) -> CredentialCheck:
        return CredentialCheck(self.write_capable, "test double")

    def execute(self, sql: str, parameters: dict[str, Any], timeout_seconds: int) -> RawResult:
        self.calls.append(sql)
        if self.failure is not None:
            raise self.failure
        names = [p.alias_or_name for p in sqlglot.parse_one(sql, read="databricks").expressions]
        return RawResult([(n, "bigint") for n in names], [{n: i for n in names} for i in (1, 2)])


def timeout_executor() -> EchoColumnsExecutor:
    return EchoColumnsExecutor(failure=QueryTimeout("Query exceeded 60 s and was interrupted"))


def block_live_network(monkeypatch) -> None:
    """Test safety net: remove the live-call opt-in and make any real httpx network request fail loudly.

    Tests must use the in-memory FakePortkey; this guarantees that valid credentials in .env can never turn a test
    into a real provider call.
    """
    from structured_agent.nl2sql.live_guard import LIVE_LLM_ENV_VAR

    def refuse(*args: Any, **kwargs: Any):
        raise AssertionError("A test attempted a real network request")

    monkeypatch.delenv(LIVE_LLM_ENV_VAR, raising=False)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    import requests   # used by OpenTelemetry/Logfire exporters
    monkeypatch.setattr(requests.Session, "send", refuse)
