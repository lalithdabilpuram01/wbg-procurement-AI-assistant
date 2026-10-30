"""Acceptance 8-9 and controller guarantees: timeouts, bounded retries, circuit breakers, budgets, cost, idempotency,
and workers that cannot be called around the controller."""
from __future__ import annotations

from decimal import Decimal

import pytest

from structured_agent.nl2sql.resilience import BreakerSettings
from structured_agent.nl2sql.result import WorkflowStatus

from wbg_orchestrator.budget import PriceTable
from wbg_orchestrator.contracts import ErrorCategory, InvestigationBudget, InvestigationStatus, WorkerName
from wbg_orchestrator.controller import RetryBackoff
from wbg_orchestrator.planner import FakePlanner, build_plan
from wbg_orchestrator.workers.base import TicketError, WorkerContext

from tests.helpers import FakeTools, FakeWorkflow, build_engine, plan, request, task, workflow_result

METRIC_PLAN = plan(task("m", operation="describe_metric", question="Which metric counts bid invitations?"))


def errors_of(out, category: ErrorCategory) -> list[dict]:
    return [e for e in out.values["errors"] if e["category"] == category.value]


# ------------------------------------------------------------------------------------------------ acceptance 8
def test_tool_timeout_is_retried_with_backoff_and_then_succeeds():
    tools = FakeTools(failures=[TimeoutError()])
    engine, parts = build_engine(planner=FakePlanner(METRIC_PLAN), tools=tools, backoff=RetryBackoff(base_seconds=0.25))
    out = engine.start(request("Investigate the metric definition step by step"))
    assert out.status == InvestigationStatus.ANSWERED and len(tools.calls) == 2
    assert parts["sleeps"] == [0.25] and out.values["usage"]["retries"] == 1
    assert [r["status"] for r in out.values["results"]] == ["failed", "succeeded"]
    assert errors_of(out, ErrorCategory.TIMEOUT)[0]["retryable"] is True


def test_retries_are_bounded():
    tools = FakeTools(failures=[TimeoutError()] * 5)
    engine, parts = build_engine(planner=FakePlanner(METRIC_PLAN), tools=tools, backoff=RetryBackoff(base_seconds=0.25))
    out = engine.start(request("Investigate the metric definition step by step"))
    assert len(tools.calls) == 2 and parts["sleeps"] == [0.25]                      # max_attempts = 2 for registry tools
    assert out.status == InvestigationStatus.INSUFFICIENT_EVIDENCE


def test_a_hung_worker_is_stopped_at_its_deadline_and_llm_tasks_are_not_retried():
    workflow = FakeWorkflow(delay_seconds=1.5)
    engine, _ = build_engine(workflow=workflow)
    out = engine.start(request("How many bid invitations were published in fiscal year 2024?", execute=True,
                               budget=InvestigationBudget(task_deadline_seconds=0.3)))
    assert errors_of(out, ErrorCategory.TIMEOUT)[0]["code"] == "task_timeout"
    assert len(workflow.calls) == 1 and out.status == InvestigationStatus.INSUFFICIENT_EVIDENCE


# ------------------------------------------------------------------------------------------------ acceptance 9
def test_circuit_breaker_opens_and_stops_calls_to_the_failing_worker():
    tools = FakeTools(failures=[ConnectionError()] * 10)
    planner = FakePlanner(plan(task("a", operation="describe_metric", question="metric a"),
                               task("b", operation="find_tables", question="tables b"),
                               task("c", operation="describe_metric", question="metric c")))
    engine, parts = build_engine(planner=planner, tools=tools,
                                 breaker_settings=BreakerSettings(failure_threshold=2, window_seconds=60, cooldown_seconds=60,
                                                                  max_cooldown_seconds=120))
    out = engine.start(request("Investigate the metrics and tables step by step"))
    assert len(tools.calls) == 2                                                      # task a: 2 attempts; then open
    assert len(errors_of(out, ErrorCategory.CIRCUIT_OPEN)) == 2
    assert parts["controller"].breaker(WorkerName.STRUCTURED).state.value == "open"
    assert out.status == InvestigationStatus.INSUFFICIENT_EVIDENCE


# ------------------------------------------------------------------------------------------------ budgets
def test_execution_budget_is_enforced_before_the_call():
    planner = FakePlanner(plan(task("a", execute=True, question="Bids in fiscal year 2024?")))
    engine, parts = build_engine(planner=planner)
    out = engine.start(request("Investigate the bids step by step", budget=InvestigationBudget(max_executions=0)))
    assert parts["workflow"].calls == [] and errors_of(out, ErrorCategory.BUDGET)[0]["code"] == "execution_limit"


def test_task_budget_stops_dispatch_and_cancels_the_rest():
    planner = FakePlanner(plan(*[task(f"t{i}", operation="describe_metric", question=f"metric {i}") for i in range(3)]))
    engine, parts = build_engine(planner=planner)
    out = engine.start(request("Investigate the metrics step by step", budget=InvestigationBudget(max_tasks=3)))
    assert out.status == InvestigationStatus.ANSWERED                               # 3 tasks fit exactly
    planner2 = FakePlanner(plan(*[task(f"t{i}", operation="describe_metric", question=f"metric {i}") for i in range(3)]))
    engine2, parts2 = build_engine(planner=planner2)
    out2 = engine2.start(request("Investigate the metrics step by step",
                                 budget=InvestigationBudget(max_tasks=2, max_llm_calls=8)))
    assert out2.status == InvestigationStatus.FAILED and "too_many_tasks" in out2.response.error_codes and parts2["tools"].calls == []


