"""The state summaries written to the log after every graph step: merged like LangGraph, masked, and short."""
from __future__ import annotations

import json

from wbg_orchestrator.state_view import MAX_LISTED, apply_update, describe_update, summarize_state

REQUEST = {"investigation_id": "inv_1", "request_id": "req_1", "mode": "auto",
           "question": "Contracts awarded to jane.doe@example.org in 2024?"}


def evidence(n: int) -> dict:
    return {"evidence_id": f"ev_{n}", "task_id": "plan_1.t1", "kind": "structured_result", "validation_status": "verified",
            "summary": "row values that must never be logged", "facts": [{"value": 42}]}


def test_updates_merge_with_the_graph_reducers():
    before = {"request": REQUEST, "task_states": {"plan_1.t1": "pending"}, "evidence": [evidence(1)], "usage": {"llm_calls": 1}}
    after = apply_update(before, {"task_states": {"plan_1.t1": "running"}, "evidence": [evidence(2)], "usage": {"llm_calls": 2}})
    assert after["task_states"] == {"plan_1.t1": "running"}
    assert [e["evidence_id"] for e in after["evidence"]] == ["ev_1", "ev_2"] and after["usage"]["llm_calls"] == 3
    assert before["usage"]["llm_calls"] == 1                                       # the original state is untouched


def test_an_update_the_reducers_refuse_leaves_the_field_as_it_was():
    before = {"task_states": {"plan_1.t1": "succeeded"}}
    assert apply_update(before, {"task_states": {"plan_1.t1": "pending"}})["task_states"] == {"plan_1.t1": "succeeded"}


def test_changes_show_transitions_and_the_records_added():
    before = {"task_states": {"plan_1.t1": "running"}}
    changes = describe_update({"task_states": {"plan_1.t1": "succeeded"}, "evidence": [evidence(1)], "errors": []}, before)
    assert changes["task_states"] == {"t1": "running -> succeeded"}
    assert changes["evidence"][0]["evidence_id"] == "ev_1" and "errors" not in changes


def test_the_summary_masks_text_and_leaves_out_rows():
    summary = summarize_state({"request": REQUEST, "phase": "verified", "evidence": [evidence(1)],
                               "response": {"status": "answered", "message": "Write to jane.doe@example.org", "claims": [1]}})
    dump = json.dumps(summary)
    assert "jane.doe" not in dump and "<EMAIL_ADDRESS>" in summary["request"]["question"]
    assert "row values" not in dump and '"value": 42' not in dump
    assert summary["evidence"] == {"count": 1, "verified": 1, "by_kind": {"structured_result": 1}}
    assert summary["response"]["status"] == "answered" and summary["response"]["claims"] == 1


def test_long_lists_are_cut_with_their_count():
    changes = describe_update({"evidence": [evidence(n) for n in range(MAX_LISTED + 5)]})
    assert len(changes["evidence"]) == MAX_LISTED + 1 and changes["evidence"][-1] == "... 5 more"
