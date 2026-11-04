"""Per-user workspace: folders that group conversations, and bookmarks on answers.

Owner-scoped like the conversation store: every operation takes the trusted `Owner`, and thread ownership is checked
by the caller (the API) before a thread is filed or bookmarked. The PostgreSQL store adds row-level security
(sql/schema.sql: folders, thread_folders, bookmarks).
"""
from __future__ import annotations

import datetime as dt
import threading
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from conversation.models import Owner, new_id, utc_now
from conversation.postgres_store import PostgresConversationStore, _is_uuid


class WorkspaceItemNotFound(KeyError):
    """The folder or bookmark does not exist for this owner."""


# A named folder that groups one owner's conversations.
class Folder(BaseModel):
    model_config = ConfigDict(frozen=True)
    folder_id: str
    name: str = Field(min_length=1, max_length=80)
    created_at: dt.datetime


# A bookmark on one assistant answer (thread and turn), with an optional short note.
class Bookmark(BaseModel):
    model_config = ConfigDict(frozen=True)
    bookmark_id: str
    thread_id: str
    turn_seq: int = Field(gt=0)
    note: str = Field(default="", max_length=200)
    created_at: dt.datetime


class WorkspaceStore(Protocol):
    """Owner-scoped storage of folders, thread filings and bookmarks (in-memory or PostgreSQL)."""
    def list_folders(self, owner: Owner) -> list[Folder]: ...
    def create_folder(self, owner: Owner, name: str) -> Folder: ...
    def rename_folder(self, owner: Owner, folder_id: str, name: str) -> Folder: ...
    def delete_folder(self, owner: Owner, folder_id: str) -> None: ...
    def file_thread(self, owner: Owner, thread_id: str, folder_id: str | None) -> None: ...
    def thread_folders(self, owner: Owner) -> dict[str, str]: ...
    def list_bookmarks(self, owner: Owner) -> list[Bookmark]: ...
    def add_bookmark(self, owner: Owner, thread_id: str, turn_seq: int, note: str = "") -> Bookmark: ...
    def delete_bookmark(self, owner: Owner, bookmark_id: str) -> None: ...


class InMemoryWorkspaceStore:
    """Dictionary-backed WorkspaceStore for tests and local runs."""

    def __init__(self) -> None:
        """Empty dictionaries; every record is kept with its owner so other owners cannot see it."""
        self._lock = threading.Lock()
        self._folders: dict[str, tuple[Owner, Folder]] = {}
        self._filed: dict[str, tuple[Owner, str]] = {}                      # thread_id -> (owner, folder_id)
        self._bookmarks: dict[str, tuple[Owner, Bookmark]] = {}

    def list_folders(self, owner: Owner) -> list[Folder]:
        return sorted((f for o, f in self._folders.values() if o == owner), key=lambda f: f.name.lower())

    def create_folder(self, owner: Owner, name: str) -> Folder:
        """Create a folder for the owner (name trimmed) and return it."""
        folder = Folder(folder_id=new_id(), name=name.strip(), created_at=utc_now())
        with self._lock:
            self._folders[folder.folder_id] = (owner, folder)
        return folder

    def rename_folder(self, owner: Owner, folder_id: str, name: str) -> Folder:
        """Rename one of the owner's folders; WorkspaceItemNotFound if it is not theirs."""
        with self._lock:
            folder = Folder(**{**self._folder(owner, folder_id).model_dump(), "name": name.strip()})
            self._folders[folder_id] = (owner, folder)
            return folder

    def delete_folder(self, owner: Owner, folder_id: str) -> None:
        """Delete one of the owner's folders; conversations filed in it stay, unfiled."""
        with self._lock:
            self._folder(owner, folder_id)
            del self._folders[folder_id]
            self._filed = {t: v for t, v in self._filed.items() if v[1] != folder_id}   # conversations stay, unfiled

    def file_thread(self, owner: Owner, thread_id: str, folder_id: str | None) -> None:
        """File a thread in one of the owner's folders, or unfile it when folder_id is None."""
        with self._lock:
            if folder_id is None:
                self._filed.pop(thread_id, None)
                return
            self._folder(owner, folder_id)
            self._filed[thread_id] = (owner, folder_id)

    def thread_folders(self, owner: Owner) -> dict[str, str]:
        return {t: f for t, (o, f) in self._filed.items() if o == owner}

    def list_bookmarks(self, owner: Owner) -> list[Bookmark]:
        return sorted((b for o, b in self._bookmarks.values() if o == owner), key=lambda b: b.created_at, reverse=True)

    def add_bookmark(self, owner: Owner, thread_id: str, turn_seq: int, note: str = "") -> Bookmark:
        """Bookmark one answer for the owner; bookmarking the same answer again returns the first bookmark."""
        with self._lock:
            existing = next((b for o, b in self._bookmarks.values()
                             if o == owner and (b.thread_id, b.turn_seq) == (thread_id, turn_seq)), None)
            if existing:
                return existing                                                   # bookmarking twice is a no-op
            bookmark = Bookmark(bookmark_id=new_id(), thread_id=thread_id, turn_seq=turn_seq, note=note.strip(),
                                created_at=utc_now())
            self._bookmarks[bookmark.bookmark_id] = (owner, bookmark)
            return bookmark

    def delete_bookmark(self, owner: Owner, bookmark_id: str) -> None:
        """Delete one of the owner's bookmarks; WorkspaceItemNotFound if it is not theirs."""
        with self._lock:
            entry = self._bookmarks.get(bookmark_id)
            if entry is None or entry[0] != owner:
                raise WorkspaceItemNotFound("bookmark not found")
            del self._bookmarks[bookmark_id]

    def _folder(self, owner: Owner, folder_id: str) -> Folder:
        """The owner's folder, or WorkspaceItemNotFound for a missing folder or another owner's folder."""
        entry = self._folders.get(folder_id)
        if entry is None or entry[0] != owner:
            raise WorkspaceItemNotFound("folder not found")
        return entry[1]


