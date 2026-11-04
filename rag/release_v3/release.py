"""Build, embed and register the `pilot_v3` release (Milestone D): the approved 35-project scope, 237 documents.

    /opt/anaconda3/bin/python3 -B -m rag.release_v3.release build      (from procurement-intelligence/)
    /opt/anaconda3/bin/python3 -B -m rag.release_v3.release embed
    /opt/anaconda3/bin/python3 -B -m rag.release_v3.release install

build:
  `pipeline_v2.run(profile="3.0.0")`, i.e. the validated 2.1.0 extraction over CORPUS_MANIFEST_V3_35.csv, writing
  rag/corpus_v3/processed/pilot_v3/. pilot_v2 (the default) and pilot_v2_1 are not written.
embed:
  A NEW per-unit store, releases/pilot_v3/store, for strategy C with tables.
  - A dry-run manifest lists exactly the units to encode.
  - The local gte-base model encodes them with network sockets blocked.
  - A second dry run must report nothing missing.
install:
  Writes releases/pilot_v3/release.json, which the retriever checks at every load. It binds together:
  - the corpus run manifest;
  - the indexed-text fingerprint;
  - the store file hashes;
  - the model configuration;
  - this step's evidence.
  It then builds the retriever once as a check.
Evidence files go to rag/release_v3/results/.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from rag.embedding_cache import adapter
from rag.embedding_cache.encoder import LocalModelEncoder
from rag.embedding_cache.identity import local_model_config
from rag.embedding_cache.store import UnitEmbeddingStore, file_sha256
from rag.embedding_cache.verify_compatibility import no_network
from rag.embedding_cache.writer import dry_run, embed_missing, write_manifest
from rag.ingestion import config as ingestion_config, pipeline_v2

RELEASE = "pilot_v3"
SCOPE = "C+tables"
RELEASE_DIR = adapter.RELEASES / RELEASE
STORE = RELEASE_DIR / "store"
RESULTS = Path(__file__).resolve().parent / "results"
EXPECTED_TOKENIZER = "tiktoken:cl100k_base"
EVIDENCE = ("build.json", "embed.json")


def corpus():
    from rag.retrieval import core
    return core.Corpus("C", include_tables=True, processed_dir=ingestion_config.OUTPUT_V3)


def build() -> dict[str, Any]:
    """Run the 3.0.0 extraction into pilot_v3 with the network blocked; raises on an unexpected tokenizer."""
    with no_network():                                                         # cached tokenizer and local PDFs only
        manifest = pipeline_v2.run(profile="3.0.0")
    statuses = manifest["document_status"]
    failed = {d: s for d, s in statuses.items() if s not in ("processed", "cached")}
    result = {"directory": str(ingestion_config.OUTPUT_V3.relative_to(ingestion_config.REPO)),
              "extraction_version": manifest["extraction_version"], "tokenizer": manifest["tokenizer"],
              "documents": len(statuses), "processed_or_cached": len(statuses) - len(failed), "failed": failed,
              "contracts_snapshot": manifest["contracts_snapshot"],
              "outputs": {k: v["rows"] for k, v in manifest["outputs"].items() if not k.startswith("restricted/")}}
    if manifest["tokenizer"] != EXPECTED_TOKENIZER:
        raise RuntimeError(f"unexpected tokenizer {manifest['tokenizer']}")
    return result


def embed() -> dict[str, Any]:
    """Encode the release's missing units into its own store (network blocked) and report what was generated."""
    config = local_model_config()
    if not STORE.exists():
        UnitEmbeddingStore(config).save(STORE)                                # a new, empty store for this release only
    units = corpus().units
    with no_network() as attempts:
        encoder = LocalModelEncoder(config)
        manifest = dry_run(STORE, config, SCOPE, units, "pilot-v3.C_tables")
        write_manifest(manifest, RESULTS / "embedding_manifest.json")
        report = embed_missing(STORE, config, manifest, units, encoder, manifest["label"]) if manifest["to_generate"] \
            else {"status": "nothing_to_generate"}
        after = dry_run(STORE, config, SCOPE, units, manifest["label"])
    return {"units": len(units), "generated": manifest["units_to_generate"], "reused": manifest["units_reused"],
            "apply_status": report.get("status"), "missing_after": after["units_to_generate"],
            "network_attempts": len(attempts), "store_entries": len(UnitEmbeddingStore.load(STORE, config))}


def install() -> dict[str, Any]:
    """Write releases/pilot_v3/release.json binding corpus, indexed text, store and model, then build the retriever
    once as a check."""
    config = local_model_config()
    UnitEmbeddingStore.load(STORE, config)                                    # full integrity check
    corpus_dir = ingestion_config.OUTPUT_V3
    manifest = json.loads((corpus_dir / "run_manifest.json").read_text())
    descriptor = {
        "release": RELEASE, "schema": "rag-release-1",
        "corpus": {"processed_dir": str(corpus_dir.relative_to(ingestion_config.REPO)),
                   "run_manifest_sha256": file_sha256(corpus_dir / "run_manifest.json"),
                   "extraction_version": manifest["extraction_version"], "code_hash": manifest["code_hash"]},
        "enabled_scopes": [SCOPE], "indexed_text_sha256": {SCOPE: adapter.indexed_text_fingerprint(corpus().units)},
        "store": {**{f: file_sha256(STORE / f) for f in ("entries.jsonl", "manifest.json", "vectors.npy")}, "path": "store",
                  "source": "rag/release_v3/release.py embed (new store)"},
        "config_fingerprint": config.fingerprint(),
        "model": {"id": config.model_id, "revision": config.model_revision, "weights_sha256": config.weights_sha256},
        "validation_evidence": {f"rag/release_v3/results/{name}": file_sha256(RESULTS / name) for name in EVIDENCE}}
    (RELEASE_DIR / "release.json").write_text(json.dumps(descriptor, indent=1, sort_keys=True))
    from rag.retrieval.versions import build_retriever
    retriever = build_retriever(RELEASE)                                      # loads and verifies against the descriptor
    return {"release_json": str((RELEASE_DIR / "release.json").relative_to(ingestion_config.REPO)),
            "verified_retriever_units": len(retriever.c.units), "matrix_rows": int(retriever.embeddings().shape[0])}


def main(step: str) -> None:
    """Run one step (build, embed or install) and save its result as evidence in results/<step>.json."""
    RESULTS.mkdir(exist_ok=True)
    result = {"build": build, "embed": embed, "install": install}[step]()
    (RESULTS / f"{step}.json").write_text(json.dumps(result, indent=1, sort_keys=True, default=str))
    print(json.dumps(result, indent=1, default=str)[:3000])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
