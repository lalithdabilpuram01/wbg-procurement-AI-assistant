"""`wbg-logs`: one job from the JSON log as an indented tree, ending with where it failed."""
from __future__ import annotations

import json

from wbg_orchestrator.log_view import main

LINES = [
    {"time": "2026-10-30T10:00:00.000+00:00", "level": "INFO", "message": "step started", "job_id": "job_old",
     "step": "job.message", "step_path": "job.message", "depth": 0},
    {"time": "2026-10-30T10:00:01.000+00:00", "level": "INFO", "message": "step started", "job_id": "job_1",
     "job_kind": "message", "step": "job.message", "step_path": "job.message", "depth": 0, "trace_id": "abc"},
    {"time": "2026-10-30T10:00:01.100+00:00", "level": "INFO", "message": "state updated", "job_id": "job_1",
     "step": "orchestrator.node.dispatch", "step_path": "job.message > orchestrator.node.dispatch", "depth": 1,
     "changes": {"task_states": {"t1": "pending -> running"}, "phase": "executing"}, "state": {"phase": "executing"}},
    {"time": "2026-10-30T10:00:01.200+00:00", "level": "INFO", "message": "next step", "job_id": "job_1",
     "step": "job.message", "depth": 0, "from_step": "dispatch", "to_step": "execute", "phase": "executing"},
    {"time": "2026-10-30T10:00:01.300+00:00", "level": "ERROR", "message": "step failed", "job_id": "job_1",
     "step": "worker.structured_data", "step_path": "job.message > orchestrator.node.execute > worker.structured_data",
     "depth": 2, "error_type": "KeyError", "error_location": "gateway.py:88:run", "error_message": "'column'",
     "traceback": "Traceback (most recent call last):\n  ...\nKeyError: 'column'"},
    {"time": "2026-10-30T10:00:01.400+00:00", "level": "INFO", "message": "job finished", "job_id": "job_1", "status": "answered"},
]


def run(tmp_path, capsys, *args: str) -> str:
    log = tmp_path / "app.jsonl"
    log.write_text("\n".join(json.dumps(line) for line in LINES) + "\nnot json\n")
    assert main(["--log", str(log), *args]) == 0
    return capsys.readouterr().out


def test_the_latest_job_reads_as_a_tree_and_names_the_failed_step(tmp_path, capsys):
    out = run(tmp_path, capsys)
    assert "Job job_1" in out and "job_old" not in out and "abc" in out
    assert "state: tasks t1: pending -> running; phase=executing" in out
    assert "-> next step: execute" in out
    assert "KeyError at gateway.py:88:run" in out
    assert "FAILED in: job.message > orchestrator.node.execute > worker.structured_data" in out


def test_state_and_traceback_are_printed_on_request(tmp_path, capsys):
    out = run(tmp_path, capsys, "job_1", "--state", "--traceback")
    assert '"phase": "executing"' in out and "Traceback (most recent call last):" in out


def test_the_job_list_marks_jobs_with_errors(tmp_path, capsys):
    out = run(tmp_path, capsys, "--list")
    assert "job_old" in out and "job_1" in out and "errors=1" in out
