"""PostgreSQL conversation store (schema `conversation` in wbg_structured_registry; see sql/schema.sql).

Runs as the least-privilege `wbg_conversation_app` role. Each transaction first sets the trusted owner
(`wbg.tenant_id`, `wbg.user_id`), so row-level security limits it to that owner's rows; every query also filters by
owner explicitly. The DSN comes from WBG_CONVERSATION_DSN (no password in code).
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from conversation.models import (ActiveContext, Decision, Owner, StructuredSummary, SummaryRecord, ThreadInfo, Turn,
                                 TurnMetadata, json_safe, new_id, utc_now)
from conversation.store import ThreadNotFound

DEFAULT_DSN = "postgresql://wbg_conversation_app@localhost:5432/wbg_structured_registry"
DEFAULT_ADMIN_DSN = "postgresql://wbg_conversation_admin@localhost:5432/wbg_structured_registry"
DEFAULT_SCHEMA = "conversation"
SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def conversation_dsn() -> str:
    return os.environ.get("WBG_CONVERSATION_DSN", DEFAULT_DSN)


def checked_schema(schema: str) -> str:
    if not SCHEMA_NAME.match(schema):
        raise ValueError("invalid schema name")
    return schema


class PostgresConversationStore:
    """ConversationStore on PostgreSQL; every method opens its own short transaction as the trusted owner."""

    def __init__(self, dsn: str | None = None, schema: str = DEFAULT_SCHEMA):
        self._dsn = dsn or conversation_dsn()
        self._schema = sql.Identifier(checked_schema(schema))

    # ------------------------------------------------------------------------------------------------ threads
    def create_thread(self, owner: Owner, title: str) -> ThreadInfo:
        now = utc_now()
        thread = ThreadInfo(thread_id=new_id(), owner=owner, title=title[:200], created_at=now, updated_at=now)
        with self._transaction(owner) as cur:
            cur.execute(self._q("INSERT INTO {s}.threads (thread_id, tenant_id, user_id, title, topic_epoch, created_at, "
                                "updated_at) VALUES (%s, %s, %s, %s, 0, %s, %s)"),
                        (thread.thread_id, owner.tenant_id, owner.user_id, thread.title, now, now))
        return thread

    def get_thread(self, owner: Owner, thread_id: str) -> ThreadInfo:
        with self._transaction(owner) as cur:
            return self._owned(cur, owner, thread_id)

    def list_threads(self, owner: Owner, limit: int = 20) -> list[ThreadInfo]:
        """The owner's threads, most recently updated first."""
        with self._transaction(owner) as cur:
            cur.execute(self._q("SELECT * FROM {s}.threads WHERE tenant_id = %s AND user_id = %s "
                                "ORDER BY updated_at DESC LIMIT %s"), (owner.tenant_id, owner.user_id, limit))
            return [_thread(row, owner) for row in cur.fetchall()]

    def set_title(self, owner: Owner, thread_id: str, title: str) -> None:
        """Rename one of the owner's threads (title cut to 200 characters)."""
        with self._transaction(owner) as cur:
            self._owned(cur, owner, thread_id)
            cur.execute(self._q("UPDATE {s}.threads SET title = %s WHERE thread_id = %s AND tenant_id = %s AND user_id = %s"),
                        (title[:200], thread_id, owner.tenant_id, owner.user_id))

    def start_new_topic(self, owner: Owner, thread_id: str) -> ThreadInfo:
        """Begin a new topic in the owner's thread by raising its topic_epoch; returns the updated thread."""
        with self._transaction(owner) as cur:
            self._owned(cur, owner, thread_id, lock=True)
            cur.execute(self._q("UPDATE {s}.threads SET topic_epoch = topic_epoch + 1, updated_at = %s "
                                "WHERE thread_id = %s AND tenant_id = %s AND user_id = %s"),
                        (utc_now(), thread_id, owner.tenant_id, owner.user_id))
            return self._owned(cur, owner, thread_id)

    # -------------------------------------------------------------------------------------------------- turns
    def append_turn(self, owner: Owner, turn: Turn) -> Turn:
        with self._transaction(owner) as cur:
            self._owned(cur, owner, turn.thread_id, lock=True)
            cur.execute(self._q("INSERT INTO {s}.turns (turn_id, thread_id, seq, role, kind, text, topic_epoch, context, "
                                "metadata, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"),
                        (turn.turn_id, turn.thread_id, turn.seq, turn.role, turn.kind.value, turn.text, turn.topic_epoch,
                         Jsonb(json_safe(turn.context)), Jsonb(json_safe(turn.metadata)), turn.created_at))
            cur.execute(self._q("UPDATE {s}.threads SET updated_at = %s WHERE thread_id = %s"), (turn.created_at, turn.thread_id))
        return turn

    def turns(self, owner: Owner, thread_id: str, topic_epoch: int | None = None, after_seq: int = 0,
              limit: int | None = None) -> list[Turn]:
        """The thread's turns after `after_seq`, optionally of one topic and only the newest `limit`, oldest first."""
        with self._transaction(owner) as cur:
            self._owned(cur, owner, thread_id)
            # The inner query takes the newest `limit` turns; the outer one returns them oldest first.
            cur.execute(self._q("SELECT * FROM (SELECT * FROM {s}.turns WHERE thread_id = %s AND seq > %s "
                                "AND (%s::int IS NULL OR topic_epoch = %s::int) ORDER BY seq DESC LIMIT %s) recent "
                                "ORDER BY seq"), (thread_id, after_seq, topic_epoch, topic_epoch, limit))
            return [_turn(row) for row in cur.fetchall()]

    def next_seq(self, owner: Owner, thread_id: str) -> int:
        """The next turn sequence number for the owner's thread (1 for an empty thread)."""
        with self._transaction(owner) as cur:
            self._owned(cur, owner, thread_id)
            cur.execute(self._q("SELECT coalesce(max(seq), 0) + 1 AS next FROM {s}.turns WHERE thread_id = %s"), (thread_id,))
            return cur.fetchone()["next"]

    # ---------------------------------------------------------------------------------------------- decisions
    def add_decision(self, owner: Owner, decision: Decision) -> Decision:
        with self._transaction(owner) as cur:
            self._owned(cur, owner, decision.thread_id)
            cur.execute(self._q("INSERT INTO {s}.decisions (decision_id, thread_id, kind, statement, approved_by, "
                                "source_turn_seq, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"),
                        (decision.decision_id, decision.thread_id, decision.kind, decision.statement, decision.approved_by,
                         decision.source_turn_seq, decision.created_at))
        return decision

    def revoke_decision(self, owner: Owner, thread_id: str, decision_id: str, reason: str) -> Decision:
        """Revoke one decision in the owner's thread and return it; KeyError if the thread has no such decision."""
        with self._transaction(owner) as cur:
            self._owned(cur, owner, thread_id)
            # coalesce keeps the first revocation time and reason, so revoking twice changes nothing.
            cur.execute(self._q("UPDATE {s}.decisions SET revoked_at = coalesce(revoked_at, %s), "
                                "revoked_reason = coalesce(revoked_reason, %s) WHERE thread_id = %s AND decision_id = %s "
                                "RETURNING *"), (utc_now(), reason, thread_id, decision_id))
            row = cur.fetchone()
        if row is None:
            raise KeyError("no such decision in this thread")
        return _decision(row)

    def decisions(self, owner: Owner, thread_id: str, include_revoked: bool = False) -> list[Decision]:
        """The thread's decisions, oldest first; revoked ones only when asked."""
        with self._transaction(owner) as cur:
            self._owned(cur, owner, thread_id)
            cur.execute(self._q("SELECT * FROM {s}.decisions WHERE thread_id = %s AND (%s OR revoked_at IS NULL) "
                                "ORDER BY created_at"), (thread_id, include_revoked))
            return [_decision(row) for row in cur.fetchall()]

    # ---------------------------------------------------------------------------------------------- summaries
    def add_summary(self, owner: Owner, summary: SummaryRecord) -> SummaryRecord:
        with self._transaction(owner) as cur:
            self._owned(cur, owner, summary.thread_id, lock=True)
            cur.execute(self._q("INSERT INTO {s}.summaries (summary_id, thread_id, version, topic_epoch, covers_from_seq, "
                                "covers_through_seq, content, generator, inputs_sha256, registry_build_id, created_at, "
                                "invalidated_at, invalidation_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"),
                        (summary.summary_id, summary.thread_id, summary.version, summary.topic_epoch, summary.covers_from_seq,
                         summary.covers_through_seq, Jsonb(json_safe(summary.content)), summary.generator,
                         summary.inputs_sha256, summary.registry_build_id, summary.created_at, summary.invalidated_at,
                         summary.invalidation_reason))
        return summary

    def latest_summary(self, owner: Owner, thread_id: str) -> SummaryRecord | None:
        """The thread's highest-version summary (valid or not), or None when there is none."""
        with self._transaction(owner) as cur:
            self._owned(cur, owner, thread_id)
            cur.execute(self._q("SELECT * FROM {s}.summaries WHERE thread_id = %s ORDER BY version DESC LIMIT 1"), (thread_id,))
            row = cur.fetchone()
        return _summary(row) if row else None

    def invalidate_summary(self, owner: Owner, thread_id: str, summary_id: str, reason: str) -> None:
        """Mark one summary of the owner's thread invalid with a reason; an already invalid one is left as it is."""
        with self._transaction(owner) as cur:
            self._owned(cur, owner, thread_id)
            cur.execute(self._q("UPDATE {s}.summaries SET invalidated_at = %s, invalidation_reason = %s "
                                "WHERE thread_id = %s AND summary_id = %s AND invalidated_at IS NULL"),
                        (utc_now(), reason, thread_id, summary_id))

    # ---------------------------------------------------------------------------------------------- internals
    @contextmanager
    def _transaction(self, owner: Owner) -> Iterator[psycopg.Cursor]:
        """A cursor in one transaction with the trusted owner set for row-level security (local to this transaction)."""
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('wbg.tenant_id', %s, true), set_config('wbg.user_id', %s, true)",
                        (owner.tenant_id, owner.user_id))
            yield cur

    def _owned(self, cur: psycopg.Cursor, owner: Owner, thread_id: str, lock: bool = False) -> ThreadInfo:
        """The owner's thread, or ThreadNotFound for a malformed id, a missing thread or another owner's thread.
        `lock` holds the thread row (FOR UPDATE) so concurrent writes to one thread wait for each other."""
        if not _is_uuid(thread_id):
            raise ThreadNotFound("thread not found")
        query = "SELECT * FROM {s}.threads WHERE thread_id = %s AND tenant_id = %s AND user_id = %s" + (" FOR UPDATE" if lock else "")
        cur.execute(self._q(query), (thread_id, owner.tenant_id, owner.user_id))
        row = cur.fetchone()
        if row is None:
            raise ThreadNotFound("thread not found")
        return _thread(row, owner)

    def _q(self, template: str) -> sql.Composed:
        return sql.SQL(template).format(s=self._schema)


