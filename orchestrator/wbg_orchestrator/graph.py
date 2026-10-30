"""The LangGraph investigation workflow: supervisor, planner, execution controller, evidence verification, response.

    START -> intake -> supervise -+-(fast path: plan built without an LLM)----------------+
                                  +-(complex)-> plan <-------------------------+          |
                                  +-(clarify / refuse) -> finalize             |          v
                                               plan -> validate_plan --(invalid, replan left)
                                                         validate_plan -> dispatch       (no human review: read-only work)
                       dispatch -> execute -> dispatch ... -> verify -> (replan if allowed) | respond -> finalize -> END
                       plan / validate_plan failures -> fail -> finalize

Nodes only return state updates (the reducers in state.py merge them). Workers are reachable only through the
execution controller, which every `execute` call goes through.
"""
from __future__ import annotations

import datetime as dt
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from structured_agent.nl2sql.llm_client import LlmError, LlmFailure
from structured_agent.nl2sql.resilience import CircuitBreaker, CircuitOpenError

from .budget import PriceTable, check_budget, priced, usage_update
from .contracts import (TERMINAL_TASK_STATES, ErrorCategory, EvidenceRecord, ExecutionError,
                        InvestigationPlan, InvestigationRequest, InvestigationResponse, InvestigationStatus, TaskState, WorkerName,
                        new_id, utc_now)
from .controller import ExecutionController
from .logs import get_logger
from .observability import AuditTrail, OrchestratorTracer, current_trace_id
from .planner import (Planner, PlannerError, PlanningContext, brief_plan, build_plan, fast_path_plan, monitor_plan,
                      package_plan, tender_plan)
from .response import DeterministicResponseGenerator, ResponseGenerator, validate_response
from .state import InvestigationState, add_counters
from .supervisor import Supervisor
from .verification import VerificationReport, ledger, verify

log = get_logger("graph")

REPLAN_CATEGORIES = {ErrorCategory.NO_EVIDENCE.value}
MALFORMED_OUTPUT = {"invalid_model_output", "model_output_truncated"}     # LlmFailure values of unusable replies
RESTATED_WORKERS = {WorkerName.MONITORING, WorkerName.LESSONS}
STEP_LABELS = {"intake": "Checking the question", "supervise": "Understanding the question", "plan": "Planning the steps",
               "validate_plan": "Checking the plan", "verify": "Checking the evidence", "respond": "Writing the answer"}
WORKER_LABELS = {WorkerName.STRUCTURED: "Querying procurement data", WorkerName.DOCUMENT: "Searching project documents",
                 WorkerName.PREDICTION: "Running the prediction models", WorkerName.PLANNING: "Finding similar projects and benchmarks",
                 WorkerName.MONITORING: "Checking milestones and early warnings", WorkerName.TENDER: "Checking the tender terms",
                 WorkerName.LESSONS: "Finding reviewed lessons"}          # their evidence is already the answer: restated, never sent to a model


@dataclass
class EngineDeps:
    controller: ExecutionController
    supervisor: Supervisor
    planner: Planner
    responder: ResponseGenerator
    guard: Any                                             # .check_input(text) -> GuardDecision (existing guardrails)
    llm_breaker: CircuitBreaker
    tracer: OrchestratorTracer = field(default_factory=OrchestratorTracer.disabled)
    audit: AuditTrail = field(default_factory=AuditTrail)
    now: Callable[[], dt.datetime] = utc_now
    memory: Callable[[str], str | None] = lambda investigation_id: None   # request-scoped memory (never in state)
    prices: PriceTable | None = None                                       # cost only from a configured price table
    cancel_requested: Callable[[str], bool] = lambda investigation_id: False   # user cancellation (checked between tasks)
    sleep: Callable[[float], None] = time.sleep                            # the pause before retrying a model call
    progress: Callable[[str, dict], None] = lambda investigation_id, event: None   # live steps for the streaming view


LLM_ATTEMPTS = 2                 # a transient provider failure (outage, 5xx, rate limit) is retried once
LLM_RETRY_SECONDS = 2.0          # pause before the retry (the provider's Retry-After when shorter)


CLAUDE_OFF = "Claude is off for this session. Start the app with `uv run wbg-api --live` for answers written by Claude."


