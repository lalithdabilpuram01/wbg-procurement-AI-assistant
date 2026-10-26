"""Live checkpoint validation on the Lakebase development branch (`dev-orchestrator`), schema `wbg_orchestrator_ckpt`.

The same checks as the offline suite (tests/test_checkpointing.py), against real Lakebase PostgreSQL 18.6:
isolation before setup, setup confined to the schema, save and read-back, restart and resume, HITL with the owner,
refusal of another user, no secrets or unrestricted rows, crash recovery, a failed task's state, OAuth credential
refresh on new connections, and every other schema unchanged. Workers are synthetic fixtures; no LLM is called.
Test rows stay in the checkpoint schema; nothing is dropped or deleted.
"""
from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from psycopg import sql

from structured_agent.nl2sql.result import WorkflowResult, WorkflowStatus

from wbg_orchestrator.checkpointing import (CHECKPOINT_OBJECTS, IsolationError, checkpointer, database_inventory,
                                            isolated_setup, lakebase_checkpoint_pool, lakebase_token_provider, verify_isolation)
from wbg_orchestrator.contracts import InvestigationStatus
from wbg_orchestrator.engine import ApprovalNotPending, InvestigationNotFound
from wbg_orchestrator.observability import OrchestratorTracer
from wbg_orchestrator.planner import FakePlanner

from tests.helpers import ALICE, BOB, SECRET, FakeWorkflow, build_engine, plan, request, task, workflow_result

QUESTION = "Investigate how many bid invitations were published in fiscal year 2024 and then explain the metric"
DATA_QUESTION = "How many bid invitations were published in fiscal year 2024?"
RESULTS: dict = {}


def planned():
    return FakePlanner(plan(task("metric", operation="describe_metric", question="Which metric counts bid invitations?"),
                            task("count", deps=("metric",), execute=True, question=DATA_QUESTION)))


def snapshot(conn: psycopg.Connection, schema: str) -> dict:
    """Objects of every other schema, plus exact row counts of their tables."""
    inventory = {s: o for s, o in database_inventory(conn).items() if s != schema}
    rows = {f"{s}.{n}": conn.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(s), sql.Identifier(n))).fetchone()
            for s, objects in inventory.items() for n, kind in objects if kind in ("r", "p")}
    return {"objects": inventory, "rows": {k: list(v.values())[0] if isinstance(v, dict) else v[0] for k, v in rows.items()}}


def thread_dump(pool, thread_id: str) -> str:
    with pool.connection() as conn:                                    # search_path pinned: unqualified names = our schema
        parts = [json.dumps(r["checkpoint"]) + json.dumps(r["metadata"])
                 for r in conn.execute("SELECT checkpoint, metadata FROM checkpoints WHERE thread_id = %s", (thread_id,))]
        parts += [bytes(r["blob"]).decode("utf-8", "replace")
                  for r in conn.execute("SELECT blob FROM checkpoint_blobs WHERE thread_id = %s AND blob IS NOT NULL", (thread_id,))]
        parts += [bytes(r["blob"]).decode("utf-8", "replace")
                  for r in conn.execute("SELECT blob FROM checkpoint_writes WHERE thread_id = %s", (thread_id,))]
    return "\n".join(parts)


@pytest.fixture(scope="module")
def tokens(ckpt_settings):
    return lakebase_token_provider(ckpt_settings)


@pytest.fixture(scope="module")
def pool(ckpt_settings, tokens):
    opened = lakebase_checkpoint_pool(ckpt_settings, tokens)
    opened.open(wait=True, timeout=60)
    yield opened
    opened.close()


@pytest.fixture(scope="module")
def before(pool, ckpt_settings):
    with pool.connection() as conn:
        return snapshot(conn, ckpt_settings.schema)


# --------------------------------------------------------------------------------------------- isolation, setup
def test_1_isolation_is_proven_before_setup_and_setup_stays_in_the_schema(ckpt_settings, tokens, pool, before):
    with psycopg.connect(**ckpt_settings.connect_kwargs(), password=tokens.get()) as raw:     # unpinned: must be refused
        with pytest.raises(IsolationError):
            verify_isolation(raw, ckpt_settings.schema)
        RESULTS["default_search_path"] = raw.execute("SHOW search_path").fetchone()[0]
    with pool.connection() as conn:
        report = verify_isolation(conn, ckpt_settings.schema)
        RESULTS["isolation"] = {"effective_schemas": report.effective_schemas, "server_version": report.server_version,
                                "objects_before_setup": report.existing_objects}
        tls = conn.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()["ssl"]
    setup = isolated_setup(pool, checkpointer(pool), ckpt_settings.schema)
    RESULTS["setup"] = {"created": setup.created, "present": setup.present, "other_schemas_unchanged": setup.other_schemas_unchanged}
    assert tls is True and setup.other_schemas_unchanged and set(setup.present) == CHECKPOINT_OBJECTS
    assert isolated_setup(pool, checkpointer(pool), ckpt_settings.schema).created == ()          # idempotent


