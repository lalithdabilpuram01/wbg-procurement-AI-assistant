"""Per-retrieval-unit embedding store, bound to ONE embedding configuration.

Entry key = SHA-256 of (config fingerprint, index scope, unit ID, SHA-256 of the exact indexed text). The index scope
(strategy + whether tables are indexed) is part of the key because the same unit text encoded inside a different
whole-index batch is not bit-identical (sentence-transformers batches by length; measured differences <= 3e-7), so
an unchanged unit always gets back the exact vector of its own index.

On disk (one directory per store): manifest.json (config, fingerprint, file hashes), entries.jsonl (keys, IDs,
hashes, provenance - never any text) and vectors.npy (float32, one row per entry). A store is loaded only if every
integrity check passes; otherwise it is rejected as a whole. Writes go to a temporary directory renamed into place.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

import numpy as np

from .identity import EmbeddingConfig

SCHEMA_VERSION = "unit-embedding-store-1"
ENTRY_FIELDS = {"key", "scope", "unit_id", "text_sha256", "vector_sha256", "row", "provenance"}
NORM_TOLERANCE = 1e-4


class CacheIntegrityError(RuntimeError):
    """The store is missing, corrupted or belongs to another embedding configuration."""


class MissingEmbeddingsError(RuntimeError):
    """Some units have no valid cached vector and must be (re)embedded before an index can be assembled."""

    def __init__(self, unit_ids: list[str]):
        super().__init__(f"{len(unit_ids)} unit(s) need embedding, e.g. {unit_ids[:3]}")
        self.unit_ids = unit_ids


class IndexedUnit(Protocol):
    unit_id: str
    text: str


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def vector_sha256(vector: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(vector, dtype="<f4").tobytes()).hexdigest()


def file_sha256(path: Path) -> str:
    """SHA-256 of a file, read in 1 MiB blocks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def entry_key(fingerprint: str, scope: str, unit_id: str, text_hash: str) -> str:
    return hashlib.sha256("\0".join((fingerprint, scope, unit_id, text_hash)).encode()).hexdigest()


@dataclass
class Plan:
    """Cache lookup result for an ordered list of units."""

    hits: list[str] = field(default_factory=list)          # unit IDs with a valid cached vector
    misses: list[str] = field(default_factory=list)        # unit IDs that need (re)embedding

    @property
    def complete(self) -> bool:
        return not self.misses


