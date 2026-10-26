"""Step 6E: opt-in RAG version selection (rag/retrieval/versions.py) and release binding. No model is loaded."""
from __future__ import annotations

import json
import shutil
from functools import lru_cache
from types import SimpleNamespace

import numpy as np
import pytest

from rag.embedding_cache import adapter
from rag.embedding_cache.store import CacheIntegrityError, file_sha256
from rag.retrieval import core, versions

RELEASE_DIR = adapter.RELEASES / "pilot_v2_1"
pytestmark = pytest.mark.skipif(not (RELEASE_DIR / "release.json").is_file(), reason="pilot_v2_1 release not installed")
CHANGED = {"24440102:t02201:w1-2", "24440102:t10401:w1-2", "24440102:t10401:w2-3", "24440102:t10801:w2-4", "24440102:t10801:w6-8",
           "24440102:t10801:w8-10", "24440102:t10901:w2-4", "24440102:t10901:w4-6", "24440102:t10901:w6-8"}


@pytest.fixture(autouse=True)
def default_environment(monkeypatch):
    for name in (core.EMBEDDING_CACHE_ENV, adapter.STORE_ENV, adapter.ATTESTATION_ENV):
        monkeypatch.delenv(name, raising=False)


@lru_cache(maxsize=None)
def legacy_c() -> np.ndarray:
    return np.load(next(core.CACHE.glob("emb_C_2396_*.npy")), allow_pickle=False)


@lru_cache(maxsize=None)
def improved_corpus():
    return versions.build_corpus("pilot_v2_1")


def index_cache_listing():
    return {p.name: file_sha256(p) for p in sorted(core.CACHE.iterdir())}


# ------------------------------------------------------------------------------------------------- defaults
def test_corpus_default_is_pilot_v2_and_unchanged():
    default, explicit = core.Corpus("C"), core.Corpus("C", processed_dir=core.PILOT)
    assert default.processed_dir == core.PILOT and default._base is None
    assert adapter.indexed_text_fingerprint(default.units) == adapter.indexed_text_fingerprint(explicit.units)


def test_default_version_is_the_existing_retriever():
    retriever = versions.build_retriever()
    assert versions.DEFAULT_VERSION == "pilot_v2" and retriever.embedding_cache == "legacy"
    assert retriever.c.processed_dir == core.PILOT and retriever.embeddings().tobytes() == legacy_c().tobytes()


def test_improved_version_serves_the_release_and_differs_only_in_the_corrected_units():
    retriever = versions.build_retriever("pilot_v2_1")
    assert retriever.c.processed_dir.name == "pilot_v2_1" and retriever.embedding_cache == "unit"
    matrix = retriever.embeddings()
    differing = {retriever.c.units[i].unit_id for i in range(len(matrix)) if matrix[i].tobytes() != legacy_c()[i].tobytes()}
    assert differing == CHANGED


def test_switching_back_is_immediate_and_leaves_no_state():
    versions.build_retriever("pilot_v2_1")
    retriever = versions.build_retriever()
    assert core.PILOT.name == "pilot_v2" and retriever.embeddings().tobytes() == legacy_c().tobytes()


# ------------------------------------------------------------------------------------------------- rejections
@pytest.mark.parametrize("call", [
    lambda: versions.build_retriever("pilot_v2_1", strategy="A"),
    lambda: versions.build_retriever("pilot_v2_1", strategy="B"),
    lambda: versions.build_retriever("pilot_v2", strategy="B"),
    lambda: versions.build_retriever("pilot_v2_1", include_tables=False),
    lambda: versions.build_retriever("pilot_v3"),
])
def test_versions_and_strategies_that_are_not_offered_are_rejected(call):
    with pytest.raises(versions.UnknownRagVersionError):
        call()


def test_improved_corpus_with_the_whole_index_cache_is_rejected_and_nothing_is_written():
    before = index_cache_listing()
    with pytest.raises(ValueError, match="whole-index cache serves only the default corpus"):
        core.Retriever(improved_corpus(), embedding_cache="legacy").embeddings()
    assert index_cache_listing() == before


def test_improved_corpus_with_the_default_store_is_rejected():
    with pytest.raises(adapter.ReleaseMismatchError, match="store_files_sha256"):
        adapter.unit_cache_embeddings(improved_corpus(), store_dir=adapter.DEFAULT_STORE)


