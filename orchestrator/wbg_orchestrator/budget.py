"""Token, cost, call and time accounting against an InvestigationBudget.

Usage is charged from what the provider actually reports. A cost is computed only from an explicitly configured
price table (model, prices, source); without one the cost stays unknown (None) and no figure is invented.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from .contracts import ErrorCategory, ExecutionError, InvestigationBudget, Usage, new_id


CACHE_READ, CACHE_WRITE = Decimal("0.1"), Decimal("1.25")      # Anthropic prompt-cache price multipliers

class PriceTable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    model: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    source: str                                  # where the prices come from (contract, price list, date)

    def cost(self, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> Decimal:
        """`input_tokens` includes cached tokens; cache reads cost 0.1x and cache writes 1.25x the input price."""
        base = Decimal(input_tokens - cache_read_tokens - cache_write_tokens)
        weighted = base + Decimal(cache_read_tokens) * CACHE_READ + Decimal(cache_write_tokens) * CACHE_WRITE
        return (weighted * self.input_usd_per_million + Decimal(output_tokens) * self.output_usd_per_million) / Decimal(1_000_000)


def priced(usage: Usage, prices: PriceTable | None) -> Usage:
    if prices is None or not (usage.input_tokens or usage.output_tokens):
        return usage
    return usage.model_copy(update={"cost_usd": prices.cost(usage.input_tokens, usage.output_tokens)})


def usage_update(usage: Usage, elapsed_ms: int = 0, tasks_executed: int = 0, attempts: int = 0,
                 retries: int = 0) -> dict[str, Any]:
    """State update for the `usage` counters (added by the reducer)."""
    return {"llm_calls": usage.llm_calls, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "executions": usage.executions, "cost_usd": str(usage.cost_usd) if usage.cost_usd is not None else None,
            "elapsed_ms": elapsed_ms, "tasks_executed": tasks_executed, "attempts": attempts, "retries": retries}


def budget_error(investigation_id: str, node: str, code: str, message: str, task_id: str | None = None) -> ExecutionError:
    return ExecutionError(error_id=new_id("err"), investigation_id=investigation_id,
                          task_id=task_id, node=node, category=ErrorCategory.BUDGET, code=code, safe_message=message)


def check_budget(used: dict[str, Any], budget: InvestigationBudget, *, investigation_id: str, node: str,
                 task_id: str | None = None, needs_llm: bool = False, needs_execution: bool = False,
                 starts_task: bool = True) -> ExecutionError | None:
    """Refuse the next step when it would exceed a limit (checked before the call, never after). Writing the final
    answer is not a task (`starts_task=False`): it is limited by the call, token, time and cost budgets only."""
    checks = [
        (starts_task and used.get("tasks_executed", 0) >= budget.max_tasks, "task_limit", "The investigation reached its task limit."),
        (used.get("elapsed_ms", 0) >= budget.deadline_seconds * 1000, "deadline", "The investigation reached its time limit."),
        (needs_llm and used.get("llm_calls", 0) >= budget.max_llm_calls, "llm_call_limit", "The LLM call budget is exhausted."),
        (used.get("input_tokens", 0) >= budget.max_input_tokens, "input_token_limit", "The input token budget is exhausted."),
        (used.get("output_tokens", 0) >= budget.max_output_tokens, "output_token_limit", "The output token budget is exhausted."),
        (needs_execution and used.get("executions", 0) >= budget.max_executions, "execution_limit",
         "The Databricks execution budget is exhausted."),
        (budget.max_cost_usd is not None and used.get("cost_usd") is not None
         and Decimal(used["cost_usd"]) >= budget.max_cost_usd, "cost_limit", "The cost budget is exhausted."),
    ]
    for exceeded, code, message in checks:
        if exceeded:
            return budget_error(investigation_id, node, code, message, task_id)
    return None
