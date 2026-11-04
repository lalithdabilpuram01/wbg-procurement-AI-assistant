"""Audit of an evaluation run (offline, from live_results/eval/results.json and the two ledgers).

    coverage       every dataset case ran; which crashed or did not finish
    scores         mean of each check per category, with the number of cases behind it
    tablelens      switch ON vs OFF per question (wins, losses, ties on value_in_answer)
    judge          score spread (a judge that gives everything 5 is not measuring), and disagreement with the exact
                   checks: judged grounded but the exact value is wrong, or judged ungrounded but the value is right
    cost           the app's own per-case cost accounting reconciled with the app ledger; the judge ledger
    upload         what was uploaded (after redaction) scanned for secrets and personal data
    latency        median and slowest case per category
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from conversation.pii import find_pii
from conversation.redaction import redact_secrets
from wbg_orchestrator.live_llm import SpendLedger

GROUNDED = 0.75                  # judge groundedness 4 or 5 of 5
EXACT_CHECKS = {"tablelens": "value_in_answer", "structured": "gold_values_in_answer"}


def scores_of(row: dict[str, Any]) -> dict[str, float]:
    return {s["key"]: s["score"] for s in row["scores"] if s["score"] is not None}


def rescored(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministic checks recomputed from the stored outputs with the current evaluator (judge scores kept as run);
    returns the rows and every score that changed, so a corrected evaluator is visible, never silent."""
    from .evaluators import deterministic
    changes, out = [], []
    for r in rows:
        recorded = scores_of(r)
        fresh = deterministic(r["inputs"], r["outputs"] or {}, r["reference"] or {})["results"]
        judge = [s for s in r["scores"] if s["key"] in ("groundedness", "relevance", "judge_failed")]
        for s in fresh:
            if s["key"] in recorded and abs(recorded[s["key"]] - s["score"]) > 1e-9:
                changes.append({"case": r["case_id"], "check": s["key"], "recorded": recorded[s["key"]], "now": s["score"]})
        out.append({**r, "scores": fresh + judge})
    return out, changes


def audit(results_dir: Path, dataset_file: Path) -> dict[str, Any]:
    """Builds the audit report of one run folder, writes it to audit.json there and returns it."""
    data = json.loads((results_dir / "results.json").read_text())
    rows, changes = rescored(data["rows"])
    dataset = json.loads(dataset_file.read_text())
    report = {"experiment": data["experiment"], "code_version": data.get("code_version"),
              "coverage": coverage(rows, dataset), "scores": category_scores(rows), "tablelens": tablelens(rows),
              "judge": judge(rows), "cost": cost(rows, results_dir), "upload": upload_scan(rows), "latency": latency(rows),
              "rescored_changes": changes}
    (results_dir / "audit.json").write_text(json.dumps(report, indent=1, default=str))
    return report


def coverage(rows, dataset) -> dict[str, Any]:
    """Which dataset cases ran, which never ran, and which crashed or did not complete."""
    expected = {e["inputs"]["case_id"] for e in dataset}
    ran = {r["case_id"] for r in rows}
    unfinished = [r["case_id"] for r in rows if r["error"] or not scores_of(r).get("completed")]
    return {"dataset_cases": len(expected), "ran": len(ran), "missing": sorted(expected - ran), "crashed_or_unfinished": unfinished}


def category_scores(rows) -> dict[str, dict[str, Any]]:
    """Mean and count of every check per category; TableLens is split into its switch-on and switch-off runs."""
    table: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        group = r["category"] + ("" if r["category"] != "tablelens" else ("_on" if r["inputs"].get("table_lens") else "_off"))
        for key, score in scores_of(r).items():
            table[group][key].append(score)
    return {g: {k: {"mean": round(statistics.mean(v), 3), "n": len(v)} for k, v in sorted(keys.items())}
            for g, keys in sorted(table.items())}


def tablelens(rows) -> dict[str, Any]:
    """Pairs the ON and OFF run of each TableLens question and lists where the switch helped or hurt value_in_answer."""
    pairs: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        if r["category"] == "tablelens":
            pairs[r["case_id"].split("-")[0]]["on" if r["inputs"]["table_lens"] else "off"] = scores_of(r).get("value_in_answer", 0)
    wins = sorted(q for q, p in pairs.items() if p.get("on", 0) > p.get("off", 0))
    losses = sorted(q for q, p in pairs.items() if p.get("on", 0) < p.get("off", 0))
    return {"questions": len(pairs), "on_correct": sum(p.get("on", 0) for p in pairs.values()),
            "off_correct": sum(p.get("off", 0) for p in pairs.values()), "on_wins": wins, "on_losses": losses}


def judge(rows) -> dict[str, Any]:
    """Judge score spread and failures, and the cases where its groundedness disagrees with the exact value check."""
    judged = [r for r in rows if "groundedness" in scores_of(r)]
    grounded = [scores_of(r)["groundedness"] for r in judged]
    disagreements = []
    for r in judged:
        exact_key = EXACT_CHECKS.get(r["category"])
        s = scores_of(r)
        if exact_key and exact_key in s:
            judged_ok, exact_ok = s["groundedness"] >= GROUNDED, s[exact_key] >= 1.0
            if judged_ok != exact_ok:
                disagreements.append({"case": r["case_id"], "judge_grounded": s["groundedness"], exact_key: s[exact_key],
                                      "judge_comment": next(x["comment"] for x in r["scores"] if x["key"] == "groundedness")[:300]})
    return {"judged": len(judged), "failed": sum("judge_failed" in scores_of(r) for r in rows),
            "groundedness_spread": {str(v): grounded.count(v) for v in sorted(set(grounded))},
            "relevance_mean": round(statistics.mean(scores_of(r)["relevance"] for r in judged), 3) if judged else None,
            "disagreements_with_exact_checks": disagreements}


def cost(rows, results_dir: Path) -> dict[str, Any]:
    """Per-case app costs summed and checked against the app ledger (the gap is unaccounted); the judge ledger."""
    per_case = sum(Decimal(str(scores_of(r).get("cost_usd", 0))) for r in rows)
    app, judge_ledger = SpendLedger(results_dir / "app_ledger.json").summary(), SpendLedger(results_dir / "judge_ledger.json").summary()
    return {"app_cases_sum_usd": str(round(per_case, 4)), "app_ledger_usd": app["committed_usd"], "app_attempts": app["attempts_used"],
            "judge_ledger_usd": judge_ledger["committed_usd"], "judge_attempts": judge_ledger["attempts_used"],
            "unaccounted_app_usd": str(round(Decimal(str(app["committed_usd"])) - per_case, 4))}


def upload_scan(rows) -> dict[str, Any]:
    """Strings as uploaded (the same redaction the client applies) checked for anything left to redact."""
    from .run import redacted
    findings = []
    for r in rows:
        for text in _strings(redacted({"inputs": r["inputs"], "outputs": r["outputs"]})):
            if redact_secrets(text) != text or find_pii(text):
                findings.append({"case": r["case_id"], "kinds": find_pii(text) or ["secret"]})
    return {"cases_scanned": len(rows), "findings": findings}


def latency(rows) -> dict[str, Any]:
    """Median and slowest (seconds, case id) per category, over the cases that produced outputs."""
    by: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for r in rows:
        if r["outputs"]:
            by[r["category"]].append((r["outputs"].get("seconds", 0), r["case_id"]))
    return {c: {"median_s": statistics.median(s for s, _ in v), "slowest": max(v)} for c, v in by.items()}


def _strings(value: Any):
    """Yields every string nested anywhere inside dicts and lists."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