# ------------------------------------------------------------------------------------------ persistence, HITL
def test_2_checkpoint_is_saved_and_reads_back_identically(pool):
    engine, _ = build_engine(checkpointer=checkpointer(pool))
    out = engine.start(request(DATA_QUESTION, execute=True, thread="conv-live-ckpt"))
    stored = engine.checkpointer.get_tuple({"configurable": {"thread_id": out.thread_id}})
    values = stored.checkpoint["channel_values"]
    for key in ("request", "plan", "evidence", "results", "task_states", "response", "usage"):
        assert json.dumps(values[key], sort_keys=True) == json.dumps(out.values[key], sort_keys=True), key
    assert out.status == InvestigationStatus.ANSWERED
    RESULTS["round_trip_thread"] = out.thread_id


def test_3_hitl_survives_a_restart_and_only_the_owner_can_resume(ckpt_settings, tokens):
    first = lakebase_checkpoint_pool(ckpt_settings, tokens)
    first.open(wait=True, timeout=60)
    engine, parts = build_engine(planner=planned(), checkpointer=checkpointer(first))
    pending = engine.start(request(QUESTION, thread="conv-live-ckpt"))
    assert pending.status == InvestigationStatus.AWAITING_APPROVAL and parts["workflow"].calls == []
    first.close()                                                    # "restart": all connections closed, graph rebuilt

    second = lakebase_checkpoint_pool(ckpt_settings, tokens)
    second.open(wait=True, timeout=60)
    try:
        workflow = FakeWorkflow()
        restarted, _ = build_engine(planner=FakePlanner(), workflow=workflow, checkpointer=checkpointer(second))
        for attempt in (lambda: restarted.resume(BOB, pending.thread_id, pending.pending_approval.approval_id, approved=True),
                        lambda: restarted.outcome(BOB, pending.thread_id), lambda: restarted.recover(BOB, pending.thread_id)):
            with pytest.raises(InvestigationNotFound):
                attempt()
        assert restarted.outcome(ALICE, pending.thread_id).status == InvestigationStatus.AWAITING_APPROVAL
        done = restarted.resume(ALICE, pending.thread_id, pending.pending_approval.approval_id, approved=True)
        assert done.status == InvestigationStatus.ANSWERED and [c["execute"] for c in workflow.calls] == [True]
        with pytest.raises(ApprovalNotPending):
            restarted.resume(ALICE, pending.thread_id, pending.pending_approval.approval_id, approved=True)
        RESULTS["hitl_restart"] = {"thread": pending.thread_id, "executions": len(workflow.calls), "status": done.status.value}
    finally:
        second.close()


def test_4_checkpoints_hold_no_secrets_or_unrestricted_rows(pool):
    rows = [{"row_id": i, "value": f"ROW-MARKER-{i:03d}"} for i in range(40)]

    def respond(question: str, execute: bool) -> WorkflowResult:
        result = workflow_result(question, execute=execute)
        return result.model_copy(update={"rows": rows + [{"row_id": 99, "value": f"api_key={SECRET}"}], "row_count": 41,
                                         "message": f"Query executed. token {SECRET}"})

    engine, _ = build_engine(workflow=FakeWorkflow(respond), checkpointer=checkpointer(pool))
    out = engine.start(request(DATA_QUESTION, execute=True, thread="conv-live-ckpt"))
    dump = thread_dump(pool, out.thread_id)
    assert dump and SECRET not in dump and "ROW-MARKER-011" in dump
    assert "ROW-MARKER-012" not in dump and "SELECT" not in dump and '"rows"' not in dump
    RESULTS["secrets_check"] = {"thread": out.thread_id, "bytes_checked": len(dump)}


def test_5_a_crash_mid_run_leaves_a_recoverable_checkpoint(pool):
    class Crash(BaseException):
        pass

    def crash(question: str, execute: bool):
        raise Crash()

    engine, _ = build_engine(planner=planned(), workflow=FakeWorkflow(crash), checkpointer=checkpointer(pool))
    pending = engine.start(request(QUESTION, thread="conv-live-ckpt"))
    with pytest.raises(Crash):
        engine.resume(ALICE, pending.thread_id, pending.pending_approval.approval_id, approved=True)
    workflow = FakeWorkflow()
    restarted, _ = build_engine(planner=FakePlanner(), workflow=workflow, checkpointer=checkpointer(pool))
    assert restarted.graph.get_state({"configurable": {"thread_id": pending.thread_id}}).next
    recovered = restarted.recover(ALICE, pending.thread_id)
    assert recovered.status == InvestigationStatus.ANSWERED and len(workflow.calls) == 1
    RESULTS["crash_recovery"] = {"thread": pending.thread_id, "status": recovered.status.value}


