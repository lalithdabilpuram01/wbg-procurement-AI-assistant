"""C3: the local web API over the conversation service, the engine and its checkpoints (offline, fakes only).

Covers: authentication, start and follow-up, background jobs, duplicate messages, one job per conversation,
cross-user isolation on every path, approval through the API, cancel, restart recovery with the same checkpoints
and conversation store, an interrupted run continued with `recover`, and input validation.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from conversation.models import Owner
from conversation.routing.routes import Route
from conversation.service import ConversationService
from conversation.store import InMemoryConversationStore

from wbg_orchestrator.api import UserDirectory, create_app
from wbg_orchestrator.app_service import AppService
from wbg_orchestrator.conversation_bridge import ConversationFrontDoor, ConversationRunner
from wbg_orchestrator.hitl import ApprovalPolicy
from wbg_orchestrator.planner import FakePlanner

from tests.helpers import AUTH, BID_SQL, FakeWorkflow, PolicyGuard, build_engine, plan, task, workflow_result

ALICE_OWNER = Owner(tenant_id="local", user_id="alice")
DATA_QUESTION = "How many bid invitations were published in fiscal year 2024?"
PLANNED = "Investigate how many bid invitations were published in fiscal year 2024 and then explain the metric"


def planned():
    return FakePlanner(plan(task("metric", operation="describe_metric", question="Which metric counts bid invitations?"),
                            task("count", deps=("metric",), execute=True, question=DATA_QUESTION)))


class Stack:
    """One 'process': an app over shared durable state (conversation store + checkpointer)."""

    def __init__(self, tmp_path: Path, store=None, saver=None, **engine_options):
        self.store, self.saver = store or InMemoryConversationStore(), saver or InMemorySaver()
        self.engine, self.parts = build_engine(checkpointer=self.saver, **engine_options)
        runners = {r: ConversationRunner(self.engine, r, AUTH) for r in (Route.STRUCTURED_DATA, Route.DOCUMENT_RAG)}
        conversations = ConversationService(self.store, runners, PolicyGuard())
        self.service = AppService(conversations, self.store, self.engine,
                                  ConversationFrontDoor(conversations, self.store, self.engine, AUTH))
        users_file = tmp_path / "users.json"
        self.tokens = {u: UserDirectory.add_user(users_file, u) for u in ("alice", "bob")}
        self.client = TestClient(create_app(self.service, UserDirectory.load(users_file)))

    def call(self, method: str, path: str, user: str | None = "alice", **kwargs):
        headers = {"Authorization": f"Bearer {self.tokens[user]}"} if user else {}
        return self.client.request(method, path, headers=headers, **kwargs)

    def conversation(self, user: str = "alice") -> str:
        return self.call("POST", "/conversations", user, json={"title": "test"}).json()["conversation_id"]

    def send(self, cid: str, text: str, client_id: str, user: str = "alice", execute: bool = False):
        return self.call("POST", f"/conversations/{cid}/messages", user,
                         json={"text": text, "execute": execute, "client_message_id": client_id})

    def wait(self, job_id: str, user: str = "alice") -> dict:
        for _ in range(200):
            job = self.call("GET", f"/jobs/{job_id}", user).json()
            if job["state"] != "running":
                return job
            time.sleep(0.02)
        raise AssertionError("job did not finish")


@pytest.fixture
def stack(tmp_path):
    return Stack(tmp_path)


# ------------------------------------------------------------------------------------------- authentication
def test_every_endpoint_needs_a_valid_token(stack):
    assert stack.client.get("/health").status_code == 200
    assert stack.call("GET", "/conversations", user=None).status_code == 401
    assert stack.client.get("/conversations", headers={"Authorization": "Bearer not-a-token"}).status_code == 401
    assert stack.client.get("/conversations", headers={"Authorization": stack.tokens["alice"]}).status_code == 401  # no scheme


def test_tokens_are_stored_only_as_hashes(tmp_path):
    users_file = tmp_path / "users.json"
    token = UserDirectory.add_user(users_file, "alice")
    assert token not in users_file.read_text() and oct(users_file.stat().st_mode & 0o777) == "0o600"


# ------------------------------------------------------------------------------------------ start, follow-up
def test_a_question_runs_as_a_job_and_the_investigation_can_be_read(stack):
    cid = stack.conversation()
    accepted = stack.send(cid, DATA_QUESTION, "msg-0001", execute=True)
    assert accepted.status_code == 202 and accepted.json()["state"] in ("running", "succeeded")
    job = stack.wait(accepted.json()["job_id"])
    assert job["state"] == "succeeded" and job["status"] == "answered" and job["request_id"]
    conversation = stack.call("GET", f"/conversations/{cid}").json()
    assert [t["role"] for t in conversation["turns"]] == ["user", "assistant"]
    assert [i["request_id"] for i in conversation["investigations"]] == [job["request_id"]]
    investigation = stack.call("GET", f"/conversations/{cid}/investigations/{job['request_id']}").json()
    assert investigation["status"] == "answered" and investigation["claims"] and investigation["evidence"]
    assert BID_SQL not in json.dumps(investigation) and "SELECT" not in json.dumps(investigation)   # no SQL text


def test_a_follow_up_continues_the_same_conversation(stack):
    cid = stack.conversation()
    stack.wait(stack.send(cid, DATA_QUESTION, "msg-0001", execute=True).json()["job_id"])
    second = stack.wait(stack.send(cid, "And how many in fiscal year 2023?", "msg-0002", execute=True).json()["job_id"])
    assert second["state"] == "succeeded"
    assert len(stack.call("GET", f"/conversations/{cid}").json()["turns"]) == 4


def test_a_repeated_message_returns_the_same_job_and_runs_once(stack):
    cid = stack.conversation()
    first = stack.send(cid, DATA_QUESTION, "msg-dup-0001", execute=True).json()
    stack.wait(first["job_id"])
    again = stack.send(cid, DATA_QUESTION, "msg-dup-0001", execute=True).json()
    assert again["job_id"] == first["job_id"] and len(stack.parts["workflow"].calls) == 1


def test_one_job_at_a_time_per_conversation(tmp_path):
    stack = Stack(tmp_path, workflow=FakeWorkflow(delay_seconds=0.4))
    cid = stack.conversation()
    first = stack.send(cid, DATA_QUESTION, "msg-0001", execute=True).json()
    assert stack.send(cid, DATA_QUESTION, "msg-0002", execute=True).status_code == 409
    stack.wait(first["job_id"])


# ---------------------------------------------------------------------------------------------- isolation
def test_another_user_sees_nothing_and_can_change_nothing(tmp_path):
    stack = Stack(tmp_path, planner=planned())
    cid = stack.conversation()
    job = stack.wait(stack.send(cid, PLANNED, "msg-0001").json()["job_id"])
    rid = job["request_id"]
    approval = stack.call("GET", f"/conversations/{cid}/investigations/{rid}").json()["pending_approval"]
    base = f"/conversations/{cid}/investigations/{rid}"
    for method, path, body in [("GET", f"/conversations/{cid}", None), ("GET", base, None), ("GET", f"/jobs/{job['job_id']}", None),
                               ("POST", f"/conversations/{cid}/messages", {"text": "hi", "client_message_id": "msg-bob-01"}),
                               ("POST", f"{base}/decision", {"approval_id": approval["approval_id"], "approved": True,
                                                             "client_message_id": "msg-bob-02"}),
                               ("POST", f"{base}/recover", {"client_message_id": "msg-bob-03"}),
                               ("POST", f"{base}/cancel", None)]:
        assert stack.call(method, path, "bob", json=body).status_code == 404, path
    assert stack.call("GET", "/conversations", "bob").json() == []
    assert stack.call("GET", base).json()["status"] == "awaiting_approval" and stack.parts["workflow"].calls == []


# ----------------------------------------------------------------------------------------------- approvals
def test_approval_through_the_api_runs_once(tmp_path):
    stack = Stack(tmp_path, planner=planned())
    cid = stack.conversation()
    rid = stack.wait(stack.send(cid, PLANNED, "msg-0001").json()["job_id"])["request_id"]
    view = stack.call("GET", f"/conversations/{cid}/investigations/{rid}").json()
    assert view["status"] == "awaiting_approval" and view["pending_approval"]["kind"] == "execute_sql"
    answer = {"approval_id": view["pending_approval"]["approval_id"], "approved": True, "client_message_id": "msg-approve-1"}
    done = stack.wait(stack.call("POST", f"/conversations/{cid}/investigations/{rid}/decision", json=answer).json()["job_id"])
    assert done["status"] == "answered" and [c["execute"] for c in stack.parts["workflow"].calls] == [True]
    replay = stack.wait(stack.call("POST", f"/conversations/{cid}/investigations/{rid}/decision",
                                   json={**answer, "client_message_id": "msg-approve-2"}).json()["job_id"])
    assert replay["state"] == "failed" and replay["error"] == "ApprovalNotPending"
    assert len(stack.parts["workflow"].calls) == 1


def test_cancel_rejects_a_pending_approval(tmp_path):
    stack = Stack(tmp_path, planner=planned())
    cid = stack.conversation()
    rid = stack.wait(stack.send(cid, PLANNED, "msg-0001").json()["job_id"])["request_id"]
    cancelled = stack.call("POST", f"/conversations/{cid}/investigations/{rid}/cancel").json()
    assert cancelled["status"] == "rejected" and stack.parts["workflow"].calls == []


def test_cancel_stops_a_running_investigation_before_its_next_task(tmp_path):
    no_approval = ApprovalPolicy(execution_requires_approval="never")
    two_tasks = FakePlanner(plan(task("a", execute=True, question="Bid invitations in fiscal year 2023?"),
                                 task("b", deps=("a",), execute=True, question="Bid invitations in fiscal year 2024?")))
    stack = Stack(tmp_path, planner=two_tasks, policy=no_approval, workflow=FakeWorkflow(delay_seconds=0.5))
    cid = stack.conversation()
    job = stack.send(cid, "Investigate how many bid invitations were published in fiscal year 2023 and then 2024", "msg-0001").json()
    for _ in range(100):                                 # wait until the investigation is visible and running
        listed = stack.call("GET", f"/conversations/{cid}").json()["investigations"]
        if listed:
            break
        time.sleep(0.02)
    rid = listed[0]["request_id"]
    stack.call("POST", f"/conversations/{cid}/investigations/{rid}/cancel")
    stack.wait(job["job_id"])
    view = stack.call("GET", f"/conversations/{cid}/investigations/{rid}").json()
    assert [t["state"] for t in view["tasks"]] == ["succeeded", "cancelled"] and "cancelled_by_user" in view["error_codes"]
    assert view["status"] == "partial" and len(stack.parts["workflow"].calls) == 1


# ---------------------------------------------------------------------------------------- restart recovery
def test_state_survives_a_restart_and_the_owner_can_still_approve(tmp_path):
    first = Stack(tmp_path, planner=planned())
    cid = first.conversation()
    rid = first.wait(first.send(cid, PLANNED, "msg-0001").json()["job_id"])["request_id"]
    first.service.shutdown()
    restarted = Stack(tmp_path, store=first.store, saver=first.saver)            # new process, same durable state
    view = restarted.call("GET", f"/conversations/{cid}/investigations/{rid}").json()
    assert view["status"] == "awaiting_approval"
    answer = {"approval_id": view["pending_approval"]["approval_id"], "approved": True, "client_message_id": "msg-approve-1"}
    done = restarted.wait(restarted.call("POST", f"/conversations/{cid}/investigations/{rid}/decision", json=answer).json()["job_id"])
    assert done["status"] == "answered" and len(restarted.parts["workflow"].calls) == 1 and first.parts["workflow"].calls == []


def test_an_interrupted_investigation_is_reported_and_can_be_recovered(tmp_path):
    class Crash(BaseException):
        pass

    def crash(question: str, execute: bool):
        raise Crash()

    no_approval = ApprovalPolicy(execution_requires_approval="never")
    first = Stack(tmp_path, planner=planned(), policy=no_approval, workflow=FakeWorkflow(crash))
    cid = first.conversation()
    with pytest.raises(Crash):                           # the run dies mid-way, like a killed process
        first.service._front_door.handle_message(ALICE_OWNER, cid, PLANNED)
    restarted = Stack(tmp_path, store=first.store, saver=first.saver, policy=no_approval)
    rid = restarted.call("GET", f"/conversations/{cid}").json()["investigations"][0]["request_id"]
    assert restarted.call("GET", f"/conversations/{cid}/investigations/{rid}").json()["status"] == "interrupted"
    done = restarted.wait(restarted.call("POST", f"/conversations/{cid}/investigations/{rid}/recover",
                                         json={"client_message_id": "msg-recover-1"}).json()["job_id"])
    assert done["status"] == "answered" and len(restarted.parts["workflow"].calls) == 1


# ---------------------------------------------------------------------------------------------- validation
@pytest.mark.parametrize("body", [{"text": "", "client_message_id": "msg-0001"}, {"text": "x" * 2001, "client_message_id": "msg-0001"},
                                  {"text": "hi", "client_message_id": "bad id!"}, {"text": "hi"}])
def test_invalid_requests_are_rejected(stack, body):
    cid = stack.conversation()
    assert stack.call("POST", f"/conversations/{cid}/messages", json=body).status_code == 422


def test_workflow_result_text_is_redacted_before_it_is_stored(tmp_path):
    secret = "pk-test-SECRET-do-not-leak-4242"
    leaky = FakeWorkflow(lambda q, e: workflow_result(q, execute=e).model_copy(update={"message": f"done {secret}"}))
    stack = Stack(tmp_path, workflow=leaky)
    cid = stack.conversation()
    rid = stack.wait(stack.send(cid, DATA_QUESTION, "msg-0001", execute=True).json()["job_id"])["request_id"]
    assert secret not in json.dumps(stack.call("GET", f"/conversations/{cid}/investigations/{rid}").json())