class PostgresWorkspaceStore(PostgresConversationStore):
    """Same database, schema and least-privilege role as the conversation store."""

    def list_folders(self, owner: Owner) -> list[Folder]:
        """The owner's folders, sorted by name (ignoring case)."""
        with self._transaction(owner) as cur:
            cur.execute(self._q("SELECT folder_id, name, created_at FROM {s}.folders WHERE tenant_id = %s AND user_id = %s "
                                "ORDER BY lower(name)"), (owner.tenant_id, owner.user_id))
            return [Folder(folder_id=str(r["folder_id"]), name=r["name"], created_at=r["created_at"]) for r in cur.fetchall()]

    def create_folder(self, owner: Owner, name: str) -> Folder:
        """Create a folder for the owner (name trimmed) and return it."""
        folder = Folder(folder_id=new_id(), name=name.strip(), created_at=utc_now())
        with self._transaction(owner) as cur:
            cur.execute(self._q("INSERT INTO {s}.folders (folder_id, tenant_id, user_id, name, created_at) VALUES (%s, %s, %s, %s, %s)"),
                        (folder.folder_id, owner.tenant_id, owner.user_id, folder.name, folder.created_at))
        return folder

    def rename_folder(self, owner: Owner, folder_id: str, name: str) -> Folder:
        """Rename one of the owner's folders (trimmed, at most 80 characters); WorkspaceItemNotFound if not theirs."""
        with self._transaction(owner) as cur:
            cur.execute(self._q("UPDATE {s}.folders SET name = %s WHERE folder_id = %s AND tenant_id = %s AND user_id = %s "
                                "RETURNING folder_id, name, created_at"),
                        (name.strip()[:80], self._uuid(folder_id), owner.tenant_id, owner.user_id))
            row = cur.fetchone()
        if row is None:
            raise WorkspaceItemNotFound("folder not found")
        return Folder(folder_id=str(row["folder_id"]), name=row["name"], created_at=row["created_at"])

    def delete_folder(self, owner: Owner, folder_id: str) -> None:
        """Delete one of the owner's folders; its filings go with it, the conversations stay."""
        with self._transaction(owner) as cur:
            cur.execute(self._q("DELETE FROM {s}.folders WHERE folder_id = %s AND tenant_id = %s AND user_id = %s"),
                        (self._uuid(folder_id), owner.tenant_id, owner.user_id))       # filings cascade; threads stay
            if cur.rowcount == 0:
                raise WorkspaceItemNotFound("folder not found")

    def file_thread(self, owner: Owner, thread_id: str, folder_id: str | None) -> None:
        """File the owner's thread in one of their folders (moving it if filed), or unfile it when folder_id is None."""
        with self._transaction(owner) as cur:
            self._owned(cur, owner, thread_id)
            if folder_id is None:
                cur.execute(self._q("DELETE FROM {s}.thread_folders WHERE thread_id = %s"), (thread_id,))
                return
            cur.execute(self._q("SELECT 1 FROM {s}.folders WHERE folder_id = %s AND tenant_id = %s AND user_id = %s"),
                        (self._uuid(folder_id), owner.tenant_id, owner.user_id))
            if cur.fetchone() is None:
                raise WorkspaceItemNotFound("folder not found")
            cur.execute(self._q("INSERT INTO {s}.thread_folders (thread_id, folder_id) VALUES (%s, %s) "
                                "ON CONFLICT (thread_id) DO UPDATE SET folder_id = EXCLUDED.folder_id"), (thread_id, folder_id))

    def thread_folders(self, owner: Owner) -> dict[str, str]:
        """Thread id to folder id for every filed thread of the owner."""
        with self._transaction(owner) as cur:
            cur.execute(self._q("SELECT tf.thread_id, tf.folder_id FROM {s}.thread_folders tf JOIN {s}.folders f "
                                "USING (folder_id) WHERE f.tenant_id = %s AND f.user_id = %s"), (owner.tenant_id, owner.user_id))
            return {str(r["thread_id"]): str(r["folder_id"]) for r in cur.fetchall()}

    def list_bookmarks(self, owner: Owner) -> list[Bookmark]:
        """The owner's bookmarks, newest first (ownership comes from the bookmarked thread)."""
        with self._transaction(owner) as cur:
            cur.execute(self._q("SELECT b.* FROM {s}.bookmarks b JOIN {s}.threads t USING (thread_id) "
                                "WHERE t.tenant_id = %s AND t.user_id = %s ORDER BY b.created_at DESC"),
                        (owner.tenant_id, owner.user_id))
            return [_bookmark(r) for r in cur.fetchall()]

    def add_bookmark(self, owner: Owner, thread_id: str, turn_seq: int, note: str = "") -> Bookmark:
        """Bookmark one answer on the owner's thread (note cut to 200 characters) and return the bookmark."""
        with self._transaction(owner) as cur:
            self._owned(cur, owner, thread_id)
            # Bookmarking the same answer twice keeps the first bookmark; the select below returns it either way.
            cur.execute(self._q("INSERT INTO {s}.bookmarks (bookmark_id, thread_id, turn_seq, note, created_at) "
                                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (thread_id, turn_seq) DO NOTHING"),
                        (new_id(), thread_id, turn_seq, note.strip()[:200], utc_now()))
            cur.execute(self._q("SELECT * FROM {s}.bookmarks WHERE thread_id = %s AND turn_seq = %s"), (thread_id, turn_seq))
            return _bookmark(cur.fetchone())

    def delete_bookmark(self, owner: Owner, bookmark_id: str) -> None:
        """Delete a bookmark on one of the owner's threads; WorkspaceItemNotFound if there is none."""
        with self._transaction(owner) as cur:
            cur.execute(self._q("DELETE FROM {s}.bookmarks b USING {s}.threads t WHERE b.bookmark_id = %s "
                                "AND b.thread_id = t.thread_id AND t.tenant_id = %s AND t.user_id = %s"),
                        (self._uuid(bookmark_id), owner.tenant_id, owner.user_id))
            if cur.rowcount == 0:
                raise WorkspaceItemNotFound("bookmark not found")

    @staticmethod
    def _uuid(value: str) -> str:
        """Reject a malformed id as "not found" before it reaches PostgreSQL, which would fail on the uuid cast."""
        if not _is_uuid(value):
            raise WorkspaceItemNotFound("not found")
        return value


def _bookmark(row: dict) -> Bookmark:
    return Bookmark(bookmark_id=str(row["bookmark_id"]), thread_id=str(row["thread_id"]), turn_seq=row["turn_seq"],
                    note=row["note"], created_at=row["created_at"])
