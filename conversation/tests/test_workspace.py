"""Workspace folders and bookmarks: the same behaviour in memory and in PostgreSQL (temporary schema, row-level
security). The PostgreSQL variant is skipped when the local conversation roles are not available."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from conversation.init_store import apply_schema
from conversation.postgres_store import DEFAULT_ADMIN_DSN, DEFAULT_DSN, PostgresConversationStore
from conversation.store import InMemoryConversationStore
from conversation.tests.conftest import ALICE, BOB
from conversation.tests.test_postgres_store import roles_available
from conversation.workspace import InMemoryWorkspaceStore, PostgresWorkspaceStore, WorkspaceItemNotFound


@pytest.fixture(scope="module")
def pg_schema():
    if not roles_available():
        pytest.skip("conversation roles not bootstrapped")
    name = f"conversation_test_{uuid.uuid4().hex[:10]}"
    apply_schema(DEFAULT_ADMIN_DSN, name)
    apply_schema(DEFAULT_ADMIN_DSN, name)                    # re-applying is non-destructive
    yield name
    with psycopg.connect(DEFAULT_ADMIN_DSN) as conn:
        conn.execute(f"DROP SCHEMA {name} CASCADE")           # the temporary test schema only


@pytest.fixture(params=["memory", "postgres"])
def stores(request):
    if request.param == "memory":
        return InMemoryConversationStore(), InMemoryWorkspaceStore()
    schema = request.getfixturevalue("pg_schema")
    return PostgresConversationStore(DEFAULT_DSN, schema), PostgresWorkspaceStore(DEFAULT_DSN, schema)


def test_folders_can_be_created_renamed_listed_and_deleted(stores):
    _, workspace = stores
    folder = workspace.create_folder(ALICE, "Ethiopia roads")
    assert workspace.rename_folder(ALICE, folder.folder_id, "Ethiopia transport").name == "Ethiopia transport"
    assert [f.name for f in workspace.list_folders(ALICE)] == ["Ethiopia transport"]
    workspace.delete_folder(ALICE, folder.folder_id)
    assert workspace.list_folders(ALICE) == []


def test_deleting_a_folder_unfiles_its_conversations_but_keeps_them(stores):
    conversations, workspace = stores
    thread = conversations.create_thread(ALICE, "Expressway delays")
    folder = workspace.create_folder(ALICE, "Roads")
    workspace.file_thread(ALICE, thread.thread_id, folder.folder_id)
    assert workspace.thread_folders(ALICE) == {thread.thread_id: folder.folder_id}
    workspace.delete_folder(ALICE, folder.folder_id)
    assert workspace.thread_folders(ALICE) == {}
    assert conversations.get_thread(ALICE, thread.thread_id).title == "Expressway delays"


def test_bookmarking_an_answer_twice_keeps_one_bookmark(stores):
    conversations, workspace = stores
    thread = conversations.create_thread(ALICE, "Package assessment")
    first = workspace.add_bookmark(ALICE, thread.thread_id, 2, "186-day estimate")
    assert workspace.add_bookmark(ALICE, thread.thread_id, 2).bookmark_id == first.bookmark_id
    assert [b.note for b in workspace.list_bookmarks(ALICE)] == ["186-day estimate"]
    workspace.delete_bookmark(ALICE, first.bookmark_id)
    assert workspace.list_bookmarks(ALICE) == []


def test_another_user_sees_and_changes_nothing(stores):
    conversations, workspace = stores
    thread = conversations.create_thread(ALICE, "Private")
    folder = workspace.create_folder(ALICE, "Mine")
    workspace.file_thread(ALICE, thread.thread_id, folder.folder_id)
    bookmark = workspace.add_bookmark(ALICE, thread.thread_id, 2)
    assert workspace.list_folders(BOB) == [] and workspace.list_bookmarks(BOB) == [] and workspace.thread_folders(BOB) == {}
    for action in (lambda: workspace.rename_folder(BOB, folder.folder_id, "x"), lambda: workspace.delete_folder(BOB, folder.folder_id),
                   lambda: workspace.delete_bookmark(BOB, bookmark.bookmark_id)):
        with pytest.raises(WorkspaceItemNotFound):
            action()
    assert workspace.list_folders(ALICE) and workspace.list_bookmarks(ALICE)


def test_a_conversation_cannot_be_filed_into_someone_elses_folder(stores):
    conversations, workspace = stores
    folder = workspace.create_folder(ALICE, "Alice only")
    thread = conversations.create_thread(BOB, "Bob's")
    with pytest.raises(WorkspaceItemNotFound):
        workspace.file_thread(BOB, thread.thread_id, folder.folder_id)
