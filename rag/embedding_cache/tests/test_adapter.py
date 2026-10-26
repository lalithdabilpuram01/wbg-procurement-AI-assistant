"""Opt-in adapter in rag/retrieval/core.py (Step 6C). Offline: the query encoder is stubbed, no model is loaded."""
from __future__ import annotations

import json
import shutil
from dataclasses import replace
from functools import lru_cache

import numpy as np
import pytest

from rag.embedding_cache import adapter, variants
from rag.embedding_cache.identity import local_model_config
from rag.embedding_cache.legacy import legacy_path
from rag.embedding_cache.store import CacheIntegrityError, MissingEmbeddingsError, file_sha256

core = variants.core
pytestmark = pytest.mark.skipif(not (adapter.DEFAULT_STORE.is_dir() and adapter.DEFAULT_ATTESTATION.is_file()),
                                reason="experimental store or compatibility attestation not present")
QUERIES = ["What is the project development objective?", "Signed contract date for the road works",
           "Key operational units under the toll road enterprise", "Project cost and financing US$ million"]


@pytest.fixture(autouse=True)
def default_environment(monkeypatch):
    for name in (core.EMBEDDING_CACHE_ENV, adapter.STORE_ENV, adapter.ATTESTATION_ENV):
        monkeypatch.delenv(name, raising=False)


@lru_cache(maxsize=None)
def corpus(scope: str, variant: str = "pilot_v2"):
    return variants.corpus(scope, variant)


def legacy_matrix(scope: str) -> np.ndarray:
    units = corpus(scope).units
    return np.load(legacy_path(core.CACHE, variants.SCOPES[scope][0], units), allow_pickle=False)


def legacy_fingerprints() -> dict[str, str]:
    return {p.name: file_sha256(p) for p in sorted(core.CACHE.glob("*.npy"))}


class StubEncoder:
    """Stands in for the query encoder: returns a fixed, normalised query vector per query."""

    def __init__(self, vectors: dict[str, np.ndarray]):
        self.vectors = vectors

    def encode(self, texts, normalize_embeddings=True, **_):
        return np.stack([self.vectors[t] for t in texts])


def query_vectors(scope: str) -> dict[str, np.ndarray]:
    matrix = legacy_matrix(scope)
    return {q: matrix[(17 * i + 3) % len(matrix)] for i, q in enumerate(QUERIES)}


# ------------------------------------------------------------------------------------------------- default
def test_default_mode_is_the_existing_whole_index_cache():
    retriever = core.Retriever(corpus("C+tables"))
    assert retriever.embedding_cache == "legacy"
    assert retriever.embeddings().tobytes() == legacy_matrix("C+tables").tobytes()


def test_unknown_mode_is_rejected(monkeypatch):
    with pytest.raises(ValueError):
        core.Retriever(corpus("C+tables"), embedding_cache="units")
    monkeypatch.setenv(core.EMBEDDING_CACHE_ENV, "per-unit")
    with pytest.raises(ValueError):
        core.Retriever(corpus("C+tables"))


# ------------------------------------------------------------------------------------------------- opt-in
@pytest.mark.parametrize("scope", ["A+tables", "A-tables", "C+tables"])
def test_opt_in_reconstructs_the_legacy_matrix_exactly(scope):
    unit, legacy = core.Retriever(corpus(scope), embedding_cache="unit").embeddings(), legacy_matrix(scope)
    assert unit.shape == legacy.shape and unit.dtype == legacy.dtype and unit.tobytes() == legacy.tobytes()


def test_opt_in_through_the_environment_setting(monkeypatch):
    monkeypatch.setenv(core.EMBEDDING_CACHE_ENV, "unit")
    retriever = core.Retriever(corpus("C+tables"))
    assert retriever.embedding_cache == "unit"
    assert retriever.embeddings().tobytes() == legacy_matrix("C+tables").tobytes()


