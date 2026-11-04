"""Controlled incremental writes to the per-unit embedding store (Step 6D preparation). Not used by core.py.

Two steps, both offline and database-free:
  dry_run(...)   -> regeneration manifest: the exact units of a corpus that have no valid vector (ID, position,
                    indexed-text SHA-256, the text hash they replace), bound to the configuration fingerprint and to
                    the SHA-256 of the store it was computed against. No text is stored. Identified by its own hash.
  apply(...)     -> adds exactly the manifest's vectors in one atomic commit, or nothing. Every vector is checked
                    (dimension, dtype, finiteness, norm); an existing identical vector makes a rerun a no-op; a
                    different vector for an existing key, an unlisted or duplicated unit, a stale manifest, another
                    configuration, a disabled scope or a corrupted store rejects the whole batch.
  embed_missing  -> encodes the manifest's units with a supplied encoder (its configuration must fingerprint the
                    same; each unit's text must still hash to the manifest value) and calls apply.
Writes to the adapter's default store need `allow_default_store=True`; nothing in the repository sets it.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol, Sequence

import numpy as np

from .adapter import DEFAULT_STORE, ENABLED_SCOPES
from .identity import EmbeddingConfig
from .store import (CacheIntegrityError, IndexedUnit, UnitEmbeddingStore, _write_synced, entry_key, file_sha256,
                    text_sha256, vector_sha256)

MANIFEST_SCHEMA = "regeneration-manifest-1"
LABEL = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


class WriteRejectedError(RuntimeError):
    """The requested write is not allowed; the store was not changed."""


class Encoder(Protocol):
    """Anything with an embedding configuration and an encode(texts) method that returns one vector per text."""
    config: EmbeddingConfig

    def encode(self, texts: list[str]) -> np.ndarray: ...


# ------------------------------------------------------------------------------------------------- manifest
def _manifest_id(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _check_scope(scope: str) -> None:
    if scope not in ENABLED_SCOPES:
        raise WriteRejectedError(f"scope {scope} is not enabled for the per-unit cache")


def dry_run(store_dir: Path, config: EmbeddingConfig, scope: str, units: Sequence[IndexedUnit], label: str) -> dict[str, Any]:
    """The regeneration manifest for `units` (in their index order) against the store as it is now."""
    _check_scope(scope)
    if not LABEL.match(label):
        raise WriteRejectedError("label must be 1-64 characters of [A-Za-z0-9._:-]")
    store = UnitEmbeddingStore.load(store_dir, config)
    previous = {e["unit_id"]: e["text_sha256"] for e in store.entries() if e["scope"] == scope}
    plan = store.plan(scope, units)
    missing = set(plan.misses)
    to_generate = [{"position": i, "unit_id": u.unit_id, "text_sha256": text_sha256(u.text),
                    "previous_text_sha256": previous.get(u.unit_id),
                    "reason": "indexed text changed" if u.unit_id in previous else "new unit"}
                   for i, u in enumerate(units) if u.unit_id in missing]
    if len({g["unit_id"] for g in to_generate}) != len(to_generate):
        raise WriteRejectedError("duplicate unit IDs in the corpus")
    body = {"schema": MANIFEST_SCHEMA, "label": label, "scope": scope, "config_fingerprint": config.fingerprint(),
            "base_store_manifest_sha256": file_sha256(store_dir / "manifest.json"), "base_store_entries": len(store),
            "units_total": len(units), "units_reused": len(plan.hits), "units_to_generate": len(to_generate),
            "unit_order_sha256": hashlib.sha256("\n".join(u.unit_id for u in units).encode()).hexdigest(),
            "to_generate": to_generate}
    return {"manifest_id": _manifest_id(body), **body}


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    """Persist a manifest atomically (temporary file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        _write_synced(tmp, json.dumps(manifest, indent=1, sort_keys=True))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def verify_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the manifest if its schema and identity hash match its content; raise WriteRejectedError otherwise."""
    body = {k: v for k, v in manifest.items() if k != "manifest_id"}
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("manifest_id") != _manifest_id(body):
        raise WriteRejectedError("manifest schema or identity hash does not match its content")
    return manifest


# ---------------------------------------------------------------------------------------------------- apply
@contextmanager
def _exclusive(store_dir: Path) -> Iterator[None]:
    """Hold an exclusive, non-blocking lock on the store while writing; a second writer is rejected at once."""
    lock_path = store_dir.parent / f".{store_dir.name}.lock"
    with lock_path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WriteRejectedError("another writer holds the store lock") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _pairs(vectors: Iterable[tuple[str, np.ndarray]]) -> dict[str, np.ndarray]:
    """Turn (unit ID, vector) pairs into a dict, rejecting any unit that appears twice."""
    out: dict[str, np.ndarray] = {}
    for unit_id, vector in vectors:
        if unit_id in out:
            raise WriteRejectedError(f"duplicate vector for {unit_id}")
        out[unit_id] = vector
    return out


def apply(store_dir: Path, config: EmbeddingConfig, manifest: dict[str, Any], vectors: Iterable[tuple[str, np.ndarray]],
          label: str, allow_default_store: bool = False) -> dict[str, Any]:
    """Add the manifest's vectors in one commit. Returns a report; raises (store unchanged) on any rejection."""
    if store_dir.resolve() == DEFAULT_STORE.resolve() and not allow_default_store:
        raise WriteRejectedError("writing to the adapter's default store needs explicit approval (allow_default_store)")
    manifest = verify_manifest(manifest)
    _check_scope(manifest["scope"])
    if not LABEL.match(label):
        raise WriteRejectedError("label must be 1-64 characters of [A-Za-z0-9._:-]")
    if manifest["config_fingerprint"] != config.fingerprint():
        raise WriteRejectedError("manifest was made for another embedding configuration")
    given = _pairs(vectors)
    expected = {g["unit_id"]: g["text_sha256"] for g in manifest["to_generate"]}
    if set(given) != set(expected):
        raise WriteRejectedError(f"vectors must cover exactly the manifest's units "
                                 f"(unlisted: {sorted(set(given) - set(expected))[:3]}, missing: {sorted(set(expected) - set(given))[:3]})")
    with _exclusive(store_dir):
        store = UnitEmbeddingStore.load(store_dir, config)             # integrity first; a corrupted store is never extended
        existing = {e["key"]: e for e in store.entries()}
        provenance = f"generated:{label}:manifest:{manifest['manifest_id'][:16]}"
        new, present, conflicts = [], 0, []
        for unit_id, text_hash in expected.items():
            key = entry_key(store.fingerprint, manifest["scope"], unit_id, text_hash)
            vector = store.validate_vector(given[unit_id])
            if key not in existing:
                new.append((unit_id, text_hash, vector))
            elif existing[key]["vector_sha256"] == vector_sha256(vector):
                present += 1
            else:
                conflicts.append(unit_id)
        if not new:                                                       # rerun of an applied manifest
            if conflicts:
                raise WriteRejectedError(f"conflicting vector for existing entry {conflicts[0]}")
            return {"status": "no_op", "already_present": present, "added": 0, "manifest_id": manifest["manifest_id"]}
        # Staleness is checked only when adding: applying a manifest changes the store, so a rerun is a no-op.
        if file_sha256(store_dir / "manifest.json") != manifest["base_store_manifest_sha256"]:
            raise WriteRejectedError("stale manifest: the store changed since the dry run")
        if conflicts:
            raise WriteRejectedError(f"conflicting vector for existing entry {conflicts[0]}")
        for unit_id, text_hash, vector in new:
            store.put_hashed(manifest["scope"], unit_id, text_hash, vector, provenance)
        committed = store.save(store_dir)
        reloaded = UnitEmbeddingStore.load(store_dir, config)
        if not all(reloaded.has_key(entry_key(store.fingerprint, manifest["scope"], u, h)) for u, h in expected.items()):
            raise CacheIntegrityError("committed store is missing manifest entries")
    return {"status": "added", "added": len(new), "already_present": present, "entries": committed["entries"],
            "store_manifest_sha256": file_sha256(store_dir / "manifest.json"), "manifest_id": manifest["manifest_id"]}


def embed_missing(store_dir: Path, config: EmbeddingConfig, manifest: dict[str, Any], units: Sequence[IndexedUnit],
                  encoder: Encoder, label: str, allow_default_store: bool = False) -> dict[str, Any]:
    """Encode exactly the manifest's units with `encoder` and apply them."""
    manifest = verify_manifest(manifest)
    if encoder.config.fingerprint() != config.fingerprint() or manifest["config_fingerprint"] != config.fingerprint():
        raise WriteRejectedError("encoder, store and manifest configurations differ")
    by_id = {u.unit_id: u for u in units}
    wanted = [(g["unit_id"], g["text_sha256"]) for g in manifest["to_generate"]]
    stale = [u for u, h in wanted if u not in by_id or text_sha256(by_id[u].text) != h]
    if stale:
        raise WriteRejectedError(f"unit text no longer matches the manifest: {stale[:3]}")
    vectors = np.asarray(encoder.encode([by_id[u].text for u, _ in wanted])) if wanted else np.zeros((0, config.dimension))
    return apply(store_dir, config, manifest, [(u, vectors[i]) for i, (u, _) in enumerate(wanted)], label, allow_default_store)
