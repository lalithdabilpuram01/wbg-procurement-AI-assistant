"""Pluggable intent classifier contract: the first decision (procurement or conversation) for a message the
deterministic rules could not place. The app plugs in a Claude classifier when it runs with --live
(wbg_orchestrator.topic_router); without one, such messages get a clarifying question (or the scope reply).

It may only *propose* a route and a score: permissions come from ROUTE_POLICIES, the router applies a minimum score,
and errors fall back to clarification. It receives the redacted message (truncated) and minimal conversation state,
never memory text or credentials.
"""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from conversation.models import TurnKind
from conversation.routing.routes import Route

MAX_CLASSIFIER_CHARS = 1000

# "calibrated_probability" may be declared only by a classifier whose scores were calibrated on held-out data.
ScoreKind = Literal["uncalibrated_score", "calibrated_probability"]


class ClassifierInput(BaseModel, frozen=True):
    text: str = Field(max_length=MAX_CLASSIFIER_CHARS)
    previous_task_route: Route | None = None
    follow_up_kind: TurnKind | None = None


class ClassifierOutput(BaseModel, frozen=True):
    route: Route
    score: float = Field(ge=0.0, le=1.0)
    score_kind: ScoreKind


class IntentClassifier(Protocol):
    """Implementations must be bounded and side-effect free (no tools, no memory access; a model call goes through the
    app's budgeted client)."""

    name: str

    def classify(self, request: ClassifierInput) -> ClassifierOutput: ...
