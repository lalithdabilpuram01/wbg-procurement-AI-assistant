"""Live Lakebase pgvector checks. Run only on explicit request:

    LAKEBASE_LIVE_TESTS=1 uv run --locked pytest tests_live -q -p no:cacheprovider

Uses the isolated schema wbg_rag_poc with synthetic data; every operation is idempotent.
"""
from __future__ import annotations

import pytest

from lakebase_poc import pgvector_store as store
from lakebase_poc import synthetic
from lakebase_poc.connection import connect
from lakebase_poc.settings import live_tests_enabled, load_settings

pytestmark = pytest.mark.skipif(not live_tests_enabled(), reason="live Lakebase tests need LAKEBASE_LIVE_TESTS=1")


@pytest.fixture(scope="module")
def conn():
    with connect(load_settings()) as connection:
        store.enable_vector(connection)
        store.create_table(connection)
        store.insert_items(connection, synthetic.items())
        store.create_hnsw_index(connection)
        store.analyze(connection)
        yield connection


def test_standard_pgvector_is_installed_and_lakebase_vector_is_not(conn):
    info = store.server_info(conn)
    assert info.vector_installed is not None and info.lakebase_vector_installed is None


def test_rows_are_complete_and_retries_insert_nothing(conn):
    assert store.insert_items(conn, synthetic.items()) == 0
    assert store.row_count(conn) == synthetic.ITEM_COUNT


def test_exact_cosine_ranking_matches_numpy(conn):
    items = synthetic.items()
    for source, query in synthetic.queries(items):
        reference = synthetic.cosine_distance_ranking(items, query, 10)
        result = store.nearest(conn, query, 10)
        assert [i for i, _ in result] == [i for i, _ in reference] and result[0][0] == source
        assert max(abs(a - b) for (_, a), (_, b) in zip(result, reference)) < 1e-5


def test_hnsw_index_is_valid_and_usable(conn):
    catalog = store.index_catalog(conn)
    assert catalog and catalog["access_method"] == "hnsw" and catalog["operator_class"] == "vector_cosine_ops"
    assert catalog["valid"] and catalog["ready"]
    plan = store.plan_nodes(conn, synthetic.queries(synthetic.items())[0][1], 10, force_index=True)
    assert any(store.INDEX in node for node in plan)
