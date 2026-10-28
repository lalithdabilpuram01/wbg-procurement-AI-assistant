"""Feedback and learning records: feedback on answers, saved preferences, learned items and the reviewer queue.

Owner-scoped like the conversation store: every operation takes the trusted `Owner`. Feedback, preferences and uses
belong to one user. A learned item belongs to the user whose feedback produced it (`personal`); a reviewer can share
it with the tenant (`shared`, a separate copy). Reviews are tenant-wide; who may decide them is checked by the caller.
The PostgreSQL store lives in Lakebase (its own schema, `wbg_learning`, on a development branch) and relies on forced
row-level security (sql/learning_schema.sql). Conversations are in another database, so the caller checks thread
ownership in the conversation store first. Nothing is deleted except a preference the user resets.
"""
from __future__ import annotations

import datetime as dt
import threading
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Protocol

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from conversation.models import Owner, new_id, utc_now
from conversation.postgres_store import _is_uuid, checked_schema

LEARNING_SCHEMA = "wbg_learning"
SCHEMA_SQL = Path(__file__).with_name("sql") / "learning_schema.sql"
Connect = Callable[[], AbstractContextManager[psycopg.Connection]]

Rating = Literal["up", "down"]
FeedbackStatus = Literal["recorded", "revising", "revised", "validated"]
ItemKind = Literal["correction", "approach"]
ReviewKind = Literal["share_correction", "share_approach", "eval_case"]
ReviewStatus = Literal["pending", "approved", "rejected"]
REASONS = ("wrong_number", "wrong_scope", "wrong_source", "missing_information", "other")
PREFERENCE_KEYS = ("default_fiscal_years", "amount_unit", "answer_length", "table_lens")
PERSONAL_ITEM_DAYS = 90                    # a personal item unused for this long is no longer applied


class LearningItemNotFound(KeyError):
    """The feedback, item or review does not exist for this owner."""


class Feedback(BaseModel):
    model_config = ConfigDict(frozen=True)
    feedback_id: str = Field(default_factory=new_id)
    thread_id: str
    turn_seq: int = Field(gt=0)
    request_id: str | None = None
    question: str = Field(default="", max_length=2000)    # the question the rated answer was for
    rating: Rating
    reasons: tuple[str, ...] = ()
    correction: str | None = Field(default=None, max_length=500)
    status: FeedbackStatus = "recorded"
    revision_request_id: str | None = None                  # the request that re-ran the question with the correction
    created_at: dt.datetime = Field(default_factory=utc_now)
    updated_at: dt.datetime = Field(default_factory=utc_now)


class LearnedItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    item_id: str = Field(default_factory=new_id)
    user_id: str                                            # whose feedback it came from
    scope: Literal["personal", "shared"] = "personal"
    kind: ItemKind
    statement: str = Field(min_length=3, max_length=500)
    question: str = Field(max_length=2000)
    details: dict[str, Any] = Field(default_factory=dict)
    embedding: tuple[float, ...] | None = None
    source_feedback_id: str | None = None
    approved_by: str | None = None
    status: Literal["active", "revoked"] = "active"
    use_count: int = 0
    last_used_at: dt.datetime | None = None
    created_at: dt.datetime = Field(default_factory=utc_now)
    revoked_at: dt.datetime | None = None

    def expired(self, now: dt.datetime) -> bool:
        """Personal items lapse after PERSONAL_ITEM_DAYS without use; shared items stay until a reviewer revokes them."""
        last = self.last_used_at or self.created_at
        return self.scope == "personal" and now - last > dt.timedelta(days=PERSONAL_ITEM_DAYS)


class Review(BaseModel):
    model_config = ConfigDict(frozen=True)
    review_id: str = Field(default_factory=new_id)
    submitted_by: str
    kind: ReviewKind
    payload: dict[str, Any]
    source_feedback_id: str | None = None
    status: ReviewStatus = "pending"
    reviewer: str | None = None
    note: str | None = Field(default=None, max_length=300)
    expected_terms: tuple[str, ...] = ()                    # eval cases: what a correct answer must contain
    created_at: dt.datetime = Field(default_factory=utc_now)
    decided_at: dt.datetime | None = None