class UnitEmbeddingStore:
    """In-memory view of one store; `save` persists it atomically."""

    def __init__(self, config: EmbeddingConfig):
        """An empty store bound to one embedding configuration, whose fingerprint is part of every cache key."""
        self.config = config
        self.fingerprint = config.fingerprint()
        self._entries: dict[str, dict[str, Any]] = {}
        self._vectors: list[np.ndarray] = []

    # ------------------------------------------------------------------------------------------------ lookup
    def key_for(self, scope: str, unit: IndexedUnit) -> str:
        return entry_key(self.fingerprint, scope, unit.unit_id, text_sha256(unit.text))

    def get(self, scope: str, unit: IndexedUnit) -> np.ndarray | None:
        entry = self._entries.get(self.key_for(scope, unit))
        return None if entry is None else self._vectors[entry["row"]]

    def plan(self, scope: str, units: Iterable[IndexedUnit]) -> Plan:
        """Split the units into cache hits and misses for one index scope."""
        plan = Plan()
        for unit in units:
            (plan.hits if self.get(scope, unit) is not None else plan.misses).append(unit.unit_id)
        return plan

    def assemble(self, scope: str, units: list[IndexedUnit]) -> np.ndarray:
        """Index matrix in the given unit order; refuses (no partial matrix) when any unit is missing."""
        rows = [self.get(scope, unit) for unit in units]
        missing = [unit.unit_id for unit, row in zip(units, rows) if row is None]
        if missing:
            raise MissingEmbeddingsError(missing)
        return np.stack(rows) if rows else np.zeros((0, self.config.dimension), dtype=np.float32)

    def __len__(self) -> int:
        return len(self._entries)

    # ------------------------------------------------------------------------------------------------- write
    def _check_vector(self, vector: np.ndarray) -> np.ndarray:
        """Check a vector against this configuration (shape, dtype, finite, norm); return a copy in the config dtype."""
        vector = np.asarray(vector)
        if vector.shape != (self.config.dimension,) or vector.dtype != np.dtype(self.config.dtype):
            raise CacheIntegrityError(f"vector {vector.shape}/{vector.dtype} does not match the config "
                                      f"({self.config.dimension}, {self.config.dtype})")
        if not np.isfinite(vector).all():
            raise CacheIntegrityError("vector has non-finite values")
        if self.config.normalize and abs(float(np.linalg.norm(vector)) - 1.0) > NORM_TOLERANCE:
            raise CacheIntegrityError("vector is not unit-normalised but the config normalises")
        return vector.astype(self.config.dtype, copy=True)

    def put(self, scope: str, unit: IndexedUnit, vector: np.ndarray, provenance: str) -> bool:
        """Add a vector; an existing entry is never replaced. Returns False when the key already exists."""
        return self.put_hashed(scope, unit.unit_id, text_sha256(unit.text), vector, provenance)

    def put_hashed(self, scope: str, unit_id: str, text_hash: str, vector: np.ndarray, provenance: str) -> bool:
        """`put` for a unit known by its ID and text hash (the writer never holds unit text)."""
        key = entry_key(self.fingerprint, scope, unit_id, text_hash)
        if key in self._entries:
            return False
        vector = self._check_vector(vector)
        self._entries[key] = {"key": key, "scope": scope, "unit_id": unit_id, "text_sha256": text_hash,
                              "vector_sha256": vector_sha256(vector), "row": len(self._vectors), "provenance": provenance}
        self._vectors.append(vector)
        return True

    def has_key(self, key: str) -> bool:
        return key in self._entries

    def validate_vector(self, vector: np.ndarray) -> np.ndarray:
        """A float copy of `vector` if it fits this configuration (shape, dtype, finite, norm); raises otherwise."""
        return self._check_vector(vector)

    def entries(self) -> list[dict[str, Any]]:
        """Copies of the entry records (IDs, hashes, provenance; no vectors, no text)."""
        return [dict(e) for e in self._entries.values()]

    def save(self, directory: Path) -> dict[str, Any]:
        """Commit the store crash-safely: write and fsync a temporary sibling, verify it loads, move the current store
        aside, rename the new one into place, then delete the old one. At no point can a partial store be loaded: a
        crash leaves either the old store, the new store, or no store (rejected by `load`; `recover` restores it)."""
        directory.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f".{directory.name}.tmp-", dir=directory.parent))
        old: Path | None = None
        try:
            matrix = np.stack(self._vectors) if self._vectors else np.zeros((0, self.config.dimension), dtype=np.float32)
            with (tmp / "vectors.npy").open("wb") as handle:
                np.save(handle, matrix.astype(self.config.dtype), allow_pickle=False)
                _sync(handle)
            _write_synced(tmp / "entries.jsonl", "".join(json.dumps(e, sort_keys=True) + "\n" for e in self._entries.values()))
            manifest = {"schema_version": SCHEMA_VERSION, "config": self.config.as_dict(), "config_fingerprint": self.fingerprint,
                        "entries": len(self._entries), "vectors_sha256": file_sha256(tmp / "vectors.npy"),
                        "entries_sha256": file_sha256(tmp / "entries.jsonl")}
            _write_synced(tmp / "manifest.json", json.dumps(manifest, indent=1, sort_keys=True))
            UnitEmbeddingStore.load(tmp, self.config)                        # complete and valid before it can be visible
            if directory.exists():
                old = directory.with_name(f".{directory.name}.old-{time.time_ns()}")
                os.replace(directory, old)
            os.replace(tmp, directory)
            _sync_dir(directory.parent)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            if old is not None and not directory.exists():
                os.replace(old, directory)                                  # roll the swap back
            raise
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
        return manifest

    # -------------------------------------------------------------------------------------------------- load
    @classmethod
    def load(cls, directory: Path, config: EmbeddingConfig) -> "UnitEmbeddingStore":
        """Load a store for `config`; any failed check rejects the whole store (CacheIntegrityError)."""
        paths = {name: directory / name for name in ("manifest.json", "entries.jsonl", "vectors.npy")}
        missing = [name for name, path in paths.items() if not path.is_file()]
        if missing:
            raise CacheIntegrityError(f"store files missing: {missing}")
        try:
            manifest = json.loads(paths["manifest.json"].read_text())
        except json.JSONDecodeError as error:
            raise CacheIntegrityError("manifest is not valid JSON") from error
        store = cls(config)
        checks = {
            "schema_version": manifest.get("schema_version") == SCHEMA_VERSION,
            "config_matches": manifest.get("config_fingerprint") == store.fingerprint,
            "manifest_config_consistent": _fingerprint_of(manifest.get("config")) == manifest.get("config_fingerprint"),
            "vectors_file_hash": manifest.get("vectors_sha256") == file_sha256(paths["vectors.npy"]),
            "entries_file_hash": manifest.get("entries_sha256") == file_sha256(paths["entries.jsonl"]),
        }
        if not all(checks.values()):
            raise CacheIntegrityError(f"store rejected: {[k for k, ok in checks.items() if not ok]}")
        try:
            matrix = np.load(paths["vectors.npy"], allow_pickle=False)
            entries = [json.loads(line) for line in paths["entries.jsonl"].read_text().splitlines() if line.strip()]
        except (ValueError, json.JSONDecodeError) as error:
            raise CacheIntegrityError("store files are unreadable") from error
        if matrix.ndim != 2 or matrix.shape != (len(entries), config.dimension) or matrix.dtype != np.dtype(config.dtype) \
                or len(entries) != manifest.get("entries"):
            raise CacheIntegrityError(f"vectors {matrix.shape}/{matrix.dtype} do not match {len(entries)} entries x {config.dimension}")
        for row, entry in enumerate(entries):
            store._load_entry(entry, row, matrix[row])
        return store

    def _load_entry(self, entry: dict[str, Any], row: int, vector: np.ndarray) -> None:
        """Add one entry read from disk after checking its fields, row order, key and vector hash."""
        if set(entry) != ENTRY_FIELDS or entry["row"] != row or entry["key"] in self._entries:
            raise CacheIntegrityError(f"entry {row} malformed, out of order or duplicated")
        if entry["key"] != entry_key(self.fingerprint, entry["scope"], entry["unit_id"], entry["text_sha256"]):
            raise CacheIntegrityError(f"entry {row} key does not match its identity fields")
        if vector_sha256(vector) != entry["vector_sha256"]:
            raise CacheIntegrityError(f"entry {row} vector hash mismatch")
        self._entries[entry["key"]] = entry
        self._vectors.append(self._check_vector(vector))


