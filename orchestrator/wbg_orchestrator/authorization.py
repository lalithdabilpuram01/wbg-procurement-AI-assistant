"""Application authorization context for the Databricks Apps MVP (shared, restricted application identity).

Data access uses ONE application identity limited to approved public procurement data. It maps to a registry principal
of the Structured Data Agent (whose roles decide objects and columns) and to the approved RAG corpus releases.
Ownership of conversations, investigations and approvals stays user-specific (`OwnerRef`).

Which operations an investigation may use comes from two independent sources that must both allow it:
  * the existing conversation `ROUTE_POLICIES` (capabilities of the routed request), and
  * this module's operation table (worker operation -> required capabilities; read-only only).
Neither a message nor a planner can widen them. Per-user Databricks OAuth and user-scoped Unity Catalog access are
future scope (not implemented): the identity below is never derived from user text.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from conversation.routing.routes import ROUTE_POLICIES, Route

# worker -> operation -> capabilities the routed request must hold (names from conversation ROUTE_POLICIES)
OPERATIONS: dict[str, dict[str, frozenset[str]]] = {
    "structured_data": {
        "answer_data_question": frozenset({"registry_search", "sql_generation", "sql_validation"}),
        "describe_metric": frozenset({"registry_search"}),
        "find_tables": frozenset({"registry_search"}),
    },
    "document_intelligence": {
        "answer_document_question": frozenset({"document_retrieval", "grounded_answering"}),
        "retrieve_passages": frozenset({"document_retrieval"}),
    },
    "ml_prediction": {
        "predict_package": frozenset({"ml_prediction"}),
        "predict_planned_packages": frozenset({"ml_prediction"}),
    },
    "procurement_planning": {
        "match_similar_projects": frozenset({"planning_drafter"}),
        "market_benchmarks": frozenset({"planning_drafter"}),
        "governance_context": frozenset({"planning_drafter"}),
    },
    "tender_design": {
        "check_tender_design": frozenset({"planning_drafter"}),     # tender design is part of procurement planning
    },
    "lesson_cards": {
        "find_lessons": frozenset({"planning_drafter"}),            # read-only: reviewed lessons for a tender, brief or project
    },
    "project_monitoring": {
        "watch_project": frozenset({"planning_drafter"}),          # the procurement plan's execution: same route
    },
}
EXECUTION_CAPABILITY = "databricks_execution"
READ_ONLY_OPERATIONS = frozenset(f"{w}:{op}" for w, ops in OPERATIONS.items() for op in ops)


class PublicDataScope(BaseModel):
    """What the shared application identity may read (approved public procurement data only)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: Literal["wbg_public_procurement"] = "wbg_public_procurement"
    structured_schemas: tuple[str, ...] = ("workspace.wbg_gold", "workspace.wbg_silver")
    rag_releases: tuple[str, ...] = ("pilot_v2", "pilot_v2_1", "pilot_v3", "pilot_v3_tablelens")
    allow_sql_execution: bool = True              # read-only gateway execution, still subject to HITL policy
    writes_allowed: Literal[False] = False        # no database modification or external action, ever


class AppAuthorizationContext(BaseModel):
    """Authorization of one investigation. Built by the trusted host (front door), never from message text."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["1"] = "1"
    app_identity: str = Field(min_length=1, max_length=100)        # label of the shared application principal
    registry_principal: str = Field(min_length=1, max_length=100)  # Structured Data Agent principal (roles)
    workspace: str = Field(min_length=1, max_length=100)
    scope: PublicDataScope = PublicDataScope()
    routes: tuple[Route, ...]                                       # routed capabilities this investigation holds
    capabilities: tuple[str, ...]
    user_scoped_data_access: Literal[False] = False                # future scope: per-user Unity Catalog access

    def allows(self, worker: str, operation: str, requires_execution: bool = False) -> bool:
        needed = OPERATIONS.get(worker, {}).get(operation)
        if needed is None or not needed <= set(self.capabilities):
            return False
        if requires_execution:
            return EXECUTION_CAPABILITY in self.capabilities and self.scope.allow_sql_execution
        return True

    @property
    def allowed_workers(self) -> tuple[str, ...]:
        return tuple(w for w, ops in OPERATIONS.items() if any(self.allows(w, op) for op in ops))


def authorization_for_routes(routes: tuple[Route, ...] | list[Route], *, app_identity: str, registry_principal: str,
                             workspace: str, scope: PublicDataScope | None = None) -> AppAuthorizationContext:
    """Capabilities = union of the routes' existing policies; only task routes with external tools count."""
    capabilities: set[str] = set()
    for route in routes:
        policy = ROUTE_POLICIES[Route(route)]
        if policy.external_tools_allowed:
            capabilities |= set(policy.capabilities)
    return AppAuthorizationContext(app_identity=app_identity, registry_principal=registry_principal, workspace=workspace,
                                   scope=scope or PublicDataScope(), routes=tuple(Route(r) for r in routes),
                                   capabilities=tuple(sorted(capabilities)))
