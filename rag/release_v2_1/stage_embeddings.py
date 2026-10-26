"""Stage the incremental embeddings for pilot_v2_1 in a COPY of the per-unit store (Step 6D).

    /opt/anaconda3/bin/python3 -B -m rag.release_v2_1.stage_embeddings      (from procurement-intelligence/)

1. Copies rag/embedding_cache/experimental_store -> rag/embedding_cache/staging/pilot_v2_1_store (once; verified).
2. Per enabled scope (C, A, A without tables): dry-run manifest from the FINAL pilot_v2_1 units (written to
   results/), then `embed_missing` with the local gte-base model for exactly the listed units. Strategy B: refused.
3. Verifies: idempotent rerun (re-encodes, must be a no-op), store integrity, vector-to-unit binding, dimension,
   finiteness, norm, configuration, unchanged vectors byte-identical to the legacy whole-index rows, pilot_v2 still
   reassembles byte-identically (rollback), and that the legacy files and the experimental store are untouched.
Network sockets are blocked throughout. Writes results/staging.json. The default store is never written.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from rag.embedding_cache import adapter, variants
from rag.embedding_cache.encoder import LocalModelEncoder
from rag.embedding_cache.identity import local_model_config
from rag.embedding_cache.legacy import legacy_path, read_legacy_matrix
from rag.embedding_cache.store import UnitEmbeddingStore, file_sha256, text_sha256, vector_sha256
from rag.embedding_cache.verify_compatibility import no_network
from rag.embedding_cache.writer import WriteRejectedError, dry_run, embed_missing, write_manifest

RESULTS = Path(__file__).resolve().parent / "results"
STAGING = adapter.HERE / "staging" / "pilot_v2_1_store"
SOURCE = adapter.DEFAULT_STORE
SCOPES = ("C+tables", "A+tables", "A-tables")
FILE_NAMES = {"C+tables": "C_with_tables", "A+tables": "A_with_tables", "A-tables": "A_without_tables"}
STEP6A_MANIFESTS = {"C+tables": adapter.HERE / "results" / "dry_run_manifest_C_p1_stacked.json",
                    "A+tables": adapter.HERE / "results" / "dry_run_manifest_A_p1_stacked.json"}


def prepare_staging() -> dict[str, Any]:
    if not STAGING.exists():
        STAGING.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SOURCE, STAGING)
        copied = all(file_sha256(STAGING / f) == file_sha256(SOURCE / f) for f in ("manifest.json", "entries.jsonl", "vectors.npy"))
        if not copied:
            raise RuntimeError("staging copy differs from the source store")
    return {"source_manifest_sha256": file_sha256(SOURCE / "manifest.json"), "staging": str(STAGING.relative_to(variants.ROOT))}


def stage_scope(scope: str, config, encoder) -> dict[str, Any]:
    units = variants.corpus(scope, "pilot_v2_1").units
    manifest = dry_run(STAGING, config, scope, units, f"pilot-v2-1.{scope.replace('+', '_')}")
    write_manifest(manifest, RESULTS / f"regeneration_manifest_{FILE_NAMES[scope]}.json")
    step6a = json.loads(STEP6A_MANIFESTS[scope].read_text()) if scope in STEP6A_MANIFESTS else None
    same_as_6a = step6a is not None and [(g["unit_id"], g["text_sha256"]) for g in step6a["to_generate"]] == \
        [(g["unit_id"], g["text_sha256"]) for g in manifest["to_generate"]]
    report = embed_missing(STAGING, config, manifest, units, encoder, manifest["label"]) if manifest["to_generate"] else {"status": "nothing_to_generate"}
    rerun = embed_missing(STAGING, config, manifest, units, encoder, manifest["label"]) if manifest["to_generate"] else report
    after = dry_run(STAGING, config, scope, units, manifest["label"])
    return {"units": len(units), "to_generate": manifest["units_to_generate"], "reused": manifest["units_reused"],
            "manifest_id": manifest["manifest_id"], "same_units_and_text_as_step6a_dry_run": same_as_6a if step6a else None,
            "apply": report, "idempotent_rerun": rerun.get("status"), "missing_after": after["units_to_generate"],
            "generated": [g["unit_id"] for g in manifest["to_generate"]]}


def verify(config, encoder) -> dict[str, Any]:
    store = UnitEmbeddingStore.load(STAGING, config)                          # full integrity check
    generated = [e for e in store.entries() if e["provenance"].startswith("generated:")]
    out: dict[str, Any] = {"entries": len(store), "generated_entries": len(generated), "scopes": {}}
    texts = {(scope, u.unit_id): u.text for scope in SCOPES for u in variants.corpus(scope, "pilot_v2_1").units}
    alone = encoder.encode([texts[(e["scope"], e["unit_id"])] for e in generated]) if generated else np.zeros((0, config.dimension))
    vectors = []
    for e, fresh in zip(generated, alone):
        unit = type("U", (), {"unit_id": e["unit_id"], "text": texts[(e["scope"], e["unit_id"])]})
        v = store.get(e["scope"], unit)
        vectors.append({"scope": e["scope"], "unit_id": e["unit_id"], "text_sha256": e["text_sha256"],
                        "text_hash_matches_pilot_v2_1": e["text_sha256"] == text_sha256(unit.text), "vector_sha256": e["vector_sha256"],
                        "dimension": int(v.shape[0]), "dtype": str(v.dtype), "finite": bool(np.isfinite(v).all()),
                        "norm": float(np.linalg.norm(v)), "provenance": e["provenance"],
                        "re_encoded_alone_cosine": float(np.dot(v, fresh) / (np.linalg.norm(v) * np.linalg.norm(fresh))),
                        "re_encoded_alone_max_abs_diff": float(np.abs(v - fresh).max())})
    out["generated"] = vectors
    for scope in SCOPES:
        strategy = variants.SCOPES[scope][0]
        base_units = variants.corpus(scope, "pilot_v2").units
        new_units = variants.corpus(scope, "pilot_v2_1").units
        legacy = read_legacy_matrix(legacy_path(variants.core.CACHE, strategy, base_units), len(base_units), config)
        new_matrix = store.assemble(scope, new_units)                    # staging content (the release gate is checked in 6E)
        old_matrix = store.assemble(scope, base_units)
        changed = {i for i, (a, b) in enumerate(zip(base_units, new_units)) if a.text != b.text}
        out["scopes"][scope] = {
            "units": len(new_units), "changed": len(changed),
            "unchanged_rows_byte_identical_to_legacy": all(new_matrix[i].tobytes() == legacy[i].tobytes()
                                                           for i in range(len(new_units)) if i not in changed),
            "changed_rows_differ_from_legacy": all(new_matrix[i].tobytes() != legacy[i].tobytes() for i in changed),
            "pilot_v2_reassembles_byte_identical": old_matrix.tobytes() == legacy.tobytes(),
            "adapter_shape": list(new_matrix.shape), "adapter_dtype": str(new_matrix.dtype),
            "order_aligned": [u.unit_id for u in new_units] == [u.unit_id for u in base_units]}
    try:
        dry_run(STAGING, config, "B+tables", variants.corpus("B+tables", "pilot_v2_1").units, "pilot-v2-1.B")
        out["strategy_b"] = "NOT REFUSED"
    except WriteRejectedError:
        out["strategy_b"] = "refused"
    return out


def run() -> dict[str, Any]:
    legacy_before = {p.name: file_sha256(p) for p in sorted(variants.core.CACHE.glob("*.npy"))}
    source_before = file_sha256(SOURCE / "manifest.json")
    config = local_model_config()
    with no_network() as attempts:
        staging = prepare_staging()
        encoder = LocalModelEncoder(config)
        scopes = {scope: stage_scope(scope, config, encoder) for scope in SCOPES}
        checks = verify(config, encoder)
    result = {"config_fingerprint": config.fingerprint(), "model_revision": config.model_revision, "weights_sha256": config.weights_sha256,
              "staging": staging, "scopes": scopes, "verification": checks,
              "staging_store": {f: file_sha256(STAGING / f) for f in ("manifest.json", "entries.jsonl", "vectors.npy")},
              "legacy_files_unchanged": legacy_before == {p.name: file_sha256(p) for p in sorted(variants.core.CACHE.glob("*.npy"))},
              "source_store_unchanged": source_before == file_sha256(SOURCE / "manifest.json"),
              "network_attempts_blocked": len(attempts)}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "staging.json").write_text(json.dumps(result, indent=1, default=str))
    return result


if __name__ == "__main__":
    out = run()
    print(json.dumps({k: v for k, v in out.items() if k != "verification"} | {"verification": {k: v for k, v in out["verification"].items()
                                                                                                  if k != "generated"}}, indent=1, default=str))
    for g in out["verification"]["generated"]:
        print(g["scope"], g["unit_id"], g["vector_sha256"][:16], round(g["norm"], 6), g["dimension"], g["finite"],
              round(g["re_encoded_alone_cosine"], 8), f"{g['re_encoded_alone_max_abs_diff']:.1e}", g["text_hash_matches_pilot_v2_1"])
