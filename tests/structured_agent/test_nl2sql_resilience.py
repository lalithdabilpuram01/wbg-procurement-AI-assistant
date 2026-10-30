"""Rate limiting, concurrency limits, circuit-breaker transitions and deadlines (fake clock, no I/O)."""
from __future__ import annotations

import threading

import pytest

from structured_agent.nl2sql.resilience import (CircuitBreaker, CircuitOpenError, CircuitState, ConcurrencyLimiter, Deadline,
                                                DeadlineExceeded, RateLimitExceeded, TokenBucketRateLimiter)
from structured_agent.nl2sql.settings import BreakerSettings


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def breaker(clock: FakeClock, transitions: list | None = None) -> CircuitBreaker:
    settings = BreakerSettings(failure_threshold=3, window_seconds=60, cooldown_seconds=30, max_cooldown_seconds=100,
                               half_open_max_probes=1, success_threshold=2)
    return CircuitBreaker("llm.test", settings, clock, lambda name, old, new: transitions.append((old.value, new.value))
                          if transitions is not None else None)


# ----------------------------------------------------------------------------------------------------- rate limits
def test_token_bucket_rejects_with_retry_after_and_refills():
    clock = FakeClock()
    limiter = TokenBucketRateLimiter("requests", capacity=2, period_seconds=60, clock=clock)
    limiter.acquire("analyst")
    limiter.acquire("analyst")
    with pytest.raises(RateLimitExceeded) as raised:
        limiter.acquire("analyst")
    assert raised.value.limit == "requests" and raised.value.retry_after_seconds == 30
    limiter.acquire("steward")                       # keys are independent
    clock.advance(30)
    limiter.acquire("analyst")                       # one token refilled


def test_concurrency_limiter_bounds_parallel_calls():
    limiter = ConcurrencyLimiter("llm_concurrency", max_concurrent=1, wait_seconds=0.05)
    holding, release = threading.Event(), threading.Event()

    def hold_slot():
        with limiter.slot():
            holding.set()
            release.wait(2)
    worker = threading.Thread(target=hold_slot)
    worker.start()
    holding.wait(2)
    with pytest.raises(RateLimitExceeded):
        with limiter.slot():
            pass
    release.set()
    worker.join()
    with limiter.slot():                             # free again
        pass


# ------------------------------------------------------------------------------------------------- circuit breaker
def test_breaker_opens_after_threshold_and_fails_fast():
    clock, transitions = FakeClock(), []
    cb = breaker(clock, transitions)
    for _ in range(3):
        cb.before_call()
        cb.record_failure()
    assert cb.state == CircuitState.OPEN and transitions == [("closed", "open")]
    with pytest.raises(CircuitOpenError) as raised:
        cb.before_call()
    assert raised.value.retry_after_seconds == 30


def test_failures_outside_the_window_do_not_open_the_breaker():
    clock = FakeClock()
    cb = breaker(clock)
    for _ in range(2):
        cb.record_failure()
    clock.advance(61)
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED


def test_ignored_outcomes_never_count():
    cb = breaker(FakeClock())
    for _ in range(10):
        cb.before_call()
        cb.record_ignored()
    assert cb.state == CircuitState.CLOSED


def test_half_open_allows_bounded_probes_then_closes():
    clock, transitions = FakeClock(), []
    cb = breaker(clock, transitions)
    for _ in range(3):
        cb.record_failure()
    clock.advance(30)
    assert cb.state == CircuitState.HALF_OPEN
    cb.before_call()                                  # the single allowed probe
    with pytest.raises(CircuitOpenError):
        cb.before_call()                              # a second concurrent probe is refused
    cb.record_success()
    cb.before_call()
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert transitions == [("closed", "open"), ("open", "half_open"), ("half_open", "closed")]


def test_probe_failure_reopens_with_a_doubled_capped_cooldown():
    clock = FakeClock()
    cb = breaker(clock)
    for _ in range(3):
        cb.record_failure()
    for expected_cooldown in (60, 100):               # 30 -> 60 -> capped at 100
        clock.advance(1000)
        cb.before_call()
        cb.record_failure()
        with pytest.raises(CircuitOpenError) as raised:
            cb.before_call()
        assert raised.value.retry_after_seconds == expected_cooldown


# -------------------------------------------------------------------------------------------------------- deadline
def test_deadline_reports_remaining_time_and_expires():
    clock = FakeClock()
    deadline = Deadline(10, clock)
    assert deadline.check(1.0) == 10
    clock.advance(9.5)
    with pytest.raises(DeadlineExceeded):
        deadline.check(1.0)
