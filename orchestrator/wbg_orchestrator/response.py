"""Response generation adapter and deterministic response validation.

A generator turns ledger evidence into claims that cite evidence ids; it may be deterministic (offline, no LLM) or
Claude through Portkey. Every claim is then validated against the ledger, independently of the generator:
  * it cites at least one evidence record of THIS investigation;
  * every number and date it states occurs in the cited evidence (reusing the RAG validator's `numbers` / `dates`);
  * figures are supported only by evidence allowed for numbers (validated dataset results, or narrative document text
    with a verified page) - never by PDF table values;
  * it states no confidence, probability or likelihood figure that the evidence does not contain;
  * it carries no secret-shaped value (the conversation redaction patterns).
Unsupported claims are removed and counted; the status reflects missing, partial and conflicting evidence.
"""
from __future__ import annotations

import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from conversation.pii import mask_pii
from conversation.redaction import redact_secrets

from .claude import PortkeyJsonClient
from .contracts import (BriefSection, EvidenceKind, EvidenceRecord, InvestigationPlan, InvestigationRequest,
                        InvestigationResponse, InvestigationStatus, NumericUse, ResponseClaim, Usage)
from .rag_modules import answering
from .verification import VerificationReport

CONFIDENCE = re.compile(r"\b(confiden\w*|probabilit\w*|likelihood|likely|certaint\w*|chance)\b", re.I)


class DraftClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=600)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    section: BriefSection | None = None


class DraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[DraftClaim] = Field(default_factory=list, max_length=28)
    limitations: list[str] = Field(default_factory=list, max_length=8)


class ResponseGenerator(Protocol):
    name: str
    is_llm: bool

    def compose(self, request: InvestigationRequest, evidence: list[EvidenceRecord],
                report: VerificationReport) -> tuple[DraftResponse | None, Usage]: ...


class DeterministicResponseGenerator:
    """Offline generator: restates verified evidence (facts and bounded summaries) and cites it. No synthesis."""

    name, is_llm = "deterministic", False

    def compose(self, request, evidence, report):
        claims = []
        for record in evidence:
            text = _restatement(record)
            if text:
                claims.append(DraftClaim(text=text[:600], evidence_ids=[record.evidence_id]))
        return DraftResponse(claims=claims[:12]), Usage()


class ClaudeResponseGenerator:
    """Claude composes claims from evidence summaries only (Portkey). Not called live in Milestone A."""

    name, is_llm = "claude", True
    SYSTEM = ("Write a short answer to the question using ONLY the evidence records provided. Every claim must cite the "
              "evidence ids it relies on. Do not state any number, date or confidence that is not in the cited evidence. "
              "Values marked NOT_APPROVED must not be stated as figures. Values marked MODEL_ESTIMATE are model "
              "predictions: present them as estimates, never as recorded facts. For records warned synthetic_training_data, "
              "say in plain words that the model was trained on synthetic data; never write warning codes or evidence ids "
              "in the text. Return only JSON matching the schema.")

    BRIEF = ("This is a PLANNING BRIEF for a new project described in the question. Group claims by section: "
             "similar_projects (which past projects are comparable and why); schedule (per package: estimated tender-to-signing "
             "time and range, chance of exceeding a year, the drivers, and what this means for the procurement plan dates); "
             "market_approach (which methods, review types and supplier origins were used, with contract values); risks (risks "
             "from similar appraisals and governance context, each with a mitigation); lessons (the main section: specific, "
             "actionable lessons from similar projects, each saying what to do differently); tender_design (packaging, "
             "specification and qualification points); monitoring (early-warning signs to watch during execution). Write at "
             "most 22 claims in total: 2-3 per section, up to 5 for lessons; omit a section without evidence. Each claim is "
             "1-2 sentences and under 50 words. Name packages by their names. Refer to past projects by name and ID.")

    def __init__(self, client: PortkeyJsonClient):
        self._client = client

    def compose(self, request, evidence, report):
        blocks = [f"[{e.evidence_id}] {e.kind.value}; numeric_use={e.numeric_use.value}; "
                  f"warnings={','.join(e.quality_warnings) or 'none'}; citation: {e.citation}\n"
                  f"facts: {'; '.join(f'{f.name}={f.value}' for f in e.facts)}\nsummary: {e.summary[:_summary_chars(e)]}"
                  for e in evidence]
        user = f"QUESTION:\n{request.question}\n\nEVIDENCE:\n" + "\n\n".join(blocks)
        system = self.SYSTEM + (" " + self.BRIEF if request.planning else "")
        parsed, content, usage = self._client.complete(system=system, user=user, schema=DraftResponse.model_json_schema(),
                                                       schema_name="investigation_answer", trace_id=request.request_id)
        try:
            return DraftResponse.model_validate(parsed if parsed is not None else content), usage
        except ValidationError:
            return None, usage


def _summary_chars(record: EvidenceRecord) -> int:
    """TableLens rows get the whole summary (a table answer is its rows); other records the usual excerpt."""
    return 1500 if record.kind == EvidenceKind.DOCUMENT_TABLE and record.numeric_use != NumericUse.NOT_APPROVED else 800


