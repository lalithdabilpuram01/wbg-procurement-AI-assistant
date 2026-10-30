"""Deterministic execution controller: the only component that validates plans and runs tasks.

No LLM output is trusted here. A plan runs only if every check passes; a task runs only through a controller ticket,
after a second permission and budget check, behind its worker's circuit breaker, within its deadline, with bounded
retries for transient infrastructure errors only (never for LLM calls, which are not retried, or for SQL execution,
which runs at most once per approval). Execution is synchronous; `ready()` returns batches of up to `max_parallel`
tasks so that bounded parallel execution can be added later without changing the graph.
"""
from __future__ import annotations

import contextvars
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from structured_agent.nl2sql.resilience import BreakerSettings, CircuitBreaker, CircuitOpenError, CircuitState

from .authorization import OPERATIONS
from .budget import PriceTable, check_budget, priced
from .contracts import (USABLE_TASK_STATES, AgentResult, AgentTask, ErrorCategory, EvidenceRecord, ExecutionError,
                        InvestigationPlan, InvestigationRequest, TaskState, Usage, WorkerName, evidence_spec, new_id)
from .observability import OrchestratorTracer
from .workers.base import TicketAuthority, TicketError, TransientWorkerError, Worker, WorkerContext

LLM_OPERATIONS = frozenset({"answer_data_question", "answer_document_question"})
FAILED_STATES = frozenset({TaskState.FAILED, TaskState.BLOCKED, TaskState.CANCELLED})
DEFAULT_BREAKER = BreakerSettings(failure_threshold=3, window_seconds=60.0, cooldown_seconds=30.0, max_cooldown_seconds=300.0)


class TaskTimeout(Exception):
    pass


@dataclass(frozen=True)
class RetryBackoff:
    base_seconds: float = 0.25
    factor: float = 2.0
    max_seconds: float = 2.0

    def delay(self, attempt: int) -> float:
        return min(self.max_seconds, self.base_seconds * self.factor ** (attempt - 1))


@dataclass
class TaskRun:
    """Everything one task execution adds to the state (returned as an update by the graph node)."""

    task_id: str
    final_state: TaskState
    results: list[AgentResult] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    errors: list[ExecutionError] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    elapsed_ms: int = 0
    attempts: int = 0
    retries: int = 0
    clarification_options: tuple[str, ...] = ()


