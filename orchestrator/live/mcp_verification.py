"""Option A verification without Claude: the agents' Gold access over the real MCP server equals the in-process path.

Real: the Structured Data MCP server (separate process, .venv-structured-mcp), the local registry, Databricks Gold with
the read-only profile, the frozen ML models. No LLM is constructed or called anywhere in this script.

    1. tools       the server starts with exactly the seven approved tools
    2. registry    search_table_registry, get_metric_definition, get_table_schema: MCP answer == in-process answer
    3. gold sql    the 8 reviewed reference queries: validate_sql then execute_readonly_sql over MCP == in-process rows
    4. agents      Prediction (2 packages) and Planning (similar projects, benchmarks, governance) run with Gold over MCP
                   and in-process; their evidence (facts and summaries) must be identical
    5. restart     the server process is killed; the next call restarts it once and succeeds

    cd orchestrator
    env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m live.mcp_verification
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any

os.environ.pop("WBG_ALLOW_LIVE_LLM_CALLS", None)          # no Claude: the live-call opt-in is removed for this process

from conversation.routing.routes import Route  # noqa: E402
from structured_agent.mcp_server import TOOL_NAMES  # noqa: E402
from structured_agent.nl2sql.tools import McpToolCaller  # noqa: E402

from evaluation.cases import BRIEF, PACKAGES, STRUCTURED  # noqa: E402
from live.c4_end_to_end import RESULTS  # noqa: E402
from wbg_orchestrator.contracts import InvestigationRequest, OwnerRef, PackageSpec, PlanningSpec, new_id  # noqa: E402
from wbg_orchestrator.factory import authorization_factory  # noqa: E402
from wbg_orchestrator.observability import OrchestratorTracer  # noqa: E402
from wbg_orchestrator.planner import brief_plan, package_plan  # noqa: E402
from wbg_orchestrator.runtime import REGISTRY_PRINCIPAL, WORKSPACE, RuntimeConfig, structured_tools  # noqa: E402
from wbg_orchestrator.tool_gateway import gold_sql  # noqa: E402
from wbg_orchestrator.workers.base import TicketAuthority, WorkerContext  # noqa: E402
from wbg_orchestrator.workers.planning import PlanningWorker  # noqa: E402
from wbg_orchestrator.workers.prediction import PredictionWorker  # noqa: E402

CONFIG = RuntimeConfig(ledger_path=RESULTS / "unused_ledger.json", max_attempts=0, max_usd=0)
AUTH = authorization_factory(REGISTRY_PRINCIPAL, WORKSPACE)
OWNER = OwnerRef.from_identity("local", "mcp-verification")
SIMILARITY = {"P148850": 0.71, "P163452": 0.52, "P143302": 0.40}       # the same stub on both sides (no document model)


def comparable(value: Any) -> Any:
    """Tool answers without per-call fields (request ids, timings, audit ids) so two transports can be compared."""
    if isinstance(value, dict):
        return {k: comparable(v) for k, v in value.items() if k not in ("request_id", "duration_ms", "timings_ms", "audit_id",
                                                                            "executed_at", "validated_at", "validation_id", "invocation_id")}
    if isinstance(value, list):
        return [comparable(v) for v in value]
    return value


def evidence_view(output) -> list[tuple]:
    return [(e.kind.value, e.citation, e.summary, tuple((f.name, f.value) for f in e.facts)) for e in output.evidence]


def run_task(worker, request: InvestigationRequest, plan, operation: str):
    authority = worker._authority
    task = next(t for t in plan.tasks if t.operation == operation)
    context = WorkerContext(ticket=authority.issue(task, 1), authorization=request.authorization, request_id=request.request_id,
                            rag_release="pilot_v3", remaining_seconds=120, package=request.package, planning=request.planning)
    return worker.run(task, context)


def request_for(**fields) -> InvestigationRequest:
    return InvestigationRequest(investigation_id=new_id("inv"), request_id=new_id("req"), conversation_thread_id="mcp-check",
                                owner=OWNER, question="MCP verification", mode="investigation",
                                authorization=AUTH((Route.STRUCTURED_DATA, Route.DOCUMENT_RAG)), **fields)


def agents(mcp_sql, local_sql) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for n, package in enumerate(PACKAGES, start=1):
        request = request_for(package=PackageSpec(**package))
        plan = package_plan(request)
        views = [evidence_view(run_task(PredictionWorker(sql, TicketAuthority()), request, plan, "predict_package"))
                 for sql in (mcp_sql, local_sql)]
        out[f"prediction_package_{n}"] = {"identical": views[0] == views[1], "evidence": len(views[0])}
    request = request_for(planning=PlanningSpec(**BRIEF))
    plan = brief_plan(request)
    for operation in ("match_similar_projects", "market_benchmarks", "governance_context"):
        views = [evidence_view(run_task(PlanningWorker(sql, TicketAuthority(), lambda text: SIMILARITY), request, plan, operation))
                 for sql in (mcp_sql, local_sql)]
        out[f"planning_{operation}"] = {"identical": views[0] == views[1], "evidence": len(views[0])}
    return out


def main() -> int:
    tracer = OrchestratorTracer.disabled()
    mcp, close_mcp = structured_tools(CONFIG, tracer)                                   # WBG_TOOL_TRANSPORT=mcp
    local, _ = structured_tools(RuntimeConfig(**{**CONFIG.__dict__, "tool_transport": "in_process"}), tracer)
    session = mcp._session
    report: dict[str, Any] = {"tools": sorted(t.name for t in session._current().list_tools()) == sorted(TOOL_NAMES)}
    try:
        registry = {"search_table_registry": {"query": "contract awards by procurement method", "top_k": 3},
                    "get_metric_definition": {"query": "tender invitation notices"},
                    "get_table_schema": {"object_fqn": "workspace.wbg_gold.vw_kpi_contract_awards"}}
        report["registry"] = {tool: comparable(mcp.call(tool, args, "mcp-check")) == comparable(local.call(tool, args, "mcp-check"))
                              for tool, args in registry.items()}
        report["gold_sql"] = {}
        for cid, _, sql, _ in STRUCTURED:
            checks = [comparable(c.call("validate_sql", {"sql": sql}, "mcp-check")) for c in (mcp, local)]
            rows = [gold_sql(c)(sql, {}) for c in (mcp, local)]
            report["gold_sql"][cid] = {"validation_identical": checks[0] == checks[1], "rows_identical": rows[0] == rows[1],
                                       "rows": len(rows[0])}
        report["agents"] = agents(gold_sql(mcp), gold_sql(local))
        subprocess.run(["pkill", "-f", "structured_agent.mcp_server"], check=False)    # kill this project's server process
        time.sleep(1)
        started = time.perf_counter()
        after = mcp.call("get_metric_definition", {"query": "tender invitation notices"}, "mcp-check")
        report["restart"] = {"answered": after.get("status") == "ok", "restarts": session.restarts,
                             "seconds": round(time.perf_counter() - started, 1)}
    finally:
        close_mcp()
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "mcp_verification.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
