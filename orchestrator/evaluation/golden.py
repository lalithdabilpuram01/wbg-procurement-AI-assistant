"""Golden evaluations: each component scored against known-correct answers, without any LLM call.

    documents   275 labelled questions on the app's default release (pilot_v3: Lakebase pgvector + bge-m3, hybrid
                search, read-only): 31 hand-verified (v1), 117 English + 112 French with gold pages derived from section
                headings (v3), 15 cross-language. recall@5, recall@10, MRR; unanswerable questions must be refused
    tablelens   the 22 hand-checked table questions (hit@1, hit@2) and 2 that no table answers (nothing found)
    routing     held-out questions labelled by their source (structured, documents, TableLens): exact route, and
                acceptable (the front door still reaches the right agent through a combined investigation)
    guardrails  hand-labelled inputs (production NeMo rails, no LLM) and personal-data masking (golden_cases.py)
    structured  the 8 reviewed Gold reference queries over the MCP server (read-only Databricks): values still equal the
                dataset's references; and the SQL validator approves them and rejects 8 unsafe statements

    cd orchestrator
    env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m evaluation.golden run          # local, all sets
    env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m evaluation.golden upload       # LangSmith

`run` writes live_results/golden/<set>.json (summary and per-case rows). `upload` sends, per set, a dataset (inputs and
references) and an experiment (outputs and scores) to LangSmith, every string redacted as in evaluation.run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from statistics import mean
from typing import Any, Callable

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from wbg_orchestrator.runtime import ORCHESTRATOR_DIR  # noqa: E402

from . import golden_cases  # noqa: E402
from .cases import STRUCTURED, reference_values  # noqa: E402

RESULTS = ORCHESTRATOR_DIR / "live_results" / "golden"
DATASET_FILE = ORCHESTRATOR_DIR / "live_results" / "eval" / "dataset.json"
SETS = ("documents", "tablelens", "routing", "guardrails", "structured")
Row = dict[str, Any]          # case_id, group, inputs, reference, outputs, scores


def row(case_id: str, group: str, inputs: dict, reference: dict, outputs: dict, scores: dict[str, float]) -> Row:
    return {"case_id": case_id, "group": group, "inputs": inputs, "reference": reference, "outputs": outputs,
            "scores": {k: float(v) for k, v in scores.items()}}


def summarize(rows: list[Row]) -> dict[str, Any]:
    """Mean of every score, overall and per group (a score is averaged only over the cases that have it)."""
    def means(subset: list[Row]) -> dict[str, float]:
        values: dict[str, list[float]] = defaultdict(list)
        for r in subset:
            for key, value in r["scores"].items():
                values[key].append(value)
        return {k: round(mean(v), 3) for k, v in sorted(values.items())} | {"cases": len(subset)}
    groups = sorted({r["group"] for r in rows})
    return {"overall": means(rows), "by_group": {g: means([r for r in rows if r["group"] == g]) for g in groups}}


# ------------------------------------------------------------------------------------------------ documents
def documents() -> list[Row]:
    from rag.release_v3.evaluate import relevant
    from rag.release_v3.multilingual import question_sets
    from rag.retrieval.versions import build_retriever

    retriever, rows = build_retriever("pilot_v3"), []
    for group, questions in question_sets().items():
        for q in questions:
            found = retriever.search(q["question"], method="hybrid", k=10, guarded=True)
            hits, gold = found["results"], q["gold"]
            top = [{"document_id": h["document_id"], "pages": [h["physical_page_start"], h["physical_page_end"]],
                    "project_id": h.get("project_id")} for h in hits[:5]]
            if gold:
                first = next((i + 1 for i, h in enumerate(hits) if any(relevant(h, g) for g in gold)), None)
                scores = {"recall@5": bool(first and first <= 5), "recall@10": bool(first), "mrr": 1 / first if first else 0.0,
                          "wrongly_refused": found["status"] != "ok"}
            else:
                scores = {"refused_correctly": found["status"] != "ok"}
            rows.append(row(q["id"], f"{group}:{q['type']}", {"question": q["question"]}, {"gold": gold},
                            {"status": found["status"], "top5": top}, scores))
    return rows


# ------------------------------------------------------------------------------------------------ TableLens
def tablelens() -> list[Row]:
    from live.tablelens_benchmark import NEGATIVES, QUESTIONS
    from rag.retrieval.versions import build_retriever, build_table_lens

    retriever, lens, rows = build_retriever("pilot_v3"), build_table_lens("pilot_v3_tablelens"), []

    def found(question: str) -> list[tuple[str, float]]:
        return [(t.table_id, round(s, 3)) for t, s in lens.find_tables(question, retriever.guard(question)["scope_projects"])]
    for n, (question, accepted, _) in enumerate(QUESTIONS, start=1):
        tables = found(question)
        ids = [t for t, _ in tables]
        rows.append(row(f"T{n:02d}", "table_questions", {"question": question}, {"tables": accepted}, {"found": tables},
                        {"hit@1": bool(ids[:1] and ids[0] in accepted), "hit@2": any(i in accepted for i in ids)}))
    for n, question in enumerate(NEGATIVES, start=1):
        tables = found(question)
        rows.append(row(f"TN{n}", "no_table", {"question": question}, {"tables": []}, {"found": tables},
                        {"nothing_found": not tables}))
    return rows


# -------------------------------------------------------------------------------------------------- routing
def routing() -> list[Row]:
    from conversation.followups import FollowUpResolver
    from conversation.models import ActiveContext
    from conversation.routing import IntentRouter, ReasonCategory, Route, RouteRequest
    from live.tablelens_benchmark import QUESTIONS
    from rag.release_v3.multilingual import question_sets
    from wbg_orchestrator.conversation_bridge import COMBINABLE, UNCLEAR

    resolver, router = FollowUpResolver(), IntentRouter()
    labelled = [(f"R-{cid}", "structured", text, Route.STRUCTURED_DATA) for cid, text, _, _ in STRUCTURED]
    labelled += [(f"R-T{n:02d}", "tablelens", q, Route.DOCUMENT_RAG) for n, (q, _, _) in enumerate(QUESTIONS, start=1)]
    labelled += [(f"R-{q['id']}", f"documents_{group}", q["question"], Route.DOCUMENT_RAG)
                 for group, questions in question_sets().items() for q in questions if q["gold"]]
    rows = []
    for case_id, group, text, expected in labelled:
        decision = router.route(RouteRequest(text, resolver.resolve(text, ActiveContext()), None))
        combined = decision.reason == ReasonCategory.MIXED_INTENT and set(decision.matched_signals) == COMBINABLE  # both agents
        acceptable = decision.route == expected or combined or decision.reason in UNCLEAR
        rows.append(row(case_id, group, {"question": text}, {"route": expected.value},
                        {"route": decision.route.value, "reason": decision.reason.value},
                        {"exact": decision.route == expected, "acceptable": acceptable}))
    return rows


# ----------------------------------------------------------------------------------------------- guardrails
def guardrails() -> list[Row]:
    from conversation.pii import redact_personal_data
    from structured_agent.guardrails.pipeline import build_guardrails

    rails, rows = build_guardrails(), []
    for case_id, text, allowed in golden_cases.INPUTS:
        decision = rails.check_input(text)
        reason = decision.reason_category.value if decision.reason_category else None
        rows.append(row(case_id, "allowed" if allowed else "blocked", {"message": text}, {"allowed": allowed},
                        {"allowed": decision.allowed, "reason": reason}, {"correct": decision.allowed == allowed}))
    for case_id, text, masked, kept in golden_cases.MASKING:
        out = redact_personal_data(text)
        scores = {}
        if masked:
            scores["masked"] = all(m not in out for m in masked)
        if kept:
            scores["kept"] = all(k in out for k in kept)
        rows.append(row(case_id, "masking", {"text": text}, {"mask": masked, "keep": kept}, {"redacted": out}, scores))
    return rows


# ----------------------------------------------------------------------------------------------- structured
def structured() -> list[Row]:
    from wbg_orchestrator.observability import OrchestratorTracer
    from wbg_orchestrator.runtime import RuntimeConfig, structured_tools
    from wbg_orchestrator.tool_gateway import gold_sql

    os.environ.pop("WBG_ALLOW_LIVE_LLM_CALLS", None)                    # no model anywhere in this set
    references = {e["inputs"]["case_id"]: e["outputs"]["values"] for e in json.loads(DATASET_FILE.read_text())
                  if e["inputs"]["category"] == "structured"}
    config = RuntimeConfig(ledger_path=RESULTS / "unused_ledger.json", max_attempts=0, max_usd=0)
    tools, close = structured_tools(config, OrchestratorTracer.disabled())          # the MCP server (WBG_TOOL_TRANSPORT)
    rows = []
    try:
        run = gold_sql(tools)
        for cid, question, sql, column in STRUCTURED:
            values = reference_values(run(sql, {}), column)
            rows.append(row(f"V-{cid}", "reference_values", {"question": question, "sql": sql}, {"values": references.get(cid)},
                            {"values": values}, {"values_equal": values == references.get(cid)}))
        for case_id, sql, approved in golden_cases.SQL:
            response = tools.call("validate_sql", {"sql": sql}, "golden-eval")
            verdict = bool((response.get("report") or {}).get("approved"))
            codes = sorted({e["code"] for e in response.get("errors", [])})
            correct = verdict if approved else (not verdict and bool(codes))       # a rejection must state its reason
            rows.append(row(case_id, "validator_approves" if approved else "validator_rejects", {"sql": sql},
                            {"approved": approved}, {"approved": verdict, "error_codes": codes}, {"correct": correct}))
    finally:
        close()
    return rows


RUNNERS: dict[str, Callable[[], list[Row]]] = {"documents": documents, "tablelens": tablelens, "routing": routing,
                                               "guardrails": guardrails, "structured": structured}


# ------------------------------------------------------------------------------------------------ commands
def run_sets(names: list[str]) -> dict[str, Any]:
    RESULTS.mkdir(parents=True, exist_ok=True)
    summaries = {}
    for name in names:
        rows = RUNNERS[name]()
        summaries[name] = summarize(rows)
        (RESULTS / f"{name}.json").write_text(json.dumps({"summary": summaries[name], "rows": rows}, indent=1, default=str))
        print(name, json.dumps(summaries[name]["overall"]), flush=True)
    return summaries


def upload_sets(names: list[str]) -> dict[str, Any]:
    """Per set: a dataset (content-hashed name) and an experiment whose outputs and scores are the local results."""
    from langsmith import evaluate

    from .run import client, code_version, langsmith_settings

    if not langsmith_settings()["LANGSMITH_API_KEY"]:
        raise SystemExit("LANGSMITH_API_KEY is not set (environment or .env)")
    ls, uploaded = client(), {}
    for name in names:
        rows = json.loads((RESULTS / f"{name}.json").read_text())["rows"]
        examples = [{"inputs": {"case_id": r["case_id"], "group": r["group"], **r["inputs"]}, "outputs": r["reference"],
                     "metadata": {"group": r["group"]}} for r in rows]
        digest = hashlib.sha256(json.dumps(examples, sort_keys=True, default=str).encode()).hexdigest()[:8]
        dataset = f"wbg-golden-{name}-{digest}"
        if not ls.has_dataset(dataset_name=dataset):
            created = ls.create_dataset(dataset, description=f"WBG golden set '{name}' (orchestrator/evaluation/golden.py)")
            ls.create_examples(dataset_id=created.id, examples=examples)
        by_case = {r["case_id"]: r for r in rows}
        results = evaluate(lambda inputs: {**by_case[inputs["case_id"]]["outputs"], "scores": by_case[inputs["case_id"]]["scores"]},
                           data=dataset, evaluators=[_scores], client=ls, experiment_prefix=f"wbg-golden-{name}",
                           metadata={"code_version": code_version(), "set": name, "llm_calls": 0}, max_concurrency=4)
        uploaded[name] = {"dataset": dataset, "experiment": results.experiment_name, "examples": len(examples),
                          "url": _experiment_url(ls, results.experiment_name)}
        print(name, json.dumps(uploaded[name]), flush=True)
    links = RESULTS / "langsmith.json"
    known = json.loads(links.read_text()) if links.exists() else {}
    links.write_text(json.dumps(known | uploaded, indent=1))                # uploading one set keeps the others' links
    return uploaded


def _scores(inputs: dict[str, Any], outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict[str, Any]:
    return {"results": [{"key": k, "score": v} for k, v in (outputs.get("scores") or {}).items()]}


def _experiment_url(ls, experiment: str) -> str | None:
    try:
        return ls.read_project(project_name=experiment).url
    except Exception:                                    # the link is a convenience; the experiment name is enough
        return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Golden evaluations (no LLM)")
    parser.add_argument("step", choices=["run", "upload"])
    parser.add_argument("--sets", nargs="*", choices=SETS, default=list(SETS))
    args = parser.parse_args(argv)
    (run_sets if args.step == "run" else upload_sets)(args.sets)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
