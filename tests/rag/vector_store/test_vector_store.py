"""Lakebase vector store, offline: SQL shape, release verification, credential cache, input checks (no connection).

    cd orchestrator && env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m pytest ../rag/vector_store/tests -q
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import numpy as np
import pytest

from rag.vector_store import index as store
from rag.vector_store import load
from rag.vector_store.lakebase import CachedToken

RECORD = store.ReleaseRecord("pilot_v3", "BAAI/bge-m3", "rev1", 1024, 1024, 3, "text-sha", "vec-sha", "rag/x")


class FakeConnection:
    def __init__(self, rows=(), release=RECORD):
        self.statements, self._rows, self._release = [], list(rows), release

    @contextmanager
    def transaction(self):
        yield

    def execute(self, statement, params=()):
        self.statements.append((statement, params))
        result = self._release if "FROM wbg_rag.releases" in statement else None
        return FakeResult([tuple(result.__dict__.values())] if result else self._rows)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


def test_scoped_search_filters_inside_the_iterative_index_scan_and_sorts_exactly():
    conn = FakeConnection(rows=[("u2", 0.7), ("u1", 0.9)])
    hits = store.nearest(conn, "pilot_v3", np.zeros(1024, dtype=np.float32), {"P2", "P1"}, limit=10)
    settings, (query, params) = conn.statements[0][0], conn.statements[1]
    assert "iterative_scan" in settings and "project_id = ANY(%s)" in query and params[2] == ["P1", "P2"]
    assert hits == [("u1", 0.9), ("u2", 0.7)]


def test_unscoped_search_has_no_project_filter():
    conn = FakeConnection()
    store.nearest(conn, "pilot_v3", np.zeros(1024, dtype=np.float32))
    assert "project_id" not in conn.statements[1][0]


def test_verify_accepts_the_matching_release():
    index = store.LakebaseVectorIndex(FakePool(FakeConnection()), "pilot_v3")
    assert index.verify("text-sha", 3, "BAAI/bge-m3", "rev1") == RECORD


@pytest.mark.parametrize("args, field", [(("other", 3, "BAAI/bge-m3", "rev1"), "indexed_text_sha256"),
                                         (("text-sha", 4, "BAAI/bge-m3", "rev1"), "units"),
                                         (("text-sha", 3, "BAAI/bge-m3", "rev2"), "model_revision")])
def test_verify_refuses_a_release_built_from_other_units_or_weights(args, field):
    index = store.LakebaseVectorIndex(FakePool(FakeConnection()), "pilot_v3")
    with pytest.raises(store.ReleaseMismatchError, match=field):
        index.verify(*args)


def test_verify_refuses_a_release_that_is_not_loaded():
    index = store.LakebaseVectorIndex(FakePool(FakeConnection(release=None)), "pilot_v3")
    with pytest.raises(store.ReleaseMismatchError, match="not loaded"):
        index.record()


def test_credentials_are_cached_and_refetched_after_the_reuse_window():
    now, fetched = [0.0], []
    token = CachedToken(lambda: fetched.append(1) or f"t{len(fetched)}", reuse_seconds=100, clock=lambda: now[0])
    assert token.get() == token.get() == "t1"
    now[0] = 100.0
    assert token.get() == "t2"


def test_load_refuses_vectors_encoded_for_another_corpus(tmp_path, monkeypatch):
    np.save(tmp_path / "bge-m3.npy", np.zeros((2, 1024), dtype=np.float32))
    (tmp_path / "bge-m3.json").write_text(json.dumps({"model": "BAAI/bge-m3", "indexed_text_sha256": "other",
                                                      "max_sequence_tokens": 1024}))
    monkeypatch.setattr(load, "EXPERIMENT", tmp_path)
    with pytest.raises(SystemExit, match="indexed_text_sha256"):
        load.release_inputs()


def test_writing_steps_need_explicit_confirmation():
    with pytest.raises(SystemExit):
        load.main(["load"])