def provider_message(error: LlmError, default: str) -> str:
    """What the user reads when a model call fails: Claude being switched off is said plainly."""
    return CLAUDE_OFF if error.failure == LlmFailure.DISABLED else default


def evidence_gaps(plan: InvestigationPlan, errors: list[dict], failed: tuple[str, ...]) -> list[str]:
    """Every failed or blocked task, by its question, with the reason it found nothing."""
    questions = {t.task_id: t.question for t in plan.tasks}
    reasons = {e["task_id"]: e["safe_message"] for e in errors if e.get("task_id") in failed}
    return [f"“{questions[t]}” ({reasons[t].rstrip('.')})" if t in reasons else f"“{questions[t]}”" for t in failed if t in questions]


def partial_gaps_opening(gaps: list[str]) -> str:
    return "No evidence found for part of the question: " + "; ".join(gaps) + ". What the evidence shows: "


def no_evidence_first(response: InvestigationResponse, plan: InvestigationPlan, errors: list[dict], failed: tuple[str, ...]) -> str:
    """The answer opens with what found no evidence."""
    gaps = evidence_gaps(plan, errors, failed)
    if not response.claims:
        return "No evidence found to answer this question." + (" Not found: " + "; ".join(gaps) + "." if gaps else "")
    if gaps:
        return (partial_gaps_opening(gaps) + response.message)[:4000]
    return response.message


def build_graph(deps: EngineDeps) -> StateGraph:
    nodes = _Nodes(deps)
    graph = StateGraph(InvestigationState)
    for name in ("intake", "supervise", "plan", "validate_plan", "dispatch", "execute", "verify", "respond", "fail", "finalize"):
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "intake")
    # every conditional edge lists where it can go, so LangGraph can draw the whole graph (get_graph().draw_mermaid())
    _route(graph, "intake", {"done": "finalize"}, "supervise")
    _route(graph, "supervise", {"planned": "validate_plan", "needs_plan": "plan", "done": "finalize"})
    graph.add_conditional_edges("plan", nodes.after_plan, ["validate_plan", "plan", "fail"])
    graph.add_conditional_edges("validate_plan", nodes.after_validation, ["dispatch", "plan", "fail"])
    _route(graph, "dispatch", {"executing": "execute"}, "verify")
    graph.add_edge("execute", "dispatch")
    graph.add_conditional_edges("verify", nodes.after_verify, ["respond", "plan"])
    for name in ("respond", "fail"):
        graph.add_edge(name, "finalize")
    graph.add_edge("finalize", END)
    return graph


def _route(graph: StateGraph, source: str, mapping: dict[str, str], default: str = "fail") -> None:
    """A conditional edge chosen by the phase the node set (`default` for any other phase)."""
    graph.add_conditional_edges(source, _by_phase(mapping, default), sorted({*mapping.values(), default}))


def _by_phase(mapping: dict[str, str], default: str | None = None) -> Callable[[InvestigationState], str]:
    def route(state: InvestigationState) -> str:
        return mapping.get(state.get("phase", ""), default or "fail")
    return route


