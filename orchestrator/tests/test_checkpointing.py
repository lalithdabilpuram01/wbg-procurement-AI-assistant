"""Milestone B: the official PostgresSaver behind `checkpointing.py`, on a throwaway local PostgreSQL 18 cluster.

The cluster mirrors the Lakebase layout (existing application schemas, a non-superuser application role); no
Lakebase, Databricks or existing local database is contacted. Covered: settings and branch policy, credential refresh
per new connection, schema isolation before setup, setup confined to the schema, save / read-back, restart and resume,
HITL with the owner, refusal of another user, no secrets or unrestricted rows in checkpoints, recovery after a crash,
and other schemas left unchanged.
"""
from __future__ import annotations

import json

import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from structured_agent.nl2sql.result import WorkflowResult, WorkflowStatus

from wbg_orchestrator.checkpointing import (CHECKPOINT_OBJECTS, CheckpointConfigError, CheckpointDbSettings, IsolationError,
                                            TokenProvider, checkpoint_pool, checkpointer, create_schema, database_inventory,
                                            isolated_setup, oauth_connection_class, verify_isolation)
from wbg_orchestrator.contracts import InvestigationStatus
from wbg_orchestrator.engine import InvestigationNotFound, checkpoint_thread_id
from wbg_orchestrator.observability import OrchestratorTracer
from wbg_orchestrator.planner import FakePlanner

from tests import pg_ephemeral
from tests.helpers import ALICE, BOB, SECRET, FakeWorkflow, build_engine, plan, request, task, workflow_result

pytestmark = pytest.mark.skipif(not pg_ephemeral.available(), reason="local PostgreSQL 18 binaries not installed")

SCHEMA = pg_ephemeral.SCHEMA
TOKEN_SECRET = "dapi-SYNTHETIC-lakebase-token-0000000000000000"
QUESTION = "Investigate how many bid invitations were published in fiscal year 2024 and then explain the metric"
DATA_QUESTION = "How many bid invitations were published in fiscal year 2024?"


@pytest.fixture(scope="module")
def cluster():
    with pg_ephemeral.ephemeral_cluster() as running:
        yield running


@pytest.fixture(scope="module")
def baseline(cluster):
    """Every other schema as it was before the checkpointer touched the database."""
    with cluster.admin() as conn:
        inventory = database_inventory(conn)
        rows = conn.execute("SELECT id, text FROM wbg_rag_poc.chunks ORDER BY id").fetchall()
    return {s: o for s, o in inventory.items() if s != SCHEMA}, rows


def open_pool(cluster, tokens: TokenProvider | None = None):
    pool = checkpoint_pool(cluster.kwargs(), SCHEMA, tokens or TokenProvider(lambda: TOKEN_SECRET), max_size=2, wait_seconds=10)
    pool.open(wait=True, timeout=30)
    return pool


@pytest.fixture(scope="module")
def setup_report(cluster, baseline):
    pool = open_pool(cluster)
    try:
        yield isolated_setup(pool, checkpointer(pool), SCHEMA)
    finally:
        pool.close()


@pytest.fixture
def pool(cluster, setup_report):
    opened = open_pool(cluster)
    yield opened
    opened.close()


def planned():
    return FakePlanner(plan(task("metric", operation="describe_metric", question="Which metric counts bid invitations?"),
                            task("count", deps=("metric",), execute=True, question=DATA_QUESTION)))


def checkpoint_dump(cluster) -> str:
    """Everything the checkpointer stored, as text (JSONB checkpoints plus blob and write payloads)."""
    with cluster.admin() as conn:
        parts = [json.dumps(r[0]) for r in conn.execute(f"SELECT checkpoint FROM {SCHEMA}.checkpoints")]
        parts += [json.dumps(r[0]) for r in conn.execute(f"SELECT metadata FROM {SCHEMA}.checkpoints")]
        parts += [bytes(r[0]).decode("utf-8", "replace") for r in conn.execute(f"SELECT blob FROM {SCHEMA}.checkpoint_blobs WHERE blob IS NOT NULL")]
        parts += [bytes(r[0]).decode("utf-8", "replace") for r in conn.execute(f"SELECT blob FROM {SCHEMA}.checkpoint_writes")]
    return "\n".join(parts)


