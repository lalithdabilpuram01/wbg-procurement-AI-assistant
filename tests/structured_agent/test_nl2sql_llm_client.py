"""Portkey Claude client (offline): exact request, schema, response handling and error classification."""
from __future__ import annotations

import httpx
import jsonschema
import pytest

from structured_agent.nl2sql.llm_client import ClaudeRequest, LlmError, LlmFailure
from structured_agent.nl2sql.settings import PORTKEY_CHAT_COMPLETIONS_URL
from structured_agent.nl2sql.sql_generator import SQL_DRAFT_SCHEMA, SYSTEM_PROMPT, SqlDraft
from tests.structured_agent.nl2sql_fakes import (TEST_API_KEY, TEST_MODEL, FakePortkey, claude_settings, completion,
                                                 offline_claude_client, sql_draft)

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer"], "properties": {"answer": {"type": "string"}}}
# Keywords Claude structured outputs support (Claude API docs, "JSON Schema Limitations").
SUPPORTED_SCHEMA_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "anyOf",
                             "allOf", "$ref", "$defs", "format", "description"}


def request(**overrides) -> ClaudeRequest:
    return ClaudeRequest(**{"system": "system text", "user": "user text", "trace_id": "req_test",
                            "json_schema": SCHEMA, "schema_name": "answer", **overrides})


def redacted(headers: httpx.Headers) -> dict[str, str]:
    """Request headers with the secret replaced, for exact comparison and safe failure output."""
    values = {k.lower(): v for k, v in headers.items() if k.lower() not in {"host", "accept", "accept-encoding", "connection",
                                                                            "user-agent", "content-length"}}
    values["x-portkey-api-key"] = "<redacted>" if values.get("x-portkey-api-key") == TEST_API_KEY else "<WRONG KEY>"
    return values


# --------------------------------------------------------------------------------------------------- exact request
def test_exact_request_headers_and_body():
    portkey = FakePortkey({"answer": "ok"})
    offline_claude_client(portkey).complete(request(timeout_seconds=12))
    sent = portkey.requests[0]
    assert sent.method == "POST" and str(sent.url) == PORTKEY_CHAT_COMPLETIONS_URL
    assert redacted(sent.headers) == {
        "content-type": "application/json",
        "x-portkey-api-key": "<redacted>",
        "x-portkey-trace-id": "req_test",
        "x-portkey-request-timeout": "12000",
    }                                                     # in particular: no x-portkey-config (no fallback Config)
    assert portkey.bodies()[0] == {
        "model": TEST_MODEL,
        "messages": [{"role": "system", "content": [{"type": "text", "text": "system text", "cache_control": {"type": "ephemeral"}}]},
                     {"role": "user", "content": "user text"}],
        "max_tokens": 16000,
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": SCHEMA}},
    }                                                     # in particular: no temperature / top_p / top_k
    assert TEST_API_KEY not in sent.content.decode()


def test_json_schema_output_can_be_switched_off():
    portkey = FakePortkey({"answer": "ok"})
    offline_claude_client(portkey, use_json_schema_output=False).complete(request())
    assert "response_format" not in portkey.bodies()[0]


# --------------------------------------------------------------------------------------------- the SQL draft schema
def _walk(schema: dict):
    yield schema
    for value in schema.get("properties", {}).values():
        yield from _walk(value)
    if isinstance(schema.get("items"), dict):
        yield from _walk(schema["items"])


def test_sql_draft_schema_meets_structured_output_rules():
    jsonschema.Draft202012Validator.check_schema(SQL_DRAFT_SCHEMA)
    for node in _walk(SQL_DRAFT_SCHEMA):
        assert set(node) <= SUPPORTED_SCHEMA_KEYWORDS, set(node) - SUPPORTED_SCHEMA_KEYWORDS
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])   # strict mode: every property required


def test_a_valid_reply_matches_both_the_schema_and_the_pydantic_model():
    reply = sql_draft("SELECT 1 AS x LIMIT 1", {"fiscal_year": 2024, "country_code": "ET"})
    jsonschema.validate(reply, SQL_DRAFT_SCHEMA)
    assert SqlDraft.model_validate(reply).typed_parameters() == {"fiscal_year": 2024, "country_code": "ET"}


def test_system_prompt_forbids_execution_and_invention():
    assert "Never invent tables or columns" in SYSTEM_PROMPT and "SELECT" in SYSTEM_PROMPT


