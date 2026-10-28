"""Worker contract, controller-issued tickets and the request-scoped result cache.

Workers run only with a ticket that the execution controller issued for exactly this task and attempt: a worker
called directly (bypassing the controller) refuses to run. A worker never raises into the graph for expected
outcomes; it returns a WorkerOutput. Transient infrastructure problems raise `TransientWorkerError` so the controller
can apply its bounded retry policy.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..authorization import AppAuthorizationContext
from ..contracts import AgentTask, EvidenceRecord, ExecutionError, TaskState, Usage, WorkerName


class TicketError(PermissionError):
    """A worker was called without a valid controller ticket."""


class TransientWorkerError(Exception):
    """Retryable infrastructure failure (connection reset, read-only tool timeout). Message must be content-free."""

    def __init__(self, code: str, retry_after_seconds: float | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class WorkerTicket:
    investigation_id: str
    task_id: str
    attempt: int
    idempotency_key: str
    signature: str


class TicketAuthority:
    """Issues and verifies HMAC tickets with a per-process secret (never stored or logged)."""

    def __init__(self) -> None:
        self._secret = secrets.token_bytes(32)

    def _sign(self, investigation_id: str, task_id: str, attempt: int, key: str) -> str:
        message = f"{investigation_id}\x00{task_id}\x00{attempt}\x00{key}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def issue(self, task: AgentTask, attempt: int) -> WorkerTicket:
        return WorkerTicket(task.investigation_id, task.task_id, attempt, task.idempotency_key,
                            self._sign(task.investigation_id, task.task_id, attempt, task.idempotency_key))

    def verify(self, ticket: WorkerTicket | None, task: AgentTask) -> None:
        if ticket is None or (ticket.task_id, ticket.investigation_id, ticket.idempotency_key) != \
                (task.task_id, task.investigation_id, task.idempotency_key):
            raise TicketError("worker called without a controller ticket for this task")
        expected = self._sign(task.investigation_id, task.task_id, ticket.attempt, task.idempotency_key)
        if not hmac.compare_digest(expected, ticket.signature):
            raise TicketError("invalid controller ticket")


class ResultCache:
    """Request-scoped rows of structured results (never checkpointed, never logged). Entries expire."""

    def __init__(self, ttl_seconds: float = 900.0, clock=time.monotonic):
        self._ttl, self._clock = ttl_seconds, clock
        self._items: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def put(self, key: str, rows: list[dict[str, Any]]) -> str:
        with self._lock:
            self._items[key] = (self._clock() + self._ttl, rows)
        return key

    def get(self, key: str) -> list[dict[str, Any]] | None:
        with self._lock:
            entry = self._items.get(key)
            if entry is None or entry[0] < self._clock():
                self._items.pop(key, None)
                return None
            return entry[1]


@dataclass
class WorkerContext:
    ticket: WorkerTicket
    authorization: AppAuthorizationContext
    request_id: str
    rag_release: str
    remaining_seconds: float
    execution_approved: bool = False                       # set only by the controller after HITL approval
    upstream_evidence: tuple[EvidenceRecord, ...] = ()     # verified evidence of the task's dependencies
    memory_block: str | None = None                        # untrusted conversation memory (never in state)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    package: Any = None                                    # PackageSpec of a package assessment (from the request)
    planning: Any = None                                   # PlanningSpec of a Planning Brief (from the request)
    tender: Any = None                                     # TenderSpec of a tender-design check (from the request)


@dataclass(frozen=True)
class WorkerOutput:
    status: TaskState
    evidence: tuple[EvidenceRecord, ...] = ()
    usage: Usage = Usage()
    error: ExecutionError | None = None
    notes: tuple[str, ...] = ()
    clarification_options: tuple[str, ...] = ()


class Worker(Protocol):
    name: WorkerName
    operations: frozenset[str]

    def run(self, task: AgentTask, context: WorkerContext) -> WorkerOutput: ...