class LearningStore(Protocol):
    def add_feedback(self, owner: Owner, feedback: Feedback) -> Feedback: ...
    def set_feedback_status(self, owner: Owner, feedback_id: str, status: FeedbackStatus,
                            revision_request_id: str | None = None) -> Feedback: ...
    def thread_feedback(self, owner: Owner, thread_id: str) -> list[Feedback]: ...
    def feedback_for_revision(self, owner: Owner, request_id: str) -> Feedback | None: ...
    def preferences(self, owner: Owner) -> dict[str, Any]: ...
    def set_preference(self, owner: Owner, key: str, value: Any) -> None: ...
    def clear_preference(self, owner: Owner, key: str) -> None: ...
    def add_item(self, owner: Owner, item: LearnedItem) -> LearnedItem: ...
    def items(self, owner: Owner, include_revoked: bool = False) -> list[LearnedItem]: ...
    def revoke_item(self, owner: Owner, item_id: str) -> LearnedItem: ...
    def record_uses(self, owner: Owner, request_id: str, item_ids: list[str]) -> None: ...
    def used_items(self, owner: Owner, request_id: str) -> list[LearnedItem]: ...
    def add_review(self, owner: Owner, review: Review) -> Review: ...
    def reviews(self, owner: Owner, status: ReviewStatus | None = None) -> list[Review]: ...
    def decide_review(self, owner: Owner, review_id: str, status: ReviewStatus, note: str | None,
                      expected_terms: tuple[str, ...] = ()) -> Review: ...


class InMemoryLearningStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._feedback: dict[str, tuple[Owner, Feedback]] = {}
        self._preferences: dict[tuple[str, str, str], Any] = {}
        self._items: dict[str, tuple[str, LearnedItem]] = {}                 # item_id -> (tenant, item)
        self._uses: dict[tuple[str, str], Owner] = {}                         # (request_id, item_id) -> owner
        self._reviews: dict[str, tuple[str, Review]] = {}                     # review_id -> (tenant, review)

    # ------------------------------------------------------------------------------------------------ feedback
    def add_feedback(self, owner: Owner, feedback: Feedback) -> Feedback:
        with self._lock:
            self._feedback[feedback.feedback_id] = (owner, feedback)
        return feedback

    def set_feedback_status(self, owner: Owner, feedback_id: str, status: FeedbackStatus,
                            revision_request_id: str | None = None) -> Feedback:
        with self._lock:
            entry = self._feedback.get(feedback_id)
            if entry is None or entry[0] != owner:
                raise LearningItemNotFound("feedback not found")
            updated = entry[1].model_copy(update={"status": status, "updated_at": utc_now(),
                                                  "revision_request_id": revision_request_id or entry[1].revision_request_id})
            self._feedback[feedback_id] = (owner, updated)
            return updated

    def thread_feedback(self, owner: Owner, thread_id: str) -> list[Feedback]:
        return sorted((f for o, f in self._feedback.values() if o == owner and f.thread_id == thread_id),
                      key=lambda f: f.created_at)

    def feedback_for_revision(self, owner: Owner, request_id: str) -> Feedback | None:
        return next((f for o, f in self._feedback.values() if o == owner and f.revision_request_id == request_id), None)

    # --------------------------------------------------------------------------------------------- preferences
    def preferences(self, owner: Owner) -> dict[str, Any]:
        return {k: v for (t, u, k), v in self._preferences.items() if (t, u) == (owner.tenant_id, owner.user_id)}

    def set_preference(self, owner: Owner, key: str, value: Any) -> None:
        with self._lock:
            self._preferences[(owner.tenant_id, owner.user_id, key)] = value

    def clear_preference(self, owner: Owner, key: str) -> None:
        with self._lock:
            self._preferences.pop((owner.tenant_id, owner.user_id, key), None)

    # --------------------------------------------------------------------------------------------------- items
    def add_item(self, owner: Owner, item: LearnedItem) -> LearnedItem:
        with self._lock:
            self._items[item.item_id] = (owner.tenant_id, item)
        return item

    def items(self, owner: Owner, include_revoked: bool = False) -> list[LearnedItem]:
        visible = [i for t, i in self._items.values() if t == owner.tenant_id and self._visible(owner, i)]
        return sorted((i for i in visible if include_revoked or i.status == "active"), key=lambda i: i.created_at, reverse=True)

    def revoke_item(self, owner: Owner, item_id: str) -> LearnedItem:
        with self._lock:
            entry = self._items.get(item_id)
            if entry is None or entry[0] != owner.tenant_id or not self._visible(owner, entry[1]):
                raise LearningItemNotFound("item not found")
            revoked = entry[1].model_copy(update={"status": "revoked", "revoked_at": utc_now()})
            self._items[item_id] = (entry[0], revoked)
            return revoked

    def record_uses(self, owner: Owner, request_id: str, item_ids: list[str]) -> None:
        now = utc_now()
        with self._lock:
            for item_id in item_ids:
                tenant, item = self._items[item_id]
                if (request_id, item_id) not in self._uses:
                    self._uses[(request_id, item_id)] = owner
                    self._items[item_id] = (tenant, item.model_copy(update={"use_count": item.use_count + 1, "last_used_at": now}))

    def used_items(self, owner: Owner, request_id: str) -> list[LearnedItem]:
        return [self._items[i][1] for (r, i), o in self._uses.items() if r == request_id and o == owner]

    # ------------------------------------------------------------------------------------------------- reviews
    def add_review(self, owner: Owner, review: Review) -> Review:
        with self._lock:
            self._reviews[review.review_id] = (owner.tenant_id, review)
        return review

    def reviews(self, owner: Owner, status: ReviewStatus | None = None) -> list[Review]:
        return sorted((r for t, r in self._reviews.values() if t == owner.tenant_id and status in (None, r.status)),
                      key=lambda r: r.created_at)

    def decide_review(self, owner: Owner, review_id: str, status: ReviewStatus, note: str | None,
                      expected_terms: tuple[str, ...] = ()) -> Review:
        with self._lock:
            entry = self._reviews.get(review_id)
            if entry is None or entry[0] != owner.tenant_id:
                raise LearningItemNotFound("review not found")
            decided = entry[1].model_copy(update={"status": status, "reviewer": owner.user_id, "note": note,
                                                  "expected_terms": expected_terms, "decided_at": utc_now()})
            self._reviews[review_id] = (entry[0], decided)
            return decided

    @staticmethod
    def _visible(owner: Owner, item: LearnedItem) -> bool:
        return item.scope == "shared" or item.user_id == owner.user_id


