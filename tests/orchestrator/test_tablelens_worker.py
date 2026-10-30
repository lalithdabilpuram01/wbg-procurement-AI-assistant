"""TableLens in the document worker and the per-message switch (offline: fake TableLens, pipeline and retriever)."""
from __future__ import annotations

from conversation.routing.routes import Route

from rag.tablelens.query import TableAnswer, TableResult
from tests.rag.tablelens.test_query import TABLE, T
from wbg_orchestrator.contracts import EvidenceKind, NumericUse, TaskState, ValidationStatus
from wbg_orchestrator.planner import build_plan
from wbg_orchestrator.workers.base import TicketAuthority, WorkerContext
from wbg_orchestrator.workers.document import DocumentIntelligenceWorker, row_ranges

from tests.orchestrator.helpers import FakePipeline, FakeRetriever, answer_response, plan, request, task

LOOKUP = TableResult(TABLE, f"SELECT project_components, ida_financing, _row, _page FROM {T} WHERE project_components ILIKE '%inst%'",
                     ["project_components", "ida_financing", "_row", "_page"],
                     [{"project_components": "2. Institutional development", "ida_financing": 18.0, "_row": 2, "_page": 22}],
                     aggregate=False, cited_rows=[(2, 22)])
TOTAL = TableResult(TABLE, f"SELECT SUM(ida_financing) AS total FROM {T} WHERE _quality = 'ok' LIMIT 50", ["total"],
                    [{"total": 370.0}], aggregate=True, cited_rows=[(1, 22), (2, 22), (3, 22)])


class FakeLens:
    release = "tablelens_pilot"

    def __init__(self, results):
        self.results, self.calls = results, []

    def ask(self, question, write_sql, scope_projects=None):
        self.calls.append((question, scope_projects))
        return TableAnswer(self.results, {"llm_calls": 1, "input_tokens": 500, "output_tokens": 40}, [])


class ScopedRetriever(FakeRetriever):
    def guard(self, q):
        return {"status": "ok", "scope_projects": {"P148850"}, "exact_units": [], "notes": []}


def run(results, *, release="pilot_v3_tablelens", response=None, operation="answer_document_question"):
    authority, lens = TicketAuthority(), FakeLens(results)
    worker = DocumentIntelligenceWorker(lambda r: FakePipeline(response), lambda r: ScopedRetriever(), authority,
                                        table_lens_for=lambda r: lens if r == "pilot_v3_tablelens" else None,
                                        table_writer=lambda request_id: (lambda q, s: (None, {})))
    req = request("How much IDA financing goes to institutional development?", routes=(Route.DOCUMENT_RAG,), release=release)
    built = build_plan(plan(task("a", worker="document_intelligence", operation=operation)), req, produced_by="fake_planner")
    context = WorkerContext(ticket=authority.issue(built.tasks[0], 1), authorization=req.authorization, request_id="req-1",
                            rag_release=release, remaining_seconds=30)
    return worker.run(built.tasks[0], context), lens


def test_table_rows_become_verified_cited_table_evidence_next_to_the_passages():
    out, lens = run([LOOKUP])
    table, passage = out.evidence
    assert out.status == TaskState.SUCCEEDED and lens.calls[0][1] == {"P148850"}
    assert table.kind == EvidenceKind.DOCUMENT_TABLE and table.validation_status == ValidationStatus.VERIFIED
    assert table.numeric_use == NumericUse.DOCUMENT_STATEMENT and table.pages == (22, 22) and "rows 2" in table.citation
    assert "IDA Financing: 18.0" in table.summary and table.provenance_value("physical_table") == TABLE.physical_name
    assert passage.kind == EvidenceKind.DOCUMENT_PASSAGE and out.usage.llm_calls == 1


def test_a_total_cites_the_rows_it_used_and_keeps_the_value_as_a_fact():
    out, _ = run([TOTAL])
    table = out.evidence[0]
    assert "computed from rows 1-3" in table.summary and [(f.name, f.value) for f in table.facts] == [("total", "370.0")]


def test_flagged_rows_are_not_approved_figures():
    flagged = TableResult(**{**LOOKUP.__dict__, "flagged": True})
    table = run([flagged])[0].evidence[0]
    assert table.numeric_use == NumericUse.NOT_APPROVED and "stacked_values_needs_review" in table.quality_warnings


def test_tables_alone_answer_when_the_text_search_finds_nothing():
    out, _ = run([LOOKUP], response=answer_response(status="insufficient_evidence", citations=[]))
    assert out.status == TaskState.SUCCEEDED and [e.kind for e in out.evidence] == [EvidenceKind.DOCUMENT_TABLE]


def test_other_releases_never_call_tablelens():
    out, lens = run([LOOKUP], release="pilot_v3")
    assert lens.calls == [] and all(e.kind != EvidenceKind.DOCUMENT_TABLE for e in out.evidence)


def test_row_ranges_are_compact():
    assert row_ranges([1, 2, 3, 5, 7, 8]) == "1-3, 5, 7-8" and row_ranges([]) == ""
