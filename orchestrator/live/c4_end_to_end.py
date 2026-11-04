"""Milestone C4: one real procurement investigation end to end through the local API (paid, bounded).

Everything is real:
- the FastAPI app over the conversation service and the LangGraph engine;
- Claude through Portkey (planner, NL2SQL, answer), behind `BudgetedClaudeClient`, with the C4 ledger at
  10 attempts / $2.00 and any limit stopping the run;
- read-only SQL with the `wbg-readonly` service principal, at most 3 executions per investigation (budget);
- document retrieval over the default corpus;
- checkpoints on the Lakebase `dev-orchestrator` branch;
- conversations in the local PostgreSQL store.

The app is served in-process with FastAPI's TestClient, so the test user's token never leaves memory.

    cd orchestrator
    WBG_ALLOW_LIVE_LLM_CALLS=true LOGFIRE_SEND_TO_LOGFIRE=true env -u VIRTUAL_ENV PYTHONPATH=..:. \
        uv run --locked python -B -m live.c4_end_to_end run --confirm-live-call
    ... then, in a NEW process (restart check, no Claude call):
        uv run --locked python -B -m live.c4_end_to_end restart --confirm-live-call
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from fastapi.testclient import TestClient  # noqa: E402

from wbg_orchestrator.api import UserDirectory, create_app  # noqa: E402
from wbg_orchestrator.live_llm import SpendLedger  # noqa: E402
from wbg_orchestrator.runtime import ORCHESTRATOR_DIR, RuntimeConfig, build_runtime  # noqa: E402

RESULTS = ORCHESTRATOR_DIR / "live_results"
STATE = RESULTS / "c4_state.json"
USERS = ORCHESTRATOR_DIR / "runs" / "c4_users.json"
USER = "c4-tester"
QUESTION = ("Investigate the number of contract awards per procurement method in Ethiopia, and then summarize the procurement "
            "delays reported in the documents for the Ethiopia Expressway project.")
JOB_TIMEOUT_SECONDS = 600
CONFIG = RuntimeConfig(ledger_path=RESULTS / "c4_llm_ledger.json", max_attempts=10, max_usd=Decimal("2.00"),
                       audit_path=ORCHESTRATOR_DIR / "runs" / "c4_audit.jsonl")


class Api:
    """Small authenticated client over the in-process app: GET and POST that raise on HTTP errors, and a job poller."""
    def __init__(self, client: TestClient, token: str):
        self._client, self._headers = client, {"Authorization": f"Bearer {token}"}

    def get(self, path: str) -> dict[str, Any]:
        return self._check(self._client.get(path, headers=self._headers))

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._check(self._client.post(path, json=body, headers=self._headers))

    def wait(self, job: dict[str, Any]) -> tuple[dict[str, Any], float]:
        """Polls a running job every 2 seconds until it ends or JOB_TIMEOUT_SECONDS pass; returns it and the seconds."""
        started = time.monotonic()
        while job["state"] == "running" and time.monotonic() - started < JOB_TIMEOUT_SECONDS:
            time.sleep(2)
            job = self.get(f"/jobs/{job['job_id']}")
        return job, round(time.monotonic() - started, 1)

    @staticmethod
    def _check(response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
        return response.json()


def start(operator_confirmed: bool) -> tuple[Api, Any]:
    service, close = build_runtime(CONFIG, operator_confirmed)
    token = UserDirectory.add_user(USERS, USER)                       # held in memory only
    return Api(TestClient(create_app(service, UserDirectory.load(USERS))), token), close


def run(operator_confirmed: bool) -> dict[str, Any]:
    """Asks QUESTION in a new conversation, grants a pending approval and records the investigation and turns."""
    api, close = start(operator_confirmed)
    try:
        cid = api.post("/conversations", {"title": "C4 end-to-end investigation"})["conversation_id"]
        job, first_seconds = api.wait(api.post(f"/conversations/{cid}/messages",
                                               {"text": QUESTION, "client_message_id": "c4-question-0001"}))
        report: dict[str, Any] = {"question": QUESTION, "conversation_id": cid, "first_job": job, "first_job_seconds": first_seconds}
        rid = job["request_id"]
        before = api.get(f"/conversations/{cid}/investigations/{rid}")
        report["before_approval"] = {k: before[k] for k in ("status", "tasks", "pending_approval")}
        # Grant the approval as an operator would, so the investigation runs to its answer.
        if before["pending_approval"]:
            decision = {"approval_id": before["pending_approval"]["approval_id"], "approved": True,
                        "reason": "C4 approved live run", "client_message_id": "c4-approval-0001"}
            report["approval_job"], report["approval_job_seconds"] = api.wait(
                api.post(f"/conversations/{cid}/investigations/{rid}/decision", decision))
        report["investigation"] = api.get(f"/conversations/{cid}/investigations/{rid}")
        report["conversation_turns"] = [{k: t[k] for k in ("seq", "role", "status", "route")}
                                        for t in api.get(f"/conversations/{cid}")["turns"]]
        # The ids let the restart phase, in a new process, read the same investigation back.
        STATE.write_text(json.dumps({"conversation_id": cid, "request_id": rid}, indent=2))
        return report
    finally:
        close()


def restart(operator_confirmed: bool) -> dict[str, Any]:
    """Reads the saved investigation back in a new process and checks status, claims and evidence match the run."""
    state = json.loads(STATE.read_text())
    api, close = start(operator_confirmed)                             # a new process: new pools, engine and workers
    try:
        cid, rid = state["conversation_id"], state["request_id"]
        after = api.get(f"/conversations/{cid}/investigations/{rid}")
        saved = json.loads((RESULTS / "c4_results.json").read_text())["run"]["investigation"]
        return {"status": after["status"], "same_status": after["status"] == saved["status"],
                "same_claims": after["claims"] == saved["claims"], "same_evidence": after["evidence"] == saved["evidence"],
                "turns": len(api.get(f"/conversations/{cid}")["turns"])}
    finally:
        close()


def main(argv: list[str]) -> int:
    """Command line: runs one phase, merges it into c4_results.json and always records the ledger, even on failure."""
    parser = argparse.ArgumentParser(description="C4 end-to-end investigation (live, bounded)")
    parser.add_argument("phase", choices=["run", "restart"])
    parser.add_argument("--confirm-live-call", action="store_true")
    args = parser.parse_args(argv)
    RESULTS.mkdir(exist_ok=True)
    results_file = RESULTS / "c4_results.json"
    results = json.loads(results_file.read_text()) if results_file.is_file() else {}
    try:
        results[args.phase] = run(args.confirm_live_call) if args.phase == "run" else restart(args.confirm_live_call)
    finally:
        results["ledger"] = SpendLedger(CONFIG.ledger_path).summary()
        results_file.write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps({"phase": args.phase, "ledger": results["ledger"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