class ExecutionController:
    def __init__(self, workers: Mapping[WorkerName, Worker], authority: TicketAuthority, *, tracer: OrchestratorTracer | None = None,
                 breaker_settings: BreakerSettings = DEFAULT_BREAKER, backoff: RetryBackoff = RetryBackoff(),
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 prices: PriceTable | None = None, max_threads: int = 4):
        self._workers = dict(workers)
        self._authority = authority
        self._tracer = tracer or OrchestratorTracer.disabled()
        self._breakers = {name: CircuitBreaker(f"worker.{name.value}", breaker_settings, clock, self._on_transition)
                          for name in self._workers}               # every breaker reports its transitions
        self._backoff, self._clock, self._sleep, self._prices = backoff, clock, sleep, prices
        self._pool = ThreadPoolExecutor(max_workers=max_threads, thread_name_prefix="orchestrator-task")

    def breaker(self, worker: WorkerName) -> CircuitBreaker:
        return self._breakers[worker]

    def _on_transition(self, name: str, old: CircuitState, new: CircuitState) -> None:
        self._tracer.event("orchestrator.circuit_breaker_transition", level="warn", breaker=name, breaker_from=old.value,
                           breaker_to=new.value)

    # ------------------------------------------------------------------------------------------- plan validation
    def validate_plan(self, plan: InvestigationPlan, request: InvestigationRequest) -> list[ExecutionError]:
        budget, auth = request.budget, request.authorization
        problems: list[tuple[str, str, str | None]] = []
        ids = [t.task_id for t in plan.tasks]
        if plan.investigation_id != request.investigation_id:
            problems.append(("plan_investigation_mismatch", "The plan belongs to another investigation.", None))
        if len(ids) != len(set(ids)):
            problems.append(("duplicate_task_id", "Task identifiers must be unique.", None))
        if len(ids) > budget.max_tasks:
            problems.append(("too_many_tasks", f"The plan has more than {budget.max_tasks} tasks.", None))
        known = set(ids)
        for task in plan.tasks:
            problems += [(c, m, task.task_id) for c, m in self._task_problems(task, auth, budget, known)]
        cycle = _cycle(plan)
        if cycle:
            problems.append(("dependency_cycle", "The task dependencies contain a cycle.", cycle))
        elif _depth(plan) > budget.max_depth:
            problems.append(("dependency_too_deep", f"The dependency chain is deeper than {budget.max_depth}.", None))
        missing = [r for r in plan.completion.required_task_ids if r not in known]
        if missing:
            problems.append(("unknown_required_task", "The completion condition names an unknown task.", missing[0]))
        if plan.completion.min_verified_evidence and not any(
                (spec := evidence_spec(t.operation, t.requires_execution)) and spec.verifiable for t in plan.tasks):
            problems.append(("verified_evidence_not_producible", "No task can produce the verified evidence the plan needs.", None))
        return [ExecutionError(error_id=new_id("err"), investigation_id=request.investigation_id, task_id=task_id,
                               node="validate_plan", category=ErrorCategory.AUTHORIZATION if code in AUTH_CODES else ErrorCategory.VALIDATION,
                               code=code, safe_message=message) for code, message, task_id in problems]

    def _task_problems(self, task: AgentTask, auth, budget, known: set[str]) -> list[tuple[str, str]]:
        problems = []
        if task.worker not in self._workers or task.operation not in OPERATIONS.get(task.worker.value, {}):
            problems.append(("unknown_operation", "The worker or operation does not exist."))
        elif not auth.allows(task.worker.value, task.operation):
            problems.append(("operation_not_authorized", "The operation is not authorized for this investigation."))
        if task.requires_execution and (task.operation != "answer_data_question"
                                        or not auth.allows(task.worker.value, task.operation, requires_execution=True)):
            problems.append(("execution_not_authorized", "SQL execution is not authorized for this task."))
        for dependency in task.depends_on:
            if dependency == task.task_id:
                problems.append(("self_dependency", "A task cannot depend on itself."))
            elif dependency not in known:
                problems.append(("unknown_dependency", "A task depends on an unknown task."))
        if task.max_attempts > budget.max_attempts_per_task:
            problems.append(("too_many_attempts", "The task allows more attempts than the budget."))
        if task.deadline_seconds and task.deadline_seconds > budget.task_deadline_seconds:
            problems.append(("task_deadline_too_long", "The task deadline exceeds the budget."))
        return problems + self._evidence_problems(task)

    @staticmethod
    def _evidence_problems(task: AgentTask) -> list[tuple[str, str]]:
        """A requirement must name kinds the operation returns (given its execution mode) and be verifiable if demanded."""
        spec = evidence_spec(task.operation, task.requires_execution)
        for requirement in task.evidence_requirements:
            if spec is None or not set(requirement.accepted_kinds) <= set(spec.kinds):
                return [("evidence_not_producible", "The task asks for evidence its operation cannot produce.")]
            if requirement.must_be_verified and not spec.verifiable:
                return [("evidence_not_verifiable", "The task demands verified evidence its operation cannot verify.")]
        return []

    # -------------------------------------------------------------------------------------------- scheduling
    @staticmethod
    def cancellations(plan: InvestigationPlan, states: Mapping[str, str]) -> dict[str, str]:
        """Pending tasks whose dependency ended unusable are cancelled (transitively over later calls)."""
        out = {}
        for task in plan.tasks:
            if TaskState(states.get(task.task_id, "pending")) != TaskState.PENDING:
                continue
            if any(TaskState(states.get(d, "pending")) in FAILED_STATES for d in task.depends_on):
                out[task.task_id] = TaskState.CANCELLED.value
        return out

    @staticmethod
    def ready(plan: InvestigationPlan, states: Mapping[str, str], limit: int) -> list[str]:
        ready = [t.task_id for t in plan.tasks if TaskState(states.get(t.task_id, "pending")) == TaskState.PENDING
                 and all(TaskState(states.get(d, "pending")) in USABLE_TASK_STATES for d in t.depends_on)]
        return ready[:limit]

    # ---------------------------------------------------------------------------------------------- execution
    def execute(self, task: AgentTask, request: InvestigationRequest, state: Mapping[str, Any], *,
                execution_allowed: bool, memory_block: str | None = None) -> TaskRun:
        reused = _previous_result(task, state)
        if reused is not None:
            return TaskRun(task.task_id, TaskState.SKIPPED, results=[AgentResult(
                task_id=task.task_id, attempt=1, worker=task.worker, idempotency_key=task.idempotency_key, status=TaskState.SKIPPED,
                evidence_ids=tuple(reused["evidence_ids"]), notes=(f"reused:{reused['task_id']}",))])
        refusal = self._refusal(task, request, state, execution_allowed)
        if refusal is not None:
            final = TaskState.FAILED
            return TaskRun(task.task_id, final, errors=[refusal], results=[self._result(task, 1, final, refusal)])
        return self._attempts(task, request, state, execution_allowed, memory_block)

    def _refusal(self, task, request, state, execution_allowed) -> ExecutionError | None:
        auth = request.authorization
        if not auth.allows(task.worker.value, task.operation, task.requires_execution):
            return _error(request, task, ErrorCategory.AUTHORIZATION, "operation_not_authorized",
                          "The operation is not authorized for this investigation.")
        return check_budget(state.get("usage", {}), request.budget, investigation_id=request.investigation_id, node="execute",
                            task_id=task.task_id, needs_llm=task.operation in LLM_OPERATIONS,
                            needs_execution=task.requires_execution and execution_allowed)

    def _attempts(self, task, request, state, execution_allowed, memory_block) -> TaskRun:
        run = TaskRun(task.task_id, TaskState.FAILED)
        breaker, worker = self._breakers[task.worker], self._workers[task.worker]
        budget = request.budget
        used_ms = state.get("usage", {}).get("elapsed_ms", 0)
        max_attempts = min(task.max_attempts, budget.max_attempts_per_task)
        upstream = _upstream_evidence(task, state)
        for attempt in range(1, max_attempts + 1):
            remaining = min(task.deadline_seconds or budget.task_deadline_seconds,
                            budget.deadline_seconds - (used_ms + run.elapsed_ms) / 1000)
            if remaining <= 0:
                error = _error(request, task, ErrorCategory.DEADLINE, "deadline", "The investigation ran out of time.")
                return self._finish(run, task, attempt, TaskState.FAILED, error)
            try:
                breaker.before_call()
            except CircuitOpenError as open_error:
                error = _error(request, task, ErrorCategory.CIRCUIT_OPEN, "circuit_open",
                               "The worker is temporarily unavailable (circuit breaker open).", retry_after=open_error.retry_after_seconds)
                return self._finish(run, task, attempt, TaskState.FAILED, error)
            context = WorkerContext(ticket=self._authority.issue(task, attempt), authorization=request.authorization,
                                    request_id=request.request_id, rag_release=request.rag_release, remaining_seconds=remaining,
                                    execution_allowed=execution_allowed, upstream_evidence=upstream, memory_block=memory_block,
                                    package=request.package, planning=request.planning, tender=request.tender)
            started = self._clock()
            run.attempts = attempt
            with self._tracer.span("orchestrator.task_attempt", investigation_id=request.investigation_id, plan_id=task.plan_id,
                                   conversation_id=request.conversation_thread_id, task_id=task.task_id,
                                   worker=task.worker.value, operation=task.operation, attempt=attempt,
                                   max_attempts=max_attempts) as span:
                try:
                    output = self._call(worker, task, context, remaining)
                except (TransientWorkerError, TaskTimeout) as transient:
                    breaker.record_failure()
                    run.elapsed_ms += round(1000 * (self._clock() - started))
                    code = transient.code if isinstance(transient, TransientWorkerError) else "task_timeout"
                    category = ErrorCategory.TIMEOUT if "timeout" in code else ErrorCategory.EXECUTION
                    error = _error(request, task, category, code, "A transient error interrupted the task.", retryable=True)
                    span(execution_state="retryable_failure", error_code=code)
                    if attempt < max_attempts:
                        delay = self._backoff.delay(attempt)
                        self._tracer.event("orchestrator.task_retry", investigation_id=request.investigation_id, plan_id=task.plan_id,
                                           conversation_id=request.conversation_thread_id, task_id=task.task_id,
                                           attempt=attempt, retry_delay_ms=round(delay * 1000), error_code=code)
                        run.results.append(self._result(task, attempt, TaskState.FAILED, error))
                        run.errors.append(error)
                        run.retries += 1
                        self._sleep(delay)
                        continue
                    return self._finish(run, task, attempt, TaskState.FAILED, error)
                except TicketError:
                    raise                                       # a programming error, never a task outcome
                except Exception as unexpected:                 # unknown worker failure: the task fails safely
                    breaker.record_failure()
                    run.elapsed_ms += round(1000 * (self._clock() - started))
                    span(error=unexpected, execution_state="failed")   # traceback to the log, location on the span
                    error = _error(request, task, ErrorCategory.INTERNAL, type(unexpected).__name__[:80],
                                   "The task failed unexpectedly.")
                    return self._finish(run, task, attempt, TaskState.FAILED, error)
                run.elapsed_ms += round(1000 * (self._clock() - started))
                if output.error is not None and output.error.category in (ErrorCategory.EXECUTION, ErrorCategory.TIMEOUT):
                    breaker.record_failure()
                elif output.error is not None:
                    breaker.record_ignored()
                else:
                    breaker.record_success()
                span(execution_state=output.status.value, evidence_count=len(output.evidence),
                     error_code=output.error.code if output.error else None)
                run.evidence.extend(output.evidence)
                run.usage = _add(run.usage, priced(output.usage, self._prices))
                run.clarification_options = output.clarification_options
                notes = output.notes + tuple(f"option:{o[:100]}" for o in output.clarification_options[:5])
                return self._finish(run, task, attempt, output.status, output.error, output.evidence, notes)
        return run

    def _call(self, worker: Worker, task: AgentTask, context: WorkerContext, seconds: float):
        """Run the worker in the task pool with a deadline. The caller's context (trace span and log ids) is copied
        into the pool thread, so the worker's span `worker.<name>` and everything it calls (Claude, MCP tools,
        document search) nest under this task attempt."""
        def traced_run() -> Any:
            with self._tracer.span(f"worker.{task.worker.value}", worker=task.worker.value, operation=task.operation,
                                   task_id=task.task_id):
                return worker.run(task, context)
        future = self._pool.submit(contextvars.copy_context().run, traced_run)
        try:
            return future.result(timeout=seconds)
        except FutureTimeout:
            context.cancel_event.set()           # cooperative cancellation; the thread is not killed
            raise TaskTimeout() from None

    def _finish(self, run: TaskRun, task: AgentTask, attempt: int, state: TaskState, error: ExecutionError | None,
                evidence=(), notes=()) -> TaskRun:
        run.final_state = state
        if error is not None:
            run.errors.append(error)
        run.results.append(self._result(task, attempt, state, error, evidence, notes, run.usage, run.elapsed_ms))
        return run

    @staticmethod
    def _result(task, attempt, state, error, evidence=(), notes=(), usage: Usage | None = None, elapsed_ms: int = 0) -> AgentResult:
        return AgentResult(task_id=task.task_id, attempt=attempt, worker=task.worker, idempotency_key=task.idempotency_key,
                           status=state, evidence_ids=tuple(e.evidence_id for e in evidence),
                           error_id=error.error_id if error else None, usage=usage or Usage(), duration_ms=elapsed_ms,
                           notes=tuple(notes))

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