# ------------------------------------------------------------------------------------------- settings / branch
def test_settings_refuse_the_production_branch_unsafe_schemas_and_plain_text_connections():
    base = {"WBG_CKPT_HOST": "h.example", "WBG_CKPT_DATABASE": "databricks_postgres", "WBG_CKPT_USER": "someone@example.org",
            "WBG_CKPT_ENDPOINT": "projects/wbg-procurement-dev/branches/dev-checkpoints/endpoints/primary"}
    settings = CheckpointDbSettings.from_env(base)
    assert settings.branch == "dev-checkpoints" and settings.schema == "wbg_orchestrator_ckpt"
    assert "someone" not in repr(settings) and "h.example" not in repr(settings)
    for override in ({"WBG_CKPT_ENDPOINT": "projects/wbg-procurement-dev/branches/production/endpoints/primary"},
                     {"WBG_CKPT_SCHEMA": "public"}, {"WBG_CKPT_SCHEMA": "pg_catalog"}, {"WBG_CKPT_SSLMODE": "disable"},
                     {"WBG_CKPT_ENDPOINT": "wbg-procurement-dev"}):
        with pytest.raises(CheckpointConfigError):
            CheckpointDbSettings.from_env({**base, **override})
    with pytest.raises(CheckpointConfigError):
        CheckpointDbSettings.from_env({k: v for k, v in base.items() if k != "WBG_CKPT_USER"})


def test_token_provider_reuses_then_refreshes_before_expiry():
    clock, issued = [0.0], []
    provider = TokenProvider(lambda: (issued.append(1), f"token-{len(issued)}")[1], lifetime_seconds=3600,
                             refresh_margin_seconds=900, clock=lambda: clock[0])
    assert provider.get() == "token-1" and provider.get() == "token-1"
    clock[0] = 2699
    assert provider.get() == "token-1"
    clock[0] = 2701                                                     # 45 minutes: refreshed before the 60-minute expiry
    assert provider.get() == "token-2" and provider.fetch_count == 2
    assert "token" not in repr(provider).replace("TokenProvider", "")


def test_every_new_connection_takes_the_current_credential(cluster, setup_report):
    clock, issued = [0.0], []
    provider = TokenProvider(lambda: (issued.append(1), f"tok-{len(issued)}")[1], lifetime_seconds=60,
                             refresh_margin_seconds=10, clock=lambda: clock[0])
    connection_class = oauth_connection_class(provider)
    with connection_class.connect(**cluster.kwargs()) as first:
        assert first.info.password == "tok-1"
    clock[0] = 51
    with connection_class.connect(**cluster.kwargs()) as second:
        assert second.info.password == "tok-2"
    pool = open_pool(cluster, provider)
    try:
        with pool.connection() as conn:
            assert conn.info.password == "tok-2" and conn.autocommit
    finally:
        pool.close()


# ----------------------------------------------------------------------------------------------- isolation
def test_an_unpinned_connection_is_refused_before_setup(cluster, setup_report):
    with psycopg.connect(**cluster.kwargs()) as conn:                 # default search_path "$user", public
        with pytest.raises(IsolationError, match="search_path"):
            verify_isolation(conn, SCHEMA)
    with cluster.admin() as conn:
        assert create_schema(conn, "wbg_ckpt_foreign") is True
        conn.execute("CREATE TABLE wbg_ckpt_foreign.not_a_checkpoint (id int)")
        conn.execute("SET search_path TO wbg_ckpt_foreign")
        with pytest.raises(IsolationError, match="non-checkpoint"):
            verify_isolation(conn, "wbg_ckpt_foreign")
        conn.execute("SET search_path TO wbg_ckpt_missing")
        with pytest.raises(IsolationError, match="does not exist"):
            verify_isolation(conn, "wbg_ckpt_missing")
        conn.execute("DROP SCHEMA wbg_ckpt_foreign CASCADE")       # test-local scratch schema only


def test_setup_creates_only_checkpointer_objects_in_the_isolated_schema(cluster, baseline, setup_report, pool):
    assert set(setup_report.created) == CHECKPOINT_OBJECTS and setup_report.other_schemas_unchanged
    with pool.connection() as conn:
        report = verify_isolation(conn, SCHEMA)
        assert report.effective_schemas == (SCHEMA,) and report.server_version.startswith("18.")
    assert isolated_setup(pool, checkpointer(pool), SCHEMA).created == ()      # idempotent
    others, rows = baseline
    with cluster.admin() as conn:
        after = database_inventory(conn)
        assert {s: o for s, o in after.items() if s != SCHEMA} == others
        assert conn.execute("SELECT id, text FROM wbg_rag_poc.chunks ORDER BY id").fetchall() == rows
        assert [n for n, _ in after["public"]] == ["existing_table"]


# ------------------------------------------------------------------------------- persistence and restarts
def test_checkpoint_is_saved_and_reads_back_identically(pool):
    engine, _ = build_engine(checkpointer=checkpointer(pool))
    out = engine.start(request(DATA_QUESTION, execute=True))
    assert out.status == InvestigationStatus.ANSWERED
    stored = engine.checkpointer.get_tuple({"configurable": {"thread_id": out.thread_id}})
    assert stored is not None and isinstance(engine.checkpointer, PostgresSaver)
    values = stored.checkpoint["channel_values"]
    for key in ("request", "plan", "evidence", "results", "task_states", "response", "usage"):
        assert json.dumps(values[key], sort_keys=True) == json.dumps(out.values[key], sort_keys=True), key


