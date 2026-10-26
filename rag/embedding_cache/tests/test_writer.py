"""Incremental writer (Step 6D preparation): synthetic vectors only, temporary stores only. No model, no network."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
from dataclasses import replace
from functools import lru_cache

import numpy as np
import pytest

from rag.embedding_cache import adapter, store as store_module, variants
from rag.embedding_cache.identity import local_model_config
from rag.embedding_cache.store import CacheIntegrityError, UnitEmbeddingStore, file_sha256, recover, text_sha256
from rag.embedding_cache.tests.test_store import CONFIG, DIM, U, vec
from rag.embedding_cache.writer import (WriteRejectedError, _manifest_id, apply, dry_run, embed_missing, verify_manifest,
                                        write_manifest)

SCOPE = "C+tables"
BASE = [U(f"doc:t001:w{i}", f"[PAD P1 ET] table row {i} | 340.00") for i in range(3)]
NEW = [U("doc:t001:w3", "[PAD P1 ET] table row 3 | 18.00"), U("doc:t001:w4", "[PAD P1 ET] table row 4 | 12.00")]
CHANGED = U("doc:t001:w1", "[PAD P1 ET] table row 1 | 340.00\n18.00")


@pytest.fixture
def store_dir(tmp_path):
    store = UnitEmbeddingStore(CONFIG)
    for i, unit in enumerate(BASE):
        store.put(SCOPE, unit, vec(i), f"test#{i}")
    store.save(tmp_path / "store")
    return tmp_path / "store"


def corpus_units():
    return [BASE[0], CHANGED, BASE[2], *NEW]


def synthetic(manifest, seed=100):
    return [(g["unit_id"], vec(seed + i)) for i, g in enumerate(manifest["to_generate"])]


def leftovers(directory):
    return sorted(p.name for p in directory.parent.iterdir() if p.name.startswith(f".{directory.name}.") and not p.name.endswith(".lock"))


# ------------------------------------------------------------------------------------------------- dry run
def test_dry_run_lists_exactly_the_changed_and_new_units_without_text(store_dir):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    assert [(g["position"], g["unit_id"], g["reason"]) for g in manifest["to_generate"]] == [
        (1, "doc:t001:w1", "indexed text changed"), (3, "doc:t001:w3", "new unit"), (4, "doc:t001:w4", "new unit")]
    assert manifest["to_generate"][0]["previous_text_sha256"] == text_sha256(BASE[1].text)
    assert manifest["units_reused"] == 2 and verify_manifest(manifest)
    assert not any(u.text in json.dumps(manifest) or "340.00" in json.dumps(manifest) for u in corpus_units())


def test_manifest_is_written_atomically_and_tamper_evident(store_dir, tmp_path):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    write_manifest(manifest, tmp_path / "m.json")
    assert json.loads((tmp_path / "m.json").read_text()) == manifest and not list(tmp_path.glob(".m.json.tmp-*"))
    tampered = {**manifest, "to_generate": manifest["to_generate"][:1]}
    with pytest.raises(WriteRejectedError, match="identity hash"):
        verify_manifest(tampered)


# ------------------------------------------------------------------------------------------------- apply
def test_apply_adds_the_vectors_atomically_and_keeps_existing_vectors_exact(store_dir):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    report = apply(store_dir, CONFIG, manifest, synthetic(manifest), "unit-test")
    assert report["status"] == "added" and report["added"] == 3 and leftovers(store_dir) == []
    loaded = UnitEmbeddingStore.load(store_dir, CONFIG)
    matrix = loaded.assemble(SCOPE, corpus_units())
    assert np.array_equal(matrix[0], vec(0)) and np.array_equal(matrix[2], vec(2))          # unchanged units: original vectors
    assert [np.array_equal(matrix[i], vec(100 + k)) for k, i in enumerate((1, 3, 4))] == [True] * 3
    assert np.array_equal(loaded.assemble(SCOPE, BASE), np.stack([vec(0), vec(1), vec(2)]))  # old text still served exactly
    assert all(e["provenance"].startswith("generated:unit-test:manifest:") for e in loaded.entries() if e["unit_id"] in
               {"doc:t001:w3", "doc:t001:w4"})


def test_rerun_with_the_same_vectors_is_a_no_op(store_dir):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    apply(store_dir, CONFIG, manifest, synthetic(manifest), "unit-test")
    before = file_sha256(store_dir / "manifest.json")
    assert apply(store_dir, CONFIG, manifest, synthetic(manifest), "unit-test")["status"] == "no_op"
    assert file_sha256(store_dir / "manifest.json") == before


def test_conflicting_vector_for_an_existing_entry_is_rejected(store_dir):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    apply(store_dir, CONFIG, manifest, synthetic(manifest), "unit-test")
    before = file_sha256(store_dir / "manifest.json")
    with pytest.raises(WriteRejectedError, match="conflicting"):
        apply(store_dir, CONFIG, manifest, synthetic(manifest, seed=500), "unit-test")
    assert file_sha256(store_dir / "manifest.json") == before


@pytest.mark.parametrize("edit", [
    lambda pairs: pairs[:-1],                                            # a manifest unit missing
    lambda pairs: pairs + [("doc:t001:w9", vec(9))],                     # a unit not in the manifest
    lambda pairs: pairs + [pairs[0]],                                    # duplicated unit
])
def test_only_exactly_the_manifest_units_may_be_written(store_dir, edit):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    before = file_sha256(store_dir / "manifest.json")
    with pytest.raises(WriteRejectedError):
        apply(store_dir, CONFIG, manifest, edit(synthetic(manifest)), "unit-test")
    assert file_sha256(store_dir / "manifest.json") == before


@pytest.mark.parametrize("bad", [np.ones(DIM, dtype=np.float32), vec(1, DIM + 1), vec(1).astype(np.float64),
                                 np.full(DIM, np.nan, dtype=np.float32)])
def test_invalid_vectors_reject_the_whole_batch(store_dir, bad):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    pairs = synthetic(manifest)
    pairs[1] = (pairs[1][0], bad)
    before = file_sha256(store_dir / "manifest.json")
    with pytest.raises(CacheIntegrityError):
        apply(store_dir, CONFIG, manifest, pairs, "unit-test")
    assert file_sha256(store_dir / "manifest.json") == before
    assert UnitEmbeddingStore.load(store_dir, CONFIG).plan(SCOPE, NEW).misses == [u.unit_id for u in NEW]


def test_another_configuration_is_rejected(store_dir):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    with pytest.raises(WriteRejectedError, match="another embedding configuration"):
        apply(store_dir, replace(CONFIG, model_revision="0" * 40), manifest, synthetic(manifest), "unit-test")
    with pytest.raises(CacheIntegrityError):
        dry_run(store_dir, replace(CONFIG, normalize=False), SCOPE, corpus_units(), "unit-test")


def test_stale_manifest_is_rejected(store_dir):
    first = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    other = dry_run(store_dir, CONFIG, SCOPE, [*BASE, U("doc:t002:w1", "[ISR P2 LK] other table")], "unit-test")
    apply(store_dir, CONFIG, other, synthetic(other), "unit-test")        # the store moves on (no overlap)
    with pytest.raises(WriteRejectedError, match="stale"):
        apply(store_dir, CONFIG, first, synthetic(first, seed=300), "unit-test")


def test_overlapping_manifest_with_a_different_vector_is_rejected(store_dir):
    first = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    other = dry_run(store_dir, CONFIG, SCOPE, [*BASE, NEW[0]], "unit-test")
    apply(store_dir, CONFIG, other, synthetic(other), "unit-test")
    before = file_sha256(store_dir / "manifest.json")
    with pytest.raises(WriteRejectedError, match="stale"):
        apply(store_dir, CONFIG, first, synthetic(first, seed=300), "unit-test")
    assert file_sha256(store_dir / "manifest.json") == before


def test_strategy_b_is_never_written(store_dir):
    with pytest.raises(WriteRejectedError, match="not enabled"):
        dry_run(store_dir, CONFIG, "B+tables", corpus_units(), "unit-test")
    body = {k: v for k, v in dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test").items() if k != "manifest_id"}
    body["scope"] = "B+tables"
    forged = {"manifest_id": _manifest_id(body), **body}
    with pytest.raises(WriteRejectedError, match="not enabled"):
        apply(store_dir, CONFIG, forged, synthetic(forged), "unit-test")


def test_corrupted_store_is_never_extended(store_dir):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    data = bytearray((store_dir / "vectors.npy").read_bytes())
    data[-1] ^= 0xFF
    (store_dir / "vectors.npy").write_bytes(bytes(data))
    with pytest.raises(CacheIntegrityError):
        apply(store_dir, CONFIG, manifest, synthetic(manifest), "unit-test")


@pytest.mark.parametrize("label", ["contains spaces", "x" * 65, "", "name@example.org"])
def test_free_text_labels_are_refused(store_dir, label):
    with pytest.raises(WriteRejectedError, match="label"):
        dry_run(store_dir, CONFIG, SCOPE, corpus_units(), label)


def test_default_store_needs_explicit_permission():
    manifest = {"schema": "x"}
    with pytest.raises(WriteRejectedError, match="default store"):
        apply(adapter.DEFAULT_STORE, CONFIG, manifest, [], "unit-test")


def test_a_second_concurrent_writer_is_refused(store_dir):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    with (store_dir.parent / f".{store_dir.name}.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        with pytest.raises(WriteRejectedError, match="lock"):
            apply(store_dir, CONFIG, manifest, synthetic(manifest), "unit-test")


# ------------------------------------------------------------------------------------------- interrupted writes
def test_failure_while_writing_the_new_copy_leaves_the_store_unchanged(store_dir, monkeypatch):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    before = file_sha256(store_dir / "manifest.json")
    monkeypatch.setattr(store_module.np, "save", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        apply(store_dir, CONFIG, manifest, synthetic(manifest), "unit-test")
    monkeypatch.undo()
    assert file_sha256(store_dir / "manifest.json") == before and leftovers(store_dir) == []


def _replace_failing_on(calls_to_fail: set[int], exc: BaseException, monkeypatch):
    real, count = os.replace, {"n": 0}

    def fake(src, dst):
        count["n"] += 1
        if count["n"] in calls_to_fail:
            raise exc
        return real(src, dst)

    monkeypatch.setattr(store_module.os, "replace", fake)


def test_error_between_the_two_renames_is_rolled_back_immediately(store_dir, monkeypatch):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    before = file_sha256(store_dir / "manifest.json")
    _replace_failing_on({2}, OSError("rename failed"), monkeypatch)
    with pytest.raises(OSError):
        apply(store_dir, CONFIG, manifest, synthetic(manifest), "unit-test")
    monkeypatch.undo()
    assert file_sha256(store_dir / "manifest.json") == before and leftovers(store_dir) == []


def test_crash_between_the_two_renames_is_recovered(store_dir, monkeypatch):
    """A hard crash (no rollback) leaves no store: loading fails closed, and recover() restores the last commit."""
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    before = file_sha256(store_dir / "manifest.json")
    _replace_failing_on({2, 3}, KeyboardInterrupt(), monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        apply(store_dir, CONFIG, manifest, synthetic(manifest), "unit-test")
    monkeypatch.undo()
    assert not store_dir.exists()
    with pytest.raises(CacheIntegrityError):
        UnitEmbeddingStore.load(store_dir, CONFIG)
    actions = recover(store_dir)
    assert actions and actions[0].startswith("restored") and leftovers(store_dir) == []
    assert file_sha256(store_dir / "manifest.json") == before
    assert apply(store_dir, CONFIG, manifest, synthetic(manifest), "unit-test")["status"] == "added"   # rerun succeeds


def test_partial_files_never_load_as_a_store(store_dir):
    partial = store_dir.parent / f".{store_dir.name}.tmp-crashed"
    partial.mkdir()
    shutil.copy(store_dir / "vectors.npy", partial / "vectors.npy")
    assert UnitEmbeddingStore.load(store_dir, CONFIG)                    # the committed store is unaffected
    with pytest.raises(CacheIntegrityError):
        UnitEmbeddingStore.load(partial, CONFIG)
    assert recover(store_dir) == [f"removed {partial.name}"] and leftovers(store_dir) == []


# ------------------------------------------------------------------------------------------------ embed path
class SyntheticEncoder:
    def __init__(self, config):
        self.config = config

    def encode(self, texts):
        return np.stack([vec(int(text_sha256(t)[:8], 16) % 10_000) for t in texts])


def test_embed_missing_with_a_matching_encoder(store_dir):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    assert embed_missing(store_dir, CONFIG, manifest, corpus_units(), SyntheticEncoder(CONFIG), "unit-test")["added"] == 3
    assert UnitEmbeddingStore.load(store_dir, CONFIG).plan(SCOPE, corpus_units()).complete


def test_embed_missing_refuses_another_encoder_configuration_or_changed_text(store_dir):
    manifest = dry_run(store_dir, CONFIG, SCOPE, corpus_units(), "unit-test")
    with pytest.raises(WriteRejectedError, match="configurations differ"):
        embed_missing(store_dir, CONFIG, manifest, corpus_units(), SyntheticEncoder(replace(CONFIG, batch_size=32)), "unit-test")
    edited = [U(u.unit_id, u.text + " edited") if u.unit_id == "doc:t001:w3" else u for u in corpus_units()]
    with pytest.raises(WriteRejectedError, match="no longer matches"):
        embed_missing(store_dir, CONFIG, manifest, edited, SyntheticEncoder(CONFIG), "unit-test")


# ------------------------------------------------------------------------------------------- pilot (copy only)
pilot = pytest.mark.skipif(not (adapter.DEFAULT_STORE.is_dir() and variants.P1_ROWS.is_file()), reason="pilot store not present")


@lru_cache(maxsize=None)
def pilot_corpus(scope, variant):
    return variants.corpus(scope, variant)


@pilot
def test_pilot_copy_strategy_c_stacked_fix_needs_exactly_nine_vectors_and_reuses_the_rest(tmp_path):
    original = file_sha256(adapter.DEFAULT_STORE / "manifest.json")
    copy = tmp_path / "store"
    shutil.copytree(adapter.DEFAULT_STORE, copy)
    config = local_model_config()
    units = pilot_corpus(SCOPE, "p1_stacked").units
    manifest = dry_run(copy, config, SCOPE, units, "pilot-synthetic")
    assert manifest["units_to_generate"] == 9 and manifest["units_reused"] == 2387
    assert all(g["unit_id"].startswith("24440102:t") and g["reason"] == "indexed text changed" for g in manifest["to_generate"])
    rng = np.random.default_rng(7)
    fake = [(g["unit_id"], (v := rng.normal(size=768).astype(np.float32)) / np.linalg.norm(v)) for g in manifest["to_generate"]]
    apply(copy, config, manifest, fake, "pilot-synthetic")
    matrix = adapter.unit_cache_embeddings(pilot_corpus(SCOPE, "p1_stacked"), store_dir=copy)
    legacy = adapter.unit_cache_embeddings(pilot_corpus(SCOPE, "pilot_v2"), store_dir=copy)
    changed = {g["position"] for g in manifest["to_generate"]}
    assert all(np.array_equal(matrix[i], legacy[i]) for i in range(len(units)) if i not in changed)
    assert all(np.array_equal(matrix[g["position"]], vec_) for g, (_, vec_) in zip(manifest["to_generate"], fake))
    assert [u.unit_id for u in units] == [u.unit_id for u in pilot_corpus(SCOPE, "pilot_v2").units]
    assert file_sha256(adapter.DEFAULT_STORE / "manifest.json") == original                 # the real store untouched
