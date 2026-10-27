"""Per-user request limits for the endpoints that start work (messages, assessments, briefs, approvals, recovery).

Sliding windows in memory (one API process on this laptop): at most `per_minute` requests in any 60 seconds and
`per_day` in any 24 hours per user. Spending is limited separately by the Claude ledger and each investigation's budget.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

MINUTE, DAY = 60.0, 86_400.0


@dataclass(frozen=True)
class Refusal:
    limit: str                 # "per_minute" | "per_day"
    allowed: int
    retry_after_seconds: int


class RateLimiter:
    def __init__(self, per_minute: int = 6, per_day: int = 200, clock: Callable[[], float] = time.monotonic):
        self.per_minute, self.per_day, self._clock = per_minute, per_day, clock
        self._seen: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "RateLimiter":
        return cls(int(os.environ.get("WBG_RATE_PER_MINUTE", "6")), int(os.environ.get("WBG_RATE_PER_DAY", "200")))

    def check(self, user_key: str) -> Refusal | None:
        """Record the request when it is within the limits; otherwise return why and when to retry."""
        now = self._clock()
        with self._lock:
            times = self._seen.setdefault(user_key, deque())
            while times and now - times[0] >= DAY:
                times.popleft()
            last_minute = [t for t in times if now - t < MINUTE]
            if len(last_minute) >= self.per_minute:
                return Refusal("per_minute", self.per_minute, max(1, round(MINUTE - (now - last_minute[0]))))
            if len(times) >= self.per_day:
                return Refusal("per_day", self.per_day, max(1, round(DAY - (now - times[0]))))
            times.append(now)
            return None