def recover(directory: Path) -> list[str]:
    """Clean up after an interrupted `save`: restore the last committed store if the swap was cut between its two
    renames, and delete leftover temporary and superseded copies. Returns the actions taken."""
    parent, name = directory.parent, directory.name
    olds = sorted(parent.glob(f".{name}.old-*"), key=lambda p: int(p.name.rsplit("-", 1)[1]))
    actions = []
    if not directory.exists() and olds:
        os.replace(olds[-1], directory)
        actions.append(f"restored {olds.pop().name}")
    for leftover in [*parent.glob(f".{name}.tmp-*"), *olds]:
        shutil.rmtree(leftover, ignore_errors=True)
        actions.append(f"removed {leftover.name}")
    return actions


def _sync(handle) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _write_synced(path: Path, text: str) -> None:
    """Write a text file and fsync it before returning."""
    with path.open("w") as handle:
        handle.write(text)
        _sync(handle)


def _sync_dir(path: Path) -> None:
    """fsync a directory so that a rename inside it survives a crash."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fingerprint_of(config: dict[str, Any] | None) -> str | None:
    """Recompute the fingerprint of a config read from JSON (lists turned back into the tuple shape that was hashed)."""
    if not isinstance(config, dict):
        return None
    normalised = {**config, "library_versions": [list(p) for p in config.get("library_versions", [])]}
    return hashlib.sha256(json.dumps(normalised, sort_keys=True).encode()).hexdigest()
