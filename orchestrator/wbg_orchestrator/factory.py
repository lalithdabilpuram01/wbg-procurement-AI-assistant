"""Wiring of the engine to the existing components (used by hosts; offline tests inject fakes instead).

`structured_worker` builds per-request NL2SQL workflows over one tool caller (the MCP server in the app, see
tool_gateway.py); `structured_worker_for_service` does the same in-process over a StructuredDataService. `document_worker` builds AnswerPipelines over `build_retriever(release)`.
Nothing here performs a network call: Portkey/Claude clients are passed in by the host (live access is granted only
through the existing dual opt-in, outside this milestone).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable

from conversation.routing.routes import Route
from structured_agent.nl2sql.resilience import CircuitBreaker
from structured_agent.nl2sql.settings import RuntimeSettings
from structured_agent.nl2sql.tools import InProcessToolCaller, ToolCaller
from structured_agent.nl2sql.workflow import NlToSqlWorkflow
from structured_agent.observability import Telemetry

from .authorization import AppAuthorizationContext, PublicDataScope, authorization_for_routes
from .controller import DEFAULT_BREAKER, ExecutionController
from .contracts import WorkerName
from .graph import EngineDeps
from .rag_modules import PROJECT, answering
from .workers.base import ResultCache, TicketAuthority
from .workers.document import DocumentIntelligenceWorker, table_flag_index
from .workers.structured import StructuredDataWorker

APP_IDENTITY = "wbg-procurement-app"


def authorization_factory(registry_principal: str, workspace: str, scope: PublicDataScope | None = None,
                          app_identity: str = APP_IDENTITY) -> Callable[[tuple[Route, ...]], AppAuthorizationContext]:
    """A function that turns a request's routes into its authorization context, with the app identity fixed."""
    def build(routes: tuple[Route, ...]) -> AppAuthorizationContext:
        return authorization_for_routes(routes, app_identity=app_identity, registry_principal=registry_principal,
                                        workspace=workspace, scope=scope)
    return build


def structured_worker(tools: ToolCaller, claude: Any, telemetry: Telemetry, authority: TicketAuthority, cache: ResultCache,
                      guardrails: Any = None, settings: RuntimeSettings = RuntimeSettings()) -> StructuredDataWorker:
    """Every request uses the same tool caller, whose principal is fixed (by the MCP server's deployment); a request
    authorized for another principal is refused rather than served with the server's identity."""
    def tools_for(auth: AppAuthorizationContext) -> ToolCaller:
        if auth.registry_principal != tools.principal_id:
            raise PermissionError("the request's principal is not the principal of the structured-data tools")
        return tools

    def workflow_for(auth: AppAuthorizationContext) -> NlToSqlWorkflow:
        return NlToSqlWorkflow(tools_for(auth), claude, telemetry, settings, guardrails=guardrails)
    return StructuredDataWorker(workflow_for, tools_for, authority, cache)


def structured_worker_for_service(service: Any, claude: Any, telemetry: Telemetry, authority: TicketAuthority,
                                  cache: ResultCache, guardrails: Any = None,
                                  settings: RuntimeSettings = RuntimeSettings()) -> StructuredDataWorker:
    """Like `structured_worker`, but tools run in-process and take the principal from each request's authorization."""
    def tools_for(auth: AppAuthorizationContext) -> InProcessToolCaller:
        return InProcessToolCaller(service, auth.registry_principal, auth.workspace)

    def workflow_for(auth: AppAuthorizationContext) -> NlToSqlWorkflow:
        return NlToSqlWorkflow(tools_for(auth), claude, telemetry, settings, guardrails=guardrails)
    return StructuredDataWorker(workflow_for, tools_for, authority, cache)


def document_worker(authority: TicketAuthority, generator_factory: Callable[[], Any], use_nli: bool = False,
                    table_writer: Callable[[str], Any] | None = None,
                    uploaded: Callable[[str, set[str] | None], list[dict]] | None = None) -> DocumentIntelligenceWorker:
    """AnswerPipeline per corpus release; the release is explicit per investigation (default pilot_v3).
    table_writer (request id -> SQL writer) enables TableLens for the releases that have one; uploaded adds passages of
    documents ingested after the release (pgvector over wbg_kg)."""
    from rag.retrieval.versions import VERSIONS, build_retriever, build_table_lens

    @lru_cache(maxsize=2)
    def retriever_for(release: str):
        return build_retriever(release)

    @lru_cache(maxsize=2)
    def pipeline_for(release: str):
        pipeline = answering("pipeline")
        return pipeline.AnswerPipeline(generator_factory(), use_nli=use_nli, retriever=retriever_for(release))

    def flags_for(release: str):
        directory = VERSIONS[release].processed_dir or (PROJECT / "rag" / "corpus_v3" / "processed" / "pilot_v2")
        return table_flag_index(str(directory))

    @lru_cache(maxsize=2)
    def table_lens_for(release: str):
        return build_table_lens(release)
    return DocumentIntelligenceWorker(pipeline_for, retriever_for, authority, flags_for, table_lens_for, table_writer, uploaded)


def semantic_project_scores(document: DocumentIntelligenceWorker, release: str) -> Callable[[str], dict[str, float]]:
    """Description -> best passage similarity per project that has documents (every such project gets a score)."""
    import numpy as np

    def scores(text: str) -> dict[str, float]:
        """Best dense similarity of `text` to any chunk, per corpus project (0.0 when nothing scores)."""
        retriever = document.retriever(release)
        units = retriever.c.units
        dense = retriever.rank_dense(text, np.array([u.unit_type == "chunk" for u in units]))
        best = {project: 0.0 for project in retriever.c.corpus_projects}
        for unit, score in zip(units, dense):
            if np.isfinite(score) and score > best[unit.project_id]:
                best[unit.project_id] = float(score)
        return best
    return scores


def controller_for(workers: dict[WorkerName, Any], authority: TicketAuthority, tracer=None, **options) -> ExecutionController:
    return ExecutionController(workers, authority, tracer=tracer, **options)


def llm_breaker(clock=None) -> CircuitBreaker:
    return CircuitBreaker("llm.portkey", DEFAULT_BREAKER, clock) if clock else CircuitBreaker("llm.portkey", DEFAULT_BREAKER)


__all__ = ["APP_IDENTITY", "EngineDeps", "authorization_factory", "controller_for", "document_worker", "llm_breaker",
           "structured_worker", "structured_worker_for_service"]