def test_tokens_are_counted_and_cost_needs_a_price_table():
    engine, _ = build_engine(planner=FakePlanner(METRIC_PLAN))
    out = engine.start(request("Investigate the metric step by step"))
    assert out.values["usage"]["input_tokens"] == 400 and out.values["usage"].get("cost_usd") is None    # never invented
    prices = PriceTable(model="test", input_usd_per_million=Decimal("3"), output_usd_per_million=Decimal("15"), source="test fixture")
    engine2, _ = build_engine(planner=FakePlanner(METRIC_PLAN), prices=prices)
    out2 = engine2.start(request("Investigate the metric step by step"))
    assert Decimal(out2.values["usage"]["cost_usd"]) == Decimal("0.003450")


# ------------------------------------------------------------------------------------------------ idempotency
def test_a_plan_with_some_evidence_answers_at_once_and_names_what_was_not_found():
    workflow = FakeWorkflow(lambda q, execute: workflow_result(q, execute=execute, status=WorkflowStatus.CANNOT_ANSWER))
    metric = task("m", operation="describe_metric", question="Which metric counts bid invitations?")
    planner = FakePlanner(plan(metric, task("d", execute=True, deps=("m",))), plan(metric, task("d2", execute=True, deps=("m",))))
    engine, parts = build_engine(planner=planner, workflow=workflow)
    out = engine.start(request("Investigate the bid invitations step by step"))
    assert out.values["replans"] == 0 and len(planner.calls) == 1 and len(parts["tools"].calls) == 1     # no second plan
    assert out.response.message.startswith("No evidence found")


# ------------------------------------------------------------------------------------------------ controller-only
def test_workers_refuse_calls_without_a_controller_ticket():
    engine, parts = build_engine()
    req = request("How many bid invitations?")
    built = build_plan(plan(task("a")), req, produced_by="fake_planner")
    worker = parts["controller"]._workers[WorkerName.STRUCTURED]
    forged = parts["authority"].issue(built.tasks[0], 1)
    context = WorkerContext(ticket=None, authorization=req.authorization, request_id="r", rag_release="pilot_v2", remaining_seconds=5)
    with pytest.raises(TicketError):
        worker.run(built.tasks[0], context)
    context.ticket = forged.__class__(**{**forged.__dict__, "signature": "0" * 64})
    with pytest.raises(TicketError):
        worker.run(built.tasks[0], context)
    assert parts["workflow"].calls == []


def test_writing_the_answer_is_not_stopped_by_the_task_limit():
    from wbg_orchestrator.budget import check_budget
    used, budget = {"tasks_executed": 6, "llm_calls": 2}, InvestigationBudget(max_tasks=6)
    assert check_budget(used, budget, investigation_id="inv", node="dispatch").code == "task_limit"
    assert check_budget(used, budget, investigation_id="inv", node="respond", needs_llm=True, starts_task=False) is None


def test_sql_drafting_tokens_count_towards_the_investigation_cost():
    prices = PriceTable(model="test", input_usd_per_million=Decimal("2"), output_usd_per_million=Decimal("10"), source="test fixture")
    drafted = FakeWorkflow(lambda q, execute: workflow_result(q, execute=execute, input_tokens=6000, output_tokens=250))
    engine, _ = build_engine(workflow=drafted, prices=prices)
    out = engine.start(request("How many bid invitations were published in fiscal year 2024?", execute=True))
    assert out.values["usage"]["input_tokens"] >= 6000 and Decimal(out.values["usage"]["cost_usd"]) >= Decimal("0.0145")


class FlakyPlanner(FakePlanner):
    """Raises the given model errors first, then plans as scripted."""

    def __init__(self, failures, *plans):
        super().__init__(*plans)
        self.failures, self.attempts = list(failures), 0

    def plan(self, request, context):
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)
        return super().plan(request, context)


def test_a_transient_planner_failure_is_retried_once_after_a_pause():
    from structured_agent.nl2sql.llm_client import LlmError, LlmFailure
    planner = FlakyPlanner([LlmError(LlmFailure.UNAVAILABLE, 503)], METRIC_PLAN)
    engine, parts = build_engine(planner=planner)
    pauses = []
    parts["deps"].sleep = pauses.append
    out = engine.start(request("Investigate the metric step by step"))
    assert out.status == InvestigationStatus.ANSWERED and planner.attempts == 2 and pauses == [2.0]


def test_an_unusable_planner_reply_is_not_retried_by_the_model_call():
    from structured_agent.nl2sql.llm_client import LlmError, LlmFailure
    planner = FlakyPlanner([LlmError(LlmFailure.UNAVAILABLE, 503), LlmError(LlmFailure.UNAVAILABLE, 503)], METRIC_PLAN)
    engine, parts = build_engine(planner=planner)
    parts["deps"].sleep = lambda seconds: None
    out = engine.start(request("Investigate the metric step by step"))
    assert out.status == InvestigationStatus.FAILED and planner.attempts == 2       # one retry, then the failure stands
    truncated = FlakyPlanner([LlmError(LlmFailure.OUTPUT_TRUNCATED)], METRIC_PLAN)
    engine2, parts2 = build_engine(planner=truncated)
    parts2["deps"].sleep = lambda seconds: pytest.fail("an unusable reply must not be retried as a provider failure")
    engine2.start(request("Investigate the metric step by step"))
