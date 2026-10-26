"""Local model-compatibility check of the legacy vectors before they may be served from the per-unit cache (Step 6C).

    /opt/anaconda3/bin/python3 -B -m rag.embedding_cache.verify_compatibility       (from procurement-intelligence/)

Re-encodes a reproducible sample of ~32 retrieval units of strategies A and C with the local model files only
(Hugging Face offline mode and every outbound socket blocked) and compares each new vector with its legacy vector.
To reproduce the original inference, each sampled unit is re-encoded inside its ORIGINAL batch: the 16 texts that
sentence-transformers put together when the whole index was encoded (its own length ordering over all units of the
strategy, batch_size=16). The same units are also encoded alone, to measure the batch effect. Negative controls
(prompt prefix, 64-token truncation, no normalisation, another unit) show what a real incompatibility looks like.

Nothing is added to any cache: only checksums and metrics are written, to results/compatibility.json, which the
opt-in adapter requires. Thresholds are fixed before the run (below).
"""
from __future__ import annotations

import hashlib
import json
import socket
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from . import variants
from .identity import local_model_config
from .legacy import legacy_path, read_legacy_matrix
from .store import file_sha256, text_sha256, vector_sha256

RESULTS = Path(__file__).resolve().parent / "results" / "compatibility.json"
SCOPES = {"A+tables": "A", "C+tables": "C", "A-tables": "A"}      # index scope -> strategy (legacy file)
BATCH_SIZE = 16
# Pass thresholds for re-encoding in the original batch (float32 rounding level); a different model, pooling, prompt
# or truncation moves cosine far below these (see the negative controls).
SAME_BATCH_MAX_ABS = 1e-5
SAME_BATCH_MIN_COSINE = 0.99999
INCOMPATIBLE_BELOW_COSINE = 0.999


class NetworkBlockedError(RuntimeError):
    """An outbound connection was attempted during the offline verification."""


@contextmanager
def no_network() -> Iterator[list[str]]:
    attempts: list[str] = []
    original_connect, original_create = socket.socket.connect, socket.create_connection

    def refuse(*args, **kwargs):
        attempts.append(repr(args[1:] if len(args) > 1 else args))
        raise NetworkBlockedError("network access is blocked during compatibility verification")

    socket.socket.connect, socket.create_connection = refuse, refuse
    try:
        yield attempts
    finally:
        socket.socket.connect, socket.create_connection = original_connect, original_create


def load_model():
    from sentence_transformers import SentenceTransformer
    return variants.core.load_cached_model(SentenceTransformer, variants.core.EMBED_MODEL, trust_remote_code=True)


def original_order(model, texts: list[str]) -> np.ndarray:
    """The order in which sentence-transformers' encode() batches these texts (its own private rules)."""
    order = np.argsort([-model._input_length(t) for t in texts])
    if model._can_flatten_inputs():
        order = model._interleave_sorted_indices(order)
    return np.asarray(order)


# ------------------------------------------------------------------------------------------------------ sample
def _by_length(units) -> list:
    return sorted(units, key=lambda u: (len(u.text), u.unit_id))


