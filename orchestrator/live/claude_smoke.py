"""Bounded live Claude smoke session (Milestone B): at most 5 Claude attempts and $1.00 committed spend, ever.

Live access needs both existing opt-ins: `--confirm-live-call` AND WBG_ALLOW_LIVE_LLM_CALLS=true in this process
environment. Every request goes through `BudgetedClaudeClient`, which reserves a worst-case cost in the persistent
ledger `live_results/claude_spend_ledger.json` before sending, so a rerun can never exceed the ceiling.

Scenarios, in order (cheapest first, so Portkey's retry header is checked before larger calls):
  0. synthetic secrets through the graph with a fake planner (no Claude call; tests trace scrubbing);
  1. a simple Claude response: fast path, synthetic structured result -> Claude answer -> claim validation;
  2. a Claude plan for a combined investigation -> contract and controller validation -> HITL approval by the owner
     -> synthetic structured fixtures plus the REAL local document retrieval (default pilot_v2, public World Bank
     documents) -> Claude answer -> claim validation;
  3. optional (`--error-probe`): one request to a non-existent model of the approved integration, which validates
     the live error mapping (a rejected request is not billed).
The Structured Data worker uses synthetic fixtures (no NL2SQL Claude call, no Databricks).

    cd orchestrator && WBG_ALLOW_LIVE_LLM_CALLS=true env -u VIRTUAL_ENV PYTHONPATH=..:. \
        uv run --locked python -B -m live.claude_smoke --confirm-live-call [--export-logfire] [--error-probe]
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["WBG_AUDIT_LOG"] = os.path.join(tempfile.mkdtemp(prefix="wbg-live-audit-"), "audit.jsonl")   # never the project log

from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from conversation.routing.routes import Route  # noqa: E402
from structured_agent.guardrails.pipeline import build_guardrails  # noqa: E402
from structured_agent.nl2sql.live_guard import authorize_live_access  # noqa: E402
from structured_agent.nl2sql.llm_client import ClaudeRequest, LlmError  # noqa: E402
from structured_agent.nl2sql.settings import load_claude_settings  # noqa: E402
from structured_agent.nl2sql.tracing import load_logfire_settings  # noqa: E402

from wbg_orchestrator.claude import PortkeyJsonClient  # noqa: E402
from wbg_orchestrator.controller import ExecutionController  # noqa: E402
from wbg_orchestrator.contracts import WorkerName  # noqa: E402
from wbg_orchestrator.engine import EngineOutcome, InvestigationEngine  # noqa: E402
from wbg_orchestrator.factory import document_worker, llm_breaker  # noqa: E402
from wbg_orchestrator.graph import EngineDeps  # noqa: E402
from wbg_orchestrator.live_llm import SONNET_5_5_PRICES, BudgetedClaudeClient, LedgerLimits, SpendLedger, SpendLimitReached  # noqa: E402
from wbg_orchestrator.observability import AuditTrail, MemoryAuditSink, OrchestratorTracer  # noqa: E402
from wbg_orchestrator.planner import ClaudePlanner, FakePlanner  # noqa: E402
from wbg_orchestrator.rag_modules import answering  # noqa: E402
from wbg_orchestrator.response import ClaudeResponseGenerator, DeterministicResponseGenerator  # noqa: E402
from wbg_orchestrator.supervisor import Supervisor  # noqa: E402
from wbg_orchestrator.workers.base import ResultCache, TicketAuthority  # noqa: E402
from wbg_orchestrator.workers.structured import StructuredDataWorker  # noqa: E402

from tests.helpers import ALICE, FakeTools, FakeWorkflow, plan, request, task  # noqa: E402

RESULTS = Path(__file__).resolve().parents[1] / "live_results"
LEDGER = RESULTS / "claude_spend_ledger.json"
EXPECTED_MODEL = "@wbg-anthropic/claude-sonnet-5-5"       # the only model whose pricing was verified
CAPS = {"investigation_answer": 4000, "investigation_plan": 8000, "error_probe": 256}
SIMPLE_QUESTION = "How many bid invitations were published in fiscal year 2024?"
INVESTIGATION = ("Investigate how many bid invitations were published in fiscal year 2024, and then check what the "
                 "Ethiopia Expressway Development Support Project documents say about procurement delays.")
SYNTHETIC_SECRETS = ("pk-live-SYNTHETIC-portkey-key-000000", "dapi-SYNTHETIC-oauth-token-000000000000",
                     "pylf_v1_us_SYNTHETICLOGFIRETOKEN0000")


class WarningLog(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[dict[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append({"logger": record.name, "level": record.levelname, "message": record.getMessage()[:160]})


def build(planner, responder, tracer: OrchestratorTracer, *, real_documents: bool) -> tuple[InvestigationEngine, dict]:
    authority, cache = TicketAuthority(), ResultCache()
    workflow, tools = FakeWorkflow(), FakeTools()
    structured = StructuredDataWorker(lambda auth: workflow, lambda auth: tools, authority, cache)
    workers = {WorkerName.STRUCTURED: structured}
    if real_documents:
        workers[WorkerName.DOCUMENT] = document_worker(authority, generator_factory=answering("generator").EvidenceOnlyGenerator)
    controller = ExecutionController(workers, authority, tracer=tracer, prices=SONNET_5_5_PRICES)
    deps = EngineDeps(controller=controller, supervisor=Supervisor(), planner=planner, responder=responder,
                      guard=build_guardrails(), llm_breaker=llm_breaker(), tracer=tracer, audit=AuditTrail(MemoryAuditSink()),
                      prices=SONNET_5_5_PRICES)
    return InvestigationEngine(deps), {"workflow": workflow, "tools": tools}


def summary(out: EngineOutcome) -> dict[str, Any]:
    values, response = out.values, out.response
    plan_state = values.get("plan") or {}
    return {
        "status": out.status.value, "produced_by": plan_state.get("produced_by"),
        "plans": [{"plan_id": p["plan_id"], "version": p["version"], "rationale": p.get("rationale"),
                   "tasks": [{k: t[k] for k in ("local_id", "worker", "operation", "question", "depends_on", "requires_execution",
                                                "optional", "max_attempts")} for t in p["tasks"]]} for p in values.get("plans", [])],
        "plan_errors": [e["code"] for e in values.get("plan_errors", [])],
        "approvals": [{k: a[k] for k in ("approval_id", "kind", "reasons", "summary")} for a in values.get("approvals", [])],
        "decisions": [{"approved": d["approved"]} for d in values.get("decisions", [])],
        "task_states": out.task_states, "errors": [e["code"] for e in values.get("errors", [])],
        "evidence": [{"kind": e.kind.value, "numeric_use": e.numeric_use.value, "validation_status": e.validation_status.value,
                      "warnings": list(e.quality_warnings), "citation": e.citation[:160]} for e in out.evidence],
        "claims": [{"text": c.text, "evidence_ids": list(c.evidence_ids)} for c in response.claims] if response else [],
        "removed_claims": list(response.removed_claims) if response else [],
        "limitations": list(response.limitations) if response else [], "usage": values.get("usage", {}),
        "replans": values.get("replans", 0)}


def run_synthetic_secrets(tracer: OrchestratorTracer) -> dict[str, Any]:
    planner = FakePlanner(plan(task("m", operation="describe_metric", question=f"Which metric? {SYNTHETIC_SECRETS[0]}"),
                               task("c", deps=("m",), execute=True, question=f"Count with token {SYNTHETIC_SECRETS[1]}")))
    engine, _ = build(planner, DeterministicResponseGenerator(), tracer, real_documents=False)
    pending = engine.start(request(f"Investigate step by step and then explain {SYNTHETIC_SECRETS[2]}", thread="conv-live-b0"))
    done = engine.resume(ALICE, pending.thread_id, pending.pending_approval.approval_id, approved=True) \
        if pending.pending_approval else pending
    return {"status": done.status.value, "llm_calls": done.values.get("usage", {}).get("llm_calls", 0)}


def run_simple(client: PortkeyJsonClient, tracer: OrchestratorTracer) -> dict[str, Any]:
    engine, parts = build(FakePlanner(), ClaudeResponseGenerator(client), tracer, real_documents=False)
    out = engine.start(request(SIMPLE_QUESTION, execute=True, thread="conv-live-b1"))
    return {**summary(out), "workflow_calls": len(parts["workflow"].calls)}


def run_investigation(client: PortkeyJsonClient, tracer: OrchestratorTracer) -> dict[str, Any]:
    engine, parts = build(ClaudePlanner(client), ClaudeResponseGenerator(client), tracer, real_documents=True)
    out = engine.start(request(INVESTIGATION, routes=(Route.STRUCTURED_DATA, Route.DOCUMENT_RAG), thread="conv-live-b2"))
    first = summary(out)
    if out.pending_approval is not None:
        out = engine.resume(ALICE, out.thread_id, out.pending_approval.approval_id, approved=True, reason="milestone B smoke")
    return {"before_approval": {"status": first["status"], "approvals": first["approvals"], "task_states": first["task_states"]},
            **summary(out), "workflow_calls": [c["execute"] for c in parts["workflow"].calls], "tool_calls": len(parts["tools"].calls)}


def run_error_probe(settings, ledger: SpendLedger, tracer: OrchestratorTracer, live) -> dict[str, Any]:
    probe = BudgetedClaudeClient(dataclasses.replace(settings, model="@wbg-anthropic/claude-nonexistent-model-0"), ledger,
                                 output_caps=CAPS, tracer=tracer, live_access=live)
    try:
        probe.complete(ClaudeRequest(system="Return JSON.", user="Reply with ok=true.", trace_id="req_error_probe",
                                     json_schema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                                                  "required": ["ok"], "additionalProperties": False}, schema_name="error_probe"))
    except LlmError as error:
        return {"failure": error.failure.value, "http_status": error.status_code, "provider_error_type": error.provider_error_type,
                "transient": error.is_transient}
    return {"failure": None}


def check_traces(exporter: InMemorySpanExporter, forbidden: list[str]) -> dict[str, Any]:
    spans = exporter.get_finished_spans()
    by_id = {s.context.span_id: s for s in spans}
    leaks = sorted({s.name for s in spans for term in forbidden
                    if term and term in (s.name + json.dumps({k: str(v) for k, v in s.attributes.items()}))})
    uncorrelated = [s.name for s in spans if s.name.startswith("orchestrator.node.")
                    and not (s.attributes.get("investigation_id") and s.attributes.get("conversation_id"))]
    orphan_calls = [s.name for s in spans if s.name == "orchestrator.llm_call" and s.attributes.get("request_id") != "req_error_probe"
                    and not (s.parent and by_id.get(s.parent.span_id) is not None
                             and by_id[s.parent.span_id].attributes.get("investigation_id"))]
    names: dict[str, int] = {}
    for span in spans:
        names[span.name] = names.get(span.name, 0) + 1
    return {"span_count": len(spans), "names": names, "leaks": leaks, "uncorrelated_node_spans": uncorrelated,
            "llm_calls_without_investigation_parent": orphan_calls,
            "llm_call_spans": [{k: s.attributes.get(k) for k in ("attempt", "status", "input_tokens", "output_tokens", "cost_usd",
                                                                  "worst_case_usd", "gateway_retry_count", "error_code", "http_status",
                                                                  "provider_error_type", "duration_ms")}
                               for s in spans if s.name == "orchestrator.llm_call"]}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Bounded live Claude smoke session (Milestone B)")
    parser.add_argument("--confirm-live-call", action="store_true")
    parser.add_argument("--export-logfire", action="store_true", help="also export metadata spans to the existing Logfire project")
    parser.add_argument("--error-probe", action="store_true")
    args = parser.parse_args(argv)
    live = authorize_live_access(args.confirm_live_call)
    base = load_claude_settings()
    if base.model != EXPECTED_MODEL:
        print(f"refused: configured model is not {EXPECTED_MODEL}; pricing is verified only for that model")
        return 2
    settings = dataclasses.replace(base, timeout_seconds=120.0, max_output_tokens=max(CAPS.values()))
    ledger = SpendLedger(LEDGER, LedgerLimits(max_attempts=5, max_usd=Decimal("1.00")))
    warnings = WarningLog()
    logging.getLogger().addHandler(warnings)
    exporter = InMemorySpanExporter()
    processors = [SimpleSpanProcessor(exporter)]
    logfire_token = None
    if args.export_logfire:
        logfire_token = load_logfire_settings().token
        if not logfire_token:
            print("refused: no existing LOGFIRE_TOKEN configured")
            return 2
        tracer = OrchestratorTracer.exporting(logfire_token, "wbg-orchestrator-milestone-b", processors)
    else:
        tracer = OrchestratorTracer.local(processors, service_name="wbg-orchestrator-milestone-b")
    client = PortkeyJsonClient(BudgetedClaudeClient(settings, ledger, output_caps=CAPS, tracer=tracer, live_access=live))

    results: dict[str, Any] = {"model": settings.model, "caps": CAPS, "timeout_seconds": settings.timeout_seconds,
                               "logfire_export": args.export_logfire}
    steps = [("synthetic_secret_scenario", lambda: run_synthetic_secrets(tracer)),
             ("simple_response", lambda: run_simple(client, tracer)),
             ("investigation", lambda: run_investigation(client, tracer))]
    if args.error_probe:
        steps.append(("error_probe", lambda: run_error_probe(settings, ledger, tracer, live)))
    for name, step in steps:
        if ledger.locked:
            results[name] = {"skipped": f"ledger locked: {ledger.locked}"}
            continue
        try:
            results[name] = step()
        except SpendLimitReached as refusal:
            results[name] = {"refused": refusal.code}
        print(f"{name}: {json.dumps(results[name].get('status') or results[name])[:200]}")
    tracer.force_flush()
    forbidden = [SIMPLE_QUESTION, "bid invitations", "Expressway", "procurement delays", settings.api_key, logfire_token or "",
                 *SYNTHETIC_SECRETS]
    forbidden += [c["text"][:40] for r in results.values() if isinstance(r, dict) for c in r.get("claims", [])]
    results["traces"] = check_traces(exporter, forbidden)
    results["ledger"] = ledger.summary()
    results["warnings"] = warnings.records
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "claude_smoke_results.json").write_text(json.dumps(results, indent=2, sort_keys=True, default=str))
    print(json.dumps({"ledger": results["ledger"], "trace_leaks": results["traces"]["leaks"],
                      "warnings": len(warnings.records)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
