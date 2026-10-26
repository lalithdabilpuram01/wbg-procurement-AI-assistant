"""Offline validation of the per-unit embedding cache against the legacy whole-index caches and the Step 6A outputs.

    /opt/anaconda3/bin/python3 -B -m rag.embedding_cache.validate_cache        (from procurement-intelligence/)

No model is loaded and nothing is embedded. Reads pilot_v2, experimental_p1/table_rows.jsonl, the legacy .npy files
(read-only) and the stored benchmark runs. Writes only:
  rag/embedding_cache/experimental_store/   per-unit store imported from the legacy caches (no text)
  rag/embedding_cache/results/validation.json
"""
from __future__ import annotations

import csv
import json
import shutil
import tempfile
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np

from rag.ingestion.sanitize import residual_risk

from . import variants
from .identity import local_model_config
from .legacy import LegacyCacheError, import_legacy, legacy_path, read_legacy_matrix
from .store import CacheIntegrityError, MissingEmbeddingsError, UnitEmbeddingStore, file_sha256, text_sha256

HERE = Path(__file__).resolve().parent
STORE_DIR = HERE / "experimental_store"
RESULTS = HERE / "results"
LEGACY_DIR = variants.core.CACHE
RUNS = variants.ROOT / "rag" / "retrieval" / "runs"
P1_VARIANTS = ("p1_stacked", "p1_full")
BM25_CONFIGS = {"bm25_A": ("A+tables", False), "bm25_C": ("C+tables", False), "bm25_A_guarded": ("A+tables", True)}
UNIT_FIELDS = ("unit_id", "unit_type", "document_id", "project_id", "page_start", "page_end", "section", "table_id",
               "activity_id", "parent_id")


def legacy_fingerprints() -> dict[str, dict[str, Any]]:
    return {p.name: {"sha256": file_sha256(p), "mtime": p.stat().st_mtime, "size": p.stat().st_size}
            for p in sorted(LEGACY_DIR.glob("*.npy"))}


def metadata(unit) -> tuple:
    return tuple(getattr(unit, f) for f in UNIT_FIELDS) + (json.dumps(unit.meta, sort_keys=True, default=str),)


# ------------------------------------------------------------------------------------------------ build + reuse
def build_store(config, corpora: dict) -> tuple[UnitEmbeddingStore, dict]:
    store, report = UnitEmbeddingStore(config), {}
    for scope, (strategy, _) in variants.SCOPES.items():
        units = corpora[(scope, "pilot_v2")].units
        try:
            report[scope] = asdict(import_legacy(store, LEGACY_DIR, strategy, scope, units))
        except LegacyCacheError as error:
            report[scope] = {"imported": 0, "error": str(error)}
    return store, report


def exact_reuse(store: UnitEmbeddingStore, config, corpora: dict) -> dict:
    out = {}
    for scope, (strategy, _) in variants.SCOPES.items():
        units = corpora[(scope, "pilot_v2")].units
        path = legacy_path(LEGACY_DIR, strategy, units)
        if not path.is_file():
            out[scope] = {"legacy_file": None}
            continue
        legacy = read_legacy_matrix(path, len(units), config)
        assembled = store.assemble(scope, units)
        out[scope] = {"legacy_file": path.name, "units": len(units), "byte_identical": bool(np.array_equal(assembled, legacy))
                      and assembled.tobytes() == legacy.tobytes(), "max_abs_diff": float(np.abs(assembled - legacy).max())}
    return out


# ---------------------------------------------------------------------------------------------- Step 6A impact
def variant_impact(store: UnitEmbeddingStore, config, corpora: dict) -> dict:
    out: dict[str, Any] = {}
    for scope, (strategy, _) in variants.SCOPES.items():
        base = corpora[(scope, "pilot_v2")].units
        base_hash = {u.unit_id: text_sha256(u.text) for u in base}
        path = legacy_path(LEGACY_DIR, strategy, base)
        legacy = read_legacy_matrix(path, len(base), config) if path.is_file() else None
        for variant in P1_VARIANTS:
            units = corpora[(scope, variant)].units
            changed = sorted(u.unit_id for u in units if base_hash.get(u.unit_id) not in (None, text_sha256(u.text)))
            plan = store.plan(scope, units)
            index = {u.unit_id: i for i, u in enumerate(base)}
            reused_exact = sum(np.array_equal(store.get(scope, u), legacy[index[u.unit_id]]) for u in units
                               if legacy is not None and store.get(scope, u) is not None)
            try:
                store.assemble(scope, units)
                assemble_refused = False
            except MissingEmbeddingsError:
                assemble_refused = True
            out[f"{scope}|{variant}"] = {
                "units": len(units), "unchanged": len(units) - len(changed), "changed": len(changed), "changed_units": changed,
                "added": sorted({u.unit_id for u in units} - set(base_hash)), "removed": sorted(set(base_hash) - {u.unit_id for u in units}),
                "same_order": [u.unit_id for u in units] == [u.unit_id for u in base],
                "metadata_aligned": all(metadata(a) == metadata(b) for a, b in zip(units, base)),
                "cache_hits": len(plan.hits), "cache_misses": len(plan.misses),
                "misses_equal_changed": sorted(plan.misses) == changed if legacy is not None else None,
                "hits_reuse_legacy_vector_exactly": reused_exact if legacy is not None else None,
                "assemble_refused_while_incomplete": assemble_refused}
    return out


