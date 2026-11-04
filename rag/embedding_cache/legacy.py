"""Import unchanged vectors from the existing whole-index caches (`rag/retrieval/index_cache/emb_<S>_<N>_<key>.npy`).

A legacy file is accepted for a list of units only if:
  * its name carries the key `core.Retriever.embeddings` computes for exactly these units in this order
    (SHA-256 of '\\n'.join(unit_id + text) + model id, first 16 hex) and their count;
  * the matrix is float32, (N, dimension), finite and unit-normalised;
  * the configuration it was produced with is the one supplied: same model id, normalisation and batch size as
    core.py uses, and evidence that the local model snapshot and libraries predate the file (one snapshot only).
Row i then belongs to unit i (core.py encodes the units in corpus order). Files are opened read-only, never written.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from .identity import MODEL_ID, EmbeddingConfig, snapshot_mtime
from .store import NORM_TOLERANCE, IndexedUnit, UnitEmbeddingStore

LEGACY_BATCH_SIZE = 16          # core.Retriever.embeddings: encode(..., batch_size=16, normalize_embeddings=True)
LEGACY_NORMALIZE = True


class LegacyCacheError(RuntimeError):
    """No legacy file matches these units, or the file fails verification."""


@dataclass
class ImportReport:
    """Outcome of importing one legacy .npy file into the store, with the provenance evidence that allowed it."""
    file: str
    scope: str
    units: int
    imported: int
    already_present: int
    evidence: dict


def legacy_key(units: Sequence[IndexedUnit], model_id: str = MODEL_ID) -> str:
    """The whole-index cache key exactly as rag/retrieval/core.py computes it."""
    return hashlib.sha256(("\n".join(u.unit_id + u.text for u in units) + model_id).encode()).hexdigest()[:16]


def legacy_path(cache_dir: Path, strategy: str, units: Sequence[IndexedUnit]) -> Path:
    return cache_dir / f"emb_{strategy}_{len(units)}_{legacy_key(units)}.npy"


def _libraries_mtime() -> float:
    """Latest install time of the embedding libraries (their dist-info RECORD files)."""
    times = []
    for name in ("sentence-transformers", "transformers", "torch"):
        record = next((f for f in importlib.metadata.distribution(name).files or [] if f.name == "RECORD"), None)
        if record is not None:
            times.append(Path(record.locate()).stat().st_mtime)
    return max(times) if times else float("inf")


def provenance_evidence(path: Path, config: EmbeddingConfig) -> dict:
    """Why these legacy vectors can be attributed to `config` (all must be True)."""
    created = path.stat().st_mtime
    return {"model_id_matches": config.model_id == MODEL_ID,
            "normalize_matches": config.normalize is LEGACY_NORMALIZE,
            "batch_size_matches": config.batch_size == LEGACY_BATCH_SIZE,
            "no_prompt": config.prompt == "",
            "single_snapshot_older_than_file": snapshot_mtime() < created,
            "libraries_installed_before_file": _libraries_mtime() < created}


def read_legacy_matrix(path: Path, n_units: int, config: EmbeddingConfig) -> np.ndarray:
    """Load and check a legacy matrix: shape, dtype, finite values and unit norm; raises LegacyCacheError otherwise."""
    if not path.is_file():
        raise LegacyCacheError(f"no legacy cache {path.name} (units, order or texts differ from the cached index)")
    try:
        matrix = np.load(path, allow_pickle=False, mmap_mode="r")
    except ValueError as error:
        raise LegacyCacheError(f"{path.name} is not a readable .npy array") from error
    if matrix.shape != (n_units, config.dimension) or matrix.dtype != np.dtype(config.dtype):
        raise LegacyCacheError(f"{path.name}: {matrix.shape}/{matrix.dtype} != ({n_units}, {config.dimension})/{config.dtype}")
    matrix = np.array(matrix)                                              # copy; the file stays untouched
    norms = np.linalg.norm(matrix, axis=1)
    if not np.isfinite(matrix).all() or (config.normalize and np.abs(norms - 1).max() > NORM_TOLERANCE):
        raise LegacyCacheError(f"{path.name}: non-finite or non-normalised rows")
    return matrix


def import_legacy(store: UnitEmbeddingStore, cache_dir: Path, strategy: str, scope: str,
                  units: Sequence[IndexedUnit]) -> ImportReport:
    """Copy every row of the verified legacy file for these units into the store under `scope`."""
    path = legacy_path(cache_dir, strategy, units)
    matrix = read_legacy_matrix(path, len(units), store.config)
    evidence = provenance_evidence(path, store.config)
    if not all(evidence.values()):
        raise LegacyCacheError(f"{path.name}: provenance not established {[k for k, ok in evidence.items() if not ok]}")
    imported = sum(store.put(scope, unit, matrix[i], f"legacy:{path.name}#{i}") for i, unit in enumerate(units))
    return ImportReport(path.name, scope, len(units), imported, len(units) - imported, evidence)
