"""Structured-output calls to Claude through the EXISTING Portkey client (no new HTTP code, no new live-call path).

`PortkeyClaudeClient` already enforces the Claude-only route, the dual live-call opt-in (`LiveAccess`), typed
`LlmError`s and no retries. Offline, it is built with an in-memory `httpx.MockTransport` only. Live, hosts pass the
budgeted subclass from `live_llm.py`.

Claude's structured outputs accept a subset of JSON Schema (no string length, numeric or complex array constraints;
`additionalProperties: false` on every object). `strict_output_schema` reduces a Pydantic schema to that subset; the
full Pydantic model still validates every reply client-side, so no constraint is lost.
"""
from __future__ import annotations

from typing import Any, Callable

from structured_agent.nl2sql.llm_client import ClaudeRequest, PortkeyClaudeClient

from .contracts import Usage

UNSUPPORTED_KEYWORDS = frozenset({"minLength", "maxLength", "pattern", "minimum", "maximum", "exclusiveMinimum",
                                  "exclusiveMaximum", "multipleOf", "minItems", "maxItems", "uniqueItems", "default", "title"})


def strict_output_schema(schema: Any) -> Any:
    """A copy of `schema` without unsupported keywords; every object is closed and lists all its properties as required."""
    if isinstance(schema, list):
        return [strict_output_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    clean: dict[str, Any] = {}
    for key, value in schema.items():
        if key in ("properties", "$defs"):                  # mappings of names (a property may be called "required")
            clean[key] = {name: strict_output_schema(sub) for name, sub in value.items()}
        elif key not in UNSUPPORTED_KEYWORDS:
            clean[key] = strict_output_schema(value)
    if "properties" in clean:
        clean["additionalProperties"] = False
        clean["required"] = list(clean["properties"])
    return clean


class PortkeyJsonClient:
    """Thin wrapper over the Portkey Claude client: sends Claude-compatible schemas, returns orchestrator Usage."""
    def __init__(self, client: PortkeyClaudeClient):
        self._client = client

    @property
    def model(self) -> str:
        return self._client.model

    def stream_text(self, *, system: str, user: str, schema_name: str, trace_id: str,
                    on_text: Callable[[str], None]) -> tuple[str, Usage]:
        """A plain-text reply streamed piece by piece to `on_text` (used for conversational answers)."""
        response = self._client.stream(ClaudeRequest(system=system, user=user, trace_id=trace_id, schema_name=schema_name),
                                       on_text)
        return response.content, Usage(llm_calls=1, input_tokens=response.input_tokens, output_tokens=response.output_tokens)

    def stream_json(self, *, system: str, user: str, schema: dict[str, Any], schema_name: str, trace_id: str,
                    on_text: Callable[[str], None]) -> tuple[dict[str, Any] | None, str, Usage]:
        """A structured reply streamed as it is written (`on_text` gets the raw JSON pieces), then parsed like complete()."""
        response = self._client.stream(ClaudeRequest(system=system, user=user, trace_id=trace_id,
                                                     json_schema=strict_output_schema(schema), schema_name=schema_name), on_text)
        return response.parsed, response.content, Usage(llm_calls=1, input_tokens=response.input_tokens,
                                                        output_tokens=response.output_tokens)

    def complete(self, *, system: str, user: str, schema: dict[str, Any], schema_name: str, trace_id: str,
                 timeout_seconds: float | None = None) -> tuple[dict[str, Any] | None, str, Usage]:
        """One structured reply: (parsed JSON or None, raw text, usage). Callers still validate the parsed JSON."""
        response = self._client.complete(ClaudeRequest(system=system, user=user, trace_id=trace_id,
                                                       json_schema=strict_output_schema(schema), schema_name=schema_name,
                                                       timeout_seconds=timeout_seconds))
        return response.parsed, response.content, Usage(llm_calls=1, input_tokens=response.input_tokens,
                                                        output_tokens=response.output_tokens)
