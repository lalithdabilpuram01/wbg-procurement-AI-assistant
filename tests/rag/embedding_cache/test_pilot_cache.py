"""Pilot tests against the real legacy caches and Step 6A rows (read-only; skipped when absent). No model is loaded."""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pytest

from rag.embedding_cache import variants
from rag.embedding_cache.identity import local_model_config
from rag.embedding_cache.legacy import LegacyCacheError, import_legacy, legacy_path, read_legacy_matrix
from rag.embedding_cache.store import MissingEmbeddingsError, UnitEmbeddingStore, text_sha256

LEGACY = variants.core.CACHE
pytestmark = pytest.mark.skipif(not (LEGACY.is_dir() and variants.P1_ROWS.is_file()), reason="pilot caches not present")

STACKED_WINDOWS = {"24440102:t02201:w1-2", "24440102:t10401:w1-2", "24440102:t10401:w2-3", "24440102:t10801:w2-4",
                   "24440102:t10801:w6-8", "24440102:t10801:w8-10", "24440102:t10901:w2-4", "24440102:t10901:w4-6",
                   "24440102:t10901:w6-8"}
RECOVERED_WINDOWS = {"33761744:t00501:w1-2", "40089196:t00501:w1-2"}


def verified(call):
    """Run a legacy-cache read; skip (not fail) when this environment cannot prove where the cache came from.

    The provenance check compares the cache files with the installed libraries, so a rebuilt virtual environment
    makes the historical caches unverifiable even though the store code is unchanged.
    """
    try:
        return call()
    except LegacyCacheError as error:
        pytest.skip(f"legacy cache provenance not verifiable in this environment: {error}")


@lru_cache(maxsize=None)
def corpus(scope: str, variant: str):
    return variants.corpus(scope, variant)


@lru_cache(maxsize=None)
def store_for(scope: str) -> UnitEmbeddingStore:
    store = UnitEmbeddingStore(local_model_config())
    verified(lambda: import_legacy(store, LEGACY, variants.SCOPES[scope][0], scope, corpus(scope, "pilot_v2").units))
    return store


def changed(scope: str, variant: str) -> set[str]:
    base = {u.unit_id: text_sha256(u.text) for u in corpus(scope, "pilot_v2").units}
    return {u.unit_id for u in corpus(scope, variant).units if base[u.unit_id] != text_sha256(u.text)}


@pytest.mark.parametrize("scope", ["A+tables", "A-tables", "C+tables"])
def test_store_reassembles_each_legacy_index_bit_for_bit(scope):
    units = corpus(scope, "pilot_v2").units
    legacy = verified(lambda: read_legacy_matrix(legacy_path(LEGACY, variants.SCOPES[scope][0], units), len(units),
                                                 local_model_config()))
    assert store_for(scope).assemble(scope, units).tobytes() == legacy.tobytes()


@pytest.mark.parametrize("scope", ["A+tables", "B+tables", "C+tables"])
@pytest.mark.parametrize("variant, expected", [("p1_stacked", STACKED_WINDOWS), ("p1_full", STACKED_WINDOWS | RECOVERED_WINDOWS)])
def test_step6a_changes_exactly_the_affected_table_windows(scope, variant, expected):
    assert changed(scope, variant) == expected


@pytest.mark.parametrize("scope", ["A+tables", "C+tables"])
def test_only_changed_units_miss_and_hits_are_the_original_vectors(scope):
    units = corpus(scope, "p1_full").units
    store = store_for(scope)
    plan = store.plan(scope, units)
    assert set(plan.misses) == changed(scope, "p1_full")
    base = {u.unit_id: i for i, u in enumerate(corpus(scope, "pilot_v2").units)}
    legacy = store.assemble(scope, corpus(scope, "pilot_v2").units)
    assert all(np.array_equal(store.get(scope, u), legacy[base[u.unit_id]]) for u in units if u.unit_id in set(plan.hits))
    with pytest.raises(MissingEmbeddingsError):
        store.assemble(scope, units)


def test_tables_off_scope_is_unaffected():
    assert changed("A-tables", "p1_full") == set()


@pytest.mark.parametrize("scope", ["A+tables", "B+tables", "C+tables"])
def test_unit_order_and_citation_metadata_stay_aligned(scope):
    fields = ("unit_id", "unit_type", "document_id", "project_id", "page_start", "page_end", "section", "table_id", "parent_id")
    base, new = corpus(scope, "pilot_v2").units, corpus(scope, "p1_full").units
    assert [tuple(getattr(u, f) for f in fields) + (u.meta,) for u in new] == \
           [tuple(getattr(u, f) for f in fields) + (u.meta,) for u in base]


def test_strategy_b_has_no_legacy_index():
    units = corpus("B+tables", "pilot_v2").units
    assert not legacy_path(LEGACY, "B", units).is_file()


def test_recovered_values_are_left_out_of_the_approved_stacked_only_variant():
    text = next(u.text for u in corpus("C+tables", "p1_stacked").units if u.unit_id == "40089196:t00501:w1-2")
    original = next(u.text for u in corpus("C+tables", "pilot_v2").units if u.unit_id == "40089196:t00501:w1-2")
    assert text == original