@pytest.mark.parametrize("method", ["dense", "hybrid", "bm25"])
@pytest.mark.parametrize("scope", ["A+tables", "C+tables"])
def test_rankings_and_citations_are_identical_in_both_modes(scope, method):
    results = {}
    for mode in ("legacy", "unit"):
        retriever = core.Retriever(corpus(scope), embedding_cache=mode)
        retriever._model = StubEncoder(query_vectors(scope))
        results[mode] = [retriever.search(q, method=method, k=10, guarded=True) for q in QUERIES]
    strip = lambda rs: [[{k: v for k, v in r.items() if k != "latency_ms"} for r in res["results"]] for res in rs]  # noqa: E731
    assert strip(results["legacy"]) == strip(results["unit"])


# ------------------------------------------------------------------------------------------------- failures
def test_strategy_b_is_not_served():
    with pytest.raises(adapter.UnitCacheNotAllowedError, match="B\\+tables"):
        core.Retriever(corpus("B+tables"), embedding_cache="unit").embeddings()


def test_changed_indexed_text_fails_with_the_missing_unit_ids():
    before = legacy_fingerprints()
    with pytest.raises(MissingEmbeddingsError) as error:
        core.Retriever(corpus("C+tables", "p1_stacked"), embedding_cache="unit").embeddings()
    assert len(error.value.unit_ids) == 9 and all(u.startswith("24440102:t") for u in error.value.unit_ids)
    assert legacy_fingerprints() == before                               # no fallback encode, no file written


def test_a_failed_unit_lookup_does_not_fall_back_on_retry():
    retriever = core.Retriever(corpus("C+tables", "p1_stacked"), embedding_cache="unit")
    for _ in range(2):
        with pytest.raises(MissingEmbeddingsError):
            retriever.embeddings()


def _attestation(tmp_path, **changes):
    record = {**json.loads(adapter.DEFAULT_ATTESTATION.read_text()), **changes}
    path = tmp_path / "attestation.json"
    path.write_text(json.dumps(record))
    return path


@pytest.mark.parametrize("changes", [{"verdict": "not_established"}, {"config_fingerprint": "0" * 64}, {"legacy_files": {}}])
def test_without_a_matching_attestation_the_cache_is_not_used(tmp_path, changes):
    with pytest.raises(adapter.UnitCacheNotAllowedError):
        adapter.unit_cache_embeddings(corpus("C+tables"), attestation=_attestation(tmp_path, **changes))


def test_missing_attestation_is_refused(tmp_path):
    with pytest.raises(adapter.UnitCacheNotAllowedError):
        adapter.unit_cache_embeddings(corpus("C+tables"), attestation=tmp_path / "absent.json")


def test_another_model_configuration_is_rejected_even_with_its_own_attestation(tmp_path):
    other = replace(local_model_config(), model_revision="0" * 40)
    attestation = _attestation(tmp_path, config_fingerprint=other.fingerprint())
    with pytest.raises(CacheIntegrityError):
        adapter.unit_cache_embeddings(corpus("C+tables"), attestation=attestation, config=other)


def test_corrupted_or_missing_store_is_rejected(tmp_path):
    damaged = tmp_path / "store"
    shutil.copytree(adapter.DEFAULT_STORE, damaged)
    data = bytearray((damaged / "vectors.npy").read_bytes())
    data[-1] ^= 0xFF
    (damaged / "vectors.npy").write_bytes(bytes(data))
    with pytest.raises(CacheIntegrityError):
        adapter.unit_cache_embeddings(corpus("C+tables"), store_dir=damaged)
    (damaged / "vectors.npy").unlink()
    with pytest.raises((CacheIntegrityError, FileNotFoundError)):
        adapter.unit_cache_embeddings(corpus("C+tables"), store_dir=damaged)


def test_store_directory_from_the_environment_is_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv(adapter.STORE_ENV, str(tmp_path / "nowhere"))
    with pytest.raises((CacheIntegrityError, FileNotFoundError)):
        core.Retriever(corpus("C+tables"), embedding_cache="unit").embeddings()


def test_legacy_whole_index_files_are_untouched_by_both_modes():
    before = legacy_fingerprints()
    for mode in ("legacy", "unit"):
        core.Retriever(corpus("A+tables"), embedding_cache=mode).embeddings()
    assert legacy_fingerprints() == before
