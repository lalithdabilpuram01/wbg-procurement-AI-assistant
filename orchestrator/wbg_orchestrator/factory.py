"""Wiring of the engine to the existing components (used by hosts; offline tests inject fakes instead).

`structured_worker_for_service` builds per-request NL2SQL workflows and tool callers for the application principal
over an existing StructuredDataService. `document_worker` builds AnswerPipelines over `build_retriever(release)`.
Nothing here performs a network call: Portkey/Claude clients are passed in by the host (live access is granted only
through the existing dual opt-in, outside this milestone).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable

from conversation.routing.routes import Route
from structured_agent.nl2sql.resilience import CircuitBreaker
from structured_agent.nl2sql.settings import RuntimeSettings
from structured_agent.nl2sql.tools import InProcessToolCaller
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
    def build(routes: tuple[Route, ...]) -> AppAuthorizationContext:
        return authorization_for_routes(routes, app_identity=app_identity, registry_principal=registry_principal,
                                        workspace=workspace, scope=scope)
    return build


def structured_worker_for_service(service: Any, claude: Any, telemetry: Telemetry, authority: TicketAuthority,
                                  cache: ResultCache, guardrails: Any = None,
                                  settings: RuntimeSettings = RuntimeSettings()) -> StructuredDataWorker:
    def tools_for(auth: AppAuthorizationContext) -> InProcessToolCaller:
        return InProcessToolCaller(service, auth.registry_principal, auth.workspace)

    def workflow_for(auth: AppAuthorizationContext) -> NlToSqlWorkflow:
        return NlToSqlWorkflow(tools_for(auth), claude, telemetry, settings, guardrails=guardrails)
    return StructuredDataWorker(workflow_for, tools_for, authority, cache)


def document_worker(authority: TicketAuthority, generator_factory: Callable[[], Any], use_nli: bool = False) -> DocumentIntelligenceWorker:
    """AnswerPipeline per corpus release; the release is explicit per investigation (default pilot_v2)."""
    from rag.retrieval.versions import VERSIONS, build_retriever

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
    return DocumentIntelligenceWorker(pipeline_for, retriever_for, authority, flags_for)


def controller_for(workers: dict[WorkerName, Any], authority: TicketAuthority, tracer=None, **options) -> ExecutionController:
    return ExecutionController(workers, authority, tracer=tracer, **options)


def llm_breaker(clock=None) -> CircuitBreaker:
    return CircuitBreaker("llm.portkey", DEFAULT_BREAKER, clock) if clock else CircuitBreaker("llm.portkey", DEFAULT_BREAKER)


__all__ = ["APP_IDENTITY", "EngineDeps", "authorization_factory", "controller_for", "document_worker", "llm_breaker",
           "structured_worker_for_service"]
