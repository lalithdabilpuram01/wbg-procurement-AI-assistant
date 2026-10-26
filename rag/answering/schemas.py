"""Structured contracts for grounded answers (pydantic). JSON schemas are exported to rag/answering/schemas/.

Generator output (DraftAnswer) may only reference evidence IDs it was given (E1, E2, ...). Final citations
(Citation) are built deterministically from indexed metadata, never from generator text.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["answered", "partial", "insufficient_evidence", "conflicting_evidence", "withheld"]


class DraftClaim(BaseModel):
    text: str = Field(description="One factual statement")
    evidence_ids: list[str] = Field(description="IDs of the evidence blocks that support the statement, e.g. ['E1']")


class DraftConflictValue(BaseModel):
    value: str
    evidence_id: str


class DraftConflict(BaseModel):
    field: str
    values: list[DraftConflictValue]


class DraftAnswer(BaseModel):
    """What the generator must return (validated; anything else is rejected)."""
    status: Literal["answered", "partial", "insufficient_evidence", "conflicting_evidence"]
    answer: str
    claims: list[DraftClaim]
    conflicts: list[DraftConflict] = []
    limitations: list[str] = []


class Citation(BaseModel):
    evidence_id: str
    unit_id: str
    unit_type: str
    project_id: str
    project_name: str | None
    document_id: str
    title: str | None
    doc_type: str
    document_date: str | None
    document_version_id: str
    physical_page_start: int
    physical_page_end: int
    printed_page_label: str | None
    section: str | None
    table_id: str | None
    excerpt: str
    source_url: str | None
    association: str
    page_check: str
    contract_links: list[dict] = []
    plan_version_id: str | None = None


class ClaimResult(BaseModel):
    text: str
    evidence_ids: list[str]
    supported: bool
    checks: dict


class Conflict(BaseModel):
    field: str
    values: list[dict]
    source: Literal["typed_record", "generator_reported", "deterministic"]


class AnswerResponse(BaseModel):
    question: str
    status: Status
    answer: str
    citations: list[Citation]
    supporting_evidence_ids: list[str]
    conflicts: list[Conflict]
    limitations: list[str]
    claims: list[ClaimResult] = []
    removed_claims: list[ClaimResult] = []
    generator: str
    generator_is_llm: bool
    timings_ms: dict = {}
    trace: dict = {}


def export(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for m in (DraftAnswer, AnswerResponse, Citation):
        (out / f"{m.__name__}.schema.json").write_text(json.dumps(m.model_json_schema(), indent=1))


if __name__ == "__main__":
    export(Path(__file__).resolve().parent / "schemas")
    print("schemas exported")