class Crash(BaseException):
    """Stands in for a killed process (not an Exception, so nothing catches it)."""


def test_an_interrupted_run_survives_a_restart_and_only_the_owner_can_recover_it(cluster, setup_report):
    def crash(question: str, execute: bool):
        raise Crash()
    first_pool = open_pool(cluster)
    engine, _ = build_engine(planner=planned(), workflow=FakeWorkflow(crash), checkpointer=checkpointer(first_pool))
    req = request(QUESTION)
    with pytest.raises(Crash):
        engine.start(req)
    thread_id = checkpoint_thread_id(req.owner, req.conversation_thread_id, req.investigation_id)
    first_pool.close()                                              # "restart": connections closed, graph rebuilt

    second_pool = open_pool(cluster)
    try:
        workflow = FakeWorkflow()
        restarted, _ = build_engine(planner=FakePlanner(), workflow=workflow, checkpointer=checkpointer(second_pool))
        with pytest.raises(InvestigationNotFound):
            restarted.recover(BOB, thread_id)
        with pytest.raises(InvestigationNotFound):
            restarted.outcome(BOB, thread_id)
        assert restarted.outcome(ALICE, thread_id).unfinished
        done = restarted.recover(ALICE, thread_id)
        assert done.status == InvestigationStatus.ANSWERED and [c["execute"] for c in workflow.calls] == [True]
        assert restarted.recover(ALICE, thread_id).status == InvestigationStatus.ANSWERED and len(workflow.calls) == 1
    finally:
        second_pool.close()


def test_checkpoints_hold_no_secrets_or_unrestricted_rows(cluster, pool):
    rows = [{"row_id": i, "value": f"ROW-MARKER-{i:03d}"} for i in range(40)]

    def respond(question: str, execute: bool) -> WorkflowResult:
        result = workflow_result(question, execute=execute)
        return result.model_copy(update={"rows": rows + [{"row_id": 99, "value": f"api_key={SECRET}"}], "row_count": 41,
                                         "message": f"Query executed. token {SECRET}"})

    engine, _ = build_engine(workflow=FakeWorkflow(respond), checkpointer=checkpointer(pool))
    out = engine.start(request(DATA_QUESTION, execute=True))
    assert out.status == InvestigationStatus.ANSWERED
    dump = checkpoint_dump(cluster)
    assert SECRET not in dump and TOKEN_SECRET not in dump
    assert "ROW-MARKER-011" in dump                                   # the first 25 cells (2 per row) are bounded facts
    assert "ROW-MARKER-012" not in dump and "ROW-MARKER-039" not in dump    # the rest stays out of checkpoints
    assert "SELECT" not in dump                                       # SQL is stored only as a hash
    assert '"rows"' not in dump


def test_a_failed_task_keeps_its_error_and_partial_state(pool):
    planner = FakePlanner(plan(task("count", execute=True, question=DATA_QUESTION),
                               task("metric", operation="describe_metric", question="Which metric?", optional=True)))
    failing = FakeWorkflow(lambda q, e: workflow_result(q, execute=e, status=WorkflowStatus.CANNOT_ANSWER, message="no data"))
    engine, _ = build_engine(planner=planner, workflow=failing, checkpointer=checkpointer(pool))
    done = engine.start(request(QUESTION))
    reread, _ = build_engine(checkpointer=checkpointer(pool))
    again = reread.outcome(ALICE, done.thread_id)
    assert again.status == done.status and again.task_states == done.task_states
    assert "failed" in again.task_states.values() and again.values["errors"] == done.values["errors"]


def test_checkpoint_saves_and_resumes_are_traced(pool):
    def crash(question: str, execute: bool):
        raise Crash()
    exporter = InMemorySpanExporter()
    tracer = OrchestratorTracer.local([SimpleSpanProcessor(exporter)])
    engine, _ = build_engine(planner=planned(), workflow=FakeWorkflow(crash), checkpointer=checkpointer(pool), tracer=tracer)
    req = request(QUESTION)
    with pytest.raises(Crash):
        engine.start(req)
    thread_id = checkpoint_thread_id(req.owner, req.conversation_thread_id, req.investigation_id)
    restarted, _ = build_engine(planner=FakePlanner(), checkpointer=checkpointer(pool), tracer=tracer)
    restarted.recover(ALICE, thread_id)                             # resumed from the checkpoint, then saved
    tracer.force_flush()
    events = [s for s in exporter.get_finished_spans() if s.name.startswith("orchestrator.checkpoint_")]
    assert {s.name for s in events} == {"orchestrator.checkpoint_saved", "orchestrator.checkpoint_resumed"}
    for span in events:
        assert span.attributes["checkpointer"] == "PostgresSaver" and span.attributes["checkpoint_thread"] == thread_id
        assert span.attributes["checkpoint_id"] and span.attributes["conversation_id"] == "conv-1"