class _Nodes:
    def __init__(self, deps: EngineDeps):
        self.d = deps

    # ------------------------------------------------------------------------------------------- helpers
    @staticmethod
    def request(state) -> InvestigationRequest:
        return InvestigationRequest.from_state(state["request"])

    @staticmethod
    def plan_of(state) -> InvestigationPlan:
        return InvestigationPlan.from_state(state["plan"])

    @staticmethod
    def _correlation(state) -> dict[str, Any]:
        """Ids that correlate every span and event of one investigation (the user only as a pseudonymous hash)."""
        request = state["request"]
        return {"investigation_id": request["investigation_id"], "conversation_id": request["conversation_thread_id"],
                "owner_hash": request["owner"]["user_hash"], "plan_id": (state.get("plan") or {}).get("plan_id")}

    @contextmanager
    def _span(self, node: str, state, **attributes):
        """The node's trace span, and its step in the live view (started, then done or failed, with its time)."""
        key = f"{node}:{attributes['task_id']}" if "task_id" in attributes else node
        label = WORKER_LABELS.get(WorkerName(attributes["worker"])) if "worker" in attributes else STEP_LABELS.get(node, node)
        self._progress(state, {"type": "step", "key": key, "label": label, "state": "running"})
        started, outcome = time.perf_counter(), "failed"
        try:
            with self.d.tracer.span(f"orchestrator.node.{node}", node=node, **{**self._correlation(state), **attributes}) as span:
                yield span
            outcome = "done"
        finally:
            self._progress(state, {"type": "step", "key": key, "label": label, "state": outcome,
                                   "ms": round(1000 * (time.perf_counter() - started))})

    def _progress(self, state, event: dict) -> None:
        try:
            self.d.progress(state["request"]["investigation_id"], event)
        except Exception:                                  # the live view must never break an investigation
            log.warning("live progress update failed", exc_info=True)

    def _event(self, name: str, state, **attributes) -> None:
        self.d.tracer.event(name, **{**self._correlation(state), **attributes})

    def _audit(self, event: str, state, **attributes) -> None:
        """One audit line: the event, its correlation ids and the Logfire trace it belongs to (metadata only)."""
        self.d.audit.record(event, request_id=state["request"]["request_id"], trace_id=current_trace_id(),
                            **{**self._correlation(state), **attributes})

    def _model_call(self, call: Callable[[], Any]) -> Any:
        """One planner or answer call behind the circuit breaker; a transient provider failure is retried once after a
        short pause. Raises CircuitOpenError, or the last LlmError (invalid output and auth errors are never retried)."""
        for attempt in range(1, LLM_ATTEMPTS + 1):
            self.d.llm_breaker.before_call()
            try:
                result = call()
            except LlmError as error:
                (self.d.llm_breaker.record_failure if error.is_transient else self.d.llm_breaker.record_ignored)()
                if not error.is_transient or attempt == LLM_ATTEMPTS:
                    raise
                self.d.sleep(min(error.retry_after_seconds or LLM_RETRY_SECONDS, LLM_RETRY_SECONDS))
                continue
            self.d.llm_breaker.record_success()
            return result
        raise AssertionError("unreachable")

    @staticmethod
    def _error(request: InvestigationRequest, node: str, category: ErrorCategory, code: str, message: str,
               task_id: str | None = None) -> ExecutionError:
        return ExecutionError(error_id=new_id("err"), investigation_id=request.investigation_id, task_id=task_id, node=node,
                              category=category, code=code[:80], safe_message=message)

    def _respond_now(self, request, status, message, *, errors=(), options=()) -> dict:
        response = InvestigationResponse(investigation_id=request.investigation_id, status=status, message=message,
                                         clarification_options=tuple(options), error_codes=tuple(e.code for e in errors))
        return {"phase": "done", "response": response.to_state(), "errors": [e.to_state() for e in errors]}

    # --------------------------------------------------------------------------------------------- intake
    def intake(self, state: InvestigationState) -> dict:
        request = self.request(state)
        with self._span("intake", state, mode=request.mode, rag_release=request.rag_release, question=request.question) as span:
            decision = self.d.guard.check_input(request.question)
            span(status="allowed" if decision.allowed else "blocked")
            self._audit("guard.input", state, status="allowed" if decision.allowed else "blocked",
                        guard_policy=getattr(decision, "policy_id", None),
                        guard_reason=str(getattr(decision.reason_category, "value", decision.reason_category or "")) or None)
            if not decision.allowed:
                error = self._error(request, "intake", ErrorCategory.GUARDRAIL, str(getattr(decision.reason_category, "value",
                                    decision.reason_category)), "The request was blocked by an input guardrail.")
                return self._respond_now(request, InvestigationStatus.BLOCKED,
                                         "This request cannot be processed because it was blocked by a safety check.", errors=[error])
        self._audit("investigation.started", state, mode=request.mode, rag_release=request.rag_release)
        return {"phase": "intake_ok", "replans": 0}

    # ------------------------------------------------------------------------------------------ supervise
    def supervise(self, state: InvestigationState) -> dict:
        request = self.request(state)
        with self._span("supervise", state) as span:
            decision = self.d.supervisor.decide(request)
            span(mode=decision.mode)
        update = {"supervisor": decision.model_dump(mode="json")}
        if decision.mode == "fast":
            plan = fast_path_plan(request, decision.worker, decision.operation, decision.requires_execution)
            self._audit("plan.created", state, plan_id=plan.plan_id, produced_by=plan.produced_by, mode=decision.mode,
                        task_count=len(plan.tasks))
            return {**update, "plan": plan.to_state(), "plans": [plan.to_state()], "phase": "planned"}
        if decision.mode in ("package", "brief", "monitor", "tender"):
            plan = {"package": package_plan, "brief": brief_plan, "monitor": monitor_plan, "tender": tender_plan}[decision.mode](request)
            self._audit("plan.created", state, plan_id=plan.plan_id, produced_by=plan.produced_by, mode=decision.mode,
                        task_count=len(plan.tasks))
            return {**update, "plan": plan.to_state(), "plans": [plan.to_state()], "phase": "planned"}
        if decision.mode == "plan":
            return {**update, "phase": "needs_plan"}
        if decision.mode == "clarify":
            return {**update, **self._respond_now(request, InvestigationStatus.CLARIFICATION_REQUIRED,
                                                  "Should I look at procurement data or procurement documents?",
                                                  options=("procurement data", "procurement documents"))}
        error = self._error(request, "supervise", ErrorCategory.AUTHORIZATION, decision.reasons[0] if decision.reasons else "refused",
                            "This request is not within what this assistant may do.")
        return {**update, **self._respond_now(request, InvestigationStatus.FAILED, error.safe_message, errors=[error])}

    # ----------------------------------------------------------------------------------------------- plan
    def plan(self, state: InvestigationState) -> dict:
        request = self.request(state)
        previous = state.get("plan")
        replanning = bool(state.get("plans")) or bool(state.get("plan_errors"))
        failures = tuple(sorted({e["code"] for e in state.get("errors", []) + state.get("plan_errors", [])}))
        update: dict[str, Any] = {"replans": 1 if replanning else 0}
        refusal = check_budget(state.get("usage", {}), request.budget, investigation_id=request.investigation_id, node="plan",
                               needs_llm=True)
        if refusal:
            return {**update, "errors": [refusal.to_state()], "phase": "plan_failed_final"}
        with self._span("plan", state, replans=state.get("replans", 0) + update["replans"]) as span:
            context = PlanningContext(memory_block=self.d.memory(request.investigation_id),
                                      previous_plan=previous if replanning else None, failures=failures)
            try:
                output = self._model_call(lambda: self.d.planner.plan(request, context))
            except CircuitOpenError:
                error = self._error(request, "plan", ErrorCategory.CIRCUIT_OPEN, "circuit_open", "Planning is temporarily unavailable.")
                return {**update, "errors": [error.to_state()], "phase": "plan_failed_final"}
            except LlmError as error:
                if error.failure.value in MALFORMED_OUTPUT:      # the model answered, but not with a usable plan
                    failure = self._error(request, "plan", ErrorCategory.PLANNER, "malformed_output",
                                          "The planner returned an invalid plan.")
                    return {**update, "plan_errors": [failure.to_state()], "errors": [failure.to_state()], "phase": "plan_failed"}
                failure = self._error(request, "plan", ErrorCategory.PROVIDER, error.failure.value,
                                      provider_message(error, "The planning model call failed."))
                return {**update, "errors": [failure.to_state()], "phase": "plan_failed_final"}
            except PlannerError as error:
                self.d.llm_breaker.record_ignored()
                failure = self._error(request, "plan", ErrorCategory.PLANNER, error.code, "The planner could not produce a plan.")
                return {**update, "plan_errors": [failure.to_state()], "errors": [failure.to_state()], "phase": "plan_failed"}
            except Exception as error:                     # an unexpected planner failure: fail safe, log the cause
                self.d.llm_breaker.record_ignored()
                span(error=error)
                failure = self._error(request, "plan", ErrorCategory.INTERNAL, type(error).__name__, "Planning failed unexpectedly.")
                return {**update, "errors": [failure.to_state()], "phase": "plan_failed_final"}
            usage = usage_update(priced(output.usage, self.d.prices))
            try:
                plan = build_plan(output.raw, request, produced_by=self.d.planner.name, version=len(state.get("plans", [])) + 1,
                                  supersedes=previous["plan_id"] if previous else None)
            except (PlannerError, ValidationError) as error:
                code = error.code if isinstance(error, PlannerError) else "contract_violation"
                failure = self._error(request, "plan", ErrorCategory.PLANNER, code, "The planner returned an invalid plan.")
                span(status="rejected", error_code=code)
                return {**update, "usage": usage, "plan_errors": [failure.to_state()], "errors": [failure.to_state()],
                        "phase": "plan_failed"}
            span(status="proposed", plan_id=plan.plan_id, task_count=len(plan.tasks))
        self._audit("plan.created", state, plan_id=plan.plan_id, produced_by=plan.produced_by, mode="plan",
                    plan_version=plan.version, task_count=len(plan.tasks))
        return {**update, "usage": usage, "plan": plan.to_state(), "plans": [plan.to_state()], "phase": "planned"}

    def after_plan(self, state: InvestigationState) -> str:
        if state.get("phase") == "planned":
            return "validate_plan"
        if state.get("phase") == "plan_failed" and state.get("replans", 0) < self.request(state).budget.max_replans:
            return "plan"
        return "fail"

    # ------------------------------------------------------------------------------------------ validation
    def validate_plan(self, state: InvestigationState) -> dict:
        request, plan = self.request(state), self.plan_of(state)
        with self._span("validate_plan", state, plan_id=plan.plan_id, task_count=len(plan.tasks)) as span:
            errors = self.d.controller.validate_plan(plan, request)
            span(validation_errors=len(errors), status="invalid" if errors else "valid")
        if errors:
            rows = [e.to_state() for e in errors]
            return {"plan_errors": rows, "errors": rows, "phase": "plan_invalid"}
        return {"task_states": {t.task_id: TaskState.PENDING.value for t in plan.tasks}, "phase": "plan_valid"}

    def after_validation(self, state: InvestigationState) -> str:
        if state.get("phase") == "plan_valid":
            return "dispatch"
        plan = self.plan_of(state)
        if plan.produced_by != "fast_path" and state.get("replans", 0) < self.request(state).budget.max_replans:
            return "plan"
        return "fail"

    # -------------------------------------------------------------------------------------------- execution
    def dispatch(self, state: InvestigationState) -> dict:
        request, plan = self.request(state), self.plan_of(state)
        states = dict(state.get("task_states", {}))
        cancelled: dict[str, str] = {}
        while new := {k: v for k, v in self.d.controller.cancellations(plan, states).items() if k not in cancelled}:
            cancelled.update(new)
            states.update(new)
        errors = []
        refusal = check_budget(state.get("usage", {}), request.budget, investigation_id=request.investigation_id, node="dispatch")
        if self.d.cancel_requested(request.investigation_id):
            refusal = self._error(request, "dispatch", ErrorCategory.CANCELLED, "cancelled_by_user",
                                  "The investigation was cancelled; remaining tasks were not run.")
        if refusal:
            stop = {t.task_id: TaskState.CANCELLED.value for t in plan.tasks if states.get(t.task_id) == TaskState.PENDING.value}
            cancelled.update(stop)
            errors.append(refusal.to_state())
            batch: list[str] = []
        else:
            batch = self.d.controller.ready(plan, states, request.budget.max_parallel)
        self._event("orchestrator.dispatch", state, plan_id=plan.plan_id, ready_count=len(batch), cancelled_count=len(cancelled))
        return {"task_states": {**cancelled, **{t: TaskState.RUNNING.value for t in batch}}, "next_batch": batch,
                "errors": errors, "phase": "executing" if batch else "executed"}

    def execute(self, state: InvestigationState) -> dict:
        request, plan = self.request(state), self.plan_of(state)
        view = {k: list(v) if isinstance(v, list) else v for k, v in state.items()}
        update: dict[str, Any] = {"task_states": {}, "results": [], "evidence": [], "errors": [], "usage": {}}
        allowed = self._execution_allowed(plan, request)
        for task_id in state.get("next_batch", []):
            task = plan.task(task_id)
            with self._span("execute", state, plan_id=plan.plan_id, task_id=task_id, worker=task.worker.value,
                            operation=task.operation) as span:
                run = self.d.controller.execute(task, request, view, execution_allowed=allowed,
                                                memory_block=self.d.memory(request.investigation_id))
                span(execution_state=run.final_state.value, attempt=run.attempts, retries=run.retries,
                     evidence_count=len(run.evidence), llm_calls=run.usage.llm_calls, input_tokens=run.usage.input_tokens,
                     output_tokens=run.usage.output_tokens, duration_ms=run.elapsed_ms)
            if run.final_state in (TaskState.FAILED, TaskState.BLOCKED):           # it ran, but found nothing it could use
                self._progress(state, {"type": "step", "key": f"execute:{task_id}", "label": WORKER_LABELS.get(task.worker),
                                       "state": "no_result"})
            rows = {"results": [r.to_state() for r in run.results], "evidence": [e.to_state() for e in run.evidence],
                    "errors": [e.to_state() for e in run.errors]}
            for key, values in rows.items():
                update[key] += values
                view[key] = view.get(key, []) + values
            update["task_states"][task_id] = run.final_state.value
            self._audit("task.finished", state, task_id=task.local_id, worker=task.worker.value, operation=task.operation,
                        status=run.final_state.value, attempt=run.attempts, retries=run.retries, duration_ms=run.elapsed_ms,
                        evidence_count=len(run.evidence), llm_calls=run.usage.llm_calls,
                        error_codes=",".join(e.code for e in run.errors) or None)
            counters = usage_update(run.usage, run.elapsed_ms, tasks_executed=0 if run.final_state == TaskState.SKIPPED else 1,
                                    attempts=run.attempts, retries=run.retries)
            update["usage"] = add_counters(update["usage"], counters)
            view["usage"] = add_counters(view.get("usage"), counters)
        return update

    @staticmethod
    def _execution_allowed(plan: InvestigationPlan, request: InvestigationRequest) -> bool:
        """Read-only SQL runs when Claude's plan asks for it, or on a fixed plan when the user asked for the figures."""
        return plan.produced_by != "fast_path" or request.execute_requested

    # ----------------------------------------------------------------------------------------- verification
    def verify(self, state: InvestigationState) -> dict:
        plan = self.plan_of(state)
        with self._span("verify", state, plan_id=plan.plan_id) as span:
            report = verify(plan, state)
            span(conflict_count=len(report.conflicts), unmet_count=len(report.unmet), evidence_count=report.verified_evidence_count)
        return {"verification": report.model_dump(mode="json"), "phase": "verified"}

    def after_verify(self, state: InvestigationState) -> str:
        plan, request = self.plan_of(state), self.request(state)
        if plan.produced_by == "fast_path" or state.get("replans", 0) >= request.budget.max_replans:
            return "respond"
        if VerificationReport.model_validate(state["verification"]).verified_evidence_count:
            return "respond"                         # some parts found evidence: answer now, and say what found nothing
        required = set(plan.completion.required_task_ids)
        failed = {e["task_id"] for e in state.get("errors", []) if e.get("category") in REPLAN_CATEGORIES}
        return "plan" if required & failed else "respond"

    def respond(self, state: InvestigationState) -> dict:
        request, plan = self.request(state), self.plan_of(state)
        report = VerificationReport.model_validate(state["verification"])
        records = ledger(state)
        used = [records[i] for ids in report.task_evidence.values() for i in ids if i in records]
        states = state.get("task_states", {})
        partial = tuple(t for t, s in states.items() if s == TaskState.PARTIAL.value)
        cancelled = tuple(t for t, s in states.items() if s == TaskState.CANCELLED.value)
        failed = tuple(t for t, s in states.items() if s in (TaskState.FAILED.value, TaskState.BLOCKED.value))
        draft, usage, errors = None, None, []
        responder = self._responder(request, plan)
        with self._span("respond", state, plan_id=plan.plan_id) as span:
            try:
                if responder.is_llm:
                    refusal = check_budget(state.get("usage", {}), request.budget, investigation_id=request.investigation_id,
                                           node="respond", needs_llm=True, starts_task=False)
                    if refusal:
                        raise _Stop(refusal)
                    draft, usage = self._model_call(lambda: self._compose(state, responder, request, plan, used, report, failed))
                else:
                    draft, usage = responder.compose(request, used, report)
            except _Stop as stop:
                errors.append(stop.error)
            except CircuitOpenError:
                errors.append(self._error(request, "respond", ErrorCategory.CIRCUIT_OPEN, "circuit_open",
                                          "Answer generation is temporarily unavailable."))
            except LlmError as error:
                errors.append(self._error(request, "respond", ErrorCategory.PROVIDER, error.failure.value,
                                          provider_message(error, "The answer generation call failed.")))
            response = validate_response(request, plan, draft, records, report, partial, cancelled, failed)
            response = self._clarification(state, response)
            if errors:
                response = response.model_copy(update={"error_codes": response.error_codes + tuple(e.code for e in errors)})
            if any(e.code == LlmFailure.DISABLED.value for e in errors):
                response = response.model_copy(update={"message": CLAUDE_OFF})
            else:
                response = response.model_copy(update={"message": no_evidence_first(response, plan, state.get("errors", []), failed)})
            span(status=response.status.value, claims_kept=len(response.claims), claims_removed=len(response.removed_claims))
            self._audit("answer.composed", state, status=response.status.value, claims_kept=len(response.claims),
                        claims_removed=len(response.removed_claims), evidence_count=len(used),
                        error_codes=",".join(response.error_codes) or None)
        update = {"response": response.to_state(), "phase": "done", "errors": [e.to_state() for e in errors]}
        if usage is not None:
            update["usage"] = usage_update(priced(usage, self.d.prices))
        return update

    def _compose(self, state, responder, request, plan, used, report, failed):
        """One answer attempt. A streaming writer's text goes to the live view as it is written: first a reset (a retry
        starts over), then the no-evidence opening when part of the question found nothing, then the claims."""
        if not getattr(responder, "streams", False):
            return responder.compose(request, used, report)
        self._progress(state, {"type": "text_reset"})
        gaps = evidence_gaps(plan, state.get("errors", []), failed)
        if gaps and used:
            self._progress(state, {"type": "text", "text": partial_gaps_opening(gaps)})
        return responder.compose(request, used, report, on_text=lambda text: self._progress(state, {"type": "text", "text": text}))

    def _responder(self, request: InvestigationRequest, plan: InvestigationPlan) -> ResponseGenerator:
        """Monitoring answers and tender checks are restated from their evidence (checks, warnings, estimates, passages)."""
        if request.tender is not None or (plan.tasks and all(t.worker in RESTATED_WORKERS for t in plan.tasks)):
            return DeterministicResponseGenerator()
        return self.d.responder

    @staticmethod
    def _clarification(state, response: InvestigationResponse) -> InvestigationResponse:
        if response.claims:
            return response
        options = [n.removeprefix("option:") for r in state.get("results", []) for n in r.get("notes", []) if n.startswith("option:")]
        needs = any(e.get("category") == ErrorCategory.CLARIFICATION.value for e in state.get("errors", []))
        if not needs:
            return response
        return response.model_copy(update={"status": InvestigationStatus.CLARIFICATION_REQUIRED,
                                           "message": "The question is ambiguous; please choose one of the options.",
                                           "clarification_options": tuple(dict.fromkeys(options))[:5]})

    # ------------------------------------------------------------------------------------------------ ends
    def fail(self, state: InvestigationState) -> dict:
        request = self.request(state)
        codes = tuple(dict.fromkeys(e["code"] for e in state.get("errors", [])))
        pending = {t: TaskState.CANCELLED.value for t, s in state.get("task_states", {}).items() if s == TaskState.PENDING.value}
        message = CLAUDE_OFF if LlmFailure.DISABLED.value in codes else "The investigation could not be planned or run safely."
        response = InvestigationResponse(investigation_id=request.investigation_id, status=InvestigationStatus.FAILED,
                                         message=message, error_codes=codes)
        return {"task_states": pending, "response": response.to_state(), "phase": "done"}

    def finalize(self, state: InvestigationState) -> dict:
        request = self.request(state)
        response = state.get("response") or {}
        usage = state.get("usage", {})
        unfinished = [t for t, s in state.get("task_states", {}).items() if TaskState(s) not in TERMINAL_TASK_STATES]
        self._event("orchestrator.investigation_finished", state, status=response.get("status"),
                    task_count=len(state.get("task_states", {})), llm_calls=usage.get("llm_calls", 0),
                    input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
                    executions=usage.get("executions", 0), cost_usd=usage.get("cost_usd"), replans=state.get("replans", 0))
        self._audit("investigation.finished", state, status=response.get("status"), task_count=len(state.get("task_states", {})),
                    unmet_count=len(unfinished), llm_calls=usage.get("llm_calls", 0), input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0), cost_usd=usage.get("cost_usd"),
                    error_codes=",".join(sorted({e["code"] for e in state.get("errors", [])})) or None)
        return {"phase": "finished"}


class _Stop(Exception):
    def __init__(self, error: ExecutionError):
        self.error = error
