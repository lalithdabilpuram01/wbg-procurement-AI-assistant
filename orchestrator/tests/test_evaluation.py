"""The LangSmith evaluation harness, offline: cases, deterministic evaluators, the judge (fake client), redaction, audit."""
from __future__ import annotations

import json

from evaluation import cases
from evaluation.audit import audit
from evaluation.evaluators import ClaudeJudge, deterministic, has_number, numbers
from evaluation.run import redacted
from wbg_orchestrator.contracts import Usage


def outputs(claims=(), evidence=(), status="answered", **extra):
    return {"job_state": "succeeded", "status": status, "claims": [{"text": t, "evidence_ids": ["ev1"], "section": None} for t in claims],
            "evidence": list(evidence), "usage": {"cost_usd": "0.02"}, "stored_user_text": [], "message": "", **extra}


def evidence(summary="", facts=(), document_id=None, pages=None, kind="document_passage"):
    return {"evidence_id": "ev1", "kind": kind, "citation": "PAD 1, p. 1", "numeric_use": "document_statement", "summary": summary,
            "facts": [{"name": n, "value": v, "unit": None} for n, v in facts], "document_id": document_id, "pages": pages}


def scores(category, out, reference, **inputs):
    return {r["key"]: r["score"] for r in deterministic({"category": category, "case_id": "x", **inputs}, out, reference)["results"]}


def test_numbers_read_thousands_separators_and_scale_words():
    assert numbers("US$1,077.1 million and 28.2% over 3 years") == [1077.1, 1077.1e6, 28.2, 3.0]
    assert has_number(17, numbers("US$17.0 million")) and has_number(61.74, numbers("US$61.74M"))
    assert has_number(1_200_000, numbers("about 1.2 million")) and not has_number(1_300_000, numbers("1.2 million"))


def test_structured_values_are_checked_in_evidence_and_answer():
    out = outputs(["Request for Bids had 120 awards."], [evidence(facts=[("contract_awards", "120"), ("contract_awards", "45")])])
    s = scores("structured", out, {"values": [120.0, 45.0]})
    assert s["gold_values_in_evidence"] == 1.0 and s["gold_values_in_answer"] == 0.5 and s["completed"] == 1.0


def test_documents_need_a_gold_document_and_page_and_unanswerable_ones_must_abstain():
    gold = {"answerable": True, "gold": [{"document_id": "24440102", "pages": [22, 23]}]}
    hit = outputs(["x"], [evidence(document_id="24440102", pages=[23, 24])])
    s = scores("documents", hit, gold)
    assert s["gold_document_cited"] == 1.0 and s["gold_page_cited"] == 1.0
    assert scores("documents", outputs(["x"], [evidence(document_id="24440102", pages=[40, 40])]), gold)["gold_page_cited"] == 0.0
    assert scores("documents", outputs(status="insufficient_evidence"), {"answerable": False})["abstained"] == 1.0


def test_tablelens_needs_every_expected_value_in_the_answer():
    reference = {"values": ["290000000", "140348308.59"]}
    out = outputs(["Estimated US$290 million; actual US$140,348,308.59."], [evidence("Estimated Amount: 290000000.0")])
    s = scores("tablelens", out, reference)
    assert s["value_in_answer"] == 1.0 and s["value_in_evidence"] == 0.0
    assert scores("tablelens", outputs(["The method is ICB."]), {"values": ["ICB"]})["value_in_answer"] == 1.0


def test_guardrails_check_the_status_and_that_the_phone_number_never_stays():
    reference = {"statuses": ["answered"], "check": "pii_masked", "secret": cases.PHONE}
    masked = outputs(stored_user_text=["My phone number is <PHONE_NUMBER>. What are the risks?"])
    leaked = outputs(stored_user_text=[f"My phone number is {cases.PHONE}."])
    assert scores("guardrails", masked, reference)["pii_masked"] == 1.0 and scores("guardrails", leaked, reference)["pii_masked"] == 0.0
    assert scores("guardrails", outputs(status="out_of_scope"), {"statuses": ["out_of_scope", "blocked"]})["guardrail_outcome"] == 1.0


