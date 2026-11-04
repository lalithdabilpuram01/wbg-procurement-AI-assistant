"""Where lesson cards and their review decisions live: in memory (tests) or the `lesson_card` table of the Knowledge
Graph schema on the Lakebase development branch. `upsert` never changes a decision; `decide` is the only way to."""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import ContextManager, Protocol

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .cards import LessonCard

SCHEMA_SQL = Path(__file__).with_name("sql") / "lesson_schema.sql"
STATUSES = ("proposed", "approved", "rejected")
COLUMNS = ("card_id", "project_id", "country", "sector", "document_id", "doc_type", "section", "section_id", "page_start",
           "page_end", "headline", "body", "topics", "stages", "categories", "origin")
Connect = Callable[[], ContextManager[psycopg.Connection]]


class LessonNotFound(LookupError):
    pass


class LessonStore(Protocol):
    """Storage of lesson cards and their review decisions."""

    def upsert(self, cards: Iterable[LessonCard]) -> int: ...
    def cards(self, status: str | None = None, limit: int = 500) -> list[LessonCard]: ...
    def decide(self, card_id: str, approved: bool, reviewer: str, note: str | None = None) -> LessonCard: ...
    def counts(self) -> dict[str, int]: ...


class InMemoryLessonStore:
    """Dictionary-backed LessonStore for tests."""

    def __init__(self) -> None:
        self._cards: dict[str, LessonCard] = {}

    def upsert(self, cards: Iterable[LessonCard]) -> int:
        """Insert or refresh cards and return how many were written; a known card keeps its review decision."""
        n = 0
        for card in cards:
            old = self._cards.get(card.card_id)
            # A known card keeps its review status, reviewer and note; only its text and tags are refreshed.
            self._cards[card.card_id] = replace(card, status=old.status, decided_by=old.decided_by, note=old.note) if old else card
            n += 1
        return n

    def cards(self, status: str | None = None, limit: int = 500) -> list[LessonCard]:
        return [c for c in self._cards.values() if status in (None, c.status)][:limit]

    def decide(self, card_id: str, approved: bool, reviewer: str, note: str | None = None) -> LessonCard:
        """Approve or reject one card and record the reviewer and note; LessonNotFound if it is unknown."""
        if card_id not in self._cards:
            raise LessonNotFound(card_id)
        card = replace(self._cards[card_id], status="approved" if approved else "rejected", decided_by=reviewer, note=note)
        self._cards[card_id] = card
        return card

    def counts(self) -> dict[str, int]:
        return {s: sum(c.status == s for c in self._cards.values()) for s in STATUSES}


class PostgresLessonStore:
    """LessonStore on the `lesson_card` table; every call takes its own connection from `connect`."""

    def __init__(self, connect: Connect, schema: str = "wbg_kg"):
        self._connect, self._schema = connect, schema

    def apply_schema(self) -> None:
        with self._connect() as conn, conn.transaction():
            conn.execute(apply_sql(self._schema), prepare=False)

    def upsert(self, cards: Iterable[LessonCard]) -> int:
        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            return upsert(cur, self._schema, cards)

    def cards(self, status: str | None = None, limit: int = 500) -> list[LessonCard]:
        """Cards with this status (all when None): IEG reviews first, then completion reports, then the rest."""
        query = self._q("SELECT * FROM {s}.lesson_card WHERE (%s::text IS NULL OR status = %s) "
                        "ORDER BY CASE doc_type WHEN 'ICRR' THEN 0 WHEN 'ICR' THEN 1 ELSE 2 END, project_id, card_id LIMIT %s")
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            return [_card(r) for r in cur.execute(query, (status, status, limit)).fetchall()]

    def decide(self, card_id: str, approved: bool, reviewer: str, note: str | None = None) -> LessonCard:
        """Record a reviewer's approval or rejection; this is the only way a card's status changes."""
        query = self._q("UPDATE {s}.lesson_card SET status = %s, decided_by = %s, decided_at = %s, note = %s, updated_at = %s "
                        "WHERE card_id = %s RETURNING *")
        now = dt.datetime.now(dt.timezone.utc)
        with self._connect() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(query, ("approved" if approved else "rejected", reviewer, now, note, now, card_id)).fetchone()
        if row is None:
            raise LessonNotFound(card_id)
        return _card(row)

    def counts(self) -> dict[str, int]:
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(self._q("SELECT status, count(*) AS n FROM {s}.lesson_card GROUP BY status")).fetchall()
        return {s: next((r["n"] for r in rows if r["status"] == s), 0) for s in STATUSES}

    def _q(self, template: str) -> sql.Composed:
        return sql.SQL(template).format(s=sql.Identifier(self._schema))


def apply_sql(schema: str) -> str:
    return SCHEMA_SQL.read_text().replace("{schema}", schema)


def upsert(cur: psycopg.Cursor, schema: str, cards: Iterable[LessonCard]) -> int:
    """Insert new cards and refresh known ones (text, tags, pages), keeping every review decision. Used inside the
    ingestion transaction too, so a document and its cards land together."""
    # Status, reviewer and note are not in COLUMNS, so refreshing a known card never touches its decision.
    statement = sql.SQL("INSERT INTO {s}.lesson_card ({cols}) VALUES ({vals}) ON CONFLICT (card_id) DO UPDATE SET {sets}, "
                        "updated_at = now()").format(
        s=sql.Identifier(schema), cols=sql.SQL(", ").join(map(sql.Identifier, COLUMNS)),
        vals=sql.SQL(", ").join(sql.Placeholder() * len(COLUMNS)),
        sets=sql.SQL(", ").join(sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in COLUMNS[1:]))
    rows = [(c.card_id, c.project_id, c.country, c.sector, c.document_id, c.doc_type, c.section, c.section_id, c.page_start,
             c.page_end, c.headline, c.text, list(c.topics), list(c.stages), list(c.categories), c.origin) for c in cards]
    if rows:
        cur.executemany(statement, rows)
    return len(rows)


def _card(row: dict) -> LessonCard:
    """Map a lesson_card row back to a LessonCard (the card text is stored in the `body` column)."""
    return LessonCard(card_id=row["card_id"], project_id=row["project_id"], country=row["country"], sector=row["sector"],
                      document_id=row["document_id"], doc_type=row["doc_type"], section=row["section"], page_start=row["page_start"],
                      page_end=row["page_end"], headline=row["headline"], text=row["body"], topics=tuple(row["topics"]),
                      stages=tuple(row["stages"]), categories=tuple(row["categories"]), status=row["status"],
                      decided_by=row["decided_by"], note=row["note"], origin=row["origin"], section_id=row["section_id"])
