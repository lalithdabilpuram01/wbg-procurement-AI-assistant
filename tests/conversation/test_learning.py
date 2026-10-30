"""Feedback and learning records: the same behaviour in memory and in PostgreSQL. The app's store is in Lakebase; the
PostgreSQL variant here mirrors it offline on a temporary local schema (one owner identity, forced row-level
security) and is skipped when the local server is not available."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from conversation.learning import (Feedback, InMemoryLearningStore, LearnedItem, LearningItemNotFound, PostgresLearningStore,
                                   Review, apply_schema, read_all)
from conversation.postgres_store import DEFAULT_ADMIN_DSN
from conversation.store import InMemoryConversationStore
from tests.conversation.conftest import ALICE, BOB, MALLORY
from tests.conversation.test_postgres_store import roles_available


@pytest.fixture(scope="module")
def pg_schema():
    if not roles_available():
        pytest.skip("conversation roles not bootstrapped")
    name = f"learning_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(DEFAULT_ADMIN_DSN) as conn:
        apply_schema(conn, name)
        apply_schema(conn, name)                              # re-applying is non-destructive
    yield name
    with psycopg.connect(DEFAULT_ADMIN_DSN) as conn:
        conn.execute(f"DROP SCHEMA {name} CASCADE")           # the temporary test schema only


@pytest.fixture(params=["memory", "postgres"])
def stores(request):
    if request.param == "memory":
        return InMemoryConversationStore(), InMemoryLearningStore()
    schema = request.getfixturevalue("pg_schema")
    return InMemoryConversationStore(), PostgresLearningStore.for_dsn(DEFAULT_ADMIN_DSN, schema)


def item(owner, **fields) -> LearnedItem:
    return LearnedItem(**{"user_id": owner.user_id, "kind": "correction", "statement": "Include closed projects",
                          "question": "Ethiopia awards", "embedding": (0.1, 0.2), **fields})


def test_feedback_is_recorded_linked_to_its_revision_and_validated(stores):
    conversations, learning = stores
    thread = conversations.create_thread(ALICE, "Awards")
    feedback = learning.add_feedback(ALICE, Feedback(thread_id=thread.thread_id, turn_seq=2, request_id="req_1", rating="down",
                                                     reasons=("wrong_scope",), correction="Only FY2024", status="revising"))
    learning.set_feedback_status(ALICE, feedback.feedback_id, "revised", "req_2")
    found = learning.feedback_for_revision(ALICE, "req_2")
    assert found.feedback_id == feedback.feedback_id and found.reasons == ("wrong_scope",)
    assert learning.set_feedback_status(ALICE, feedback.feedback_id, "validated").revision_request_id == "req_2"
    assert [f.status for f in learning.thread_feedback(ALICE, thread.thread_id)] == ["validated"]
    assert learning.thread_feedback(BOB, thread.thread_id) == [] and learning.feedback_for_revision(BOB, "req_2") is None
    with pytest.raises(LearningItemNotFound):
        learning.set_feedback_status(BOB, feedback.feedback_id, "validated")


def test_preferences_are_per_user_and_can_be_reset(stores):
    _, learning = stores
    learning.set_preference(ALICE, "amount_unit", "usd_millions")
    learning.set_preference(ALICE, "amount_unit", "usd")
    learning.set_preference(ALICE, "default_fiscal_years", [2015, 2024])
    assert learning.preferences(ALICE) == {"amount_unit": "usd", "default_fiscal_years": [2015, 2024]}
    assert learning.preferences(BOB) == {}
    learning.clear_preference(ALICE, "amount_unit")
    assert learning.preferences(ALICE) == {"default_fiscal_years": [2015, 2024]}


def test_personal_items_stay_with_their_owner_and_shared_items_reach_the_tenant(stores):
    _, learning = stores
    personal = learning.add_item(ALICE, item(ALICE))
    shared = learning.add_item(BOB, item(ALICE, scope="shared", approved_by="bob", statement="Use the notice activity view"))
    assert {i.item_id for i in learning.items(ALICE)} == {personal.item_id, shared.item_id}
    assert [i.item_id for i in learning.items(BOB)] == [shared.item_id]
    assert learning.items(MALLORY) == []                                       # another tenant
    assert learning.items(ALICE)[0].embedding is not None
    with pytest.raises(LearningItemNotFound):
        learning.revoke_item(BOB, personal.item_id)
    assert learning.revoke_item(ALICE, personal.item_id).status == "revoked"
    assert [i.item_id for i in learning.items(ALICE)] == [shared.item_id]
    assert len(learning.items(ALICE, include_revoked=True)) == 2


def test_uses_are_recorded_once_per_request_and_counted(stores):
    _, learning = stores
    saved = learning.add_item(ALICE, item(ALICE))
    learning.record_uses(ALICE, "req_9", [saved.item_id])
    learning.record_uses(ALICE, "req_9", [saved.item_id])                      # a repeat is not counted twice
    learning.record_uses(ALICE, "req_10", [saved.item_id])
    assert [i.item_id for i in learning.used_items(ALICE, "req_9")] == [saved.item_id]
    assert learning.used_items(BOB, "req_9") == []
    current = learning.items(ALICE)[0]
    assert current.use_count == 2 and current.last_used_at is not None


def test_reviews_are_tenant_wide_and_decided_once(stores):
    _, learning = stores
    review = learning.add_review(ALICE, Review(submitted_by="alice", kind="eval_case", payload={"question": "Awards in FY2024?"}))
    assert [r.review_id for r in learning.reviews(BOB, "pending")] == [review.review_id]   # same tenant (a reviewer)
    assert learning.reviews(MALLORY) == []
    decided = learning.decide_review(BOB, review.review_id, "approved", "good case", ("2024",))
    assert (decided.status, decided.reviewer, decided.expected_terms) == ("approved", "bob", ("2024",))
    assert learning.reviews(BOB, "pending") == [] and learning.reviews(BOB, "approved")[0].note == "good case"
    with pytest.raises(LearningItemNotFound):
        learning.decide_review(MALLORY, review.review_id, "rejected", None)


def test_row_level_security_holds_for_the_owner_identity_and_only_the_operator_reads_across_users(pg_schema):
    learning = PostgresLearningStore.for_dsn(DEFAULT_ADMIN_DSN, pg_schema)
    learning.add_feedback(ALICE, Feedback(thread_id=str(uuid.uuid4()), turn_seq=2, rating="up"))
    learning.add_review(ALICE, Review(submitted_by="alice", kind="eval_case", payload={"question": "q"}))
    with psycopg.connect(DEFAULT_ADMIN_DSN) as conn:                         # the tables' owner, no WHERE clause
        conn.execute("SELECT set_config('wbg.tenant_id', %s, false), set_config('wbg.user_id', %s, false)",
                     (BOB.tenant_id, BOB.user_id))
        assert conn.execute(f"SELECT count(*) FROM {pg_schema}.feedback").fetchone()[0] == 0
        conn.execute("RESET wbg.tenant_id")
        conn.execute("RESET wbg.user_id")
        assert conn.execute(f"SELECT count(*) FROM {pg_schema}.reviews").fetchone()[0] == 0
    rows = read_all(lambda: psycopg.connect(DEFAULT_ADMIN_DSN), pg_schema)
    assert len(rows["feedback"]) >= 1 and len(rows["reviews"]) >= 1
