"""Retrieval regression of pilot_v2_1 + staged per-unit embeddings against the frozen baseline (Step 6D).

    /opt/anaconda3/bin/python3 -B -m rag.release_v2_1.evaluate_release      (from procurement-intelligence/)

Runs the unchanged rag/retrieval/evaluate.py (all 11 configurations, strategies A and C) twice, recording every
search's full result list:
  reference  pilot_v2, whole-index cache (must reproduce the frozen baseline run 20261025T164420Z exactly)
  candidate  pilot_v2_1, per-unit cache read from the STAGING store (explicit store_dir; no environment variables)
Each run writes a NEW directory rag/retrieval/runs/<UTC>_<label>/. Every ranking difference is attributed: it either
involves one of the units whose indexed text changed, or is a BM25 corpus-statistics shift (document frequencies of the
changed tokens). Local models only; sockets blocked. Writes results/evaluation.json.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from rag.embedding_cache import adapter, variants
from rag.embedding_cache.store import text_sha256
from rag.embedding_cache.validate_cache import BASELINE_RUN, RUNS
from rag.embedding_cache.verify_compatibility import no_network

from .stage_embeddings import STAGING

import evaluate  # noqa: E402  (rag/retrieval/evaluate.py, on sys.path via variants)

RESULTS = Path(__file__).resolve().parent / "results"
VOLATILE = ("latency_ms", "latency_ms_median", "latency_ms_p95")


def _scope(strategy: str, include_tables: bool) -> str:
    return f"{strategy}{'+' if include_tables else '-'}tables"


def run_evaluate(label: str, variant: str, staged: bool) -> tuple[Path, list[dict[str, Any]]]:
    searches: list[dict[str, Any]] = []
    base = variants.core.Retriever

    class Recording(base):
        def __init__(self, corpus):
            super().__init__(corpus, embedding_cache="unit" if staged else "legacy")

        def embeddings(self):
            if staged and self._emb is None:
                self._emb = adapter.unit_cache_embeddings(self.c, store_dir=STAGING)
            return super().embeddings()

        def search(self, q, **kwargs):
            res = super().search(q, **kwargs)
            searches.append({"scope": _scope(self.c.strategy, self.c.include_tables), "query": q, **kwargs,
                             "status": res["status"], "notes": res["notes"], "results": res["results"]})
            return res

    def corpus_factory(strategy: str, include_tables: bool = True):
        return variants.corpus(_scope(strategy, include_tables), variant)

    before = set(RUNS.iterdir())
    saved = evaluate.Retriever, evaluate.Corpus
    evaluate.Retriever, evaluate.Corpus = Recording, corpus_factory
    try:
        evaluate.main()
    finally:
        evaluate.Retriever, evaluate.Corpus = saved
    created = sorted(set(RUNS.iterdir()) - before)
    if len(created) != 1:
        raise RuntimeError(f"expected one new run directory, found {created}")
    target = created[0].with_name(f"{created[0].name}_{label}")
    created[0].rename(target)
    (target / "full_results.json").write_text(json.dumps(searches, indent=1, default=str))
    return target, searches


def per_question(run: Path) -> dict[tuple[str, str], dict[str, str]]:
    with (run / "per_question.csv").open() as handle:
        return {(r["config"], r["qid"]): {k: v for k, v in r.items() if k not in VOLATILE} for r in csv.DictReader(handle)}


def metrics(run: Path) -> dict[str, Any]:
    return {n: {k: v for k, v in m.items() if k not in VOLATILE} for n, m in json.loads((run / "metrics.json").read_text())["configs"].items()}


def changed_units() -> set[str]:
    out = set()
    for scope in ("A+tables", "C+tables", "A-tables"):
        old = {u.unit_id: text_sha256(u.text) for u in variants.corpus(scope, "pilot_v2").units}
        out |= {u.unit_id for u in variants.corpus(scope, "pilot_v2_1").units if old[u.unit_id] != text_sha256(u.text)}
    return out


def _rank(value: str) -> int | None:
    return int(float(value)) if value else None


def compare(reference: Path, candidate: Path, ref_searches: list, cand_searches: list, changed: set[str]) -> dict[str, Any]:
    baseline = RUNS / BASELINE_RUN
    base_pq, ref_pq, cand_pq = per_question(baseline), per_question(reference), per_question(candidate)
    base_m, ref_m, cand_m = metrics(baseline), metrics(reference), metrics(candidate)
    aligned = all((a["query"], a.get("method"), a.get("guarded"), a.get("rerank"), a["scope"]) ==
                  (b["query"], b.get("method"), b.get("guarded"), b.get("rerank"), b["scope"]) for a, b in zip(ref_searches, cand_searches))
    differences = []
    for a, b in zip(ref_searches, cand_searches):
        ids_a, ids_b = [r["unit_id"] for r in a["results"]], [r["unit_id"] for r in b["results"]]
        if ids_a != ids_b or a["status"] != b["status"]:
            involved = sorted((set(ids_a) | set(ids_b)) & changed)
            differences.append({"scope": a["scope"], "query": a["query"], "method": a.get("method"), "guarded": a.get("guarded"),
                                "rerank": a.get("rerank"), "status_changed": a["status"] != b["status"], "changed_units_involved": involved,
                                "attribution": "changed indexed text" if involved else "bm25 corpus statistics (document frequencies)",
                                "before": ids_a, "after": ids_b})
    same_ids_other_changes = sum(1 for a, b in zip(ref_searches, cand_searches)
                                 if [r["unit_id"] for r in a["results"]] == [r["unit_id"] for r in b["results"]] and a["results"] != b["results"])
    rank_changes = []
    for key, row in ref_pq.items():
        new = cand_pq[key]
        if (row["first_relevant_rank"], row["recall@5"], row["recall@10"], row["top1"], row["status"]) != \
                (new["first_relevant_rank"], new["recall@5"], new["recall@10"], new["top1"], new["status"]):
            before, after = _rank(row["first_relevant_rank"]), _rank(new["first_relevant_rank"])
            rank_changes.append({"config": key[0], "qid": key[1], "first_relevant_rank": [before, after], "top1": [row["top1"], new["top1"]],
                                 "recall@5": [row["recall@5"], new["recall@5"]], "recall@10": [row["recall@10"], new["recall@10"]],
                                 "regression": (after or 99) > (before or 99) or (row["recall@10"] == "True" and new["recall@10"] != "True")})
    cite_ref = json.loads((reference / "citation_check.json").read_text())
    cite_cand = json.loads((candidate / "citation_check.json").read_text())
    return {
        "runs": {"baseline": BASELINE_RUN, "reference": reference.name, "candidate": candidate.name},
        "reference_reproduces_baseline": {"per_question": ref_pq == base_pq, "metrics": ref_m == base_m},
        "searches": {"reference": len(ref_searches), "candidate": len(cand_searches), "aligned": aligned,
                     "identical": len(ref_searches) - len(differences) - same_ids_other_changes,
                     "same_unit_ids_scores_or_excerpts_changed": same_ids_other_changes, "ranking_differences": len(differences)},
        "metrics": {"baseline": base_m, "candidate": cand_m},
        "metric_deltas": {n: {k: (base_m[n][k], cand_m[n][k]) for k in ("recall@5", "recall@10", "MRR", "precision@5",
                                                                         "all_groups@10_on_multi_source", "citation_verified_rate_top5",
                                                                         "insufficient_correctly_flagged", "answerable_wrongly_flagged")
                              if base_m[n][k] != cand_m[n][k]} for n in base_m},
        "per_question_changes": rank_changes,
        "regressions": [c for c in rank_changes if c["regression"]],
        "ranking_differences": differences,
        "citations": {"reference": cite_ref, "candidate": cite_cand,
                      "newly_failed": sorted(set(cite_cand["failed_units"]) - set(cite_ref["failed_units"])),
                      "newly_verified": sorted(set(cite_ref["failed_units"]) - set(cite_cand["failed_units"]))},
        "privacy_check_identical": json.loads((reference / "privacy_check.json").read_text()) == json.loads((candidate / "privacy_check.json").read_text()),
        "guard_statuses_identical": all(a["status"] == b["status"] and a["notes"] == b["notes"] for a, b in zip(ref_searches, cand_searches)),
    }


PHONE_LIKE = r"(?:\+\d{1,3}[\s-]?)?(?:\(\d{2,4}\)[\s-]?)?\d{3,4}[\s-]\d{3,4}(?:[\s-]\d{2,4})?"   # evaluate.py's heuristic


def supplementary_checks(changed: set[str]) -> dict[str, Any]:
    """Value-level citation check of the changed units, the phone-like heuristic delta, and the sanitiser's own
    residual-risk detectors over every indexed unit of pilot_v2_1 (read-only; PDFs via evaluate.PageText)."""
    import re

    from rag.ingestion.sanitize import TOKEN, residual_risk
    out: dict[str, Any] = {"changed_unit_values_on_cited_pages": {}, "phone_like_new_matches": [], "residual_risk": {}}
    for scope in ("A+tables", "C+tables", "A-tables"):
        old = {u.unit_id: u for u in variants.corpus(scope, "pilot_v2").units}
        new = variants.corpus(scope, "pilot_v2_1")
        pages = evaluate.PageText(new)
        for unit in new.units:
            if unit.unit_id in changed and unit.unit_id not in out["changed_unit_values_on_cited_pages"]:
                text = "".join(pages.get(unit.document_id, p) for p in range(unit.page_start, unit.page_end + 1))
                values = [v for row in unit.excerpt.split(" || ") for cell in row.split(" | ") for v in cell.split("\n")
                          if len(evaluate.norm(v)) >= 2 and not TOKEN.fullmatch(v.strip())]
                found = [v for v in values if evaluate.norm(v) in text]
                rest = [v for v in values if v not in found]
                words_found = [v for v in rest if all(evaluate.norm(w) in text for w in re.findall(r"[A-Za-z0-9]+", TOKEN.sub(" ", v)))]
                old_cells = {c for row in old[unit.unit_id].excerpt.split(" || ") for c in row.split(" | ")}
                out["changed_unit_values_on_cited_pages"][unit.unit_id] = {
                    "values": len(values), "found_verbatim": len(found), "found_word_by_word": len(words_found),
                    "not_found": [v for v in rest if v not in words_found],
                    "word_by_word_cells_unchanged_from_pilot_v2": all(v in old_cells for v in words_found)}
            if re.search(PHONE_LIKE, unit.excerpt) and not re.search(PHONE_LIKE, old[unit.unit_id].excerpt):
                out["phone_like_new_matches"].append({"scope": scope, "unit_id": unit.unit_id,
                                                      "matches": re.findall(PHONE_LIKE, unit.excerpt)})
        totals: dict[str, int] = {}
        for unit in new.units:
            for k, v in residual_risk(unit.text).items():
                totals[k] = totals.get(k, 0) + v
        out["residual_risk"][scope] = totals
    return out


def run() -> dict[str, Any]:
    changed = changed_units()
    with no_network() as attempts:
        reference, ref_searches = run_evaluate("pilot_v2_reference", "pilot_v2", staged=False)
        candidate, cand_searches = run_evaluate("pilot_v2_1_staging", "pilot_v2_1", staged=True)
    result = {"changed_units": sorted(changed), **compare(reference, candidate, ref_searches, cand_searches, changed),
              "supplementary": supplementary_checks(changed), "network_attempts_blocked": len(attempts)}
    result["supplementary"]["secondary_metric_changes"] = secondary_metric_changes(result["runs"])
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "evaluation.json").write_text(json.dumps(result, indent=1, default=str))
    return result


def secondary_metric_changes(runs: dict[str, str]) -> list[dict[str, Any]]:
    """Per-question precision@5 and top-5 citation-verification changes (reference vs candidate run)."""
    ref, cand = per_question(RUNS / runs["reference"]), per_question(RUNS / runs["candidate"])
    fields = ("precision@5", "citations_verified_top5", "citations_checked_top5")
    return [{"config": k[0], "qid": k[1], **{f: [ref[k][f], cand[k][f]] for f in fields}}
            for k in ref if any(ref[k][f] != cand[k][f] for f in fields)]


def merge_supplementary() -> dict[str, Any]:
    """Recompute only the supplementary checks and merge them into an existing results/evaluation.json."""
    result = json.loads((RESULTS / "evaluation.json").read_text())
    result["supplementary"] = supplementary_checks(set(result["changed_units"]))
    result["supplementary"]["secondary_metric_changes"] = secondary_metric_changes(result["runs"])
    (RESULTS / "evaluation.json").write_text(json.dumps(result, indent=1, default=str))
    return result


if __name__ == "__main__":
    import sys
    if "--supplementary-only" in sys.argv:
        print(json.dumps(merge_supplementary()["supplementary"], indent=1, ensure_ascii=False))
        raise SystemExit(0)
    out = run()
    print(json.dumps({k: out[k] for k in ("runs", "reference_reproduces_baseline", "searches", "metric_deltas", "regressions",
                                          "privacy_check_identical", "guard_statuses_identical", "network_attempts_blocked")}, indent=1))
    print("citations newly failed:", out["citations"]["newly_failed"], "newly verified:", out["citations"]["newly_verified"])
    for d in out["ranking_differences"]:
        print(d["scope"], d["method"], d["guarded"], d["rerank"], d["attribution"], d["changed_units_involved"], "|", d["query"][:60])
