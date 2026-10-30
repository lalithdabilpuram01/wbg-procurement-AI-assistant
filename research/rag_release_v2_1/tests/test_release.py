"""Release checkpoint for pilot_v2_1 + the staged per-unit store (read-only; no model is loaded)."""
from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pytest

from rag.embedding_cache import adapter, variants
from rag.embedding_cache.identity import local_model_config
from rag.embedding_cache.legacy import legacy_path, read_legacy_matrix
from rag.embedding_cache.store import UnitEmbeddingStore, file_sha256
from rag.release_v2_1.stage_embeddings import RESULTS, STAGING

pytestmark = pytest.mark.skipif(not (STAGING.is_dir() and (RESULTS / "evaluation.json").is_file()), reason="release not staged")
CHANGED = {"24440102:t02201:w1-2", "24440102:t10401:w1-2", "24440102:t10401:w2-3", "24440102:t10801:w2-4", "24440102:t10801:w6-8",
           "24440102:t10801:w8-10", "24440102:t10901:w2-4", "24440102:t10901:w4-6", "24440102:t10901:w6-8"}


@lru_cache(maxsize=None)
def corpus(scope, variant):
    return variants.corpus(scope, variant)


@lru_cache(maxsize=None)
def staging_store():
    return UnitEmbeddingStore.load(STAGING, local_model_config())


def test_staging_store_holds_exactly_the_18_generated_vectors():
    generated = [e for e in staging_store().entries() if e["provenance"].startswith("generated:")]
    assert len(generated) == 18 and {e["unit_id"] for e in generated} == CHANGED
    assert {e["scope"] for e in generated} == {"A+tables", "C+tables"}


@pytest.mark.parametrize("scope", ["C+tables", "A+tables", "A-tables"])
def test_new_index_reuses_every_unchanged_legacy_vector_byte_for_byte(scope):
    base = corpus(scope, "pilot_v2").units
    legacy = read_legacy_matrix(legacy_path(variants.core.CACHE, variants.SCOPES[scope][0], base), len(base), local_model_config())
    new_units = corpus(scope, "pilot_v2_1").units
    matrix = staging_store().assemble(scope, new_units)
    assert [u.unit_id for u in new_units] == [u.unit_id for u in base]
    for i, unit in enumerate(new_units):
        assert (matrix[i].tobytes() == legacy[i].tobytes()) is (unit.unit_id not in CHANGED)
    assert np.isfinite(matrix).all() and np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=1e-4)


@pytest.mark.parametrize("scope", ["C+tables", "A+tables"])
def test_rollback_pilot_v2_still_reassembles_from_the_staging_store(scope):
    base = corpus(scope, "pilot_v2").units
    legacy = read_legacy_matrix(legacy_path(variants.core.CACHE, variants.SCOPES[scope][0], base), len(base), local_model_config())
    assert staging_store().assemble(scope, base).tobytes() == legacy.tobytes()


def test_the_default_store_was_not_written():
    store = UnitEmbeddingStore.load(adapter.DEFAULT_STORE, local_model_config())
    assert len(store) == 5363 and not any(e["provenance"].startswith("generated:") for e in store.entries())
    assert file_sha256(adapter.DEFAULT_STORE / "manifest.json").startswith("d224651c")


def test_strategy_b_is_not_served_from_staging():
    with pytest.raises(adapter.UnitCacheNotAllowedError):
        adapter.unit_cache_embeddings(corpus("B+tables", "pilot_v2_1"), store_dir=STAGING)


def test_recorded_acceptance_results():
    evaluation = json.loads((RESULTS / "evaluation.json").read_text())
    assert evaluation["reference_reproduces_baseline"] == {"per_question": True, "metrics": True}
    assert evaluation["regressions"] == [] and evaluation["citations"]["newly_failed"] == []
    assert evaluation["guard_statuses_identical"] and evaluation["network_attempts_blocked"] == 0
    assert all(not v["not_found"] for v in evaluation["supplementary"]["changed_unit_values_on_cited_pages"].values())
    assert all(r == {"email": 0, "phone": 0} for r in evaluation["supplementary"]["residual_risk"].values())
    staging = json.loads((RESULTS / "staging.json").read_text())
    assert staging["legacy_files_unchanged"] and staging["source_store_unchanged"] and staging["network_attempts_blocked"] == 0
    assert all(s["idempotent_rerun"] in ("no_op", "nothing_to_generate") for s in staging["scopes"].values())
