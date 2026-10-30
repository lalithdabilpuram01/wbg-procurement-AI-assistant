"""Milestone E: one real package assessment end to end through the local API (paid, bounded).

Real: the FastAPI app; the read-only Gold lookups through the Structured Data Agent gateway (`wbg-readonly`); the three
frozen models; lessons from the documents (pilot_v3, bge-m3 vectors in Lakebase); Claude through Portkey for the
answer, behind `BudgetedClaudeClient` with its own ledger at 10 attempts / $2.00; checkpoints on `dev-orchestrator`.

    cd orchestrator
    WBG_ALLOW_LIVE_LLM_CALLS=true env -u VIRTUAL_ENV PYTHONPATH=..:. \
        uv run --locked python -B -m live.e_package_end_to_end --confirm-live-call
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from live.c4_end_to_end import Api, RESULTS, USERS  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from wbg_orchestrator.api import UserDirectory, create_app  # noqa: E402
from wbg_orchestrator.live_llm import SpendLedger  # noqa: E402
from wbg_orchestrator.runtime import ORCHESTRATOR_DIR, RuntimeConfig, build_runtime  # noqa: E402

USER = "e-tester"
PACKAGE = {"project_id": "P148850", "procurement_method": "Request for Bids", "procurement_category": "Works",
           "review_type": "Prior", "bid_window_days": 60, "tender_date": "2026-12-01"}
CONFIG = RuntimeConfig(ledger_path=RESULTS / "e_llm_ledger.json", max_attempts=10, max_usd=Decimal("2.00"),
                       audit_path=ORCHESTRATOR_DIR / "runs" / "e_audit.jsonl")


def run(operator_confirmed: bool) -> dict[str, Any]:
    service, close = build_runtime(CONFIG, operator_confirmed)
    try:
        token = UserDirectory.add_user(USERS, USER)                                 # held in memory only
        api = Api(TestClient(create_app(service, UserDirectory.load(USERS))), token)
        cid = api.post("/conversations", {"title": "E package assessment"})["conversation_id"]
        job, seconds = api.wait(api.post(f"/conversations/{cid}/packages", {**PACKAGE, "client_message_id": "e-package-0002"}))
        investigation = api.get(f"/conversations/{cid}/investigations/{job['request_id']}") if job.get("request_id") else {}
        return {"package": PACKAGE, "job": job, "seconds": seconds, "investigation": investigation,
                "turns": [{k: t[k] for k in ("seq", "role", "status", "route")} for t in api.get(f"/conversations/{cid}")["turns"]]}
    finally:
        close()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="E package assessment (live, bounded)")
    parser.add_argument("--confirm-live-call", action="store_true")
    args = parser.parse_args(argv)
    RESULTS.mkdir(exist_ok=True)
    results: dict[str, Any] = {}
    try:
        results["run"] = run(args.confirm_live_call)
    finally:
        results["ledger"] = SpendLedger(CONFIG.ledger_path).summary()
        (RESULTS / "e_package_results.json").write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps({"status": results["run"]["job"].get("status"), "seconds": results["run"]["seconds"],
                      "ledger": results["ledger"]}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