def test_6_a_failed_task_keeps_its_error_and_state(pool):
    planner = FakePlanner(plan(task("count", execute=True, question=DATA_QUESTION),
                               task("metric", operation="describe_metric", question="Which metric?", optional=True)))
    failing = FakeWorkflow(lambda q, e: workflow_result(q, execute=e, status=WorkflowStatus.CANNOT_ANSWER, message="no data"))
    engine, _ = build_engine(planner=planner, workflow=failing, checkpointer=checkpointer(pool))
    pending = engine.start(request(QUESTION, thread="conv-live-ckpt"))
    done = engine.resume(ALICE, pending.thread_id, pending.pending_approval.approval_id, approved=True)
    again = build_engine(checkpointer=checkpointer(pool))[0].outcome(ALICE, pending.thread_id)
    assert again.status == done.status and again.task_states == done.task_states and "failed" in again.task_states.values()
    assert again.values["errors"] == done.values["errors"]
    RESULTS["failed_task"] = {"thread": pending.thread_id, "status": again.status.value,
                              "error_codes": [e["code"] for e in again.values["errors"]]}


def test_7_new_connections_take_a_fresh_oauth_credential(ckpt_settings):
    clock = [0.0]
    rotating = lakebase_token_provider(ckpt_settings, clock=lambda: clock[0])   # real CLI credentials, simulated time
    pool = lakebase_checkpoint_pool(ckpt_settings, rotating)
    pool.open(wait=True, timeout=60)
    try:
        with pool.connection() as conn:
            first = conn.info.password
            conn.execute("SELECT 1")
        clock[0] = 46 * 60                                              # past the 45-minute refresh point
        pool.drain()                                                    # retire connections, as max_lifetime would
        with pool.connection() as conn:
            conn.execute("SELECT 1")
            second = conn.info.password
        assert rotating.fetch_count == 2 and first and second          # both real tokens were accepted by Lakebase
        RESULTS["oauth_refresh"] = {"credentials_fetched": rotating.fetch_count, "both_accepted": True,
                                    "tokens_differ": first != second}
    finally:
        pool.close()


def test_8_checkpoint_events_are_traced(pool):
    exporter = InMemorySpanExporter()
    tracer = OrchestratorTracer.local([SimpleSpanProcessor(exporter)])
    engine, _ = build_engine(planner=planned(), checkpointer=checkpointer(pool), tracer=tracer)
    pending = engine.start(request(QUESTION, thread="conv-live-ckpt"))
    engine.resume(ALICE, pending.thread_id, pending.pending_approval.approval_id, approved=True)
    tracer.force_flush()
    events = [s for s in exporter.get_finished_spans() if s.name.startswith("orchestrator.checkpoint_")]
    assert {s.name for s in events} == {"orchestrator.checkpoint_saved", "orchestrator.checkpoint_resumed"}
    assert all(s.attributes["checkpointer"] == "PostgresSaver" and s.attributes["checkpoint_id"] for s in events)


def test_9_every_other_schema_is_unchanged(pool, ckpt_settings, before):
    with pool.connection() as conn:
        after = snapshot(conn, ckpt_settings.schema)
        counts = {r["t"]: r["n"] for r in conn.execute(
            "SELECT 'checkpoints' AS t, count(*) AS n FROM checkpoints UNION ALL SELECT 'checkpoint_blobs', count(*) FROM checkpoint_blobs "
            "UNION ALL SELECT 'checkpoint_writes', count(*) FROM checkpoint_writes UNION ALL "
            "SELECT 'threads', count(DISTINCT thread_id) FROM checkpoints")}
        size = conn.execute("SELECT coalesce(sum(pg_total_relation_size(c.oid)), 0)::bigint AS b FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s", (ckpt_settings.schema,)).fetchone()["b"]
    assert after == before
    RESULTS.update(other_schemas_unchanged=True, other_schema_rows=after["rows"], checkpoint_rows=counts, checkpoint_schema_bytes=size)
    output = Path(__file__).resolve().parents[1] / "live_results" / "lakebase_checkpoint_live_results.json"
    output.write_text(json.dumps(RESULTS, default=list, sort_keys=True, indent=2))
