"""Supervisor: chooses the fast path (one task, no LLM) or the planning agent, using the existing routing rules.

Reused from conversation/routing/rules.py: `normalize`, `is_tool_directive` (a message cannot pick tools) and
`task_signals` (deterministic data / document phrases). The conversation front door has already applied redaction,
the input guardrail, follow-up resolution and routing; its route arrives as `route_hint`.
Complex = signals of both workers, an explicit investigation request, or multi-step wording (COMPLEX markers).
Routes outside the investigation's AppAuthorizationContext are refused, never widened.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from conversation.routing import rules
from conversation.routing.routes import Route

from .contracts import InvestigationRequest, WorkerName

ROUTE_WORKERS = {Route.STRUCTURED_DATA: (WorkerName.STRUCTURED, "answer_data_question"),
                 Route.DOCUMENT_RAG: (WorkerName.DOCUMENT, "answer_document_question")}
UNAVAILABLE_ROUTES = {Route.ML_PREDICTION, Route.PROCUREMENT_PLANNING}
COMPLEX = re.compile(r"\b(investigat\w*|root causes?|explain why|step by step|and then|cross[- ]check\w*|reconcil\w*|"
                     r"verify (?:it |them |this )?against|in each case|for each of)\b", re.I)


class SupervisorDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["fast", "plan", "package", "brief", "monitor", "tender", "clarify", "refuse"]
    worker: WorkerName | None = None
    operation: str | None = None
    requires_execution: bool = False
    routes: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()                 # rule ids only (safe to log)


class Supervisor:
    def decide(self, request: InvestigationRequest) -> SupervisorDecision:
        if request.tender is not None:
            return self._tender(request)
        if request.package is not None:
            return self._package(request)
        if request.planning is not None:
            return self._brief(request)
        if request.monitor_project is not None:
            return self._monitor(request)
        text = rules.normalize(request.question)
        if rules.is_tool_directive(text):
            return SupervisorDecision(mode="refuse", reasons=("tool_directive_refused",))
        signals = rules.task_signals(text)
        if set(signals) & UNAVAILABLE_ROUTES and not set(signals) & set(ROUTE_WORKERS):
            return SupervisorDecision(mode="refuse", reasons=("route_unavailable",))
        routes = {r for r in signals if r in ROUTE_WORKERS}
        if request.route_hint:
            routes.add(Route(request.route_hint))
        allowed = {Route(r) for r in request.authorization.routes}
        if routes - allowed:
            return SupervisorDecision(mode="refuse", routes=tuple(sorted(r.value for r in routes)),
                                      reasons=("route_not_authorized",))
        reasons = tuple(sorted(i for ids in signals.values() for i in ids))
        complex_wording = bool(COMPLEX.search(text))
        if request.mode == "investigation" or len(routes) > 1 or (complex_wording and request.mode != "fast"):
            routes = routes or {r for r in allowed if r in ROUTE_WORKERS}
            return SupervisorDecision(mode="plan", routes=tuple(sorted(r.value for r in routes)),
                                      reasons=reasons + (("complex_wording",) if complex_wording else ()))
        if len(routes) == 1:
            route = next(iter(routes))
            worker, operation = ROUTE_WORKERS[route]
            return SupervisorDecision(mode="fast", worker=worker, operation=operation, routes=(route.value,), reasons=reasons,
                                      requires_execution=worker == WorkerName.STRUCTURED and request.execute_requested)
        return SupervisorDecision(mode="clarify", reasons=("no_clear_intent",))

    @staticmethod
    def _brief(request: InvestigationRequest) -> SupervisorDecision:
        """A structured Planning Brief request (from the API) gets the fixed brief plan."""
        routes = (Route.PROCUREMENT_PLANNING.value, Route.ML_PREDICTION.value, Route.DOCUMENT_RAG.value)
        if Route.PROCUREMENT_PLANNING not in {Route(r) for r in request.authorization.routes}:
            return SupervisorDecision(mode="refuse", routes=routes, reasons=("route_not_authorized",))
        return SupervisorDecision(mode="brief", routes=routes, reasons=("planning_brief",))

    @staticmethod
    def _tender(request: InvestigationRequest) -> SupervisorDecision:
        """A structured tender-design check (from the API, never from message text) gets the fixed tender plan."""
        routes = (Route.PROCUREMENT_PLANNING.value, Route.ML_PREDICTION.value, Route.DOCUMENT_RAG.value)
        if Route.PROCUREMENT_PLANNING not in {Route(r) for r in request.authorization.routes}:
            return SupervisorDecision(mode="refuse", routes=routes, reasons=("route_not_authorized",))
        return SupervisorDecision(mode="tender", routes=routes, reasons=("tender_design",))

    @staticmethod
    def _monitor(request: InvestigationRequest) -> SupervisorDecision:
        """A monitoring question about one project (detected by the front door) gets the fixed monitoring plan."""
        routes = (Route.PROCUREMENT_PLANNING.value,)
        if Route.PROCUREMENT_PLANNING not in {Route(r) for r in request.authorization.routes}:
            return SupervisorDecision(mode="refuse", routes=routes, reasons=("route_not_authorized",))
        return SupervisorDecision(mode="monitor", routes=routes, reasons=("project_monitoring",))

    @staticmethod
    def _package(request: InvestigationRequest) -> SupervisorDecision:
        """A structured package request (from the API, never from message text) gets the fixed assessment plan."""
        routes = (Route.ML_PREDICTION.value, Route.DOCUMENT_RAG.value)
        if Route.ML_PREDICTION not in {Route(r) for r in request.authorization.routes}:
            return SupervisorDecision(mode="refuse", routes=routes, reasons=("route_not_authorized",))
        return SupervisorDecision(mode="package", routes=routes, reasons=("package_assessment",))