# ------------------------------------------------------------------------------------------------------- responses
def test_successful_reply_is_parsed_with_usage():
    response = offline_claude_client(FakePortkey({"answer": "ok"})).complete(request())
    assert response.parsed == {"answer": "ok"} and response.input_tokens == 1200 and response.output_tokens == 80


@pytest.mark.parametrize("reply, failure", [
    (completion("this is not json"), LlmFailure.INVALID_OUTPUT),
    (completion("[1, 2, 3]"), LlmFailure.INVALID_OUTPUT),                                     # JSON but not an object
    (httpx.Response(200, json={"model": "claude-test-model", "choices": []}), LlmFailure.INVALID_OUTPUT),
    (httpx.Response(200, json={"model": "claude-test-model"}), LlmFailure.INVALID_OUTPUT),
    (completion('{"answer": "cut off', finish_reason="length"), LlmFailure.OUTPUT_TRUNCATED),
])
def test_malformed_or_truncated_replies_are_rejected(reply, failure):
    with pytest.raises(LlmError) as raised:
        offline_claude_client(FakePortkey(reply)).complete(request())
    assert raised.value.failure == failure and not raised.value.is_transient


@pytest.mark.parametrize("model, provider", [("grok-3", "x-ai"), ("grok-3", "anthropic"), ("claude-test-model", "x-ai")])
def test_a_reply_from_a_non_claude_model_is_rejected(model, provider):
    with pytest.raises(LlmError) as raised:
        offline_claude_client(FakePortkey(completion({"answer": "ok"}, model=model, provider=provider))).complete(request())
    assert raised.value.failure == LlmFailure.UNEXPECTED_PROVIDER


# ---------------------------------------------------------------------------------------------- error classification
PROMPT_ECHO = "echo of the prompt: user text"


@pytest.mark.parametrize("reply, failure, transient, error_type", [
    (httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": PROMPT_ECHO}}),
     LlmFailure.BAD_REQUEST, False, "invalid_request_error"),
    (httpx.Response(404, json={"error": {"type": "not_found_error", "message": PROMPT_ECHO}}), LlmFailure.BAD_REQUEST, False, "not_found_error"),
    (httpx.Response(401, json={"error": {"type": "authentication_error", "message": "bad key"}}), LlmFailure.AUTH, False, "authentication_error"),
    (httpx.Response(403), LlmFailure.AUTH, False, None),
    (httpx.Response(429, headers={"retry-after": "7"}, json={"error": {"type": "rate_limit_error"}}), LlmFailure.RATE_LIMITED, True, "rate_limit_error"),
    (httpx.Response(500), LlmFailure.UNAVAILABLE, True, None),
    (httpx.Response(503, json={"error": {"code": "overloaded"}}), LlmFailure.UNAVAILABLE, True, "overloaded"),
    (httpx.Response(529, json={"error": {"type": "overloaded_error"}}), LlmFailure.UNAVAILABLE, True, "overloaded_error"),
    (httpx.Response(408), LlmFailure.TIMEOUT, True, None),
    (httpx.ReadTimeout("timed out"), LlmFailure.TIMEOUT, True, None),
    (httpx.ConnectError("refused"), LlmFailure.CONNECTION, True, None),
])
def test_provider_errors_are_classified_without_retrying(reply, failure, transient, error_type):
    portkey = FakePortkey(reply)
    with pytest.raises(LlmError) as raised:
        offline_claude_client(portkey).complete(request())
    error = raised.value
    assert error.failure == failure and error.is_transient == transient and error.provider_error_type == error_type
    assert len(portkey.requests) == 1                                         # exactly one upstream attempt
    assert PROMPT_ECHO not in str(error) and "user text" not in str(error) and TEST_API_KEY not in str(error)


def test_status_code_and_retry_after_are_kept():
    with pytest.raises(LlmError) as raised:
        offline_claude_client(FakePortkey(httpx.Response(429, headers={"retry-after": "7"}))).complete(request())
    assert raised.value.status_code == 429 and raised.value.retry_after_seconds == 7


def test_free_text_error_codes_are_not_kept():
    reply = httpx.Response(400, json={"error": {"type": "Your prompt mentions Ethiopia contracts", "code": "x" * 200}})
    with pytest.raises(LlmError) as raised:
        offline_claude_client(FakePortkey(reply)).complete(request())
    assert raised.value.provider_error_type is None


def test_settings_repr_hides_the_api_key():
    assert TEST_API_KEY not in repr(claude_settings())