def test_corpus_without_a_registered_release_is_rejected(tmp_path):
    corpus = SimpleNamespace(strategy="C", include_tables=True, processed_dir=tmp_path, units=improved_corpus().units)
    with pytest.raises(adapter.UnitCacheNotAllowedError, match="no release"):
        adapter.unit_cache_embeddings(corpus)


def test_changed_indexed_text_is_rejected_by_the_release_fingerprint():
    units = list(improved_corpus().units)
    units[0] = SimpleNamespace(unit_id=units[0].unit_id, text=units[0].text + " edited")
    corpus = SimpleNamespace(strategy="C", include_tables=True, processed_dir=improved_corpus().processed_dir, units=units)
    with pytest.raises(adapter.ReleaseMismatchError, match="indexed_text_fingerprint"):
        adapter.unit_cache_embeddings(corpus)


@pytest.fixture
def release_copy(tmp_path, monkeypatch):
    target = tmp_path / "releases" / "pilot_v2_1"
    shutil.copytree(RELEASE_DIR, target)
    monkeypatch.setattr(adapter, "RELEASES", tmp_path / "releases")
    return target


@pytest.mark.parametrize("edit, problem", [
    (lambda r: r["corpus"].update(run_manifest_sha256="0" * 64), "corpus_run_manifest_sha256"),
    (lambda r: r["corpus"].update(extraction_version="ingest-pilot-2.0.0"), "corpus_extraction_version"),
    (lambda r: r.update(enabled_scopes=[]), "scope_enabled_in_release"),
    (lambda r: r.update(config_fingerprint="0" * 64), "config_fingerprint"),
    (lambda r: r["store"].update({"vectors.npy": "0" * 64}), "store_files_sha256"),
])
def test_a_release_descriptor_that_does_not_match_is_rejected(release_copy, edit, problem):
    record = json.loads((release_copy / "release.json").read_text())
    edit(record)
    (release_copy / "release.json").write_text(json.dumps(record))
    with pytest.raises(adapter.ReleaseMismatchError, match=problem):
        adapter.unit_cache_embeddings(improved_corpus())


def test_a_corrupted_release_store_is_rejected(release_copy):
    data = bytearray((release_copy / "store" / "vectors.npy").read_bytes())
    data[-1] ^= 0xFF
    (release_copy / "store" / "vectors.npy").write_bytes(bytes(data))
    with pytest.raises((adapter.ReleaseMismatchError, CacheIntegrityError)):
        adapter.unit_cache_embeddings(improved_corpus())


def test_recorded_integration_validation():
    record = json.loads((adapter.HERE.parent / "release_v2_1" / "results" / "integration_validation.json").read_text())
    assert all(record["default"].values()) and all(record["pilot_v2_1"].values())
    assert record["guard_statuses_identical"] and record["citations"]["newly_failed"] == []
    assert record["switch_back"]["identical"] == record["switch_back"]["searches"] == 31
    assert record["residual_risk_C"] == {"email": 0, "phone": 0} and record["privacy"]["pilot_v2_1_equals_step6d_candidate_C"]
    assert record["legacy_files_unchanged"] and record["network_attempts_blocked"] == 0


def test_recorded_answer_level_comparison():
    """Evidence-only (no LLM) answers: only Q27 differs (its long corrected table window becomes a top-3 verbatim echo,
    which the unchanged validator rejects as an evidence dump in both versions); guarded outcomes are identical."""
    record = json.loads((adapter.HERE.parent / "release_v2_1" / "results" / "answer_comparison.json").read_text())
    assert set(record["differing_questions"]) <= {"Q27"} and record["guarded_outcomes_identical"]
    assert record["network_attempts_blocked"] == 0
    for version in ("pilot_v2", "pilot_v2_1"):
        assert all(c["page_check"] == "verified" for x in record["differing_questions"].get("Q27", {}).values() for c in x["citations"])
    plain = lambda v: [n for c in record["financing_table_claim_checks"][v] for n in c["unsupported_numbers"] if n < 1e6]  # noqa: E731
    assert plain("pilot_v2_1") == [] and plain("pilot_v2")          # the corrected values ground claims; glued values did not
