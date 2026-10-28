"""Wiring of the real components for the local app (`python -m wbg_orchestrator.api serve`).

Every live dependency is opt-in and fails closed:
- Claude through Portkey:
  - needs the existing dual opt-in: `--confirm-live-call` plus WBG_ALLOW_LIVE_LLM_CALLS=true in the process;
  - every call (planner, answer writer, NL2SQL) goes through `BudgetedClaudeClient` and a persistent spend ledger
    (WBG_LLM_LEDGER, WBG_LLM_MAX_ATTEMPTS, WBG_LLM_MAX_USD).
- Databricks SQL (Gold), through the tools of the Structured Data MCP server (WBG_TOOL_TRANSPORT=mcp, the default):
  - the server is a separate process; it alone connects to Databricks, as the profile in WBG_DATABRICKS_PROFILE
    (default `wbg-readonly`), and its gateway refuses any write-capable identity;
  - the Structured Data agent uses all seven tools; the Prediction and Planning agents use execute_readonly_sql;
  - WBG_TOOL_TRANSPORT=in_process runs the same tools inside the app (no server process).
- In-process tools: document retrieval, TableLens, the frozen ML models, similarity scoring (see tool_gateway.py).
- Checkpoints: the isolated `PostgresSaver` on the Lakebase development branch (WBG_CKPT_ENDPOINT); `production` is refused.
- Conversations: the existing PostgreSQL conversation store (WBG_CONVERSATION_DSN).
- Documents: the default RAG release with the evidence-only generator; `pilot_v3_tablelens` (chosen per message)
  adds TableLens, whose SQL is written by Claude through the same budgeted client (`table_sql`).
- Audit:
  - orchestrator events go to `orchestrator/runs/app_audit.jsonl`, never the historical project log;
  - Logfire export happens only if the existing LOGFIRE_* settings switch it on.
"""
from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

from conversation.postgres_store import PostgresConversationStore
from conversation.routing.routes import Route
from conversation.service import ConversationService
from conversation.pii import redact_personal_data
from conversation.workspace import PostgresWorkspaceStore
from structured_agent.config import Settings
from structured_agent.guardrails.pipeline import build_guardrails
from structured_agent.nl2sql.live_guard import authorize_live_access
from structured_agent.nl2sql.settings import load_claude_settings
from structured_agent.nl2sql.tools import InProcessToolCaller, McpToolCaller, ToolCaller
from structured_agent.nl2sql.tracing import load_logfire_settings
from structured_agent.observability import JsonlAuditSink as AgentAuditSink, Telemetry
from structured_agent.service import StructuredDataService

from .app_service import AppService
from .checkpointing import CheckpointDbSettings, checkpointer, isolated_setup, lakebase_checkpoint_pool, lakebase_token_provider
from .claude import PortkeyJsonClient
from .controller import ExecutionController
from .contracts import WorkerName
from .conversation_bridge import ConversationFrontDoor, ConversationRunner
from .engine import InvestigationEngine
from .factory import authorization_factory, document_worker, llm_breaker, semantic_project_scores, structured_worker
from .graph import EngineDeps
from .live_llm import SONNET_5_5_PRICES, BudgetedClaudeClient, LedgerLimits, SpendLedger
from .observability import AuditTrail, JsonlAuditSink, OrchestratorTracer
from .planner import ClaudePlanner
from .rag_modules import answering
from .response import ClaudeResponseGenerator
from .supervisor import Supervisor
from .table_sql import TableSqlWriter
from .tool_gateway import TRANSPORTS, SupervisedMcpSession, gold_sql, mcp_parameters
from .workers.base import ResultCache, TicketAuthority
from .workers.planning import PlanningWorker
from .workers.prediction import PredictionWorker

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
DEV_CHECKPOINT_ENDPOINT = "projects/wbg-procurement-dev/branches/dev-orchestrator/endpoints/primary"
OUTPUT_CAPS = {"investigation_plan": 8000, "investigation_answer": 9000, "sql_draft": 4000, "table_sql": 1000}
REGISTRY_PRINCIPAL, WORKSPACE = "analyst-demo", "wbg-procurement-poc"


@dataclass(frozen=True)
class RuntimeConfig:
    ledger_path: Path
    max_attempts: int
    max_usd: Decimal
    databricks_profile: str = "wbg-readonly"
    checkpoint_endpoint: str = DEV_CHECKPOINT_ENDPOINT
    rag_release: str = "pilot_v3"
    audit_path: Path = ORCHESTRATOR_DIR / "runs" / "app_audit.jsonl"
    tool_transport: str = "mcp"                  # how agents reach the structured-data tools: mcp | in_process

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        env = os.environ
        return cls(ledger_path=Path(env.get("WBG_LLM_LEDGER", ORCHESTRATOR_DIR / "runs" / "llm_ledger.json")),
                   max_attempts=int(env.get("WBG_LLM_MAX_ATTEMPTS", "10")), max_usd=Decimal(env.get("WBG_LLM_MAX_USD", "2.00")),
                   databricks_profile=env.get("WBG_DATABRICKS_PROFILE", "wbg-readonly"),
                   checkpoint_endpoint=env.get("WBG_CKPT_ENDPOINT", DEV_CHECKPOINT_ENDPOINT),
                   tool_transport=env.get("WBG_TOOL_TRANSPORT", "mcp"))