# ------------------------------------------------------------------------------------------------------ helpers
AUTH_CODES = {"operation_not_authorized", "execution_not_authorized"}


def _cycle(plan: InvestigationPlan) -> str | None:
    """A task id on a dependency cycle, or None (Kahn's algorithm over known edges)."""
    ids = {t.task_id for t in plan.tasks}
    indegree = {t.task_id: sum(1 for d in t.depends_on if d in ids) for t in plan.tasks}
    children: dict[str, list[str]] = {i: [] for i in ids}
    for task in plan.tasks:
        for dependency in task.depends_on:
            if dependency in ids:
                children[dependency].append(task.task_id)
    queue = [i for i, n in indegree.items() if n == 0]
    seen = 0
    while queue:
        node = queue.pop()
        seen += 1
        for child in children[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    return None if seen == len(ids) else next(i for i, n in indegree.items() if n > 0)


def _depth(plan: InvestigationPlan) -> int:
    by_id = {t.task_id: t for t in plan.tasks}
    memo: dict[str, int] = {}

    def depth(task_id: str) -> int:
        if task_id not in memo:
            deps = [d for d in by_id[task_id].depends_on if d in by_id]
            memo[task_id] = 1 + max((depth(d) for d in deps), default=0)
        return memo[task_id]
    return max(depth(t) for t in by_id)


def _previous_result(task: AgentTask, state: Mapping[str, Any]) -> dict | None:
    """An earlier usable result with the same idempotency key (e.g. a task repeated by a replan)."""
    for result in reversed(state.get("results", [])):
        if result["idempotency_key"] == task.idempotency_key and result["task_id"] != task.task_id \
                and TaskState(result["status"]) in USABLE_TASK_STATES:
            return result
    return None


def _upstream_evidence(task: AgentTask, state: Mapping[str, Any]) -> tuple[EvidenceRecord, ...]:
    ids = {e for r in state.get("results", []) if r["task_id"] in task.depends_on for e in r["evidence_ids"]}
    return tuple(EvidenceRecord.from_state(e) for e in state.get("evidence", [])
                 if e["evidence_id"] in ids and e["validation_status"] == "verified")


def _add(a: Usage, b: Usage) -> Usage:
    cost = None if a.cost_usd is None and b.cost_usd is None else (a.cost_usd or 0) + (b.cost_usd or 0)
    return Usage(llm_calls=a.llm_calls + b.llm_calls, input_tokens=a.input_tokens + b.input_tokens,
                 output_tokens=a.output_tokens + b.output_tokens, executions=a.executions + b.executions, cost_usd=cost)


def _error(request: InvestigationRequest, task: AgentTask, category: ErrorCategory, code: str, message: str, *,
           retryable: bool = False, retry_after: float | None = None) -> ExecutionError:
    return ExecutionError(error_id=new_id("err"), investigation_id=request.investigation_id, task_id=task.task_id, node="execute",
                          category=category, code=code[:80], retryable=retryable, retry_after_seconds=retry_after,
                          safe_message=message)
