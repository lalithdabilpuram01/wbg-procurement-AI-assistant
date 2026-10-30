"""The agent catalog: the contract of every worker operation, in one place (rendered by `python -m wbg_orchestrator.catalog`).

Each entry states what the operation reads, what it returns, which data it touches, whether it calls an LLM and its
approval rule. tests/test_agent_contracts.py fails when the catalog, the workers, the authorization table, the evidence
specifications and the planner schema disagree, so the contract and the code cannot drift apart.

Shared rules for every operation (enforced in code, not by the catalog):
  * messages are typed, frozen, closed records with a schema version (contracts.py);
  * a worker runs only with an HMAC ticket the controller issued for that task and attempt (workers/base.py);
  * the operation must be allowed by the request's routes and capabilities, never by message text (authorization.py);
  * evidence must match the operation's specification, and required evidence must be verified (verification.py);
  * errors carry a category, a code and a safe message, never exception text (contracts.ExecutionError);
  * budgets (tasks, calls, tokens, time, executions, cost) are checked before every step (budget.py).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from .authorization import OPERATIONS
from .contracts import OPERATION_EVIDENCE, evidence_spec
from .planner import DEFAULT_ATTEMPTS


@dataclass(frozen=True)
class OperationContract:
    worker: str
    operation: str
    reads: str                 # inputs taken from the task and the worker context
    returns: str               # in plain words; the evidence kinds come from contracts.evidence_spec
    data: str                  # systems touched (always read-only)
    llm: bool                  # calls Claude inside the worker
    approval: str              # when a user approval is required (none: every operation is read-only)


CATALOG = (
    OperationContract("structured_data", "answer_data_question", "task question; conversation memory",
                      "figures from approved Gold/Silver data (or the validated query only)",
                      "Databricks via the read-only principal and the SQL validator", True,
                      "none (read-only SQL)"),
    OperationContract("structured_data", "describe_metric", "task question", "a registered metric definition",
                      "local registry metadata", False, "none"),
    OperationContract("structured_data", "find_tables", "task question", "approved tables or views for the question",
                      "local registry metadata", False, "none"),
    OperationContract("document_intelligence", "answer_document_question",
                      "task question; similar-project scope from upstream evidence", "page-checked passages with citations",
                      "Lakebase pgvector (bge-m3) + local corpus, read-only", False, "none"),
    OperationContract("document_intelligence", "retrieve_passages", "task question", "raw matching passages (not page-checked)",
                      "Lakebase pgvector (bge-m3) + local corpus, read-only", False, "none"),
    OperationContract("ml_prediction", "predict_package", "the package of the request (structured, never text)",
                      "three model estimates (Models 2-3 marked synthetic)",
                      "frozen models (hash-checked) + Gold via fixed read-only SQL", False, "none"),
    OperationContract("ml_prediction", "predict_planned_packages", "the planning spec; suggested methods from upstream evidence",
                      "three model estimates per planned package", "frozen models + Gold via fixed read-only SQL", False, "none"),
    OperationContract("procurement_planning", "match_similar_projects", "the planning spec (profile)",
                      "up to 8 similar past projects with reasons and a document scope",
                      "Gold via fixed read-only SQL + Lakebase similarity", False, "none (fixed, reviewed SQL)"),
    OperationContract("procurement_planning", "market_benchmarks", "the planning spec (country, categories)",
                      "methods, review types and supplier origin used historically", "Gold via fixed read-only SQL", False,
                      "none (fixed, reviewed SQL)"),
    OperationContract("procurement_planning", "governance_context", "the planning spec (country)",
                      "latest CPIA, WGI and income group", "Gold via fixed read-only SQL", False, "none (fixed, reviewed SQL)"),
    OperationContract("tender_design", "check_tender_design", "the tender spec (package, estimate, lots, draft text)",
                      "terms read from the draft and checks against history (bid window, method, review, packaging, "
                      "qualification ratios)", "Gold notices via fixed read-only SQL + typed procurement-plan records", False,
                      "none (fixed, reviewed SQL; deterministic rules)"),
    OperationContract("lesson_cards", "find_lessons", "the tender spec, planning spec or monitored project",
                      "reviewer-approved lesson cards that fit the lifecycle stage (or how many await review)",
                      "lesson cards in the Knowledge Graph schema (Lakebase)", False, "none (only approved cards are used)"),
    OperationContract("project_monitoring", "watch_project", "a project ID from the monitoring question",
                      "status summary and cited early warnings (plan rows, contract records, ISR ratings)",
                      "typed procurement-plan records and ISR ratings (pilot_v3 corpus)", False, "none (read-only, deterministic rules)"),
)


GOLD_SQL = ("execute_readonly_sql", "mcp")
# The tools each operation uses and where they run in the app (WBG_TOOL_TRANSPORT=mcp): over the Structured Data MCP
# server, or in-process inside the LangGraph app (tool_gateway.py). With in_process, the MCP tools run in the app too.
TOOLS = {
    "answer_data_question": tuple((t, "mcp") for t in ("search_table_registry", "get_table_schema", "get_table_relationships",
                                                       "get_metric_definition", "get_verified_sql_examples", "validate_sql",
                                                       "execute_readonly_sql")),
    "describe_metric": (("get_metric_definition", "mcp"),),
    "find_tables": (("search_table_registry", "mcp"),),
    "answer_document_question": (("lakebase_vector_search", "in_process"), ("tablelens_table_sql", "in_process")),
    "retrieve_passages": (("lakebase_vector_search", "in_process"),),
    "predict_package": (GOLD_SQL, ("frozen_ml_models", "in_process")),
    "predict_planned_packages": (GOLD_SQL, ("frozen_ml_models", "in_process")),
    "match_similar_projects": (GOLD_SQL, ("similar_project_scoring", "in_process")),
    "market_benchmarks": (GOLD_SQL,),
    "governance_context": (GOLD_SQL,),
    "watch_project": (("monitoring_rules", "in_process"),),
    "find_lessons": (("lesson_card_store", "in_process"),),
    "check_tender_design": (GOLD_SQL, ("tender_rules", "in_process")),
}


def tools_text(operation: str) -> str:
    by: dict[str, list[str]] = {}
    for tool, transport in TOOLS[operation]:
        by.setdefault(transport, []).append(tool)
    return "; ".join(f"{transport.replace('_', '-')}: {', '.join(tools)}" for transport, tools in by.items())


def as_markdown() -> str:
    lines = ["| Worker | Operation | Reads | Returns (evidence kinds) | Data (read-only) | Tools (where they run) | LLM | Attempts | Approval |",
             "|---|---|---|---|---|---|---|---|---|"]
    for c in CATALOG:
        spec = evidence_spec(c.operation, requires_execution=True) or OPERATION_EVIDENCE[c.operation]
        kinds = ", ".join(k.value for k in spec.kinds) + ("" if spec.verifiable else " (unverified)")
        lines.append(f"| {c.worker} | {c.operation} | {c.reads} | {c.returns} ({kinds}) | {c.data} | {tools_text(c.operation)} | "
                     f"{'yes' if c.llm else 'no'} | {DEFAULT_ATTEMPTS.get(c.operation, 2)} | {c.approval} |")
    return "\n".join(lines)


def catalogued() -> set[tuple[str, str]]:
    return {(c.worker, c.operation) for c in CATALOG}


def authorized() -> set[tuple[str, str]]:
    return {(worker, op) for worker, ops in OPERATIONS.items() for op in ops}


if __name__ == "__main__":
    sys.stdout.write(as_markdown() + "\n")
