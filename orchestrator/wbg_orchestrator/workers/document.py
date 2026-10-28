"""Thin adapter over the existing Document Intelligence (RAG) components.

Operations (read-only):
  answer_document_question -> AnswerPipeline(generator, retriever=build_retriever(release)).answer(question)
                              (guarded retrieval, evidence selection, page checks, generation, claim validation)
  retrieve_passages        -> build_retriever(release).search(question, method="hybrid", k=5, guarded=True)
The RAG release is explicit per investigation: "pilot_v3" (default; 237 documents, bge-m3 vectors in Lakebase),
"pilot_v3_tablelens" (the same, with the 10 pilot PDFs' tables answered by SQL), "pilot_v2" or "pilot_v2_1". Every
citation becomes a ledger record with its document, version, pages, page check and corpus release. Table windows from
the text index are never approved numerical evidence; extraction flags of pilot_v2_1 rows become quality warnings.

TableLens (pilot_v3_tablelens only): the question finds a table by its description, Claude writes one SELECT over that
table's registered schema, TableLens validates and runs it read-only. The returned rows (or a total and the rows it used)
become one DOCUMENT_TABLE record per table with document, table, rows and pages; values from rows the extraction did
not flag are document statements, flagged rows are not approved.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Protocol

from conversation.redaction import redact_secrets
from rag.tablelens.query import labelled_rows

from ..contracts import (MAX_SUMMARY_CHARS, AgentTask, ErrorCategory, EvidenceKind, EvidenceRecord, ExecutionError, Fact,
                         NumericUse, SourceSystem, TaskState, Usage, ValidationStatus, WorkerName, new_id)
from .base import TicketAuthority, TransientWorkerError, WorkerContext, WorkerOutput

VERIFIED_PAGE_CHECKS = {"verified", "verified_with_text_exception"}
STATUS_MAP = {"answered": TaskState.SUCCEEDED, "partial": TaskState.PARTIAL, "conflicting_evidence": TaskState.SUCCEEDED}
WINDOW = re.compile(r":w(\d+)-(\d+)$")
MAX_EXCERPT = 600


class AnswerPipelineLike(Protocol):
    def answer(self, question: str) -> Any: ...          # rag/answering/schemas.AnswerResponse


class RetrieverLike(Protocol):
    def search(self, q: str, method: str = "hybrid", k: int = 10, guarded: bool = True, rerank: bool = False) -> dict: ...
    def guard(self, q: str) -> dict: ...


class TableLensLike(Protocol):
    release: str
    def ask(self, question: str, write_sql: Callable, scope_projects: set[str] | None = None) -> Any: ...   # TableAnswer


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
                 authority: TicketAuthority, table_flags: Callable[[str], TableFlagIndex | None] | None = None,
                 table_lens_for: Callable[[str], TableLensLike | None] | None = None,
                 table_writer: Callable[[str], Callable] | None = None):
        """table_lens_for: the release's TableLens or None; table_writer: request id -> SQL writer (Claude)."""
        self._pipeline_for, self._retriever_for = pipeline_for, retriever_for
        self._authority = authority
        self._table_flags = table_flags or (lambda release: None)
        self._table_lens_for = table_lens_for or (lambda release: None)
        self._table_writer = table_writer

    def retriever(self, release: str) -> RetrieverLike:
        """The release's (cached) retriever, shared with the planning worker's description similarity."""
        return self._retriever_for(release)

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
        question = scoped_question(task.question, context.upstream_evidence)
        response = self._pipeline_for(context.rag_release).answer(question)
        usage = _usage(response)
        if response.status == "withheld":
            return WorkerOutput(TaskState.BLOCKED, usage=usage, error=_error(
                task, ErrorCategory.GUARDRAIL, "personal_data_withheld", "Personal contact data is not returned."))
        tables, table_usage = self._tables(task, question, context)
        usage = _combined(usage, table_usage)
        if response.status == "insufficient_evidence" or not response.citations:
            if tables:
                return WorkerOutput(TaskState.SUCCEEDED, tables, usage, notes=("tablelens_only",))
            return WorkerOutput(TaskState.FAILED, usage=usage, error=_error(
                task, ErrorCategory.NO_EVIDENCE, "insufficient_evidence", "The documents do not contain enough evidence."))
        conflict = response.status == "conflicting_evidence"
        flags = self._table_flags(context.rag_release)
        evidence = tables + tuple(_citation_evidence(task, c.model_dump(), context.rag_release, flags, conflict)
                                  for c in response.citations)
        notes = (("conflicting_evidence",) if conflict else ()) + \
            ((f"removed_claims:{len(response.removed_claims)}",) if response.removed_claims else ())
        status = TaskState.SUCCEEDED if tables else STATUS_MAP.get(response.status, TaskState.PARTIAL)
        return WorkerOutput(status, evidence, usage, notes=notes)

    def _retrieve(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        question = scoped_question(task.question, context.upstream_evidence)
        result = self._retriever_for(context.rag_release).search(question, method="hybrid", k=5, guarded=True)
        if result["status"] == "withheld_personal_data":
            return WorkerOutput(TaskState.BLOCKED, error=_error(task, ErrorCategory.GUARDRAIL, "personal_data_withheld",
                                                                  "Personal contact data is not returned."))
        tables, usage = self._tables(task, question, context)
        if result["status"] != "ok" or not result["results"]:
            if tables:
                return WorkerOutput(TaskState.SUCCEEDED, tables, usage, notes=("tablelens_only",))
            return WorkerOutput(TaskState.FAILED, usage=usage, error=_error(task, ErrorCategory.NO_EVIDENCE, result["status"],
                                                                              "No document passage matches."))
        flags = self._table_flags(context.rag_release)
        return WorkerOutput(TaskState.SUCCEEDED, tables + tuple(_citation_evidence(task, r, context.rag_release, flags, False)
                                                                for r in result["results"]), usage)

    def _tables(self, task: AgentTask, question: str, context: WorkerContext) -> tuple[tuple[EvidenceRecord, ...], Usage]:
        """TableLens evidence when the release answers PDF tables by SQL; none otherwise or outside the guard's scope."""
        lens = self._table_lens_for(context.rag_release)
        if lens is None or self._table_writer is None:
            return (), Usage()
        guard = self._retriever_for(context.rag_release).guard(question)
        if guard["status"] != "ok":
            return (), Usage()
        answer = lens.ask(question, self._table_writer(context.request_id), guard["scope_projects"])
        used = answer.usage
        usage = Usage(llm_calls=used.get("llm_calls", 0), input_tokens=used.get("input_tokens", 0),
                      output_tokens=used.get("output_tokens", 0))
        return tuple(_table_evidence(task, r, context.rag_release, lens.release) for r in answer.results), usage


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


def _table_evidence(task: AgentTask, result: Any, release: str, table_release: str) -> EvidenceRecord:
    """One TableLens result (rows, or a total and the rows it used) as a cited DOCUMENT_TABLE record."""
    table = result.table
    rows = sorted({row for row, _ in result.cited_rows})
    pages = sorted({page for _, page in result.cited_rows}) or sorted(table.pages)
    lines = (["The question's filter matched no row; all rows of the table are shown."] if result.broadened else []) + \
        labelled_rows(result)
    if result.aggregate and rows:
        lines.append(f"computed from rows {row_ranges(rows)} of the table")
    verified = not result.flagged
    facts = tuple(Fact(name=str(k)[:120], value=str(v)[:200]) for k, v in result.rows[0].items()) \
        if result.aggregate and len(result.rows) == 1 else ()
    rows_label = f", rows {row_ranges(rows)}" if rows else ""
    return EvidenceRecord(
        evidence_id=new_id("ev"), investigation_id=task.investigation_id, task_id=task.task_id,
        source_system=SourceSystem.RAG_PDF_CORPUS, kind=EvidenceKind.DOCUMENT_TABLE, project_ids=(table.project_id,),
        document_id=table.document_id, table_id=table.table_id, unit_id=f"tablelens:{table.table_id}",
        pages=(pages[0], pages[-1]), source_version=f"rag:{release}",
        citation=f"{table.doc_type} {table.document_id}, table '{(table.section or 'untitled')[-90:]}', "
                 f"p. {pages[0]}-{pages[-1]}{rows_label}"[:300],
        provenance=(("tablelens_release", table_release), ("physical_table", table.physical_name),
                    ("sql_sha256", hashlib.sha256(result.sql.encode()).hexdigest()), ("sql", result.sql[:500]),
                    ("rows", row_ranges(rows) or "-"), ("corpus_release", release)),
        validation_status=ValidationStatus.VERIFIED if verified else ValidationStatus.UNVERIFIED,
        numeric_use=NumericUse.DOCUMENT_STATEMENT if verified else NumericUse.NOT_APPROVED,
        quality_warnings=("tablelens_sql_result",) + (("tablelens_filter_broadened",) if result.broadened else ()) +
                         (() if verified else ("stacked_values_needs_review",)),
        facts=facts, summary=redact_secrets("\n".join(lines))[:MAX_SUMMARY_CHARS])


def row_ranges(rows: list[int]) -> str:
    """[1, 2, 3, 5] -> '1-3, 5'."""
    spans: list[list[int]] = []
    for row in rows:
        if spans and row == spans[-1][1] + 1:
            spans[-1][1] = row
        else:
            spans.append([row, row])
    return ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in spans)