# ------------------------------------------------------------------------------------------- invalidation checks
def config_invalidation(config, corpora: dict) -> dict:
    """Each variation of the configuration must find no reusable entry and must reject the saved store."""
    variations = {"model_revision": replace(config, model_revision="0" * 40), "weights": replace(config, weights_sha256="0" * 64),
                  "remote_code": replace(config, remote_code_revision="0" * 40), "normalize_off": replace(config, normalize=False),
                  "max_seq_length_512": replace(config, max_seq_length=512), "prompt_prefix": replace(config, prompt="query: "),
                  "dimension_1024": replace(config, dimension=1024), "batch_size_32": replace(config, batch_size=32),
                  "torch_upgrade": replace(config, library_versions=tuple((n, "99" if n == "torch" else v) for n, v in config.library_versions))}
    units = corpora[("C+tables", "pilot_v2")].units
    out = {}
    for name, other in variations.items():
        try:
            UnitEmbeddingStore.load(STORE_DIR, other)
            rejected = False
        except CacheIntegrityError:
            rejected = True
        out[name] = {"store_rejected": rejected, "fingerprint_differs": other.fingerprint() != config.fingerprint(),
                     "hits_with_fresh_store": len(UnitEmbeddingStore(other).plan("C+tables", units).hits)}
    return out


def corruption_checks(config) -> dict:
    """Copies of the saved store, each damaged one way, must be rejected."""
    damages = {
        "manifest_missing": lambda d: (d / "manifest.json").unlink(),
        "vectors_missing": lambda d: (d / "vectors.npy").unlink(),
        "vector_byte_flipped": lambda d: _flip_last_byte(d / "vectors.npy"),
        "entries_truncated": lambda d: (d / "entries.jsonl").write_text((d / "entries.jsonl").read_text().split("\n", 1)[1]),
        "manifest_not_json": lambda d: (d / "manifest.json").write_text("{"),
    }
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, damage in damages.items():
            copy = Path(tmp) / name
            shutil.copytree(STORE_DIR, copy)
            damage(copy)
            try:
                UnitEmbeddingStore.load(copy, config)
                out[name] = "accepted"
            except CacheIntegrityError as error:
                out[name] = f"rejected: {error}"
    return out


