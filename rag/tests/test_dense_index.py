"""The retriever's opt-in external dense index (e.g. Lakebase pgvector), with a fake index (offline, no model)."""
from __future__ import annotations

import numpy as np
import pytest

from rag.retrieval import core, versions

QUESTION = "What procurement delays are reported for Ethiopia?"


class FakeIndex:
    def __init__(self, hits):
        self.hits, self.calls = hits, []

    def nearest(self, query, scope_projects=None, limit=300):
        self.calls.append((query, scope_projects))
        return [(u, s) for u, s in self.hits if not scope_projects or self.project[u] in scope_projects]


@pytest.fixture(scope="module")
def corpus() -> core.Corpus:
    return core.Corpus("C")


def retriever_with(corpus, hits) -> tuple[core.Retriever, FakeIndex]:
    index = FakeIndex(hits)
    index.project = {u.unit_id: u.project_id for u in corpus.units}
    return core.Retriever(corpus, dense_index=index, query_encoder=lambda q: np.ones(4, dtype=np.float32)), index


def test_dense_search_returns_only_the_index_neighbours_in_its_order(corpus):
    ids = [u.unit_id for u in corpus.units[:3]]
    retriever, _ = retriever_with(corpus, [(ids[2], 0.9), (ids[0], 0.5)])
    result = retriever.search("any question", method="dense", k=5, guarded=False)
    assert [r["unit_id"] for r in result["results"]] == [ids[2], ids[0]]


def test_the_guard_scope_is_passed_to_the_index(corpus):
    ethiopia = next(u for u in corpus.units if u.project_id == "P148850")
    retriever, index = retriever_with(corpus, [(ethiopia.unit_id, 0.8)])
    result = retriever.search(QUESTION, method="hybrid", k=5)
    assert index.calls[0][1] == {"P148850", "P163452"}
    assert all(r["project_id"] in {"P148850", "P163452"} for r in result["results"])


def test_an_index_needs_a_query_encoder(corpus):
    with pytest.raises(ValueError, match="together"):
        core.Retriever(corpus, dense_index=FakeIndex([]))


def test_pilot_v3_is_served_from_lakebase_and_the_local_store_is_evaluation_only():
    assert versions.get_version("pilot_v3").vector_store == "lakebase"
    assert versions.get_version("pilot_v3_gte_local").embedding_cache == "unit"
