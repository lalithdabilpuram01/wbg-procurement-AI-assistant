"""Opt-in bridge from `rag/retrieval/core.Retriever.embeddings()` to the verified per-unit store (Step 6C).

Used only when a Retriever is created with `embedding_cache="unit"` (or RAG_EMBEDDING_CACHE=unit). It returns the
index matrix for the corpus' units, in corpus order, only if:
  1. the scope is enabled (strategy B is not: decision 4 of Step 6C);
  2. a compatibility attestation exists for the current model configuration and covers every legacy file the store's
     vectors were imported from (written by verify_compatibility.py after re-encoding a sample locally);
  3. the store loads with all integrity checks for the current configuration;
  4. every unit has an entry for its exact indexed text.
A corpus other than the default pilot_v2 (Corpus(processed_dir=...)) is served only through a registered release
(releases/<name>/release.json, Step 6E) whose descriptor matches the corpus version (run-manifest SHA-256 and extraction
version), the exact indexed-text fingerprint of the requested scope, the store's file hashes and the model configuration.
Any failure raises; nothing is re-encoded, written, or served from another cache.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Protocol, Sequence

import numpy as np

from .identity import EmbeddingConfig, local_model_config
from .store import IndexedUnit, UnitEmbeddingStore, file_sha256, text_sha256

HERE = Path(__file__).resolve().parent
STORE_ENV, ATTESTATION_ENV = "RAG_UNIT_CACHE_DIR", "RAG_UNIT_CACHE_ATTESTATION"
DEFAULT_STORE = HERE / "experimental_store"
DEFAULT_ATTESTATION = HERE / "results" / "compatibility.json"
ENABLED_SCOPES = ("A+tables", "A-tables", "C+tables")
DEFAULT_CORPUS = HERE.parent / "corpus_v3" / "processed" / "pilot_v2"
RELEASES = HERE / "releases"
STORE_FILES = ("manifest.json", "entries.jsonl", "vectors.npy")


class UnitCacheNotAllowedError(RuntimeError):
    """The per-unit cache may not serve this request (scope disabled or compatibility not attested)."""


class ReleaseMismatchError(UnitCacheNotAllowedError):
    """The corpus, scope, store or configuration does not match the registered release."""


class CorpusLike(Protocol):
    """The parts of a retrieval Corpus that the adapter reads (strategy, table flag, processed folder and units)."""
    strategy: str
    include_tables: bool
    processed_dir: Path
    units: Sequence[IndexedUnit]


def scope_of(corpus: CorpusLike) -> str:
    return f"{corpus.strategy}{'+' if corpus.include_tables else '-'}tables"


def legacy_sources(store_dir: Path) -> set[str]:
    """Legacy .npy file names the store's vectors were imported from (from the entries' provenance)."""
    names = set()
    for line in (store_dir / "entries.jsonl").read_text().splitlines():
        provenance = json.loads(line)["provenance"]
        if provenance.startswith("legacy:"):
            names.add(provenance.removeprefix("legacy:").split("#", 1)[0])
    return names


def check_attestation(path: Path, config: EmbeddingConfig, store_dir: Path) -> dict[str, Any]:
    """The compatibility record must be a pass for this exact configuration and cover the store's legacy sources."""
    if not path.is_file():
        raise UnitCacheNotAllowedError(f"no compatibility attestation at {path}")
    record = json.loads(path.read_text())
    covered = {name for name, info in record.get("legacy_files", {}).items() if info.get("compatible") is True}
    problems = [name for name, ok in (
        ("verdict", record.get("verdict") == "compatible"),
        ("config_fingerprint", record.get("config_fingerprint") == config.fingerprint()),
        ("legacy_files_covered", legacy_sources(store_dir) <= covered)) if not ok]
    if problems:
        raise UnitCacheNotAllowedError(f"compatibility attestation does not allow this store: {problems}")
    return record


def indexed_text_fingerprint(units: Sequence[IndexedUnit]) -> str:
    """SHA-256 over the ordered (unit ID, indexed-text SHA-256) pairs: identifies an index's exact content and order."""
    return hashlib.sha256("\n".join(f"{u.unit_id}\t{text_sha256(u.text)}" for u in units).encode()).hexdigest()


def find_release(processed_dir: Path) -> tuple[Path, dict[str, Any]] | None:
    """The registered release whose corpus is `processed_dir` (releases/*/release.json), if any."""
    for descriptor in sorted(RELEASES.glob("*/release.json")):
        record = json.loads(descriptor.read_text())
        if (HERE.parents[1] / record["corpus"]["processed_dir"]).resolve() == Path(processed_dir).resolve():
            return descriptor.parent, record
    return None


def check_release(record: dict[str, Any], corpus: CorpusLike, scope: str, store_dir: Path, config: EmbeddingConfig) -> None:
    """Corpus version, indexed text, store content and model configuration must all match the release descriptor."""
    run_manifest = Path(corpus.processed_dir) / "run_manifest.json"
    problems = [name for name, ok in (
        ("scope_enabled_in_release", scope in record["enabled_scopes"]),
        ("corpus_run_manifest_sha256", run_manifest.is_file() and file_sha256(run_manifest) == record["corpus"]["run_manifest_sha256"]),
        ("corpus_extraction_version", run_manifest.is_file()
         and json.loads(run_manifest.read_text()).get("extraction_version") == record["corpus"]["extraction_version"]),
        ("indexed_text_fingerprint", scope in record["indexed_text_sha256"]
         and indexed_text_fingerprint(corpus.units) == record["indexed_text_sha256"][scope]),
        ("store_files_sha256", all((store_dir / f).is_file() and file_sha256(store_dir / f) == record["store"][f] for f in STORE_FILES)),
        ("config_fingerprint", record["config_fingerprint"] == config.fingerprint())) if not ok]
    if problems:
        raise ReleaseMismatchError(f"release {record['release']} does not match: {problems}")


def unit_cache_embeddings(corpus: CorpusLike, store_dir: Path | None = None, attestation: Path | None = None,
                          config: EmbeddingConfig | None = None) -> np.ndarray:
    """Index matrix for `corpus.units` from the verified per-unit store; raises on any gap or failed check."""
    scope = scope_of(corpus)
    if scope not in ENABLED_SCOPES:
        raise UnitCacheNotAllowedError(f"scope {scope} is not enabled for the per-unit cache")
    config = config or local_model_config()
    processed_dir = Path(getattr(corpus, "processed_dir", DEFAULT_CORPUS))
    if processed_dir.resolve() != DEFAULT_CORPUS.resolve():
        release = find_release(processed_dir)
        if release is None:
            raise UnitCacheNotAllowedError(f"no release is registered for the corpus {processed_dir}")
        release_dir, record = release
        store_dir = store_dir or (Path(os.environ[STORE_ENV]) if os.environ.get(STORE_ENV) else release_dir / "store")
        check_release(record, corpus, scope, store_dir, config)
    store_dir = store_dir or Path(os.environ.get(STORE_ENV) or DEFAULT_STORE)
    attestation = attestation or Path(os.environ.get(ATTESTATION_ENV) or DEFAULT_ATTESTATION)
    check_attestation(attestation, config, store_dir)
    store = UnitEmbeddingStore.load(store_dir, config)
    return store.assemble(scope, list(corpus.units))
