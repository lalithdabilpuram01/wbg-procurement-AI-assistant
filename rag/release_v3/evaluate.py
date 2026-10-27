"""Retrieval evaluation for the pilot_v3 release (Milestone D): old default vs new corpus and options.

    /opt/anaconda3/bin/python3 -B -m rag.release_v3.evaluate        (from procurement-intelligence/)

Benchmarks
  v1   the 31 hand-verified questions of rag/retrieval/benchmark_v1.json (10 pilot documents). They remain valid on
       pilot_v3, which contains those documents plus 227 more, so they also measure distraction by the larger corpus.
  v3   generated from pilot_v3's section structure, written to results/benchmark_v3.json:
       - for each project, questions by project NAME (no project id) whose gold pages are the section that answers
         them: the PAD "development objective" and "risk" sections, the ICR/ICRR "lessons" sections and the PAD
         procurement sections;
       - abstention questions that must be refused (out of scope, unknown reference, personal contact data).
       Gold is derived from headings (not hand-verified); results say so.
Configurations (hybrid search, strategy C with tables, k = 10)
  pilot_v2                      the current default (v1 only: it does not contain the other projects)
  pilot_v3                      new corpus, no project-name resolution, no re-ranker
  pilot_v3+names                with project-name resolution (the registered pilot_v3 behaviour)
  pilot_v3+names+rerank         plus the cached cross-encoder re-ranker
Metrics: recall@5, recall@10, MRR, project precision@5 (share of top-5 from the asked project), abstention.
Relevance rule: as rag/retrieval/evaluate.py (same document and an overlapping physical page).
Writes results/evaluation.json and results/per_question_v3.csv. No network access; nothing outside results/ is written.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from rag.embedding_cache.verify_compatibility import no_network
from rag.ingestion import config as ingestion_config
from rag.retrieval import core
from rag.retrieval.versions import build_retriever

sys.path.insert(0, str(Path(core.__file__).resolve().parent))
from evaluate import relevant  # noqa: E402  (the existing relevance rule)

RESULTS = Path(__file__).resolve().parent / "results"
V1 = Path(core.__file__).resolve().parent / "benchmark_v1.json"
TOPICS = [  # (type, document types, heading pattern, question template)
    ("objective", ("PAD",), r"development objective", "What is the project development objective of the {title}?"),
    ("risks", ("PAD",), r"\brisks?\b", "What are the key risks identified for the {title} in {country}?"),
    ("lessons", ("ICR", "ICRR"), r"\blessons?\b", "What lessons were learned from the {title}?"),
    ("procurement", ("PAD",), r"\bprocurement\b", "How is procurement organized and managed for the {title}?"),
]
ABSTAIN = [
    ("out_of_scope_project", "What is the development objective of project P999999?"),
    ("out_of_scope_country", "What lessons were learned from road projects in Kenya?"),
    ("unknown_reference", "What is the planned signing date for activity ET-ERA-999999-CW-RFB?"),
    ("unknown_contract", "Which supplier won contract number 9999999?"),
    ("personal_data", "What is the email address and phone number of the project procurement specialist?"),
]


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def build_benchmark_v3() -> dict[str, Any]:
    shared = ingestion_config.OUTPUT_V3 / "shared"
    projects = {p["project_id"]: p["api"] for p in _jsonl(shared / "projects.jsonl")}
    docs = {d["document_id"]: d for d in _jsonl(shared / "documents.jsonl")}
    questions = []
    for topic, doc_types, heading, template in TOPICS:
        pattern = re.compile(heading, re.I)
        gold_by_project: dict[str, list[dict]] = {}
        for section in _jsonl(shared / "sections.jsonl"):
            doc = docs.get(section["document_id"])
            if doc and doc["doc_type"] in doc_types and pattern.search(section["heading_text"]) and section["heading_level"] >= 1:
                pages = list(range(section["physical_page_start"], section["physical_page_end"] + 1))[:6]
                gold_by_project.setdefault(doc["project_id"], []).append({"document_id": doc["document_id"], "pages": pages})
        for pid in sorted(gold_by_project):
            api = projects.get(pid, {})
            if not api.get("project_title"):
                continue
            questions.append({"id": f"V3-{topic}-{pid}", "type": topic, "project_id": pid,
                              "question": template.format(title=api["project_title"], country=api.get("country_name", "")),
                              "gold": gold_by_project[pid][:4], "require_all": False})
    questions += [{"id": f"V3-abstain-{name}", "type": "insufficient", "project_id": None, "question": q, "gold": [],
                   "require_all": False} for name, q in ABSTAIN]
    return {"version": "v3-generated-1", "corpus": str(ingestion_config.OUTPUT_V3.relative_to(ingestion_config.REPO)),
            "gold_rule": "pages of the document sections whose heading matches the topic (not hand-verified)",
            "questions": questions}


def run_benchmark(retriever, questions: list[dict], rerank: bool) -> list[dict[str, Any]]:
    rows = []
    for q in questions:
        res = retriever.search(q["question"], method="hybrid", k=10, guarded=True, rerank=rerank)
        hits, groups = res["results"], q["gold"]
        first = next((i + 1 for i, r in enumerate(hits) if any(relevant(r, g) for g in groups)), None)
        top5 = hits[:5]
        rows.append({"qid": q["id"], "type": q["type"], "answerable": bool(groups), "status": res["status"],
                     "recall@5": any(relevant(r, g) for r in top5 for g in groups) if groups else None,
                     "recall@10": any(relevant(r, g) for r in hits for g in groups) if groups else None,
                     "mrr": (1 / first if first else 0.0) if groups else None,
                     "project_precision@5": (sum(r["project_id"] == q.get("project_id") for r in top5) / len(top5))
                     if q.get("project_id") and top5 else None,
                     "abstained": res["status"] != "ok"})
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    df = pd.DataFrame(rows)
    answerable, unanswerable = df[df.answerable], df[~df.answerable]

    def mean(column: str) -> float | None:
        values = pd.to_numeric(answerable[column], errors="coerce").dropna()
        return round(float(values.mean()), 3) if len(values) else None
    return {"questions": len(df), "recall@5": mean("recall@5"), "recall@10": mean("recall@10"), "MRR": mean("mrr"),
            "project_precision@5": mean("project_precision@5"),
            "abstained_correctly": f"{int(unanswerable['abstained'].sum())}/{len(unanswerable)}",
            "answerable_wrongly_refused": int(answerable["abstained"].sum()),
            "by_type_recall@5": answerable.assign(hit=answerable["recall@5"].astype(float)).groupby("type")["hit"].mean().round(3).to_dict()}


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    v1 = json.loads(V1.read_text())["questions"]
    benchmark_v3 = build_benchmark_v3()
    (RESULTS / "benchmark_v3.json").write_text(json.dumps(benchmark_v3, indent=1))
    v3 = benchmark_v3["questions"]
    report: dict[str, Any] = {"benchmark_v3": {"questions": len(v3), "gold_rule": benchmark_v3["gold_rule"]}, "configs": {}}
    per_question = []
    with no_network():
        default = build_retriever("pilot_v2")
        report["configs"]["pilot_v2"] = {"v1": summarize(run_benchmark(default, v1, rerank=False))}
        named = build_retriever("pilot_v3_gte_local")                                      # registered: project names on
        plain = core.Retriever(named.c, embedding_cache="unit")                  # same corpus and vectors, names off
        for name, retriever, rerank in (("pilot_v3", plain, False), ("pilot_v3+names", named, False),
                                        ("pilot_v3+names+rerank", named, True)):
            rows_v1, rows_v3 = run_benchmark(retriever, v1, rerank), run_benchmark(retriever, v3, rerank)
            report["configs"][name] = {"v1": summarize(rows_v1), "v3": summarize(rows_v3)}
            per_question += [{"config": name, **r} for r in rows_v3]
            print(name, json.dumps({k: {m: v for m, v in s.items() if m != "by_type_recall@5"}
                                    for k, s in report["configs"][name].items()}), flush=True)
    pd.DataFrame(per_question).to_csv(RESULTS / "per_question_v3.csv", index=False)
    (RESULTS / "evaluation.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report["configs"]["pilot_v2"]))


if __name__ == "__main__":
    main()
