"""Step 6E integration validation: strategy-C evaluation of both RAG versions through rag.retrieval.versions.

    /opt/anaconda3/bin/python3 -B -m rag.release_v2_1.validate_integration      (from procurement-intelligence/)

Runs the unchanged evaluate.py (strategy-C configurations: bm25_C, dense_C, hybrid_C, hybrid_C_guarded) twice through
the public version API, recording every full result list:
  default    versions.build_corpus() + core.Retriever(corpus)  -> must equal the frozen baseline and the Step 6D-prep
             strategy-C legacy run exactly
  pilot_v2_1 versions.build_corpus("pilot_v2_1") + its registered release -> must equal the Step 6D staged candidate
Then switches back to the default in the same process and repeats every guarded hybrid search. Local models only;
sockets blocked; no environment variables are set. Writes results/integration_validation.json and two NEW run
directories rag/retrieval/runs/<UTC>_6e_<version>/.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rag.embedding_cache import variants
from rag.embedding_cache.store import file_sha256
from rag.embedding_cache.validate_cache import BASELINE_RUN, RUNS
from rag.embedding_cache.verify_compatibility import no_network

from .evaluate_release import changed_units, metrics, per_question, supplementary_checks

import evaluate  # noqa: E402  (rag/retrieval/evaluate.py, on sys.path via variants)
import versions  # noqa: E402  (rag/retrieval/versions.py, same top-level `core` module as evaluate.py)

RESULTS = Path(__file__).resolve().parent / "results" / "integration_validation.json"
C_CONFIGS = [c for c in evaluate.CONFIGS if c[1] == "C"]
STEP6D_PREP_LEGACY_C = "20261026T161334Z_strategyC_legacy"
STEP6D_CANDIDATE = "20261026T163616Z_pilot_v2_1_staging"


def run_version(version: str) -> tuple[Path, list[dict[str, Any]]]:
    searches: list[dict[str, Any]] = []
    spec = versions.get_version(version)

    class Recording(versions.core.Retriever):
        def __init__(self, corpus):
            super().__init__(corpus, embedding_cache=spec.embedding_cache)

        def search(self, q, **kwargs):
            res = super().search(q, **kwargs)
            searches.append({"scope": f"{self.c.strategy}{'+' if self.c.include_tables else '-'}tables", "query": q, **kwargs,
                             "status": res["status"], "notes": res["notes"], "results": res["results"]})
            return res

    before = set(RUNS.iterdir())
    saved = evaluate.CONFIGS, evaluate.Retriever, evaluate.Corpus
    evaluate.CONFIGS, evaluate.Retriever = C_CONFIGS, Recording
    evaluate.Corpus = lambda strategy, include_tables=True: versions.build_corpus(version, strategy, include_tables)
    try:
        evaluate.main()
    finally:
        evaluate.CONFIGS, evaluate.Retriever, evaluate.Corpus = saved
    created = sorted(set(RUNS.iterdir()) - before)
    if len(created) != 1:
        raise RuntimeError(f"expected one new run directory, found {created}")
    target = created[0].with_name(f"{created[0].name}_6e_{version}")
    created[0].rename(target)
    (target / "full_results.json").write_text(json.dumps(searches, indent=1, default=str))
    return target, searches


def _subset(table: dict, configs: set[str]) -> dict:
    return {k: v for k, v in table.items() if (k[0] if isinstance(k, tuple) else k) in configs}


def _stored_searches(run: str, scope: str | None = None) -> list[dict]:
    searches = json.loads((RUNS / run / "full_results.json").read_text())
    rows = [s for s in searches if scope is None or s.get("scope") == scope]
    return [{k: v for k, v in s.items() if k != "scope"} for s in rows]


def _strip_scope(searches: list[dict]) -> list[dict]:
    return [{k: v for k, v in s.items() if k != "scope"} for s in searches]


def switch_back(default_searches: list[dict]) -> dict[str, Any]:
    """Default retriever rebuilt after the pilot_v2_1 run: every guarded hybrid search must equal the default run's."""
    expected = [s for s in default_searches if s.get("method") == "hybrid" and s.get("guarded") and s["query"] != "warm up"]
    retriever = versions.build_retriever()
    again = [retriever.search(s["query"], method="hybrid", k=10, guarded=True) for s in expected]
    identical = sum((a["status"], a["notes"], a["results"]) == (s["status"], s["notes"], s["results"]) for a, s in zip(again, expected))
    return {"searches": len(expected), "identical": identical, "corpus": retriever.c.processed_dir.name,
            "embedding_cache": retriever.embedding_cache, "core_PILOT_unchanged": versions.core.PILOT.name == "pilot_v2",
            "environment_untouched": not any(os.environ.get(k) for k in ("RAG_EMBEDDING_CACHE", "RAG_UNIT_CACHE_DIR"))}


