"""Answer-level comparison of the two RAG versions with the deterministic evidence-only generator (Step 6E).

    /opt/anaconda3/bin/python3 -B -m rag.release_v2_1.answer_comparison      (from procurement-intelligence/)

Runs rag/answering's AnswerPipeline (EvidenceOnlyGenerator: no language model; NLI off) over the 31 benchmark questions
with build_retriever("pilot_v2") and build_retriever("pilot_v2_1"), and compares status, cited units and pages,
guard outcomes and privacy flags. Also checks factual claims about the corrected financing table with the unchanged
claim validator. Sockets blocked. Writes results/answer_comparison.json only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from rag.embedding_cache.verify_compatibility import no_network

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "rag" / "answering"))
RESULTS = Path(__file__).resolve().parent / "results" / "answer_comparison.json"
CLAIMS = ("IDA financing covers the total project costs of US$370.00 million (100 percent).",
          "The construction of the high capacity highway costs US$340.00 million, financed by IDA.",
          "Institutional development and regulatory framework costs US$18.00 million.")


def summarise(response) -> dict[str, Any]:
    return {"status": response.status, "removed_claims": len(response.removed_claims),
            "citations": [{"unit_id": c.unit_id, "document_id": c.document_id, "pages": [c.physical_page_start, c.physical_page_end],
                           "page_check": c.page_check} for c in response.citations]}


def run() -> dict[str, Any]:
    questions = json.loads((ROOT / "rag" / "retrieval" / "benchmark_v1.json").read_text())["questions"]
    with no_network() as attempts:
        from generator import EvidenceOnlyGenerator
        from pipeline import AnswerPipeline
        from query import understand
        from validate import check_claim

        from rag.retrieval.versions import build_retriever
        pipes = {v: AnswerPipeline(EvidenceOnlyGenerator(), use_nli=False, retriever=build_retriever(v)) for v in ("pilot_v2", "pilot_v2_1")}
        answers = {v: {q["id"]: summarise(p.answer(q["question"])) for q in questions} for v, p in pipes.items()}
        claim_checks = {}
        q27 = next(q for q in questions if q["id"] == "Q27")
        for v, p in pipes.items():
            pack = p.sel.select(q27["question"], understand(q27["question"]))
            ev = {e.eid: e for e in pack.items}
            table = next(e for e in pack.items if e.unit_id == "24440102:t02201:w1-2")
            claim_checks[v] = [{"claim": c, **{k: check_claim({"text": c, "evidence_ids": [table.eid]}, ev, None)[k]
                                               for k in ("supported", "unsupported_numbers")}} for c in CLAIMS]
    old, new = answers["pilot_v2"], answers["pilot_v2_1"]
    differing = {qid: {"pilot_v2": old[qid], "pilot_v2_1": new[qid]} for qid in old if old[qid] != new[qid]}
    statuses = lambda a: {s: sum(1 for x in a.values() if x["status"] == s) for s in sorted({x["status"] for x in a.values()})}  # noqa: E731
    result = {"questions": len(questions), "status_counts": {"pilot_v2": statuses(old), "pilot_v2_1": statuses(new)},
              "differing_questions": differing,
              "guarded_outcomes_identical": all((old[q]["status"] in ("withheld", "insufficient_evidence"))
                                                == (new[q]["status"] in ("withheld", "insufficient_evidence")) for q in old),
              "financing_table_claim_checks": claim_checks, "network_attempts_blocked": len(attempts)}
    RESULTS.write_text(json.dumps(result, indent=1, default=str))
    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, default=str))
