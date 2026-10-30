"""Documents uploaded after the corpus release, searched next to it (offline: fake pipeline, retriever and search)."""
from __future__ import annotations

from conversation.routing.routes import Route

from wbg_orchestrator.contracts import EvidenceKind, NumericUse, TaskState, ValidationStatus
from wbg_orchestrator.planner import build_plan
from wbg_orchestrator.uploaded_documents import UploadedDocumentSearch, in_corpus
from wbg_orchestrator.workers.base import TicketAuthority, WorkerContext
from wbg_orchestrator.workers.document import DocumentIntelligenceWorker

from tests.orchestrator.helpers import FakePipeline, FakeRetriever, answer_response, plan, request, task

HIT = {"chunk_id": "40150001:c:0007", "document_id": "40150001", "text": "Lessons: bidding documents were issued late.",
       "page_start": 34, "page_end": 35, "doc_type": "ICR", "project_id": "P148850", "title": "ICR", "similarity": 0.71}


class ScopedRetriever(FakeRetriever):
    def guard(self, q):
        return {"status": "ok", "scope_projects": {"P148850"}, "exact_units": [], "notes": []}


def run(search, *, response=None):
    authority, calls = TicketAuthority(), []

    def uploaded(question, projects):
        calls.append(projects)
        return search(question, projects)
    worker = DocumentIntelligenceWorker(lambda r: FakePipeline(response), lambda r: ScopedRetriever(), authority, uploaded=uploaded)
    req = request("What lessons did the new ICR record for P148850?", routes=(Route.DOCUMENT_RAG,), release="pilot_v3")
    built = build_plan(plan(task("a", worker="document_intelligence", operation="answer_document_question")), req,
                       produced_by="fake_planner")
    context = WorkerContext(ticket=authority.issue(built.tasks[0], 1), authorization=req.authorization, request_id="req-1",
                            rag_release="pilot_v3", remaining_seconds=30)
    return worker.run(built.tasks[0], context), calls


def test_an_uploaded_passage_is_cited_next_to_the_corpus_within_the_project_scope():
    out, calls = run(lambda q, p: [HIT])
    uploaded = [e for e in out.evidence if e.source_version == "kg:uploaded"]
    assert out.status == TaskState.SUCCEEDED and calls == [{"P148850"}] and len(out.evidence) > 1
    [record] = uploaded
    assert record.kind == EvidenceKind.DOCUMENT_PASSAGE and record.pages == (34, 35) and "(uploaded)" in record.citation
    assert record.validation_status == ValidationStatus.UNVERIFIED and record.numeric_use == NumericUse.NOT_APPROVED


def test_an_uploaded_document_alone_can_answer_when_the_corpus_has_nothing():
    out, _ = run(lambda q, p: [HIT], response=answer_response(status="insufficient_evidence", citations=[]))
    assert out.status == TaskState.SUCCEEDED and [e.document_id for e in out.evidence] == ["40150001"]


def test_a_failing_upload_search_never_breaks_the_corpus_answer():
    def broken(q, p):
        raise ConnectionError("Lakebase unreachable")
    out, _ = run(broken)
    assert out.status == TaskState.SUCCEEDED and all(e.source_version != "kg:uploaded" for e in out.evidence)


def test_corpus_documents_are_not_searched_twice():
    corpus = frozenset({"34345370", "40025990"})
    assert in_corpus("34345370", corpus) and in_corpus("procurement_plans-40025990-p148850", corpus)
    assert not in_corpus("40150001", corpus)


class FakeCursor:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.store["sql"].append((sql, params))
        self.rows = [{"document_id": d} for d in ("34345370", "40150001")] if "DISTINCT" in sql else \
            [HIT, {**HIT, "chunk_id": "40150001:c:0008", "similarity": 0.2}]

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, row_factory=None):
        return FakeCursor(self.store)


def test_the_search_skips_corpus_documents_and_weak_matches():
    store = {"sql": []}
    search = UploadedDocumentSearch(lambda: FakeConnection(store), lambda q: [0.5] * 1024, corpus=frozenset({"34345370"}))
    hits = search.search("late bidding documents", projects={"P148850"})
    sql, params = store["sql"][-1]
    assert [h["chunk_id"] for h in hits] == ["40150001:c:0007"]
    assert params["documents"] == ["40150001"] and params["projects"] == ["P148850"] and "halfvec" in sql