def run() -> dict[str, Any]:
    names = {c[0] for c in C_CONFIGS}
    legacy_files = {p.name: file_sha256(p) for p in sorted(versions.core.CACHE.glob("*.npy"))}
    with no_network() as attempts:
        default_dir, default_searches = run_version("pilot_v2")
        improved_dir, improved_searches = run_version("pilot_v2_1")
        back = switch_back(default_searches)
    supplementary = supplementary_checks(changed_units())
    base_pq, base_m = per_question(RUNS / BASELINE_RUN), metrics(RUNS / BASELINE_RUN)
    cand_pq, cand_m = per_question(RUNS / STEP6D_CANDIDATE), metrics(RUNS / STEP6D_CANDIDATE)
    cite = {name: json.loads((d / "citation_check.json").read_text()) for name, d in (("default", default_dir), ("pilot_v2_1", improved_dir))}
    privacy = {name: json.loads((d / "privacy_check.json").read_text()) for name, d in (("default", default_dir), ("pilot_v2_1", improved_dir))}
    step6d_privacy = json.loads((RUNS / STEP6D_CANDIDATE / "privacy_check.json").read_text())
    result = {
        "runs": {"default": default_dir.name, "pilot_v2_1": improved_dir.name, "baseline": BASELINE_RUN,
                 "step6d_prep_legacy_c": STEP6D_PREP_LEGACY_C, "step6d_candidate": STEP6D_CANDIDATE},
        "default": {"per_question_equals_baseline": per_question(default_dir) == _subset(base_pq, names),
                    "metrics_equal_baseline": metrics(default_dir) == _subset(base_m, names),
                    "full_results_equal_step6d_prep_legacy_run": _strip_scope(default_searches) == _stored_searches(STEP6D_PREP_LEGACY_C)},
        "pilot_v2_1": {"per_question_equals_step6d_candidate": per_question(improved_dir) == _subset(cand_pq, names),
                       "metrics_equal_step6d_candidate": metrics(improved_dir) == _subset(cand_m, names),
                       "full_results_equal_step6d_candidate": _strip_scope(improved_searches) == _stored_searches(STEP6D_CANDIDATE, "C+tables")},
        "metrics": {"default": metrics(default_dir), "pilot_v2_1": metrics(improved_dir)},
        "searches": len(improved_searches),
        "guard_statuses_identical": all((a["status"], a["notes"]) == (b["status"], b["notes"]) for a, b in zip(default_searches, improved_searches)),
        "citations": {**cite, "newly_failed": sorted(set(cite["pilot_v2_1"]["failed_units"]) - set(cite["default"]["failed_units"]))},
        "changed_unit_values_on_cited_pages": {k: v for k, v in supplementary["changed_unit_values_on_cited_pages"].items()},
        "residual_risk_C": supplementary["residual_risk"]["C+tables"],
        "privacy": {**privacy, "pilot_v2_1_equals_step6d_candidate_C": privacy["pilot_v2_1"].get("C_tablesTrue") == step6d_privacy.get("C_tablesTrue")},
        "switch_back": back,
        "legacy_files_unchanged": legacy_files == {p.name: file_sha256(p) for p in sorted(versions.core.CACHE.glob("*.npy"))},
        "network_attempts_blocked": len(attempts),
    }
    RESULTS.write_text(json.dumps(result, indent=1, default=str))
    return result


if __name__ == "__main__":
    out = run()
    print(json.dumps({k: v for k, v in out.items() if k not in ("metrics", "citations", "privacy", "changed_unit_values_on_cited_pages")},
                     indent=1, default=str))
    print("citations newly failed:", out["citations"]["newly_failed"], "| privacy equal to 6D:", out["privacy"]["pilot_v2_1_equals_step6d_candidate_C"])
