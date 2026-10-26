"""Claude through the Portkey AI gateway (Claude-only route).

Uses Portkey's documented REST endpoint (POST https://api.portkey.ai/v1/chat/completions, header
`x-portkey-api-key`) with httpx, which is already installed; no new dependency.

Claude-only routing:
  * the request names only the Model Catalog model (`@wbg-anthropic/<slug>`) and NEVER sends `x-portkey-config`,
    so the saved Claude -> Grok fallback Config is not used. (The Portkey service key itself must have no default
    Config attached; that is a dashboard setting this code cannot see.)
  * defence in depth: a response produced by a non-Claude model is rejected (UNEXPECTED_PROVIDER) and discarded.
  * no retries here or in Portkey (no Config means no gateway retries): one HTTP request per call.

Request format (verified 2026-10-26 against Portkey and Claude API documentation):
  * no sampling parameters: Claude Sonnet 5.5 (and Claude 4.7+ generally) rejects non-default `temperature`,
    `top_p` and `top_k` with HTTP 400. The first version sent `temperature: 0`.
  * `max_tokens` caps thinking plus answer together (Sonnet 5.5 thinks adaptively by default), so the default is
    generous and a reply cut off at the limit (`finish_reason: "length"`) is reported as OUTPUT_TRUNCATED.

Live access: any transport other than an in-memory httpx.MockTransport is a real network transport and needs a
LiveAccess grant (live_guard.py); the environment opt-in is re-checked before every live request.

Errors keep the failure class, HTTP status and the provider's short error type code (e.g. invalid_request_error).
Error messages and response bodies (which may echo the prompt) are never kept.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx

from structured_agent.nl2sql.live_guard import LiveAccess, LiveCallsDisabled, require_live_env_opt_in
from structured_agent.nl2sql.settings import PORTKEY_CHAT_COMPLETIONS_URL, ClaudeSettings

SAFE_ERROR_TYPE = re.compile(r"^[a-z0-9_.-]{1,64}$")   # short provider codes only, never free text


class LlmFailure(str, Enum):
    RATE_LIMITED = "provider_rate_limited"        # 429
    UNAVAILABLE = "provider_unavailable"          # 5xx, 529
    TIMEOUT = "provider_timeout"                  # client timeout or 408
    CONNECTION = "provider_connection_error"
    AUTH = "provider_auth_error"                  # 401 / 403: configuration problem, retrying cannot help
    BAD_REQUEST = "provider_bad_request"          # other 4xx
    INVALID_OUTPUT = "invalid_model_output"       # reply is not the requested structure
    OUTPUT_TRUNCATED = "model_output_truncated"   # reply stopped at max_tokens (finish_reason "length")
    UNEXPECTED_PROVIDER = "unexpected_provider"   # reply not produced by Claude (possible fallback)


# Transient upstream failures: these count towards the LLM circuit breaker.
TRANSIENT_FAILURES = {LlmFailure.RATE_LIMITED, LlmFailure.UNAVAILABLE, LlmFailure.TIMEOUT, LlmFailure.CONNECTION}


class LlmError(Exception):
    def __init__(self, failure: LlmFailure, status_code: int | None = None, retry_after_seconds: float | None = None,
                 provider_error_type: str | None = None):
        self.failure = failure
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.provider_error_type = provider_error_type   # e.g. "invalid_request_error"; validated short code
        details = [f"HTTP {status_code}"] if status_code else []
        details += [provider_error_type] if provider_error_type else []
        super().__init__(failure.value + (f" ({', '.join(details)})" if details else ""))

    @property
    def is_transient(self) -> bool:
        return self.failure in TRANSIENT_FAILURES


@dataclass(frozen=True)
class ClaudeRequest:
    system: str
    user: str
    trace_id: str                                  # sent as x-portkey-trace-id for log correlation in Portkey
    json_schema: dict[str, Any] | None = None      # requested output structure
    schema_name: str = "structured_output"
    timeout_seconds: float | None = None           # overrides the settings default (e.g. remaining deadline)


@dataclass(frozen=True)
class ClaudeResponse:
    content: str
    parsed: dict[str, Any] | None
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: int


class PortkeyClaudeClient:
    def __init__(self, settings: ClaudeSettings, *, transport: httpx.MockTransport | None = None,
                 live_access: LiveAccess | None = None):
        """Offline (tests): pass an httpx.MockTransport. Live: pass a LiveAccess grant from authorize_live_access().

        Anything else fails closed: no transport and no grant, or a grant without the environment opt-in.
        """
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise LiveCallsDisabled("Only in-memory httpx.MockTransport may be injected; real transports need a LiveAccess grant")
        if transport is None:
            if not isinstance(live_access, LiveAccess):
                raise LiveCallsDisabled("No LiveAccess grant: real Portkey requests are disabled by default")
            require_live_env_opt_in()
        self._settings = settings
        self._live = transport is None
        self._http = httpx.Client(transport=transport)

    @property
    def timeout_seconds(self) -> float:
        return self._settings.timeout_seconds

    @property
    def model(self) -> str:
        """The configured Model Catalog identifier (not a secret)."""
        return self._settings.model

    def complete(self, request: ClaudeRequest) -> ClaudeResponse:
        if self._live:
            require_live_env_opt_in()   # re-checked on every live request
        timeout = request.timeout_seconds or self._settings.timeout_seconds
        started = time.perf_counter()
        try:
            response = self._http.post(PORTKEY_CHAT_COMPLETIONS_URL, headers=self._headers(request, timeout),
                                       json=self._body(request), timeout=timeout)
        except httpx.TimeoutException:
            raise LlmError(LlmFailure.TIMEOUT) from None
        except httpx.TransportError:
            raise LlmError(LlmFailure.CONNECTION) from None
        if response.status_code != 200:
            raise self._http_error(response)
        return self._parse(response.json(), request, round(1000 * (time.perf_counter() - started)))

    # ------------------------------------------------------------------------------------------------------ request
    def _headers(self, request: ClaudeRequest, timeout_seconds: float) -> dict[str, str]:
        # Deliberately no x-portkey-config header: Claude-only routing.
        return {"Content-Type": "application/json", "x-portkey-api-key": self._settings.api_key,
                "x-portkey-trace-id": request.trace_id,
                "x-portkey-request-timeout": str(int(timeout_seconds * 1000))}

    def _body(self, request: ClaudeRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._settings.model,
            "messages": [{"role": "system", "content": request.system}, {"role": "user", "content": request.user}],
            "max_tokens": self._settings.max_output_tokens,
            # No temperature/top_p/top_k: Claude Sonnet 5.5 returns HTTP 400 for non-default sampling values.
        }
        if request.json_schema is not None and self._settings.use_json_schema_output:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": request.schema_name, "strict": True, "schema": request.json_schema}}
        return body

    # ----------------------------------------------------------------------------------------------------- response
    @staticmethod
    def _http_error(response: httpx.Response) -> LlmError:
        status = response.status_code
        error_type = _provider_error_type(response)
        if status == 429:
            return LlmError(LlmFailure.RATE_LIMITED, status, _retry_after_seconds(response), error_type)
        if status in (500, 502, 503, 504, 529):
            return LlmError(LlmFailure.UNAVAILABLE, status, _retry_after_seconds(response), error_type)
        if status == 408:
            return LlmError(LlmFailure.TIMEOUT, status, provider_error_type=error_type)
        if status in (401, 403):
            return LlmError(LlmFailure.AUTH, status, provider_error_type=error_type)
        return LlmError(LlmFailure.BAD_REQUEST, status, provider_error_type=error_type)

    @staticmethod
    def _parse(payload: dict[str, Any], request: ClaudeRequest, duration_ms: int) -> ClaudeResponse:
        model = str(payload.get("model", ""))
        provider = str(payload.get("provider", "anthropic"))
        if "claude" not in model.lower() or provider.lower() != "anthropic":
            raise LlmError(LlmFailure.UNEXPECTED_PROVIDER)
        try:
            choice = payload["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LlmError(LlmFailure.INVALID_OUTPUT) from None
        if choice.get("finish_reason") == "length":
            raise LlmError(LlmFailure.OUTPUT_TRUNCATED)
        parsed = _parse_json_object(content) if request.json_schema is not None else None
        usage = payload.get("usage") or {}
        return ClaudeResponse(content=content, parsed=parsed, model=model,
                              input_tokens=int(usage.get("prompt_tokens", 0)), output_tokens=int(usage.get("completion_tokens", 0)),
                              duration_ms=duration_ms)


def _parse_json_object(content: str) -> dict[str, Any]:
    """The structured reply as a JSON object (tolerates a ```json fence); otherwise INVALID_OUTPUT."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raise LlmError(LlmFailure.INVALID_OUTPUT) from None
    if not isinstance(parsed, dict):
        raise LlmError(LlmFailure.INVALID_OUTPUT)
    return parsed


def _provider_error_type(response: httpx.Response) -> str | None:
    """The provider's short error code (error.type or error.code), if it is a safe token. Messages are dropped."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return None
    if not isinstance(error, dict):
        return None
    for key in ("type", "code"):
        value = error.get(key)
        if isinstance(value, str) and SAFE_ERROR_TYPE.match(value):
            return value
    return None


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