# --------------------------------------------------------------------------------------------- row mapping
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _is_uuid(value: str) -> bool:
    return bool(UUID.match(value))


def _thread(row: dict[str, Any], owner: Owner) -> ThreadInfo:
    return ThreadInfo(thread_id=str(row["thread_id"]), owner=owner, title=row["title"], created_at=row["created_at"],
                      updated_at=row["updated_at"], topic_epoch=row["topic_epoch"])


def _turn(row: dict[str, Any]) -> Turn:
    """Turn from a database row, with its context and metadata JSON validated back into models."""
    return Turn(turn_id=str(row["turn_id"]), thread_id=str(row["thread_id"]), seq=row["seq"], role=row["role"],
                kind=row["kind"], text=row["text"], topic_epoch=row["topic_epoch"],
                context=ActiveContext.model_validate(row["context"]), metadata=TurnMetadata.model_validate(row["metadata"]),
                created_at=row["created_at"])


def _decision(row: dict[str, Any]) -> Decision:
    return Decision(decision_id=str(row["decision_id"]), thread_id=str(row["thread_id"]), kind=row["kind"],
                    statement=row["statement"], approved_by=row["approved_by"], source_turn_seq=row["source_turn_seq"],
                    created_at=row["created_at"], revoked_at=row["revoked_at"], revoked_reason=row["revoked_reason"])


def _summary(row: dict[str, Any]) -> SummaryRecord:
    """SummaryRecord from a database row, with its JSON content validated as a StructuredSummary."""
    return SummaryRecord(summary_id=str(row["summary_id"]), thread_id=str(row["thread_id"]), version=row["version"],
                         topic_epoch=row["topic_epoch"], covers_from_seq=row["covers_from_seq"],
                         covers_through_seq=row["covers_through_seq"], content=StructuredSummary.model_validate(row["content"]),
                         generator=row["generator"], inputs_sha256=row["inputs_sha256"],
                         registry_build_id=row["registry_build_id"], created_at=row["created_at"],
                         invalidated_at=row["invalidated_at"], invalidation_reason=row["invalidation_reason"])
