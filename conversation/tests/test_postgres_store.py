"""PostgreSQL conversation store in a temporary schema: durability, owner isolation (queries + row-level security),
append-only turns and least privilege. Skipped when the local conversation roles are not available."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from conversation.init_store import apply_schema
from conversation.models import ActiveContext, Decision, TurnKind
from conversation.postgres_store import DEFAULT_ADMIN_DSN, DEFAULT_DSN, PostgresConversationStore
from conversation.routing import Route
from conversation.service import ConversationService
from conversation.store import ThreadNotFound
from conversation.tests.conftest import ALICE, BOB, MALLORY, make_turn
from conversation.tests.test_service import SCENARIO, FakeStructuredAgent, PolicyGuard


def roles_available() -> bool:
    try:
        with psycopg.connect(DEFAULT_ADMIN_DSN, connect_timeout=2), psycopg.connect(DEFAULT_DSN, connect_timeout=2):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(not roles_available(), reason="conversation roles not bootstrapped")


@pytest.fixture(scope="module")
def schema():
    name = f"conversation_test_{uuid.uuid4().hex[:10]}"
    apply_schema(DEFAULT_ADMIN_DSN, name)
    apply_schema(DEFAULT_ADMIN_DSN, name)            # re-applying is non-destructive
    yield name
    with psycopg.connect(DEFAULT_ADMIN_DSN) as conn:
        conn.execute(f"DROP SCHEMA {name} CASCADE")   # the temporary test schema only


@pytest.fixture
def store(schema) -> PostgresConversationStore:
    return PostgresConversationStore(DEFAULT_DSN, schema)


def test_round_trip_is_durable_across_store_instances(store, schema):
    thread = store.create_thread(ALICE, "Kenya activity")
    store.append_turn(ALICE, make_turn(thread.thread_id, 1, "user", "Show procurement activity in Kenya.",
                                       ActiveContext(topic="t", fiscal_years=[2024]), standalone_question="q"))
    store.add_decision(ALICE, Decision(thread_id=thread.thread_id, kind="constraint", statement="Only IDA.", approved_by="alice"))
    reopened = PostgresConversationStore(DEFAULT_DSN, schema)
    turns = reopened.turns(ALICE, thread.thread_id)
    assert [(t.seq, t.kind, t.context.fiscal_years, t.metadata.standalone_question) for t in turns] == [
        (1, TurnKind.QUESTION, [2024], "q")]
    assert reopened.next_seq(ALICE, thread.thread_id) == 2
    assert [d.statement for d in reopened.decisions(ALICE, thread.thread_id)] == ["Only IDA."]
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.append_turn(ALICE, make_turn(thread.thread_id, 1, "user", "duplicate seq"))


@pytest.mark.parametrize("intruder", [BOB, MALLORY])
def test_other_owners_cannot_see_or_write(store, intruder):
    thread = store.create_thread(ALICE, "private")
    store.append_turn(ALICE, make_turn(thread.thread_id, 1, "user", "Show procurement activity in Kenya."))
    for action in (lambda: store.get_thread(intruder, thread.thread_id), lambda: store.turns(intruder, thread.thread_id),
                   lambda: store.append_turn(intruder, make_turn(thread.thread_id, 2, "user", "x")),
                   lambda: store.latest_summary(intruder, thread.thread_id), lambda: store.start_new_topic(intruder, thread.thread_id)):
        with pytest.raises(ThreadNotFound):
            action()
    assert thread.thread_id not in [t.thread_id for t in store.list_threads(intruder)]
    with pytest.raises(ThreadNotFound):
        store.get_thread(ALICE, "not-a-uuid")


def test_row_level_security_hides_other_owners_rows_even_without_filters(store, schema):
    thread = store.create_thread(ALICE, "rls")
    store.append_turn(ALICE, make_turn(thread.thread_id, 1, "user", "Show procurement activity in Kenya."))
    with psycopg.connect(DEFAULT_DSN) as conn:
        conn.execute("SELECT set_config('wbg.tenant_id', %s, true), set_config('wbg.user_id', %s, true)",
                     (MALLORY.tenant_id, MALLORY.user_id))
        assert conn.execute(f"SELECT count(*) FROM {schema}.turns").fetchone()[0] == 0
        assert conn.execute(f"SELECT count(*) FROM {schema}.threads").fetchone()[0] == 0
    with psycopg.connect(DEFAULT_DSN) as conn:                           # no owner set: nothing visible
        assert conn.execute(f"SELECT count(*) FROM {schema}.turns").fetchone()[0] == 0


@pytest.mark.parametrize("statement", ["UPDATE {s}.turns SET text = 'changed'", "DELETE FROM {s}.turns",
                                       "DELETE FROM {s}.summaries", "DROP TABLE {s}.turns",
                                       "UPDATE {s}.threads SET tenant_id = 'other'"])
def test_runtime_role_is_append_only_and_cannot_change_owners(schema, statement):
    with psycopg.connect(DEFAULT_DSN) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(statement.format(s=schema))


def test_runtime_role_cannot_read_the_registry_schema():
    with psycopg.connect(DEFAULT_DSN) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT count(*) FROM registry.registry_builds")


def test_scenario_on_postgres(store):
    agent = FakeStructuredAgent()
    service = ConversationService(store, {Route.STRUCTURED_DATA: agent}, PolicyGuard())
    thread = service.start_thread(ALICE)
    replies = [service.handle_message(ALICE, thread.thread_id, message) for message in SCENARIO]
    assert [r.status for r in replies] == ["answered", "answered", "answered", "route_unavailable", "route_unavailable"]
    assert len(store.turns(ALICE, thread.thread_id)) == 10
    assert store.get_thread(ALICE, thread.thread_id).title == "Show procurement activity in Kenya."