def _restatement(record: EvidenceRecord) -> str:
    if record.kind == EvidenceKind.STRUCTURED_RESULT and record.facts:
        values = "; ".join(f"{f.name} = {f.value}" for f in record.facts[:10])
        return f"Result from {', '.join(record.source_objects)}: {values}."
    if record.kind == EvidenceKind.METRIC_DEFINITION:
        return f"{record.citation}: {record.summary}"
    if record.kind == EvidenceKind.DOCUMENT_TABLE and record.numeric_use != NumericUse.NOT_APPROVED and record.summary:
        return f"{record.citation}: {record.summary[:400]}"                  # TableLens rows (SQL over the PDF table)
    if record.kind == EvidenceKind.DOCUMENT_TABLE:
        return f"A table in {record.citation} is relevant; its values are not verified and are not restated."
    if record.kind in (EvidenceKind.DOCUMENT_PASSAGE, EvidenceKind.TYPED_PLAN_RECORD) and record.summary:
        return f"{record.citation}: {record.summary[:400]}"
    if record.kind == EvidenceKind.TABLE_CANDIDATE:
        return f"Relevant approved table: {', '.join(record.source_objects)}."
    if record.kind in (EvidenceKind.MODEL_PREDICTION, EvidenceKind.SIMILAR_PROJECT):
        return record.summary[:600]
    return ""


# ------------------------------------------------------------------------------------------------- validation
def _support_text(records: list[EvidenceRecord]) -> str:
    return " ".join(f"{r.citation} {r.summary} " + " ".join(f"{f.name} {f.value} {f.unit or ''}" for f in r.facts) for r in records)


def check_claim(claim: DraftClaim, ledger: dict[str, EvidenceRecord]) -> str | None:
    """None when supported, else a reason code."""
    validate = answering("validate")
    cited = [ledger[i] for i in claim.evidence_ids if i in ledger]
    if len(cited) != len(claim.evidence_ids):
        return "unknown_evidence"
    if redact_secrets(claim.text) != claim.text:
        return "secret_shaped_value"
    body = claim.text
    for record in cited:                     # citations are provenance (document ids, pages), not figures
        body = body.replace(record.citation, " ")
    claim_numbers = validate.numbers(body, scale=False)
    allowed = [r for r in cited if r.numeric_use != NumericUse.NOT_APPROVED]
    if claim_numbers - validate.numbers(_support_text(cited)):
        return "unsupported_number"
    if claim_numbers - validate.numbers(_support_text(allowed)):
        return "unapproved_numeric_source"
    if validate.dates(body) - validate.dates(_support_text(cited)):
        return "unsupported_date"
    if CONFIDENCE.search(body) and not CONFIDENCE.search(_support_text(cited)):
        return "unsupported_confidence"
    return None


def validate_response(request: InvestigationRequest, plan: InvestigationPlan | None, draft: DraftResponse | None,
                      ledger: dict[str, EvidenceRecord], report: VerificationReport | None,
                      partial_tasks: tuple[str, ...] = (), cancelled_tasks: tuple[str, ...] = (),
                      failed_tasks: tuple[str, ...] = ()) -> InvestigationResponse:
    kept, removed = [], []
    for claim in (draft.claims if draft else []):
        reason = check_claim(claim, ledger)
        if reason:
            removed.append(reason)
        else:
            kept.append(ResponseClaim(text=claim.text, evidence_ids=tuple(claim.evidence_ids), section=claim.section))
    limitations = list(draft.limitations if draft else [])[:8]
    if report:
        limitations += [f"Missing verified evidence for task {u.split(':')[0].split('.')[-1]} ({u.split(':')[1]})." for u in report.unmet]
        if "pdf_table_value_unverified" in report.quality_warnings:
            limitations.append("PDF table values are cited for context only; they are not verified figures.")
        if "synthetic_training_data" in report.quality_warnings:
            limitations.append("Amendment and re-bidding risks come from models trained on synthetic data; they are not "
                               "validated World Bank risk estimates.")
        if "dev_owner_credentials" in report.quality_warnings:
            limitations.append("Data was read with development credentials (not a production read-only identity).")
        if any(w in report.quality_warnings for w in ("stacked_values_needs_review", "lost_page_break_values_flagged")):
            limitations.append("Some cited table rows are flagged for review by the PDF extraction.")
    if partial_tasks:
        limitations.append(f"{len(partial_tasks)} task(s) returned partial results.")
    if failed_tasks:
        limitations.append(f"{len(failed_tasks)} task(s) failed or were blocked; their evidence is missing.")
    if cancelled_tasks:
        limitations.append(f"{len(cancelled_tasks)} task(s) were not run because an earlier task failed.")
    if removed:
        limitations.append(f"{len(removed)} statement(s) were removed because the evidence did not support them.")
    kept = [c.model_copy(update={"text": mask_pii(c.text)}) for c in kept]          # personal data never leaves in answers
    status = _status(kept, report, partial_tasks + failed_tasks, cancelled_tasks, removed)
    message = " ".join(c.text for c in kept) if kept else "The available evidence is not sufficient to answer this question."
    if status == InvestigationStatus.CONFLICTING_EVIDENCE:
        message = "The evidence conflicts; both values are shown with their sources. " + message
    return InvestigationResponse(
        investigation_id=request.investigation_id, status=status, message=message[:4000], claims=tuple(kept),
        removed_claims=tuple(removed), limitations=tuple(dict.fromkeys(limitations)),
        evidence_ids=tuple(dict.fromkeys(i for c in kept for i in c.evidence_ids)))


def _status(kept, report, partial_tasks, cancelled_tasks, removed) -> InvestigationStatus:
    cited = {i for claim in kept for i in claim.evidence_ids}
    # a conflict decides the status only when the answer relies on it (or nothing else could be said)
    if report and any(not kept or cited & set(c.evidence_ids) for c in report.conflicts):
        return InvestigationStatus.CONFLICTING_EVIDENCE
    if not kept:
        return InvestigationStatus.INSUFFICIENT_EVIDENCE
    if (report and not report.required_complete) or partial_tasks or cancelled_tasks or removed:
        return InvestigationStatus.PARTIAL
    return InvestigationStatus.ANSWERED
