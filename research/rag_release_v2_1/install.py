"""Install the validated pilot_v2_1 per-unit store at a stable, versioned location and register the release (Step 6E).

    /opt/anaconda3/bin/python3 -B -m rag.release_v2_1.install           (from procurement-intelligence/)

Copies rag/embedding_cache/staging/pilot_v2_1_store -> rag/embedding_cache/releases/pilot_v2_1/store (never over a
different existing release; an identical one is left as is), verifies the copy against the hashes recorded in Step 6D,
and writes releases/pilot_v2_1/release.json binding together: the corpus (path, run-manifest SHA-256, extraction
version), the exact indexed-text fingerprint of each enabled scope, the store file hashes, the embedding configuration
and the validation evidence. Only strategy C (with tables) is enabled. No model is loaded; nothing else is written.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from rag.embedding_cache import adapter
from rag.embedding_cache.identity import local_model_config
from rag.embedding_cache.store import UnitEmbeddingStore, file_sha256
from rag.ingestion import config as ingestion_config

from .stage_embeddings import RESULTS, STAGING

RELEASE = "pilot_v2_1"
RELEASE_DIR = adapter.RELEASES / RELEASE
ENABLED_SCOPES = ("C+tables",)
EVIDENCE = ("staging.json", "evaluation.json", "corpus_verification.json", "build.json")


class InstallError(RuntimeError):
    """The release cannot be installed safely."""


def _store_hashes(directory: Path) -> dict[str, str]:
    return {f: file_sha256(directory / f) for f in adapter.STORE_FILES}


def install_store() -> dict[str, str]:
    staged = json.loads((RESULTS / "staging.json").read_text())["staging_store"]
    if _store_hashes(STAGING) != staged:
        raise InstallError("staging store no longer matches the Step 6D validated hashes")
    target = RELEASE_DIR / "store"
    if target.exists():
        if _store_hashes(target) != staged:
            raise InstallError(f"{target} exists with different content; refusing to overwrite")
        return staged
    tmp = RELEASE_DIR / f".store.tmp-{os.getpid()}"
    try:
        shutil.copytree(STAGING, tmp, ignore=shutil.ignore_patterns(".*"))
        if _store_hashes(tmp) != staged:
            raise InstallError("copy does not match the staging store")
        os.replace(tmp, target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return staged


def descriptor(store_hashes: dict[str, str]) -> dict[str, Any]:
    from rag.retrieval import core
    corpus_dir = ingestion_config.OUTPUT_V2_1
    manifest = json.loads((corpus_dir / "run_manifest.json").read_text())
    texts = {}
    for scope in ENABLED_SCOPES:
        strategy, tables = scope[0], scope[1] == "+"
        texts[scope] = adapter.indexed_text_fingerprint(core.Corpus(strategy, include_tables=tables, processed_dir=corpus_dir).units)
    config = local_model_config()
    return {"release": RELEASE, "schema": "rag-release-1",
            "corpus": {"processed_dir": str(corpus_dir.relative_to(ingestion_config.REPO)),
                       "run_manifest_sha256": file_sha256(corpus_dir / "run_manifest.json"),
                       "extraction_version": manifest["extraction_version"], "code_hash": manifest["code_hash"]},
            "enabled_scopes": list(ENABLED_SCOPES), "indexed_text_sha256": texts,
            "store": {**store_hashes, "path": "store", "source": "rag/embedding_cache/staging/pilot_v2_1_store (Step 6D)"},
            "config_fingerprint": config.fingerprint(), "model": {"id": config.model_id, "revision": config.model_revision,
                                                                  "weights_sha256": config.weights_sha256},
            "validation_evidence": {f"rag/release_v2_1/results/{name}": file_sha256(RESULTS / name) for name in EVIDENCE}}


def run() -> dict[str, Any]:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    hashes = install_store()
    record = descriptor(hashes)
    store = UnitEmbeddingStore.load(RELEASE_DIR / "store", local_model_config())          # full integrity check
    path = RELEASE_DIR / "release.json"
    if path.exists() and json.loads(path.read_text()) != record:
        raise InstallError("a different release.json is already registered; refusing to overwrite")
    tmp = path.with_name(f".release.json.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(record, indent=1, sort_keys=True))
    os.replace(tmp, path)
    return {"release_dir": str(RELEASE_DIR.relative_to(ingestion_config.REPO)), "entries": len(store), "release": record}


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