def _flip_last_byte(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[-1] ^= 0xFF
    path.write_bytes(bytes(data))


# ------------------------------------------------------------------------------------------- sanitisation checks
def sanitisation_checks(corpora: dict) -> dict:
    texts = {u.unit_id: u.text for c in corpora.values() for u in c.units}
    stored = (STORE_DIR / "entries.jsonl").read_text() + (STORE_DIR / "manifest.json").read_text()
    samples = [t.split("\n", 1)[-1][:40] for t in texts.values() if len(t.split("\n", 1)[-1]) >= 40]
    risk = Counter()
    for text in texts.values():
        risk.update({k: v for k, v in residual_risk(text).items() if v})
    entry_fields = {k for line in (STORE_DIR / "entries.jsonl").read_text().splitlines() for k in json.loads(line)}
    return {"entry_fields": sorted(entry_fields), "text_snippets_found_in_store": sum(s in stored for s in samples),
            "snippets_checked": len(samples), "residual_risk_in_indexed_texts": dict(risk),
            "sources_read": "pilot_v2 shared/ and strategies/ via core.jl; experimental_p1/table_rows.jsonl (sanitised); never restricted/"}


# -------------------------------------------------------------------------------------------- retrieval checks
BASELINE_RUN = "20261025T164420Z"           # the whole-index benchmark run all comparisons refer to


def latest_run() -> Path:
    return RUNS / BASELINE_RUN


def bm25_results(corpus, questions: list[dict]) -> dict[tuple[str, bool], dict[str, Any]]:
    import evaluate  # noqa: PLC0415  (rag/retrieval/evaluate.py: read-only use of relevant())
    retriever, out = variants.core.Retriever(corpus), {}
    for guarded in (False, True):
        for q in questions:
            hits = retriever.search(q["question"], method="bm25", k=10, guarded=guarded)["results"]
            first = next((i + 1 for i, r in enumerate(hits) if any(evaluate.relevant(r, g) for g in q["gold"])), None)
            out[(q["id"], guarded)] = {"top1": hits[0]["unit_id"] if hits else None, "first": first,
                                       "top10": [r["unit_id"] for r in hits]}
    return out


def retrieval_comparison(corpora: dict) -> dict:
    bench = json.loads((variants.ROOT / "rag" / "retrieval" / "benchmark_v1.json").read_text())
    questions = bench["questions"]
    run = latest_run()
    with (run / "per_question.csv").open() as handle:
        stored = {(r["config"], r["qid"]): r for r in csv.DictReader(handle)}
    runs = {(scope, variant): bm25_results(corpora[(scope, variant)], questions)
            for scope in ("A+tables", "C+tables") for variant in ("pilot_v2",) + P1_VARIANTS}
    out: dict[str, Any] = {"stored_run": run.name, "reproduction": {}, "p1_effect": {}}
    for name, (scope, guarded) in BM25_CONFIGS.items():
        now = runs[(scope, "pilot_v2")]
        same = sum(now[(q["id"], guarded)]["top1"] == (stored[(name, q["id"])]["top1"] or None)
                   and (now[(q["id"], guarded)]["first"] or "") == (int(float(stored[(name, q["id"])]["first_relevant_rank"]))
                                                                  if stored[(name, q["id"])]["first_relevant_rank"] else "")
                   for q in questions)
        out["reproduction"][name] = f"{same}/{len(questions)} questions identical (top-1 unit and first relevant rank)"
        for variant in P1_VARIANTS:
            new = runs[(scope, variant)]
            answerable = [q for q in questions if q["gold"]]
            recall = lambda r, k: sum(bool(r[(q["id"], guarded)]["first"]) and r[(q["id"], guarded)]["first"] <= k for q in answerable)  # noqa: E731
            out["p1_effect"][f"{name}|{variant}"] = {
                "questions_with_changed_top10": sum(now[(q["id"], guarded)]["top10"] != new[(q["id"], guarded)]["top10"] for q in questions),
                "questions_with_changed_first_relevant_rank": sum(now[(q["id"], guarded)]["first"] != new[(q["id"], guarded)]["first"] for q in questions),
                "recall@5": f"{recall(now, 5)}/{len(answerable)} -> {recall(new, 5)}/{len(answerable)}",
                "recall@10": f"{recall(now, 10)}/{len(answerable)} -> {recall(new, 10)}/{len(answerable)}"}
    return out


# --------------------------------------------------------------------------------------------------------- run
def run() -> dict[str, Any]:
    before = legacy_fingerprints()
    config = local_model_config()
    corpora = {(scope, variant): variants.corpus(scope, variant) for scope in variants.SCOPES for variant in ("pilot_v2",) + P1_VARIANTS}
    store, imports = build_store(config, corpora)
    manifest = store.save(STORE_DIR)
    store = UnitEmbeddingStore.load(STORE_DIR, config)                         # everything below uses the reloaded store
    b_units = corpora[("B+tables", "pilot_v2")].units
    other = {(u.unit_id, text_sha256(u.text)) for s in ("A+tables", "C+tables") for u in corpora[(s, "pilot_v2")].units}
    result = {
        "config": config.as_dict(), "config_fingerprint": config.fingerprint(),
        "store": {"directory": str(STORE_DIR.relative_to(variants.ROOT)), "entries": manifest["entries"],
                  "vectors_sha256": manifest["vectors_sha256"]},
        "legacy_import": imports,
        "exact_reuse_pilot_v2": exact_reuse(store, config, corpora),
        "step6a_impact": variant_impact(store, config, corpora),
        "strategy_B_cross_scope_candidates": sum((u.unit_id, text_sha256(u.text)) in other for u in b_units),
        "config_invalidation": config_invalidation(config, corpora),
        "corruption": corruption_checks(config),
        "sanitisation": sanitisation_checks(corpora),
        "retrieval_comparison": retrieval_comparison(corpora),
    }
    after = legacy_fingerprints()
    result["legacy_files_unchanged"] = {"before": before, "identical_after": before == after}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "validation.json").write_text(json.dumps(result, indent=1, default=str))
    return result


if __name__ == "__main__":
    summary = run()
    print(json.dumps({k: summary[k] for k in ("legacy_import", "exact_reuse_pilot_v2", "legacy_files_unchanged")}, indent=1, default=str))
