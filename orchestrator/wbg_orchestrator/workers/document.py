"""Thin adapter over the existing Document Intelligence (RAG) components.

Operations (read-only):
  answer_document_question -> AnswerPipeline(generator, retriever=build_retriever(release)).answer(question)
                              (guarded retrieval, evidence selection, page checks, generation, claim validation)
  retrieve_passages        -> build_retriever(release).search(question, method="hybrid", k=5, guarded=True)
The RAG release is explicit per investigation: "pilot_v2" (current default) or "pilot_v2_1" (opt-in). Every citation
becomes a ledger record with its document, version, pages, page check and corpus release. PDF table values are never
approved numerical evidence; extraction flags of pilot_v2_1 rows become quality warnings.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Protocol

from conversation.redaction import redact_secrets

from ..contracts import (AgentTask, ErrorCategory, EvidenceKind, EvidenceRecord, ExecutionError, NumericUse, SourceSystem,
                         TaskState, Usage, ValidationStatus, WorkerName, new_id)
from .base import TicketAuthority, TransientWorkerError, WorkerContext, WorkerOutput

VERIFIED_PAGE_CHECKS = {"verified", "verified_with_text_exception"}
STATUS_MAP = {"answered": TaskState.SUCCEEDED, "partial": TaskState.PARTIAL, "conflicting_evidence": TaskState.SUCCEEDED}
WINDOW = re.compile(r":w(\d+)-(\d+)$")
MAX_EXCERPT = 600


class AnswerPipelineLike(Protocol):
    def answer(self, question: str) -> Any: ...          # rag/answering/schemas.AnswerResponse


class RetrieverLike(Protocol):
    def search(self, q: str, method: str = "hybrid", k: int = 10, guarded: bool = True, rerank: bool = False) -> dict: ...


class TableFlagIndex:
    """Extraction flags of table rows (pilot_v2_1 `extraction_flags`), read from the sanitised shared table rows."""

    def __init__(self, processed_dir: Path):
        self._flags: dict[tuple[str, int], dict] = {}
        path = processed_dir / "shared" / "table_rows.jsonl"
        if path.is_file():
            for line in path.read_text().splitlines():
                row = json.loads(line)
                if row.get("extraction_flags"):
                    self._flags[(row["table_id"], row["row_index"])] = row["extraction_flags"]

    def warnings(self, table_id: str | None, unit_id: str | None) -> tuple[str, ...]:
        match = WINDOW.search(unit_id or "")
        if not table_id or not match:
            return ()
        found = set()
        for row in range(int(match.group(1)), int(match.group(2)) + 1):
            flags = self._flags.get((table_id, row), {})
            if "stacked_values" in flags:
                found.add("stacked_values_needs_review")
            if "lost_values" in flags:
                found.add("lost_page_break_values_flagged")
        return tuple(sorted(found))


class DocumentIntelligenceWorker:
    name = WorkerName.DOCUMENT
    operations = frozenset({"answer_document_question", "retrieve_passages"})

    def __init__(self, pipeline_for: Callable[[str], AnswerPipelineLike], retriever_for: Callable[[str], RetrieverLike],
                 authority: TicketAuthority, table_flags: Callable[[str], TableFlagIndex | None] | None = None):
        self._pipeline_for, self._retriever_for = pipeline_for, retriever_for
        self._authority = authority
        self._table_flags = table_flags or (lambda release: None)

    def run(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        self._authority.verify(context.ticket, task)
        if context.rag_release not in context.authorization.scope.rag_releases:
            return WorkerOutput(TaskState.FAILED, error=_error(task, ErrorCategory.AUTHORIZATION, "rag_release_not_in_scope",
                                                               "The requested document corpus release is not approved."))
        try:
            if task.operation == "answer_document_question":
                return self._answer(task, context)
            if task.operation == "retrieve_passages":
                return self._retrieve(task, context)
        except TimeoutError as error:
            raise TransientWorkerError("document_timeout") from error
        except ConnectionError as error:
            raise TransientWorkerError("document_connection") from error
        return WorkerOutput(TaskState.FAILED, error=_error(task, ErrorCategory.AUTHORIZATION, "unknown_operation",
                                                             "The operation is not offered by this worker."))

    def _answer(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        response = self._pipeline_for(context.rag_release).answer(task.question)
        usage = _usage(response)
        if response.status == "withheld":
            return WorkerOutput(TaskState.BLOCKED, usage=usage, error=_error(
                task, ErrorCategory.GUARDRAIL, "personal_data_withheld", "Personal contact data is not returned."))
        if response.status == "insufficient_evidence" or not response.citations:
            return WorkerOutput(TaskState.FAILED, usage=usage, error=_error(
                task, ErrorCategory.NO_EVIDENCE, "insufficient_evidence", "The documents do not contain enough evidence."))
        conflict = response.status == "conflicting_evidence"
        flags = self._table_flags(context.rag_release)
        evidence = tuple(_citation_evidence(task, c.model_dump(), context.rag_release, flags, conflict)
                         for c in response.citations)
        notes = (("conflicting_evidence",) if conflict else ()) + \
            ((f"removed_claims:{len(response.removed_claims)}",) if response.removed_claims else ())
        return WorkerOutput(STATUS_MAP.get(response.status, TaskState.PARTIAL), evidence, usage, notes=notes)

    def _retrieve(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        result = self._retriever_for(context.rag_release).search(task.question, method="hybrid", k=5, guarded=True)
        if result["status"] == "withheld_personal_data":
            return WorkerOutput(TaskState.BLOCKED, error=_error(task, ErrorCategory.GUARDRAIL, "personal_data_withheld",
                                                                  "Personal contact data is not returned."))
        if result["status"] != "ok" or not result["results"]:
            return WorkerOutput(TaskState.FAILED, error=_error(task, ErrorCategory.NO_EVIDENCE, result["status"],
                                                                 "No document passage matches."))
        flags = self._table_flags(context.rag_release)
        return WorkerOutput(TaskState.SUCCEEDED, tuple(_citation_evidence(task, r, context.rag_release, flags, False)
                                                         for r in result["results"]))


# ------------------------------------------------------------------------------------------------------ helpers
def _citation_evidence(task: AgentTask, c: dict[str, Any], release: str, flags: TableFlagIndex | None,
                       conflict: bool) -> EvidenceRecord:
    unit_type = c.get("unit_type")
    page_check = c.get("page_check")
    verified = page_check in VERIFIED_PAGE_CHECKS
    warnings = list(flags.warnings(c.get("table_id"), c.get("unit_id")) if flags else ())
    if unit_type == "table":
        kind, source, numeric = EvidenceKind.DOCUMENT_TABLE, SourceSystem.RAG_PDF_CORPUS, NumericUse.NOT_APPROVED
        warnings.append("pdf_table_value_unverified")
    elif unit_type == "activity":
        kind, source = EvidenceKind.TYPED_PLAN_RECORD, SourceSystem.TYPED_PLAN_RECORDS
        numeric = NumericUse.DOCUMENT_STATEMENT if verified else NumericUse.NOT_APPROVED
        warnings.append("pdf_extracted_typed_record")
    else:
        kind, source = EvidenceKind.DOCUMENT_PASSAGE, SourceSystem.RAG_PDF_CORPUS
        numeric = NumericUse.DOCUMENT_STATEMENT if verified else NumericUse.NOT_APPROVED
    if page_check is None:
        warnings.append("page_not_checked")
    if conflict:
        warnings.append("conflicting_evidence")
    start, end = c.get("physical_page_start"), c.get("physical_page_end")
    label = f", printed p. {c['printed_page_label']}" if c.get("printed_page_label") else ""
    return EvidenceRecord(
        evidence_id=new_id("ev"), investigation_id=task.investigation_id, task_id=task.task_id, source_system=source, kind=kind,
        project_ids=(c["project_id"],) if c.get("project_id") else (), document_id=c.get("document_id"),
        document_version_id=c.get("document_version_id"), table_id=c.get("table_id"), unit_id=c.get("unit_id"),
        pages=(start, end) if start is not None and end is not None else None, source_version=f"rag:{release}",
        citation=f"{c.get('doc_type', 'document')} {c.get('document_id')}, p. {start}-{end}{label}"[:300],
        provenance=tuple((k, str(v)) for k, v in (("unit_id", c.get("unit_id")), ("page_check", page_check),
                                                   ("corpus_release", release), ("source_url", c.get("source_url"))) if v),
        validation_status=ValidationStatus.VERIFIED if verified else ValidationStatus.UNVERIFIED,
        numeric_use=numeric, quality_warnings=tuple(dict.fromkeys(warnings)), summary=redact_secrets(c.get("excerpt") or "")[:MAX_EXCERPT])


def _usage(response: Any) -> Usage:
    meta = (response.trace or {}).get("generator_meta", {}) if hasattr(response, "trace") else {}
    if not getattr(response, "generator_is_llm", False):
        return Usage()
    return Usage(llm_calls=int(meta.get("attempts", 1) or 1), input_tokens=int(meta.get("prompt_tokens") or 0),
                 output_tokens=int(meta.get("output_tokens") or 0))


def _error(task: AgentTask, category: ErrorCategory, code: str, message: str) -> ExecutionError:
    return ExecutionError(error_id=new_id("err"), investigation_id=task.investigation_id, task_id=task.task_id,
                          node="execute", category=category, code=code[:80], safe_message=message)


@lru_cache(maxsize=4)
def table_flag_index(processed_dir: str) -> TableFlagIndex:
    return TableFlagIndex(Path(processed_dir))