def _combined(a: Usage, b: Usage) -> Usage:
    return Usage(llm_calls=a.llm_calls + b.llm_calls, input_tokens=a.input_tokens + b.input_tokens,
                 output_tokens=a.output_tokens + b.output_tokens)


def _usage(response: Any) -> Usage:
    meta = (response.trace or {}).get("generator_meta", {}) if hasattr(response, "trace") else {}
    if not getattr(response, "generator_is_llm", False):
        return Usage()
    return Usage(llm_calls=int(meta.get("attempts", 1) or 1), input_tokens=int(meta.get("prompt_tokens") or 0),
                 output_tokens=int(meta.get("output_tokens") or 0))


def scoped_question(question: str, upstream: tuple[EvidenceRecord, ...]) -> str:
    """When an upstream step chose similar projects (Planning Brief), name them: the retrieval guard then searches only
    their documents."""
    projects = [e.project_ids[0] for e in upstream if e.kind == EvidenceKind.SIMILAR_PROJECT and e.project_ids
                and any(f.name == "document_scope" and f.value == "yes" for f in e.facts)]
    return f"{question} (projects {', '.join(projects)})" if projects else question


def _error(task: AgentTask, category: ErrorCategory, code: str, message: str) -> ExecutionError:
    return ExecutionError(error_id=new_id("err"), investigation_id=task.investigation_id, task_id=task.task_id,
                          node="execute", category=category, code=code[:80], safe_message=message)


@lru_cache(maxsize=4)
def table_flag_index(processed_dir: str) -> TableFlagIndex:
    return TableFlagIndex(Path(processed_dir))