def _scope_picks(scope: str, corpus, order: np.ndarray, full: bool) -> list[tuple[str, str, str]]:
    """Rule-based picks for one index scope; `full` adds the per-document-type and table/edge-length rules."""
    units = corpus.units
    chunks = _by_length([u for u in units if u.unit_type == "chunk"])
    tables = _by_length([u for u in units if u.unit_type == "table"])
    acts = _by_length([u for u in units if u.unit_type == "activity"])
    picks = [(scope, chunks[len(chunks) // 2].unit_id, "median chunk"), (scope, acts[len(acts) // 2].unit_id, "median activity"),
             (scope, units[order[0]].unit_id, "first in batch order"), (scope, units[order[-1]].unit_id, "last in batch order (final batch)")]
    if not full:
        return picks
    for doc_type in sorted({corpus.docs[u.document_id]["doc_type"] for u in chunks}):
        group = [u for u in chunks if corpus.docs[u.document_id]["doc_type"] == doc_type]
        picks.append((scope, group[len(group) // 2].unit_id, f"chunk median length {doc_type}"))
    picks += [(scope, chunks[-1].unit_id, "longest chunk"), (scope, chunks[0].unit_id, "shortest chunk"),
              (scope, tables[-1].unit_id, "longest table window"), (scope, tables[0].unit_id, "shortest table window"),
              (scope, tables[len(tables) // 2].unit_id, "median table window")]
    arbitrary = sorted(units, key=lambda u: hashlib.sha256(u.unit_id.encode()).hexdigest())[:2]
    return picks + [(scope, u.unit_id, "arbitrary (lowest SHA-256 of unit ID)") for u in arbitrary]


def select_sample(corpora: dict, legacy: dict, orders: dict) -> list[dict[str, Any]]:
    """Deterministic rule-based sample. A and C (with tables): chunks per document type (median length), the longest and
    shortest chunk and table window, a median table window, chunk and activity, the first and last unit in batch order,
    2 arbitrary units; 3 units shared by A and C whose two legacy vectors differ (checked in both). A without tables
    (its own legacy file): median chunk and activity, first and last in batch order."""
    picks = [p for scope in SCOPES for p in _scope_picks(scope, corpora[scope], orders[scope], full=scope != "A-tables")]
    a_units, c_units = corpora["A+tables"].units, corpora["C+tables"].units
    a_index = {u.unit_id: i for i, u in enumerate(a_units)}
    shared = [(float(np.abs(legacy["C+tables"][j] - legacy["A+tables"][a_index[u.unit_id]]).max()), u.unit_id)
              for j, u in enumerate(c_units) if u.unit_id in a_index and u.text == a_units[a_index[u.unit_id]].text]
    differing = sorted(d for d in shared if d[0] > 0)
    for diff, unit_id in (differing[0], differing[len(differing) // 2], differing[-1]):
        picks += [(scope, unit_id, f"shared A/C, legacy A-C diff {diff:.1e}") for scope in ("A+tables", "C+tables")]
    sample: dict[tuple[str, str], dict[str, Any]] = {}
    for scope, unit_id, reason in picks:
        item = sample.setdefault((scope, unit_id), {"scope": scope, "strategy": SCOPES[scope], "unit_id": unit_id, "reasons": []})
        item["reasons"].append(reason)
    return list(sample.values())


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def compare(new: np.ndarray, old: np.ndarray) -> dict[str, Any]:
    return {"max_abs_diff": float(np.abs(new - old).max()), "cosine": cosine(new, old), "bit_identical": new.tobytes() == old.tobytes()}


# ------------------------------------------------------------------------------------------------------ run
def run() -> dict[str, Any]:
    config = local_model_config()
    corpora = {scope: variants.corpus(scope, "pilot_v2") for scope in SCOPES}
    legacy_files = {scope: legacy_path(variants.core.CACHE, s, corpora[scope].units) for scope, s in SCOPES.items()}
    legacy = {scope: read_legacy_matrix(path, len(corpora[scope].units), config) for scope, path in legacy_files.items()}
    started = time.perf_counter()
    with no_network() as attempts:
        model = load_model()
        dimension = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
        settings = {"max_seq_length": model.max_seq_length, "dimension": dimension(),
                    "modules": [type(m).__name__ for m in model], "pooling": getattr(model[1], "pooling_mode", None)
                    or model[1].get_pooling_mode_str(),
                    "device": str(model.device), "can_flatten_inputs": bool(model._can_flatten_inputs())}
        orders = {scope: original_order(model, [u.text for u in corpora[scope].units]) for scope in SCOPES}
        sample = select_sample(corpora, legacy, orders)
        records = [encode_and_compare(model, corpora, legacy, orders, item) for item in sample]
        controls = negative_controls(model, corpora, legacy, sample)
    elapsed = time.perf_counter() - started
    result = summarise(config, settings, legacy_files, records, controls, attempts, elapsed)
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(result, indent=1))
    return result


_batch_cache: dict[tuple[str, int], np.ndarray] = {}


def encode_and_compare(model, corpora, legacy, orders, item) -> dict[str, Any]:
    scope = item["scope"]
    units = corpora[scope].units
    index = next(i for i, u in enumerate(units) if u.unit_id == item["unit_id"])
    position = int(np.where(orders[scope] == index)[0][0])
    batch, slot = divmod(position, BATCH_SIZE)
    members = orders[scope][batch * BATCH_SIZE:(batch + 1) * BATCH_SIZE]
    if (scope, batch) not in _batch_cache:                     # the original batch, members in their original order
        _batch_cache[(scope, batch)] = model.encode([units[i].text for i in members], batch_size=BATCH_SIZE,
                                                    normalize_embeddings=True, show_progress_bar=False)
    same_batch = _batch_cache[(scope, batch)][list(members).index(index)]
    alone = model.encode([units[index].text], batch_size=BATCH_SIZE, normalize_embeddings=True, show_progress_bar=False)[0]
    old = legacy[scope][index]
    unit = units[index]
    return {**item, "unit_type": unit.unit_type, "doc_type": corpora[scope].docs[unit.document_id]["doc_type"],
            "text_chars": len(unit.text), "text_sha256": text_sha256(unit.text), "batch_index": batch, "slot_in_batch": slot,
            "batch_members": len(members), "legacy_vector_sha256": vector_sha256(old), "legacy_norm": float(np.linalg.norm(old)),
            "same_batch": {**compare(same_batch, old), "vector_sha256": vector_sha256(same_batch), "norm": float(np.linalg.norm(same_batch))},
            "alone": {**compare(alone, old), "vector_sha256": vector_sha256(alone)}}


def negative_controls(model, corpora, legacy, sample) -> dict[str, Any]:
    """What an incompatible configuration looks like for 8 of the sampled units (encoded alone)."""
    chosen = sample[:8]
    texts, olds = [], []
    for item in chosen:
        scope = item["scope"]
        index = next(i for i, u in enumerate(corpora[scope].units) if u.unit_id == item["unit_id"])
        texts.append(corpora[scope].units[index].text)
        olds.append(legacy[scope][index])
    olds = np.stack(olds)
    out = {}
    prompt = model.encode(texts, batch_size=BATCH_SIZE, normalize_embeddings=True, prompt="query: ", show_progress_bar=False)
    out["prompt_prefix"] = [cosine(a, b) for a, b in zip(prompt, olds)]
    original_length = model.max_seq_length
    try:
        model.max_seq_length = 64
        short = model.encode(texts, batch_size=BATCH_SIZE, normalize_embeddings=True, show_progress_bar=False)
    finally:
        model.max_seq_length = original_length
    out["truncated_64_tokens"] = [cosine(a, b) for a, b in zip(short, olds)]
    raw = model.encode(texts, batch_size=BATCH_SIZE, normalize_embeddings=False, show_progress_bar=False)
    out["no_normalisation_norms"] = [float(np.linalg.norm(v)) for v in raw]
    out["another_unit"] = [cosine(prompt_free, olds[(i + 1) % len(olds)])
                           for i, prompt_free in enumerate(model.encode(texts, batch_size=BATCH_SIZE, normalize_embeddings=True, show_progress_bar=False))]
    return {name: {"min": min(values), "max": max(values)} for name, values in out.items()}


def summarise(config, settings, legacy_files, records, controls, attempts, elapsed) -> dict[str, Any]:
    same = [r["same_batch"] for r in records]
    alone = [r["alone"] for r in records]
    passes = all(s["max_abs_diff"] <= SAME_BATCH_MAX_ABS and s["cosine"] >= SAME_BATCH_MIN_COSINE for s in same)
    settings_ok = settings["max_seq_length"] == config.max_seq_length and settings["dimension"] == config.dimension
    by = lambda key: {k: {"units": len(g), "bit_identical": sum(r["same_batch"]["bit_identical"] for r in g),  # noqa: E731
                          "max_abs_diff": max(r["same_batch"]["max_abs_diff"] for r in g),
                          "min_cosine": min(r["same_batch"]["cosine"] for r in g),
                          "alone_max_abs_diff": max(r["alone"]["max_abs_diff"] for r in g)}
                      for k, g in _group(records, key).items()}
    detectable = all(controls[k]["max"] < INCOMPATIBLE_BELOW_COSINE for k in ("prompt_prefix", "another_unit"))
    compatible = passes and settings_ok and detectable and not attempts
    return {
        "verdict": "compatible" if compatible else "not_established",
        "config_fingerprint": config.fingerprint(), "config": config.as_dict(), "loaded_model_settings": settings,
        "thresholds": {"same_batch_max_abs_diff": SAME_BATCH_MAX_ABS, "same_batch_min_cosine": SAME_BATCH_MIN_COSINE,
                       "incompatible_below_cosine": INCOMPATIBLE_BELOW_COSINE},
        "legacy_files": {p.name: {"sha256": file_sha256(p), "compatible": compatible} for p in legacy_files.values()},
        "checks": {"same_batch_within_thresholds": passes, "loaded_settings_match_config": settings_ok,
                   "controls_detectable": detectable, "no_network_attempts": not attempts},
        "network_attempts_blocked": len(attempts), "seconds": round(elapsed, 1), "sampled_units": len(records),
        "summary": {"same_batch": {"bit_identical": sum(s["bit_identical"] for s in same), "max_abs_diff": max(s["max_abs_diff"] for s in same),
                                   "min_cosine": min(s["cosine"] for s in same)},
                    "alone": {"bit_identical": sum(s["bit_identical"] for s in alone), "max_abs_diff": max(s["max_abs_diff"] for s in alone),
                              "min_cosine": min(s["cosine"] for s in alone)},
                    "by_scope": by(lambda r: r["scope"]), "by_unit_type": by(lambda r: r["unit_type"]),
                    "by_slot": by(lambda r: "first slot" if r["slot_in_batch"] == 0 else "last slot" if r["slot_in_batch"] == r["batch_members"] - 1 else "middle slot")},
        "negative_controls_cosine_or_norm": controls,
        "units": records,
    }


def _group(records, key) -> dict[str, list]:
    groups: dict[str, list] = {}
    for record in records:
        groups.setdefault(key(record), []).append(record)
    return groups


if __name__ == "__main__":
    out = run()
    print(json.dumps({k: out[k] for k in ("verdict", "sampled_units", "network_attempts_blocked", "seconds", "loaded_model_settings",
                                          "summary", "negative_controls_cosine_or_norm")}, indent=1))
