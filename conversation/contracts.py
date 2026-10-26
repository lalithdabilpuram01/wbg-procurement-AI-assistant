"""Minimal memory contracts for the future LangGraph supervisor and other agents (interfaces only; not implemented).

They fix the shapes other components can rely on now, without building a generic memory framework:
* `CheckpointStore`      - LangGraph thread checkpoints, keyed by the same opaque thread id and trusted owner;
* `LongTermMemoryRetriever` - relevant memory from the owner's earlier threads (must never cross owners);
* `EvidenceReference`    - RAG document / ML prediction / structured-result references (see models.py);
* `PredictionContext`    - what an ML prediction may contribute to the context (no features, no raw rows);
* `Decision`             - human approval / decision records (see models.py; also used for HITL approvals);
* `SavedResponse`        - a bookmarked assistant answer;
* `Workspace`            - an optional named set of projects that threads can be attached to.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from conversation.models import Decision, EvidenceReference, Owner, new_id, utc_now

__all__ = ["CheckpointStore", "LongTermMemoryRetriever", "MemoryHit", "PredictionContext", "SavedResponse", "Workspace",
           "Decision", "EvidenceReference"]


class CheckpointStore(Protocol):
    """Persistence for orchestration checkpoints. `state` must be JSON-safe and contain no credentials or result rows."""

    def put(self, owner: Owner, thread_id: str, checkpoint_id: str, state: dict[str, Any]) -> None: ...
    def latest(self, owner: Owner, thread_id: str) -> tuple[str, dict[str, Any]] | None: ...


class MemoryHit(BaseModel):
    thread_id: str
    turn_seq: int | None = None
    text: str                              # generated or remembered text: untrusted, not evidence
    score: float
    source: Literal["summary", "turn", "decision"]


class LongTermMemoryRetriever(Protocol):
    """Relevant memory across the owner's own threads. Implementations must filter by `owner` before ranking."""

    def retrieve(self, owner: Owner, query: str, limit: int = 5) -> list[MemoryHit]: ...


class PredictionContext(BaseModel):
    """What an ML prediction may add to a conversation: the reference and the headline, never the input features."""

    prediction_id: str
    model_name: str                        # one of the three frozen Module 2 models
    model_version: str
    target: str                            # e.g. "tender_time_days"
    value: float | str
    interval: tuple[float, float] | None = None
    synthetic_labels: bool                 # true for the amendment and re-bid models (trained on synthetic labels)
    created_at: dt.datetime = Field(default_factory=utc_now)


class SavedResponse(BaseModel):
    saved_id: str = Field(default_factory=new_id)
    owner: Owner
    thread_id: str
    turn_seq: int                          # the assistant turn that was saved
    label: str = Field(min_length=1, max_length=120)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    created_at: dt.datetime = Field(default_factory=utc_now)


class Workspace(BaseModel):
    workspace_id: str = Field(default_factory=new_id)
    owner: Owner
    name: str = Field(min_length=1, max_length=120)
    project_ids: list[str] = Field(default_factory=list)
    thread_ids: list[str] = Field(default_factory=list)
    created_at: dt.datetime = Field(default_factory=utc_now)
