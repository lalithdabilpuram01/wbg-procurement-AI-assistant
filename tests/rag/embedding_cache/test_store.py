"""Synthetic tests of the per-unit store and legacy import (no model, no pilot data)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pytest

from rag.embedding_cache.identity import EmbeddingConfig
from rag.embedding_cache.legacy import LegacyCacheError, import_legacy, legacy_path
from rag.embedding_cache.store import (CacheIntegrityError, MissingEmbeddingsError, UnitEmbeddingStore, file_sha256,
                                       vector_sha256)

DIM = 8
CONFIG = EmbeddingConfig(model_id="Alibaba-NLP/gte-base-en-v1.5", model_revision="a" * 40, weights_sha256="b" * 64,
                         remote_code_revision="c" * 40, model_config_sha256="d" * 64, pooling="cls_token", max_seq_length=8192,
                         dimension=DIM, dtype="float32", normalize=True, prompt="", batch_size=16,
                         library_versions=(("sentence-transformers", "5.4.1"), ("transformers", "4.57.3"), ("torch", "2.9.1")))


@dataclass
class U:
    unit_id: str
    text: str


def vec(seed: int, dim: int = DIM) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=dim).astype(np.float32)
    return v / np.linalg.norm(v)


UNITS = [U(f"doc:t001:w{i}", f"[PAD P1 ET] table row {i} | 340.00") for i in range(5)]


def filled(scope: str = "C+tables") -> UnitEmbeddingStore:
    store = UnitEmbeddingStore(CONFIG)
    for i, unit in enumerate(UNITS):
        store.put(scope, unit, vec(i), f"test#{i}")
    return store


# ---------------------------------------------------------------------------------------------- reuse and misses
def test_unchanged_units_reuse_their_vectors_exactly_and_follow_the_requested_order():
    store = filled()
    shuffled = UNITS[::-1]
    matrix = store.assemble("C+tables", shuffled)
    assert all(np.array_equal(matrix[i], vec(len(UNITS) - 1 - i)) for i in range(len(UNITS)))


def test_changed_text_invalidates_only_that_unit():
    store = filled()
    changed = [UNITS[0], U(UNITS[1].unit_id, UNITS[1].text.replace("340.00", "340.00\n18.00")), *UNITS[2:]]
    plan = store.plan("C+tables", changed)
    assert plan.misses == [UNITS[1].unit_id] and len(plan.hits) == 4
    with pytest.raises(MissingEmbeddingsError) as error:
        store.assemble("C+tables", changed)                              # never a partial or stale matrix
    assert error.value.unit_ids == [UNITS[1].unit_id]


def test_same_text_under_another_unit_id_or_scope_is_a_miss():
    store = filled()
    assert store.plan("C+tables", [U("doc:t001:w99", UNITS[0].text)]).misses == ["doc:t001:w99"]
    assert store.plan("A+tables", UNITS).misses == [u.unit_id for u in UNITS]


def test_existing_entries_are_never_replaced():
    store = filled()
    assert store.put("C+tables", UNITS[0], vec(99), "other") is False
    assert np.array_equal(store.get("C+tables", UNITS[0]), vec(0))


@pytest.mark.parametrize("bad", [np.ones(DIM, dtype=np.float32), vec(1, DIM + 1), vec(1).astype(np.float64),
                                 np.full(DIM, np.nan, dtype=np.float32)])
def test_vectors_that_do_not_fit_the_config_are_refused(bad):
    with pytest.raises(CacheIntegrityError):
        UnitEmbeddingStore(CONFIG).put("C+tables", UNITS[0], bad, "x")


# ------------------------------------------------------------------------------------------------- persistence
def test_round_trip_and_atomic_save(tmp_path):
    filled().save(tmp_path / "store")
    loaded = UnitEmbeddingStore.load(tmp_path / "store", CONFIG)
    assert np.array_equal(loaded.assemble("C+tables", UNITS), filled().assemble("C+tables", UNITS))
    assert [p.name for p in tmp_path.iterdir()] == ["store"]               # no temporary directory left behind


@pytest.mark.parametrize("field, value", [("model_revision", "0" * 40), ("weights_sha256", "0" * 64), ("normalize", False),
                                          ("max_seq_length", 512), ("prompt", "query: "), ("dimension", 1024),
                                          ("batch_size", 32), ("library_versions", (("torch", "3.0"),))])
def test_a_store_is_rejected_for_any_other_configuration(tmp_path, field, value):
    filled().save(tmp_path / "store")
    other = replace(CONFIG, **{field: value})
    with pytest.raises(CacheIntegrityError):
        UnitEmbeddingStore.load(tmp_path / "store", other)
    assert UnitEmbeddingStore(other).plan("C+tables", UNITS).hits == []


def _rehash(directory: Path) -> None:
    """Simulate a consistent-looking tampering: refresh the manifest file hashes."""
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["vectors_sha256"] = file_sha256(directory / "vectors.npy")
    manifest["entries_sha256"] = file_sha256(directory / "entries.jsonl")
    (directory / "manifest.json").write_text(json.dumps(manifest))


def _tamper_vector(directory: Path) -> None:
    matrix = np.load(directory / "vectors.npy")
    matrix[2] = vec(42)
    np.save(directory / "vectors.npy", matrix)
    _rehash(directory)


def _add_text_field(directory: Path) -> None:
    lines = (directory / "entries.jsonl").read_text().splitlines()
    entry = json.loads(lines[0])
    entry["text"] = UNITS[0].text
    (directory / "entries.jsonl").write_text("\n".join([json.dumps(entry)] + lines[1:]) + "\n")
    _rehash(directory)


def _swap_unit_ids(directory: Path) -> None:
    entries = [json.loads(line) for line in (directory / "entries.jsonl").read_text().splitlines()]
    entries[0]["unit_id"], entries[1]["unit_id"] = entries[1]["unit_id"], entries[0]["unit_id"]
    (directory / "entries.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))
    _rehash(directory)


def _drop_manifest(directory: Path) -> None:
    (directory / "manifest.json").unlink()


def _flip_byte(directory: Path) -> None:
    data = bytearray((directory / "vectors.npy").read_bytes())
    data[-3] ^= 0x01
    (directory / "vectors.npy").write_bytes(bytes(data))


def _wrong_shape(directory: Path) -> None:
    np.save(directory / "vectors.npy", np.load(directory / "vectors.npy")[:3])
    _rehash(directory)


@pytest.mark.parametrize("damage", [_tamper_vector, _add_text_field, _swap_unit_ids, _drop_manifest, _flip_byte, _wrong_shape])
def test_missing_or_corrupted_stores_are_rejected(tmp_path, damage):
    filled().save(tmp_path / "store")
    damage(tmp_path / "store")
    with pytest.raises(CacheIntegrityError):
        UnitEmbeddingStore.load(tmp_path / "store", CONFIG)


def test_store_files_hold_no_unit_text(tmp_path):
    filled().save(tmp_path / "store")
    stored = "".join(p.read_text() for p in (tmp_path / "store").glob("*.json*"))
    assert not any(u.text in stored or "340.00" in stored for u in UNITS)


# --------------------------------------------------------------------------------------------------- legacy
def legacy_file(directory: Path, units, matrix) -> Path:
    path = legacy_path(directory, "C", units)
    np.save(path, matrix)
    return path


def test_legacy_rows_are_imported_by_unit_order(tmp_path):
    matrix = np.stack([vec(i) for i in range(len(UNITS))])
    legacy_file(tmp_path, UNITS, matrix)
    store = UnitEmbeddingStore(CONFIG)
    report = import_legacy(store, tmp_path, "C", "C+tables", UNITS)
    assert report.imported == len(UNITS) and all(report.evidence.values())
    assert np.array_equal(store.assemble("C+tables", UNITS), matrix)


def test_legacy_file_for_other_units_or_texts_is_not_used(tmp_path):
    legacy_file(tmp_path, UNITS, np.stack([vec(i) for i in range(len(UNITS))]))
    changed = [U(UNITS[0].unit_id, UNITS[0].text + " changed"), *UNITS[1:]]
    with pytest.raises(LegacyCacheError):
        import_legacy(UnitEmbeddingStore(CONFIG), tmp_path, "C", "C+tables", changed)
    with pytest.raises(LegacyCacheError):
        import_legacy(UnitEmbeddingStore(CONFIG), tmp_path, "C", "C+tables", UNITS[::-1])   # order is part of the key


@pytest.mark.parametrize("matrix", [np.stack([vec(i) for i in range(4)]),                 # wrong row count
                                    np.stack([vec(i) * 2 for i in range(5)]),             # not normalised
                                    np.stack([vec(i, DIM + 1) for i in range(5)])])       # wrong dimension
def test_malformed_legacy_matrices_are_rejected(tmp_path, matrix):
    legacy_file(tmp_path, UNITS, matrix)
    with pytest.raises(LegacyCacheError):
        import_legacy(UnitEmbeddingStore(CONFIG), tmp_path, "C", "C+tables", UNITS)


def test_legacy_file_older_than_the_local_model_snapshot_is_not_attributed(tmp_path):
    path = legacy_file(tmp_path, UNITS, np.stack([vec(i) for i in range(len(UNITS))]))
    os.utime(path, (946684800, 946684800))                                  # 2000-01-01: predates model and libraries
    with pytest.raises(LegacyCacheError, match="provenance"):
        import_legacy(UnitEmbeddingStore(CONFIG), tmp_path, "C", "C+tables", UNITS)


def test_vector_hash_is_stable():
    assert vector_sha256(vec(1)) == vector_sha256(vec(1).copy()) != vector_sha256(vec(2))
