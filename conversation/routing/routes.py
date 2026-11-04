"""Routes, their permitted capabilities, and the typed routing decision.

`ROUTE_POLICIES` is the single source of truth for what a route may touch. Permissions are always taken from this
table, never from the user's message or from a classifier, so neither can widen a route's tool access.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# The one route a message is sent to; ROUTE_POLICIES below fixes what each route may use.
class Route(str, Enum):
    CONVERSATION = "conversation"                    # greetings, small talk, general explanations; no evidence needed
    CONVERSATION_MEMORY = "conversation_memory"      # recall/summary of the user's own authorized conversation history
    STRUCTURED_DATA = "structured_data"              # verified procurement data via the NL2SQL agent
    DOCUMENT_RAG = "document_rag"                    # procurement documents, precedents, lessons learned
    ML_PREDICTION = "ml_prediction"                  # explicit forecasting and risk prediction
    PROCUREMENT_PLANNING = "procurement_planning"    # procurement plan / PPSD drafting, decisions, future HITL
    CLARIFICATION = "clarification"                  # meaning cannot be resolved safely


TASK_ROUTES = frozenset({Route.STRUCTURED_DATA, Route.DOCUMENT_RAG, Route.ML_PREDICTION, Route.PROCUREMENT_PLANNING})
CONVERSATIONAL_ROUTES = frozenset({Route.CONVERSATION, Route.CONVERSATION_MEMORY})


# Why the router chose a route: a fixed label that is safe to log (never message text).
class ReasonCategory(str, Enum):
    GREETING = "greeting"
    GRATITUDE = "gratitude"
    SMALL_TALK = "small_talk"
    FAREWELL = "farewell"
    CONCEPTUAL_QUESTION = "conceptual_question"
    TOPIC_CHANGE = "topic_change"
    MEMORY_RECALL = "memory_recall"
    ABOUT_PREVIOUS_ANSWER = "about_previous_answer"  # explain / simplify / shorten what was already said (history only)
    EVIDENCE_RECHECK = "evidence_recheck"            # recall that must be re-verified by the previous task's agent
    EXPLICIT_TASK_REQUEST = "explicit_task_request"
    FOLLOW_UP_INHERITED = "follow_up_inherited"
    AMBIGUOUS_REFERENT = "ambiguous_referent"
    MIXED_INTENT = "mixed_intent"
    TOOL_DIRECTIVE_REFUSED = "tool_directive_refused"   # the message tried to choose tools or routes itself
    NO_CLEAR_INTENT = "no_clear_intent"
    EMPTY_MESSAGE = "empty_message"
    CLASSIFIER = "classifier"
    CLASSIFIER_LOW_SCORE = "classifier_low_score"
    CLASSIFIER_ERROR = "classifier_error"


MemoryAccess = Literal["none", "recent_context", "conversation_history"]


# What one route may use: its capabilities, how much conversation memory it reads, and retrieval or tools.
class RoutePolicy(BaseModel, frozen=True):
    capabilities: tuple[str, ...]
    memory_access: MemoryAccess
    retrieval_allowed: bool            # registry search, document retrieval, embeddings
    external_tools_allowed: bool       # MCP tools, Databricks, ML models, any specialist agent


ROUTE_POLICIES: dict[Route, RoutePolicy] = {
    Route.CONVERSATION: RoutePolicy(capabilities=("conversation_responder",), memory_access="recent_context",
                                    retrieval_allowed=False, external_tools_allowed=False),
    Route.CONVERSATION_MEMORY: RoutePolicy(capabilities=("conversation_memory_reader", "conversation_responder"),
                                           memory_access="conversation_history", retrieval_allowed=False,
                                           external_tools_allowed=False),
    Route.STRUCTURED_DATA: RoutePolicy(capabilities=("registry_search", "sql_generation", "sql_validation",
                                                     "databricks_execution"),
                                       memory_access="recent_context", retrieval_allowed=True, external_tools_allowed=True),
    Route.DOCUMENT_RAG: RoutePolicy(capabilities=("document_retrieval", "embedding_search", "grounded_answering"),
                                    memory_access="recent_context", retrieval_allowed=True, external_tools_allowed=True),
    Route.ML_PREDICTION: RoutePolicy(capabilities=("ml_prediction",), memory_access="recent_context",
                                     retrieval_allowed=True, external_tools_allowed=True),
    Route.PROCUREMENT_PLANNING: RoutePolicy(capabilities=("planning_drafter", "decision_records", "human_approval"),
                                            memory_access="recent_context", retrieval_allowed=True,
                                            external_tools_allowed=True),
    Route.CLARIFICATION: RoutePolicy(capabilities=(), memory_access="none", retrieval_allowed=False,
                                     external_tools_allowed=False),
}

ConfidenceLevel = Literal["high", "medium", "low"]
# How the confidence was obtained. Rule-based levels are heuristic labels, not probabilities; only a classifier that
# reports a calibrated probability may set `calibrated=True`.
ConfidenceBasis = Literal["deterministic_rule", "conversation_inheritance", "classifier_score", "none"]


# The router's typed decision for one message; `decision()` copies its permissions from ROUTE_POLICIES.
class RoutingDecision(BaseModel, frozen=True):
    route: Route
    reason: ReasonCategory
    confidence_level: ConfidenceLevel
    confidence_basis: ConfidenceBasis
    score: float | None = None                 # only a classifier's score; None for rules
    calibrated: bool = False                   # True only for a classifier that declares calibrated probabilities
    required_capabilities: tuple[str, ...]
    memory_access: MemoryAccess
    retrieval_allowed: bool
    external_tools_allowed: bool
    reset_topic: bool = False
    inherited_from: Route | None = None
    decided_by: str                            # "rules" or the classifier's name
    matched_signals: tuple[str, ...] = ()      # rule ids only (safe to log; never message text)
    clarification: str | None = None
    clarification_options: tuple[str, ...] = Field(default=())

    @property
    def is_task(self) -> bool:
        return self.route in TASK_ROUTES

    def log_attributes(self) -> dict[str, object]:
        """Safe routing metadata for audit and tracing (no message text)."""
        return {"route": self.route.value, "route_reason": self.reason.value, "confidence_level": self.confidence_level,
                "confidence_basis": self.confidence_basis, "retrieval_allowed": self.retrieval_allowed,
                "external_tools_allowed": self.external_tools_allowed, "reset_topic": self.reset_topic,
                "inherited_route": self.inherited_from.value if self.inherited_from else None,
                "route_decided_by": self.decided_by}


def decision(route: Route, reason: ReasonCategory, level: ConfidenceLevel, basis: ConfidenceBasis, decided_by: str = "rules",
             **details: object) -> RoutingDecision:
    """Build a decision whose permissions always come from ROUTE_POLICIES."""
    policy = ROUTE_POLICIES[route]
    return RoutingDecision(route=route, reason=reason, confidence_level=level, confidence_basis=basis,
                           required_capabilities=policy.capabilities, memory_access=policy.memory_access,
                           retrieval_allowed=policy.retrieval_allowed, external_tools_allowed=policy.external_tools_allowed,
                           decided_by=decided_by, **details)
