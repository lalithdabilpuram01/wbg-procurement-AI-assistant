"""Isolated strategy-C retrieval evaluation in both embedding-cache modes (Step 6D, Part A).

    /opt/anaconda3/bin/python3 -B -m rag.embedding_cache.evaluate_strategy_c       (from procurement-intelligence/)

Runs the unchanged rag/retrieval/evaluate.py benchmark, restricted to its strategy-C configurations (bm25_C, dense_C,
hybrid_C, hybrid_C_guarded), twice: with Retriever(corpus, embedding_cache="legacy") and with
Retriever(corpus, embedding_cache="unit"). The mode is passed explicitly to each Retriever (no environment
variable). Every search's full result list is recorded. Each run writes a NEW directory
rag/retrieval/runs/<UTC>_strategyC_<mode>/; nothing existing is overwritten. A further in-process parity check covers
every method x guarded/unguarded (and hybrid + rerank) for all benchmark questions. Local models only; sockets
blocked. Writes the comparison to results/strategy_c_eval_comparison.json.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from . import variants
from .validate_cache import BASELINE_RUN, RUNS
from .verify_compatibility import no_network

import evaluate  # noqa: E402  (rag/retrieval/evaluate.py, on sys.path via variants)

RESULTS = Path(__file__).resolve().parent / "results" / "strategy_c_eval_comparison.json"
C_CONFIGS = [c for c in evaluate.CONFIGS if c[1] == "C"]
VOLATILE = ("latency_ms", "latency_ms_median", "latency_ms_p95")


def run_mode(mode: str) -> tuple[Path, list[dict[str, Any]]]:
    """One evaluate.py run (strategy C only) with every Retriever in `mode`; returns its directory and searches."""
    searches: list[dict[str, Any]] = []

    class RecordingRetriever(variants.core.Retriever):
        def __init__(self, corpus):
            super().__init__(corpus, embedding_cache=mode)

        def search(self, q, **kwargs):
            res = super().search(q, **kwargs)
            searches.append({"query": q, **kwargs, "status": res["status"], "notes": res["notes"], "results": res["results"]})
            return res

    before = set(RUNS.iterdir())
    saved = evaluate.CONFIGS, evaluate.Retriever
    evaluate.CONFIGS, evaluate.Retriever = C_CONFIGS, RecordingRetriever
    try:
        evaluate.main()
    finally:
        evaluate.CONFIGS, evaluate.Retriever = saved
    created = sorted(set(RUNS.iterdir()) - before)
    if len(created) != 1:
        raise RuntimeError(f"expected one new run directory, found {created}")
    target = created[0].with_name(f"{created[0].name}_strategyC_{mode}")
    created[0].rename(target)                                         # our own new directory: label it with the mode
    (target / "full_results.json").write_text(json.dumps(searches, indent=1, default=str))
    return target, searches


def _per_question(run: Path, configs: set[str]) -> dict[tuple[str, str], dict[str, str]]:
    with (run / "per_question.csv").open() as handle:
        return {(r["config"], r["qid"]): {k: v for k, v in r.items() if k not in VOLATILE}
                for r in csv.DictReader(handle) if r["config"] in configs}


def _metrics(run: Path, configs: set[str]) -> dict[str, Any]:
    summary = json.loads((run / "metrics.json").read_text())["configs"]
    return {name: {k: v for k, v in m.items() if k not in VOLATILE} for name, m in summary.items() if name in configs}


def compare_runs(legacy: Path, unit: Path, legacy_searches: list, unit_searches: list) -> dict[str, Any]:
    names = {c[0] for c in C_CONFIGS}
    baseline = RUNS / BASELINE_RUN
    pq = {name: _per_question(run, names) for name, run in (("legacy", legacy), ("unit", unit), ("baseline", baseline))}
    mt = {name: _metrics(run, names) for name, run in (("legacy", legacy), ("unit", unit), ("baseline", baseline))}
    same_json = lambda f: json.loads((legacy / f).read_text()) == json.loads((unit / f).read_text())  # noqa: E731
    return {
        "runs": {"legacy": legacy.name, "unit": unit.name, "baseline": BASELINE_RUN},
        "configs": sorted(names),
        "searches_recorded": {"legacy": len(legacy_searches), "unit": len(unit_searches)},
        "full_result_lists_identical": legacy_searches == unit_searches,
        "searches_identical": sum(a == b for a, b in zip(legacy_searches, unit_searches)),
        "per_question_identical_unit_vs_legacy": pq["unit"] == pq["legacy"],
        "per_question_identical_vs_baseline": {"legacy": pq["legacy"] == pq["baseline"], "unit": pq["unit"] == pq["baseline"]},
        "metrics_identical_unit_vs_legacy": mt["unit"] == mt["legacy"],
        "metrics_identical_vs_baseline": {"legacy": mt["legacy"] == mt["baseline"], "unit": mt["unit"] == mt["baseline"]},
        "citation_check_identical": same_json("citation_check.json"),
        "privacy_check_identical": same_json("privacy_check.json"),
        "metrics": mt["unit"],
        "baseline_differences": {k: {"baseline": mt["baseline"].get(k), "now": mt["unit"].get(k)} for k in names
                                 if mt["baseline"].get(k) != mt["unit"].get(k)},
    }


def full_parity(questions: list[dict]) -> dict[str, Any]:
    """Every method x guarded/unguarded, plus hybrid + rerank, legacy vs unit, real query encoding (local model)."""
    corpus = variants.corpus("C+tables")
    legacy = variants.core.Retriever(corpus, embedding_cache="legacy")
    unit = variants.core.Retriever(corpus, embedding_cache="unit")
    unit._model, unit._rerank = legacy.model(), legacy.reranker()       # same local model objects for both modes
    combos = [(m, g, False) for m in ("bm25", "dense", "hybrid") for g in (False, True)] + [("hybrid", True, True)]
    out = {}
    for method, guarded, rerank in combos:
        identical = 0
        for q in questions:
            a = legacy.search(q["question"], method=method, k=10, guarded=guarded, rerank=rerank)
            b = unit.search(q["question"], method=method, k=10, guarded=guarded, rerank=rerank)
            identical += (a["status"], a["notes"], a["results"]) == (b["status"], b["notes"], b["results"])
        out[f"{method}|{'guarded' if guarded else 'unguarded'}{'|rerank' if rerank else ''}"] = f"{identical}/{len(questions)}"
    return out


def run() -> dict[str, Any]:
    questions = json.loads((variants.ROOT / "rag" / "retrieval" / "benchmark_v1.json").read_text())["questions"]
    with no_network() as attempts:
        legacy_dir, legacy_searches = run_mode("legacy")
        unit_dir, unit_searches = run_mode("unit")
        result = compare_runs(legacy_dir, unit_dir, legacy_searches, unit_searches)
        result["in_process_parity"] = full_parity(questions)
    result["network_attempts_blocked"] = len(attempts)
    RESULTS.write_text(json.dumps(result, indent=1, default=str))
    return result


if __name__ == "__main__":
    print(json.dumps({k: v for k, v in run().items() if k != "metrics"}, indent=1, default=str))
