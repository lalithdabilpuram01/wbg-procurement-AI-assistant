"""Budgeted live Claude calls: a hard, application-side attempt and spend ceiling around the EXISTING Portkey client.

`PortkeyClaudeClient` stays the only HTTP path (Claude-only route without a Portkey Config, LiveAccess dual opt-in,
typed `LlmError`, no client retries). `BudgetedClaudeClient` subclasses it and adds, for every request:

1. a reservation in a persistent `SpendLedger` BEFORE the request is sent. The request is refused when the attempt
   limit is reached, or when its worst-case cost could take the committed spend over the ceiling;
2. an explicit `max_tokens` per output schema. On Claude, `max_tokens` bounds thinking and answer together, so it is
   also the reasoning-token limit;
3. settlement after the response: the provider's reported tokens, the estimated cost, and Portkey's
   `x-portkey-retry-attempt-count` header. Any sign of gateway retries, or input above the pre-call bound, locks the
   ledger.

Worst case = (input bound x input price + max_tokens x output price) x 1.1. The input bound counts one token per
UTF-8 byte of everything sent (system, user, schema) plus a fixed framing allowance. The 1.1 factor covers the US-only
inference multiplier. A failed attempt stays charged at its worst case, because the provider may have billed it. The
ceiling is enforced by this application; it is not a provider-side guarantee.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx

from structured_agent.nl2sql.llm_client import ClaudeRequest, ClaudeResponse, LlmError, LlmFailure, PortkeyClaudeClient
from structured_agent.nl2sql.settings import ClaudeSettings

from .budget import PriceTable
from .observability import OrchestratorTracer

# Verified 2026-10-26 at https://platform.claude.com/docs/en/about-claude/pricing (model pricing table):
# Claude Sonnet 5.5, base input $2 / MTok, output $10 / MTok; full 1M context at standard pricing.
SONNET_5_5_PRICES = PriceTable(model="claude-sonnet-5-5", input_usd_per_million=Decimal("2"),
                               output_usd_per_million=Decimal("10"),
                               source="platform.claude.com/docs/en/about-claude/pricing, verified 2026-10-26")
GEO_MULTIPLIER = Decimal("1.1")                 # US-only inference_geo multiplier (worst case; not requested)
FRAMING_TOKENS = 2_000                          # message framing + structured-output instructions added by the API
RETRY_HEADER = "x-portkey-retry-attempt-count"  # "0": no retries configured; -1 or >0: the gateway retried


class SpendLimitReached(RuntimeError):
    """The next live request could exceed the attempt or spend ceiling (or the ledger is locked)."""

    def __init__(self, code: str):
        super().__init__(f"live Claude request refused: {code}")
        self.code = code


def input_token_bound(request: ClaudeRequest) -> int:
    sent = (request.system + (request.cache_prefix or "") + request.user
            + (json.dumps(request.json_schema) if request.json_schema else ""))
    return len(sent.encode("utf-8")) + FRAMING_TOKENS


def model_slug(model: str) -> str:
    return model.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class LedgerLimits:
    max_attempts: int = 5
    max_usd: Decimal = Decimal("1.00")


class SpendLedger:
    """Append-only record of live attempts, saved to disk before and after each request (survives a crash)."""

    def __init__(self, path: Path, limits: LedgerLimits = LedgerLimits(), prices: PriceTable = SONNET_5_5_PRICES):
        self.path = Path(path)
        self.prices = prices
        data = json.loads(self.path.read_text()) if self.path.is_file() else {}
        stored = data.get("limits")
        self.limits = LedgerLimits(stored["max_attempts"], Decimal(stored["max_usd"])) if stored else limits
        self.attempts: list[dict[str, Any]] = data.get("attempts", [])
        self.locked: str | None = data.get("locked")

    # ------------------------------------------------------------------------------------------------ accounting
    @staticmethod
    def _charge(attempt: dict[str, Any]) -> Decimal:
        if attempt["status"] == "ok":
            return Decimal(attempt["conservative_usd"])
        return Decimal(attempt["worst_case_usd"])           # reserved, failed or unknown: assume the worst

    def committed_usd(self) -> Decimal:
        return sum((self._charge(a) for a in self.attempts), Decimal("0"))

    def estimated_usd(self) -> Decimal:
        return sum((Decimal(a.get("estimated_usd") or "0") for a in self.attempts), Decimal("0"))

    def remaining_attempts(self) -> int:
        return self.limits.max_attempts - len(self.attempts)

    def reserve(self, *, purpose: str, trace_id: str, model: str, max_tokens: int, input_bound: int) -> int:
        # worst case: every input token written to the prompt cache (1.25x), at the regional multiplier
        worst = (self.prices.cost(input_bound, max_tokens, cache_write_tokens=input_bound) * GEO_MULTIPLIER).quantize(Decimal("0.000001"))
        if self.locked:
            raise SpendLimitReached(f"ledger_locked:{self.locked}")
        if len(self.attempts) >= self.limits.max_attempts:
            raise SpendLimitReached("attempt_limit")
        if self.committed_usd() + worst > self.limits.max_usd:
            raise SpendLimitReached("cost_limit")
        self.attempts.append({"attempt": len(self.attempts) + 1, "purpose": purpose, "trace_id": trace_id, "model": model,
                              "max_tokens": max_tokens, "input_bound": input_bound, "worst_case_usd": str(worst),
                              "status": "reserved", "reserved_at": _now()})
        self._save()
        return len(self.attempts) - 1

    def settle(self, index: int, *, status: str, input_tokens: int = 0, output_tokens: int = 0,
               http_status: int | None = None, provider_error_type: str | None = None,
               gateway_retry_count: str | None = None, duration_ms: int = 0, cache_read_tokens: int = 0,
               cache_write_tokens: int = 0) -> dict[str, Any]:
        attempt = self.attempts[index]
        estimated = self.prices.cost(input_tokens, output_tokens, cache_read_tokens, cache_write_tokens) if status == "ok" else None
        attempt.update(status=status, input_tokens=input_tokens, output_tokens=output_tokens, http_status=http_status,
                       cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens,
                       provider_error_type=provider_error_type, gateway_retry_count=gateway_retry_count,
                       duration_ms=duration_ms, settled_at=_now(),
                       estimated_usd=str(estimated.quantize(Decimal("0.000001"))) if estimated is not None else None,
                       conservative_usd=str((estimated * GEO_MULTIPLIER).quantize(Decimal("0.000001")))
                       if estimated is not None else None)
        if gateway_retry_count not in (None, "0"):
            self.locked = "gateway_retries_detected"
        if status == "ok" and input_tokens > attempt["input_bound"]:
            self.locked = "input_bound_exceeded"
        self._save()
        return attempt

    def summary(self) -> dict[str, Any]:
        return {"limits": {"max_attempts": self.limits.max_attempts, "max_usd": str(self.limits.max_usd)},
                "prices": self.prices.model_dump(mode="json"), "attempts_used": len(self.attempts),
                "estimated_usd": str(self.estimated_usd()), "committed_usd": str(self.committed_usd()),
                "locked": self.locked}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**self.summary(), "attempts": self.attempts}
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


# ------------------------------------------------------------------------------------------------------ client
class ClaudeOff:
    """The Claude client of an app started without --live: every call stops before any request, so nothing is spent.
    Features that never call Claude (project watch, monitoring, tender checks, lesson cards) work as usual."""

    model = "claude-off"
    timeout_seconds = 60.0

    def output_cap(self, request: ClaudeRequest) -> int:
        return 0

    def complete(self, request: ClaudeRequest) -> ClaudeResponse:
        raise LlmError(LlmFailure.DISABLED)

    def stream(self, request: ClaudeRequest, on_text: Callable[[str], None]) -> ClaudeResponse:
        raise LlmError(LlmFailure.DISABLED)


class BudgetedClaudeClient(PortkeyClaudeClient):
    """The existing Portkey client with pre-call spend reservation, per-schema token caps and gateway-retry checks."""

    def __init__(self, settings: ClaudeSettings, ledger: SpendLedger, *, output_caps: Mapping[str, int],
                 tracer: OrchestratorTracer | None = None, transport: httpx.MockTransport | None = None, live_access=None):
        super().__init__(settings, transport=transport, live_access=live_access)
        self._ledger = ledger
        self._caps = dict(output_caps)
        self._tracer = tracer or OrchestratorTracer.disabled()
        self._gateway_retry_count: str | None = None
        self._http.event_hooks["response"].append(self._read_gateway_headers)

    def output_cap(self, request: ClaudeRequest) -> int:
        return min(self._caps.get(request.schema_name, self._settings.max_output_tokens), self._settings.max_output_tokens)

    def complete(self, request: ClaudeRequest) -> ClaudeResponse:
        return self._ledgered(request, lambda: super(BudgetedClaudeClient, self).complete(request))

    def stream(self, request: ClaudeRequest, on_text: Callable[[str], None]) -> ClaudeResponse:
        """A streamed plain-text reply, reserved and settled in the spend ledger exactly like `complete`."""
        return self._ledgered(request, lambda: super(BudgetedClaudeClient, self).stream(request, on_text))

    def _ledgered(self, request: ClaudeRequest, call: Callable[[], ClaudeResponse]) -> ClaudeResponse:
        cap = self.output_cap(request)
        index = self._ledger.reserve(purpose=request.schema_name, trace_id=request.trace_id, model=self.model,
                                     max_tokens=cap, input_bound=input_token_bound(request))
        worst = float(self._ledger.attempts[index]["worst_case_usd"])
        self._gateway_retry_count = None
        started = time.perf_counter()
        with self._tracer.span("orchestrator.llm_call", request_id=request.trace_id, model=model_slug(self.model),
                               attempt=index + 1, worst_case_usd=worst) as span:
            try:
                response = call()
            except LlmError as error:
                self._ledger.settle(index, status=error.failure.value, http_status=error.status_code,
                                    provider_error_type=error.provider_error_type,
                                    gateway_retry_count=self._gateway_retry_count, duration_ms=_ms(started))
                span(status="failed", error_code=error.failure.value, http_status=error.status_code,
                     provider_error_type=error.provider_error_type, gateway_retry_count=self._gateway_retry_count,
                     committed_usd=float(self._ledger.committed_usd()), attempts_used=len(self._ledger.attempts))
                raise
            except Exception:
                self._ledger.settle(index, status="client_error", duration_ms=_ms(started))
                raise
            attempt = self._ledger.settle(index, status="ok", input_tokens=response.input_tokens,
                                          output_tokens=response.output_tokens, gateway_retry_count=self._gateway_retry_count,
                                          duration_ms=response.duration_ms, cache_read_tokens=response.cache_read_tokens,
                                          cache_write_tokens=response.cache_write_tokens)
            span(status="ok", input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                 cache_read_tokens=response.cache_read_tokens, cache_write_tokens=response.cache_write_tokens,
                 cost_usd=float(attempt["estimated_usd"]), gateway_retry_count=self._gateway_retry_count,
                 committed_usd=float(self._ledger.committed_usd()), attempts_used=len(self._ledger.attempts),
                 duration_ms=response.duration_ms)
        return response

    def _body(self, request: ClaudeRequest) -> dict[str, Any]:
        body = super()._body(request)
        body["max_tokens"] = self.output_cap(request)
        return body

    def _read_gateway_headers(self, response: httpx.Response) -> None:
        value = response.headers.get(RETRY_HEADER)
        self._gateway_retry_count = value.strip()[:8] if value is not None else None


def _ms(started: float) -> int:
    return round(1000 * (time.perf_counter() - started))
