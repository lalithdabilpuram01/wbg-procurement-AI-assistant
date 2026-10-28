"""The evaluation target: one case through the real local API, exactly as the UI sends it.

Every case gets a new conversation (no memory carried between cases). A pending approval is granted, as an operator
would, so the investigation runs to its answer. The output is the investigation view the UI reads (status, claims,
evidence, limitations, usage) plus the request id, which links the case to the app's audit log and Logfire trace.
"""
from __future__ import annotations

import itertools
import time
from typing import Any

from fastapi.testclient import TestClient

from live.c4_end_to_end import Api
from wbg_orchestrator.api import UserDirectory, create_app
from wbg_orchestrator.rate_limit import RateLimiter
from wbg_orchestrator.runtime import ORCHESTRATOR_DIR, RuntimeConfig, build_runtime

USERS = ORCHESTRATOR_DIR / "runs" / "eval_users.json"
USER = "eval-runner"
MAX_APPROVALS = 2
EVIDENCE_SUMMARY_CHARS = 600


class AppHarness:
    """The app built once (runtime, API, an evaluation user); `__call__` is the LangSmith target."""

    def __init__(self, config: RuntimeConfig, operator_confirmed: bool):
        self._service, self._close = build_runtime(config, operator_confirmed)
        token = UserDirectory.add_user(USERS, USER)                         # held in memory only
        app = create_app(self._service, UserDirectory.load(USERS), limiter=RateLimiter(60, 1000))
        self._api = Api(TestClient(app), token)
        self._ids = itertools.count(1)

    def close(self) -> None:
        self._close()

    def __call__(self, inputs: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        cid = self._api.post("/conversations", {"title": f"eval {inputs['case_id']}"})["conversation_id"]
        job, _ = self._api.wait(self._api.post(f"/conversations/{cid}/{self._endpoint(inputs)}", self._body(inputs)))
        for _ in range(MAX_APPROVALS):
            view = self._investigation(cid, job)
            if not (view and view.get("pending_approval")):
                break
            decision = {"approval_id": view["pending_approval"]["approval_id"], "approved": True,
                        "client_message_id": self._client_id()}
            job, _ = self._api.wait(self._api.post(f"/conversations/{cid}/investigations/{job['request_id']}/decision", decision))
        view = self._investigation(cid, job) or {}
        turns = self._api.get(f"/conversations/{cid}")["turns"]
        return {"job_state": job["state"], "status": view.get("status") or job.get("status"), "request_id": job.get("request_id"),
                "error": job.get("error"), "message": view.get("message") or _last_answer(turns),
                "claims": view.get("claims", []), "limitations": view.get("limitations", []),
                "error_codes": view.get("error_codes", []), "tasks": view.get("tasks", []),
                "evidence": [_evidence(e) for e in view.get("evidence", [])], "usage": view.get("usage", {}),
                "stored_user_text": [t["text"] for t in turns if t["role"] == "user"],
                "seconds": round(time.monotonic() - started, 1)}

    def _endpoint(self, inputs: dict[str, Any]) -> str:
        return {"message": "messages", "plan": "plans", "package": "packages"}[inputs["kind"]]

    def _body(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if inputs["kind"] == "plan":
            return {**inputs["plan"], "client_message_id": self._client_id()}
        if inputs["kind"] == "package":
            return {**inputs["package"], "client_message_id": self._client_id()}
        return {"text": inputs["text"], "execute": inputs.get("execute", False), "table_lens": inputs.get("table_lens", False),
                "client_message_id": self._client_id()}

    def _investigation(self, cid: str, job: dict[str, Any]) -> dict[str, Any] | None:
        if not job.get("request_id"):
            return None
        try:
            return self._api.get(f"/conversations/{cid}/investigations/{job['request_id']}")
        except RuntimeError:                                        # not an investigation (e.g. a scope reply)
            return None

    def _client_id(self) -> str:
        return f"eval-{time.time_ns()}-{next(self._ids):04d}"


def _evidence(e: dict[str, Any]) -> dict[str, Any]:
    return {k: e.get(k) for k in ("evidence_id", "kind", "citation", "numeric_use", "validation_status", "warnings",
                                  "document_id", "pages", "project_ids", "facts")} | \
        {"summary": (e.get("summary") or "")[:EVIDENCE_SUMMARY_CHARS]}


def _last_answer(turns: list[dict[str, Any]]) -> str:
    return next((t["text"] for t in reversed(turns) if t["role"] == "assistant"), "")
