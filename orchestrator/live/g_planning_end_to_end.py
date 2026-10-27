"""Planning Brief live run: one brief for a new project, then one planner investigation of its similar projects.

Real: the API, the read-only Gold lookups (`wbg-readonly`), the frozen models, document search over pilot_v3 (bge-m3 in
Lakebase), Claude through Portkey (brief composer, planner, answer) behind its own ledger (10 attempts / $1.00), and
checkpoints on `dev-orchestrator`. A pending SQL approval in the investigation is approved by this operator run.

    cd orchestrator
    WBG_ALLOW_LIVE_LLM_CALLS=true env -u VIRTUAL_ENV PYTHONPATH=..:. \
        uv run --locked python -B -m live.g_planning_end_to_end --confirm-live-call
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from decimal import Decimal
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from live.c4_end_to_end import Api, RESULTS, USERS  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from wbg_orchestrator.api import UserDirectory, create_app  # noqa: E402
from wbg_orchestrator.live_llm import SpendLedger  # noqa: E402
from wbg_orchestrator.runtime import ORCHESTRATOR_DIR, RuntimeConfig, build_runtime  # noqa: E402

PLAN = {"profile": {"country_code": "ET", "sector": "Transportation", "approval_date": "2027-06-30", "total_cost_usd": 300000000,
                    "description": "Rehabilitate about 120 km of rural and feeder roads and build three bridges in Oromia, "
                                   "with road maintenance capacity building for the regional roads authority."},
        "packages": [{"name": "Road rehabilitation works", "procurement_category": "Works", "procurement_method": "Request for Bids",
                      "review_type": "Prior", "bid_window_days": 45, "tender_date": "2027-10-01"},
                     {"name": "Construction supervision", "procurement_category": "Consultant Services",
                      "review_type": "Prior", "bid_window_days": 30, "tender_date": "2027-08-01"}]}
FOLLOW_UP = "Investigate what caused delays in the works contracts of these similar projects and what lessons were recorded."
CONFIG = RuntimeConfig(ledger_path=RESULTS / "g_llm_ledger_3.json", max_attempts=10, max_usd=Decimal("1.00"),
                       audit_path=ORCHESTRATOR_DIR / "runs" / "g_audit.jsonl")


def run(operator_confirmed: bool) -> dict[str, Any]:
    service, close = build_runtime(CONFIG, operator_confirmed)
    try:
        api = Api(TestClient(create_app(service, UserDirectory.load(USERS))), UserDirectory.add_user(USERS, "g-tester"))
        cid = api.post("/conversations", {"title": "Planning brief live run"})["conversation_id"]
        brief_job, brief_seconds = api.wait(api.post(f"/conversations/{cid}/plans", {**PLAN, "client_message_id": "g-plan-0007"}))
        brief = api.get(f"/conversations/{cid}/investigations/{brief_job['request_id']}")
        job, seconds = api.wait(api.post(f"/conversations/{cid}/messages", {"text": FOLLOW_UP, "client_message_id": "g-msg-0007"}))
        investigation = api.get(f"/conversations/{cid}/investigations/{job['request_id']}")
        for round_ in (1, 2):                                   # a replan may ask for approval once more
            if not investigation.get("pending_approval"):
                break
            decision = {"approval_id": investigation["pending_approval"]["approval_id"], "approved": True,
                        "reason": "operator-approved live run", "client_message_id": f"g-approve-0{round_}"}
            job, more = api.wait(api.post(f"/conversations/{cid}/investigations/{job['request_id']}/decision", decision))
            seconds += more
            investigation = api.get(f"/conversations/{cid}/investigations/{job['request_id']}")
        return {"brief": {"job": brief_job, "seconds": brief_seconds, "view": brief,
                          "evidence_kinds": Counter(e["kind"] for e in brief["evidence"]),
                          "sections": Counter(c["section"] for c in brief["claims"])},
                "investigation": {"job": job, "seconds": seconds, "view": investigation}}
    finally:
        close()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Planning Brief live run (bounded)")
    parser.add_argument("--confirm-live-call", action="store_true")
    args = parser.parse_args(argv)
    RESULTS.mkdir(exist_ok=True)
    results: dict[str, Any] = {}
    try:
        results["run"] = run(args.confirm_live_call)
    finally:
        results["ledger"] = SpendLedger(CONFIG.ledger_path).summary()
        (RESULTS / "g_planning_results.json").write_text(json.dumps(results, indent=2, default=str))
    run_ = results["run"]
    print(json.dumps({"brief": {k: run_["brief"][k] for k in ("seconds", "evidence_kinds", "sections")} | {"status": run_["brief"]["job"]["status"]},
                      "investigation": {"status": run_["investigation"]["job"]["status"], "seconds": run_["investigation"]["seconds"]},
                      "ledger": {k: results["ledger"][k] for k in ("attempts_used", "committed_usd")}}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
