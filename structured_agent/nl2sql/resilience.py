"""Minimal runtime resilience controls for the first workflow (single process, in memory).

  * TokenBucketRateLimiter  per-key request rates (e.g. per principal: requests, LLM calls, Databricks executions)
  * ConcurrencyLimiter      bounded concurrent LLM / Databricks calls
  * CircuitBreaker          CLOSED -> OPEN -> HALF-OPEN -> CLOSED per upstream route
  * Deadline                total execution time of one user request

These are NOT sufficient for a multi-instance deployment: each process would keep its own counters and breaker
state. Production needs shared state (Redis), per docs/llm_resilience_architecture.md.
"""
from __future__ import annotations

import math
import threading
import time
from contextlib import contextmanager
from enum import Enum
from typing import Callable, Iterator

from structured_agent.nl2sql.settings import BreakerSettings

Clock = Callable[[], float]


class RateLimitExceeded(Exception):
    """Raised when a rate limit has no token left; carries the limit name and the wait in seconds."""
    def __init__(self, limit: str, retry_after_seconds: float):
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"rate limit '{limit}' exceeded; retry after {retry_after_seconds:.0f} s")


class CircuitOpenError(Exception):
    """Raised while a circuit breaker is open; carries the breaker name and the wait in seconds."""
    def __init__(self, breaker: str, retry_after_seconds: float):
        self.breaker = breaker
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"circuit '{breaker}' is open; retry after {retry_after_seconds:.0f} s")


class DeadlineExceeded(Exception):
    pass


# ---------------------------------------------------------------------------------------------------- rate limiting
class TokenBucketRateLimiter:
    """`capacity` requests per `period_seconds` per key, refilled continuously."""

    def __init__(self, name: str, capacity: int, period_seconds: float = 60.0, clock: Clock = time.monotonic):
        """Every key starts with a full bucket, refilled at `capacity / period_seconds` tokens per second."""
        self.name = name
        self._capacity = capacity
        self._refill_per_second = capacity / period_seconds
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}   # key -> (tokens, last refill time)
        self._lock = threading.Lock()

    def acquire(self, key: str) -> None:
        """Take one token for `key`, or raise RateLimitExceeded with the time until one is available."""
        with self._lock:
            now = self._clock()
            tokens, last = self._buckets.get(key, (float(self._capacity), now))
            tokens = min(self._capacity, tokens + (now - last) * self._refill_per_second)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                raise RateLimitExceeded(self.name, math.ceil((1 - tokens) / self._refill_per_second))
            self._buckets[key] = (tokens - 1, now)


class ConcurrencyLimiter:
    """Caps how many calls of one kind run at the same time; a caller waits at most `wait_seconds` for a slot."""

    def __init__(self, name: str, max_concurrent: int, wait_seconds: float):
        self.name = name
        self._semaphore = threading.BoundedSemaphore(max_concurrent)
        self._wait_seconds = wait_seconds

    @contextmanager
    def slot(self) -> Iterator[None]:
        """Hold one slot for the duration of the block; raise RateLimitExceeded if none frees up in time."""
        if not self._semaphore.acquire(timeout=self._wait_seconds):
            raise RateLimitExceeded(self.name, retry_after_seconds=1)
        try:
            yield
        finally:
            self._semaphore.release()


# --------------------------------------------------------------------------------------------------- circuit breaker
class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


TransitionListener = Callable[[str, CircuitState, CircuitState], None]


class CircuitBreaker:
    """Counts eligible transient failures of one upstream route.

    Callers use: before_call() -> upstream call -> exactly one of record_success() / record_failure() /
    record_ignored(). record_ignored() is for outcomes that say nothing about upstream health (client errors,
    policy denials, invalid output); it only frees a HALF-OPEN probe slot.
    """

    def __init__(self, name: str, settings: BreakerSettings, clock: Clock = time.monotonic,
                 on_transition: TransitionListener | None = None):
        """Start closed with no recorded failures; `on_transition` is told about every state change."""
        self.name = name
        self._settings = settings
        self._clock = clock
        self._on_transition = on_transition
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failure_times: list[float] = []
        self._cooldown = settings.cooldown_seconds
        self._opened_at = 0.0
        self._probes_in_flight = 0
        self._probe_successes = 0

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._refresh_open_state()
            return self._state

    def before_call(self) -> None:
        """Ask to make one upstream call; raises CircuitOpenError while the circuit is open or probes are full."""
        with self._lock:
            self._refresh_open_state()
            if self._state == CircuitState.OPEN:
                raise CircuitOpenError(self.name, self._remaining_cooldown())
            if self._state == CircuitState.HALF_OPEN:
                if self._probes_in_flight >= self._settings.half_open_max_probes:
                    raise CircuitOpenError(self.name, retry_after_seconds=1)
                self._probes_in_flight += 1

    def record_success(self) -> None:
        """Record a successful call; enough successes in HALF-OPEN close the circuit again."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._probes_in_flight -= 1
                self._probe_successes += 1
                if self._probe_successes >= self._settings.success_threshold:
                    self._cooldown = self._settings.cooldown_seconds
                    self._failure_times.clear()
                    self._transition(CircuitState.CLOSED)

    def record_failure(self) -> None:
        """Record a transient failure; a failed probe (cooldown doubled) or too many in the window opens it."""
        with self._lock:
            now = self._clock()
            if self._state == CircuitState.HALF_OPEN:
                self._probes_in_flight -= 1
                self._cooldown = min(self._cooldown * 2, self._settings.max_cooldown_seconds)
                self._open(now)
                return
            window_start = now - self._settings.window_seconds
            self._failure_times = [t for t in self._failure_times if t >= window_start] + [now]
            if len(self._failure_times) >= self._settings.failure_threshold:
                self._open(now)

    def record_ignored(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._probes_in_flight -= 1

    # ------------------------------------------------------------------------------------------------- internals
    def _refresh_open_state(self) -> None:
        """Move OPEN to HALF-OPEN once the cooldown has passed (checked lazily on each access)."""
        if self._state == CircuitState.OPEN and self._remaining_cooldown() <= 0:
            self._probes_in_flight = 0
            self._probe_successes = 0
            self._transition(CircuitState.HALF_OPEN)

    def _remaining_cooldown(self) -> float:
        return max(0.0, self._opened_at + self._cooldown - self._clock())

    def _open(self, now: float) -> None:
        self._opened_at = now
        self._failure_times.clear()
        self._transition(CircuitState.OPEN)

    def _transition(self, new_state: CircuitState) -> None:
        """Move to `new_state` and notify the listener when the state really changed."""
        old_state, self._state = self._state, new_state
        # The listener (tracing, audit) runs while the lock is held, so it must be quick and must not call back.
        if self._on_transition and old_state != new_state:
            self._on_transition(self.name, old_state, new_state)


# --------------------------------------------------------------------------------------------------------- deadline
class Deadline:
    """Time budget for one user request, shared by every stage of the workflow."""

    def __init__(self, total_seconds: float, clock: Clock = time.monotonic):
        self._clock = clock
        self._expires_at = clock() + total_seconds

    def remaining(self) -> float:
        return max(0.0, self._expires_at - self._clock())

    def check(self, minimum_seconds: float = 0.0) -> float:
        """Remaining seconds; raise DeadlineExceeded when less than `minimum_seconds` are left."""
        remaining = self.remaining()
        if remaining <= minimum_seconds:
            raise DeadlineExceeded(f"request deadline reached ({remaining:.1f} s left)")
        return remaining