def build_runtime(config: RuntimeConfig, operator_confirmed: bool) -> tuple[AppService, Callable[[], None]]:
    """The real application service and a function that releases its connections."""
    tracer = _tracer()
    claude = _claude_client(config, operator_confirmed, tracer)
    authority, guardrails = TicketAuthority(), build_guardrails()
    tools, close_tools = structured_tools(config, tracer)
    structured = _structured_worker(config, tools, claude, guardrails, authority)
    json_client = PortkeyJsonClient(claude)
    document = document_worker(authority, generator_factory=answering("generator").EvidenceOnlyGenerator,
                               table_writer=TableSqlWriter(json_client).writer)
    gold = gold_sql(tools)                                       # fixed Gold lookups via execute_readonly_sql
    planning = PlanningWorker(gold, authority, semantic_project_scores(document, config.rag_release))
    controller = ExecutionController({WorkerName.STRUCTURED: structured, WorkerName.DOCUMENT: document,
                                      WorkerName.PREDICTION: PredictionWorker(gold, authority), WorkerName.PLANNING: planning},
                                     authority, tracer=tracer, prices=SONNET_5_5_PRICES)
    deps = EngineDeps(controller=controller, supervisor=Supervisor(), planner=ClaudePlanner(json_client),
                      responder=ClaudeResponseGenerator(json_client), guard=guardrails, llm_breaker=llm_breaker(), tracer=tracer,
                      audit=AuditTrail(JsonlAuditSink(config.audit_path)), prices=SONNET_5_5_PRICES)
    pool = _checkpoint_pool(config)
    engine = InvestigationEngine(deps, checkpointer=checkpointer(pool))
    authorization_for = authorization_factory(REGISTRY_PRINCIPAL, WORKSPACE)
    store = PostgresConversationStore()
    runners = {route: ConversationRunner(engine, route, authorization_for, config.rag_release)
               for route in (Route.STRUCTURED_DATA, Route.DOCUMENT_RAG)}
    conversations = ConversationService(store, runners, guardrails, text_filter=redact_personal_data)
    service = AppService(conversations, store, engine,
                         ConversationFrontDoor(conversations, store, engine, authorization_for, rag_release=config.rag_release),
                         workspace=PostgresWorkspaceStore())

    def close() -> None:
        service.shutdown()
        pool.close()
        close_tools()
    return service, close


# ------------------------------------------------------------------------------------------------ internals
def _tracer() -> OrchestratorTracer:
    settings = load_logfire_settings()
    if settings.send_to_logfire and settings.token:
        return OrchestratorTracer.exporting(settings.token, "wbg-orchestrator-app")
    return OrchestratorTracer.disabled()


def _claude_client(config: RuntimeConfig, operator_confirmed: bool, tracer: OrchestratorTracer) -> BudgetedClaudeClient:
    live = authorize_live_access(operator_confirmed)                       # raises unless both opt-ins are present
    settings = dataclasses.replace(load_claude_settings(), timeout_seconds=120.0, max_output_tokens=max(OUTPUT_CAPS.values()))
    ledger = SpendLedger(config.ledger_path, LedgerLimits(max_attempts=config.max_attempts, max_usd=config.max_usd))
    return BudgetedClaudeClient(settings, ledger, output_caps=OUTPUT_CAPS, tracer=tracer, live_access=live)


def structured_tools(config: RuntimeConfig, tracer: OrchestratorTracer) -> tuple[ToolCaller, Callable[[], None]]:
    """The structured-data tools and a function that releases them: the MCP server process, or the service in-process."""
    if config.tool_transport not in TRANSPORTS:
        raise ValueError(f"WBG_TOOL_TRANSPORT must be one of {TRANSPORTS}")
    if config.tool_transport == "mcp":
        session = SupervisedMcpSession(mcp_parameters(REGISTRY_PRINCIPAL, config.databricks_profile,
                                                      config.audit_path.with_name("mcp_audit.jsonl")), tracer).open()
        return McpToolCaller(session, REGISTRY_PRINCIPAL), session.close
    settings = _settings(config)
    return InProcessToolCaller(StructuredDataService.from_environment(settings), REGISTRY_PRINCIPAL, WORKSPACE), lambda: None


def _settings(config: RuntimeConfig) -> Settings:
    return dataclasses.replace(Settings(), databricks_profile=config.databricks_profile, allow_dev_owner_credentials=False,
                               audit_log_path=config.audit_path.with_name("structured_audit.jsonl"))


def _structured_worker(config: RuntimeConfig, tools: ToolCaller, claude: BudgetedClaudeClient, guardrails,
                       authority: TicketAuthority):
    telemetry = Telemetry(AgentAuditSink(_settings(config).audit_log_path))
    return structured_worker(tools, claude, telemetry, authority, ResultCache(), guardrails=guardrails)


def _checkpoint_pool(config: RuntimeConfig):
    from infrastructure.lakebase_poc.lakebase_poc.settings import load_settings as lakebase_login
    login = lakebase_login()
    settings = CheckpointDbSettings(host=_endpoint_host(config.checkpoint_endpoint, login.databricks_profile),
                                    database="databricks_postgres", user=login.user, endpoint=config.checkpoint_endpoint,
                                    databricks_profile=login.databricks_profile)
    pool = lakebase_checkpoint_pool(settings, lakebase_token_provider(settings))
    pool.open(wait=True, timeout=60)
    isolated_setup(pool, checkpointer(pool), settings.schema)             # verifies isolation; creates nothing new
    return pool


def _endpoint_host(endpoint: str, profile: str) -> str:
    import json
    import subprocess

    from infrastructure.lakebase_poc.lakebase_poc.credentials import databricks_cli
    described = subprocess.run([databricks_cli(), "postgres", "get-endpoint", endpoint, "-p", profile, "-o", "json"],
                               capture_output=True, text=True, check=True, timeout=90)
    return json.loads(described.stdout)["status"]["hosts"]["host"]
