"""Pluggable intent classifier contract (for a future lightweight routing model; none is selected or installed).

A classifier is consulted only when the deterministic rules find no clear intent. It may only *propose* a route and a
score: permissions come from ROUTE_POLICIES, the router applies a minimum score, and errors fall back to clarification.
It receives the redacted message (truncated) and minimal conversation state, never memory text or credentials.
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
    """Implementations must be local, bounded and side-effect free (no network, no tools, no memory access)."""

    name: str

    def classify(self, request: ClassifierInput) -> ClassifierOutput: ...