def test_planning_requires_its_evidence_kinds_and_sections():
    out = outputs(["a"], [evidence(kind="model_prediction")] * 3)
    s = scores("planning", out, {"kinds": {"model_prediction": 3, "similar_project": 3}, "min_sections": 4})
    assert s["required_evidence"] == 0.0 and s["brief_sections"] == 0.0


class FakeJson:
    def __init__(self, parsed):
        self.parsed, self.calls = parsed, 0

    def complete(self, **request):
        self.calls += 1
        return self.parsed, "", Usage(llm_calls=1)


def test_the_judge_scores_answered_cases_and_skips_the_rest():
    fake = FakeJson({"groundedness": 5, "relevance": 3, "unsupported_claims": [], "rationale": "ok"})
    judge = ClaudeJudge(fake)
    result = judge({"category": "documents", "case_id": "d", "text": "q"}, outputs(["x"], [evidence("x")]), {})["results"]
    assert {r["key"]: r["score"] for r in result} == {"groundedness": 1.0, "relevance": 0.5}
    assert judge({"category": "guardrails", "case_id": "g"}, outputs(["x"]), {}) == {"results": []}
    assert judge({"category": "documents", "case_id": "d"}, outputs(status="insufficient_evidence"), {}) == {"results": []}
    assert fake.calls == 1


def test_uploads_are_redacted():
    assert cases.PHONE not in json.dumps(redacted({"outputs": {"text": f"call {cases.PHONE}"}}))


def test_cases_cover_every_category_with_references():
    run_sql = lambda sql, params: [{"procurement_method": "RFB", "contract_awards": 120}, {"procurement_method": "RFQ", "contract_awards": 45}]
    sets = {"v3": [{"id": f"V3-{t}-{n}", "type": t, "question": f"q {t} {n}", "gold": [{"document_id": "1", "pages": [1]}]}
                   for t in cases.DOCUMENT_SAMPLE for n in range(3)],
            "v1": [{"id": "N1", "type": "narrative", "question": "q", "gold": [{"document_id": "2", "pages": [2]}]}]}
    built = cases.all_cases(run_sql, sets)
    by = {c: [x for x in built if x.category == c] for c in ("structured", "documents", "tablelens", "planning", "guardrails")}
    assert [len(v) for v in by.values()] == [8, 10, 44, 3, 6]
    assert by["structured"][0].reference["values"] == [120.0, 45.0] and by["tablelens"][1].inputs["table_lens"] is True


def test_the_audit_pairs_tablelens_runs_and_flags_judge_disagreement(tmp_path):
    def row(case_id, category, table_lens, value, grounded):
        claims = [{"text": "The value is 18." if value else "Not stated.", "evidence_ids": ["e"], "section": None}]
        return {"case_id": case_id, "category": category, "error": None, "reference": {"values": ["18"]},
                "inputs": {"table_lens": table_lens, "case_id": case_id, "category": category},
                "outputs": {"seconds": 3.0, "job_state": "succeeded", "status": "answered", "claims": claims, "evidence": [], "usage": {}},
                "scores": [{"key": "completed", "score": 1.0, "comment": ""}, {"key": "value_in_answer", "score": value, "comment": ""},
                           {"key": "groundedness", "score": grounded, "comment": "fine"}, {"key": "relevance", "score": 1.0, "comment": ""},
                           {"key": "cost_usd", "score": 0.0, "comment": ""}]}
    rows = [row("T01-off", "tablelens", False, 0.0, 1.0), row("T01-on", "tablelens", True, 1.0, 1.0)]
    (tmp_path / "results.json").write_text(json.dumps({"experiment": "e", "rows": rows}))
    (tmp_path / "dataset.json").write_text(json.dumps([{"inputs": {"case_id": "T01-off"}}, {"inputs": {"case_id": "T01-on"}}]))
    report = audit(tmp_path, tmp_path / "dataset.json")
    assert report["tablelens"]["on_wins"] == ["T01"] and report["coverage"]["missing"] == []
    assert [d["case"] for d in report["judge"]["disagreements_with_exact_checks"]] == ["T01-off"]
    assert report["rescored_changes"] == []