def schema_statements(schema: str = LEARNING_SCHEMA) -> str:
    name = checked_schema(schema)
    return SCHEMA_SQL.read_text().replace("{schema_literal}", name).replace("{schema}", name)


def apply_schema(conn: psycopg.Connection, schema: str = LEARNING_SCHEMA) -> None:
    """Create the schema, tables and policies if missing (non-destructive; safe to re-run)."""
    with conn.transaction():
        conn.execute(schema_statements(schema), prepare=False)         # a multi-statement script cannot be prepared


class PostgresLearningStore:
    """`connect` yields a connection: the Lakebase pool's in the app (`pool.connection`), a plain one in tests."""

    def __init__(self, connect: Connect, schema: str = LEARNING_SCHEMA):
        self._connect = connect
        self._schema = sql.Identifier(checked_schema(schema))

    @classmethod
    def for_dsn(cls, dsn: str, schema: str = LEARNING_SCHEMA) -> "PostgresLearningStore":
        return cls(lambda: psycopg.connect(dsn), schema)

    # ------------------------------------------------------------------------------------------------ feedback
    def add_feedback(self, owner: Owner, feedback: Feedback) -> Feedback:
        with self._transaction(owner) as cur:
            cur.execute(self._q(
                "INSERT INTO {s}.feedback (feedback_id, tenant_id, user_id, thread_id, turn_seq, request_id, question, rating, "
                "reasons, correction, status, revision_request_id, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"),
                (feedback.feedback_id, owner.tenant_id, owner.user_id, feedback.thread_id, feedback.turn_seq,
                 feedback.request_id, feedback.question, feedback.rating, list(feedback.reasons), feedback.correction,
                 feedback.status, feedback.revision_request_id, feedback.created_at, feedback.updated_at))
        return feedback

    def set_feedback_status(self, owner: Owner, feedback_id: str, status: FeedbackStatus,
                            revision_request_id: str | None = None) -> Feedback:
        with self._transaction(owner) as cur:
            cur.execute(self._q(
                "UPDATE {s}.feedback SET status = %s, revision_request_id = coalesce(%s, revision_request_id), updated_at = %s "
                "WHERE feedback_id = %s AND tenant_id = %s AND user_id = %s RETURNING *"),
                (status, revision_request_id, utc_now(), _uuid(feedback_id), owner.tenant_id, owner.user_id))
            row = cur.fetchone()
        if row is None:
            raise LearningItemNotFound("feedback not found")
        return row_to_feedback(row)

    def thread_feedback(self, owner: Owner, thread_id: str) -> list[Feedback]:
        with self._transaction(owner) as cur:
            cur.execute(self._q("SELECT * FROM {s}.feedback WHERE thread_id = %s AND tenant_id = %s AND user_id = %s "
                                "ORDER BY created_at"), (_uuid(thread_id), owner.tenant_id, owner.user_id))
            return [row_to_feedback(r) for r in cur.fetchall()]

    def feedback_for_revision(self, owner: Owner, request_id: str) -> Feedback | None:
        with self._transaction(owner) as cur:
            cur.execute(self._q("SELECT * FROM {s}.feedback WHERE revision_request_id = %s AND tenant_id = %s AND user_id = %s "
                                "ORDER BY created_at DESC LIMIT 1"), (request_id, owner.tenant_id, owner.user_id))
            row = cur.fetchone()
        return row_to_feedback(row) if row else None

    # --------------------------------------------------------------------------------------------- preferences
    def preferences(self, owner: Owner) -> dict[str, Any]:
        with self._transaction(owner) as cur:
            cur.execute(self._q("SELECT key, value FROM {s}.preferences WHERE tenant_id = %s AND user_id = %s"),
                        (owner.tenant_id, owner.user_id))
            return {r["key"]: r["value"] for r in cur.fetchall()}

    def set_preference(self, owner: Owner, key: str, value: Any) -> None:
        with self._transaction(owner) as cur:
            cur.execute(self._q("INSERT INTO {s}.preferences (tenant_id, user_id, key, value, updated_at) VALUES (%s, %s, %s, %s, %s) "
                                "ON CONFLICT (tenant_id, user_id, key) DO UPDATE SET value = EXCLUDED.value, "
                                "updated_at = EXCLUDED.updated_at"),
                        (owner.tenant_id, owner.user_id, key, Jsonb(value), utc_now()))

    def clear_preference(self, owner: Owner, key: str) -> None:
        with self._transaction(owner) as cur:
            cur.execute(self._q("DELETE FROM {s}.preferences WHERE tenant_id = %s AND user_id = %s AND key = %s"),
                        (owner.tenant_id, owner.user_id, key))

    # --------------------------------------------------------------------------------------------------- items
    def add_item(self, owner: Owner, item: LearnedItem) -> LearnedItem:
        with self._transaction(owner) as cur:
            cur.execute(self._q(
                "INSERT INTO {s}.learned_items (item_id, tenant_id, user_id, scope, kind, statement, question, details, embedding, "
                "source_feedback_id, approved_by, status, use_count, last_used_at, created_at, revoked_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"),
                (item.item_id, owner.tenant_id, item.user_id, item.scope, item.kind, item.statement, item.question,
                 Jsonb(item.details), list(item.embedding) if item.embedding else None, item.source_feedback_id,
                 item.approved_by, item.status, item.use_count, item.last_used_at, item.created_at, item.revoked_at))
        return item

    def items(self, owner: Owner, include_revoked: bool = False) -> list[LearnedItem]:
        with self._transaction(owner) as cur:
            cur.execute(self._q("SELECT * FROM {s}.learned_items WHERE tenant_id = %s AND (user_id = %s OR scope = 'shared') "
                                + ("" if include_revoked else "AND status = 'active' ") + "ORDER BY created_at DESC"),
                        (owner.tenant_id, owner.user_id))
            return [row_to_item(r) for r in cur.fetchall()]

    def revoke_item(self, owner: Owner, item_id: str) -> LearnedItem:
        with self._transaction(owner) as cur:
            cur.execute(self._q("UPDATE {s}.learned_items SET status = 'revoked', revoked_at = %s WHERE item_id = %s "
                                "AND tenant_id = %s AND (user_id = %s OR scope = 'shared') RETURNING *"),
                        (utc_now(), _uuid(item_id), owner.tenant_id, owner.user_id))
            row = cur.fetchone()
        if row is None:
            raise LearningItemNotFound("item not found")
        return row_to_item(row)

    def record_uses(self, owner: Owner, request_id: str, item_ids: list[str]) -> None:
        now = utc_now()
        with self._transaction(owner) as cur:
            for item_id in item_ids:
                cur.execute(self._q("INSERT INTO {s}.learning_uses (request_id, item_id, tenant_id, user_id, created_at) "
                                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"),
                            (request_id, _uuid(item_id), owner.tenant_id, owner.user_id, now))
                if cur.rowcount:
                    cur.execute(self._q("UPDATE {s}.learned_items SET use_count = use_count + 1, last_used_at = %s "
                                        "WHERE item_id = %s"), (now, item_id))

    def used_items(self, owner: Owner, request_id: str) -> list[LearnedItem]:
        with self._transaction(owner) as cur:
            cur.execute(self._q("SELECT i.* FROM {s}.learning_uses u JOIN {s}.learned_items i USING (item_id) "
                                "WHERE u.request_id = %s AND u.tenant_id = %s AND u.user_id = %s ORDER BY i.kind"),
                        (request_id, owner.tenant_id, owner.user_id))
            return [row_to_item(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------------------------------------- reviews
    def add_review(self, owner: Owner, review: Review) -> Review:
        with self._transaction(owner) as cur:
            cur.execute(self._q(
                "INSERT INTO {s}.reviews (review_id, tenant_id, submitted_by, kind, payload, source_feedback_id, status, "
                "reviewer, note, expected_terms, created_at, decided_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"),
                (review.review_id, owner.tenant_id, review.submitted_by, review.kind, Jsonb(review.payload),
                 review.source_feedback_id, review.status, review.reviewer, review.note, list(review.expected_terms),
                 review.created_at, review.decided_at))
        return review

    def reviews(self, owner: Owner, status: ReviewStatus | None = None) -> list[Review]:
        with self._transaction(owner) as cur:
            cur.execute(self._q("SELECT * FROM {s}.reviews WHERE tenant_id = %s AND (%s::text IS NULL OR status = %s) "
                                "ORDER BY created_at"), (owner.tenant_id, status, status))
            return [row_to_review(r) for r in cur.fetchall()]

    def decide_review(self, owner: Owner, review_id: str, status: ReviewStatus, note: str | None,
                      expected_terms: tuple[str, ...] = ()) -> Review:
        with self._transaction(owner) as cur:
            cur.execute(self._q("UPDATE {s}.reviews SET status = %s, reviewer = %s, note = %s, expected_terms = %s, decided_at = %s "
                                "WHERE review_id = %s AND tenant_id = %s RETURNING *"),
                        (status, owner.user_id, note, list(expected_terms), utc_now(), _uuid(review_id), owner.tenant_id))
            row = cur.fetchone()
        if row is None:
            raise LearningItemNotFound("review not found")
        return row_to_review(row)


    # ---------------------------------------------------------------------------------------------- internals
    @contextmanager
    def _transaction(self, owner: Owner) -> Iterator[psycopg.Cursor]:
        """One transaction with the trusted owner set, so forced row-level security limits it to that owner's rows."""
        with self._connect() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('wbg.tenant_id', %s, true), set_config('wbg.user_id', %s, true)",
                        (owner.tenant_id, owner.user_id))
            yield cur

    def _q(self, template: str) -> sql.Composed:
        return sql.SQL(template).format(s=self._schema)


def read_all(connect: Connect, schema: str = LEARNING_SCHEMA) -> dict[str, list[dict[str, Any]]]:
    """Every row of every user, for the operator report and exports only (read-only, `operator_read` policy)."""
    name = sql.Identifier(checked_schema(schema))
    with connect() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SELECT set_config('wbg.operator', 'report', true)")
        tables = {}
        for table in ("feedback", "learned_items", "learning_uses", "reviews"):
            cur.execute(sql.SQL("SELECT * FROM {}.{}").format(name, sql.Identifier(table)))
            tables[table] = cur.fetchall()
        return tables


# --------------------------------------------------------------------------------------------- row mapping
def _uuid(value: str) -> str:
    if not _is_uuid(value):
        raise LearningItemNotFound("not found")
    return value


def row_to_feedback(row: dict[str, Any]) -> Feedback:
    return Feedback(**_without(row, "tenant_id", "user_id") | {
        "feedback_id": str(row["feedback_id"]), "thread_id": str(row["thread_id"]), "reasons": tuple(row["reasons"])})


def row_to_item(row: dict[str, Any]) -> LearnedItem:
    return LearnedItem(**_without(row, "tenant_id") | {
        "item_id": str(row["item_id"]), "embedding": tuple(row["embedding"]) if row["embedding"] else None,
        "source_feedback_id": str(row["source_feedback_id"]) if row["source_feedback_id"] else None})


def row_to_review(row: dict[str, Any]) -> Review:
    return Review(**_without(row, "tenant_id") | {
        "review_id": str(row["review_id"]), "expected_terms": tuple(row["expected_terms"]),
        "source_feedback_id": str(row["source_feedback_id"]) if row["source_feedback_id"] else None})


def _without(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in keys}
