"""Grounded answer workflow: question -> guarded retrieval -> evidence selection / verification -> generation ->
deterministic validation -> final response or abstention. Citations are built from index metadata, never from model text.

Usage (from procurement-intelligence/):
  HF_HUB_OFFLINE=1 /opt/anaconda3/bin/python3 rag/answering/pipeline.py "question" [--generator ollama|evidence_only] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "retrieval"))
from core import Corpus, Retriever  # noqa: E402
from evidence import EvidenceSelector  # noqa: E402
from generator import EvidenceOnlyGenerator, OllamaGenerator, TypedRecordAnswerer  # noqa: E402
from query import understand  # noqa: E402
from schemas import AnswerResponse, Citation, ClaimResult, Conflict  # noqa: E402
from validate import NLI, check_claim, link_status_notes, numbers, typed_conflicts  # noqa: E402

ABSTAIN_TEXT = {"insufficient_evidence": "The indexed documents and typed records do not contain enough evidence to answer this question.",
                "withheld": "This request asks for personal contact information, which is masked in the corpus and is not returned."}


class AnswerPipeline:
    def __init__(self, generator, strategy: str = "C", use_nli: bool = True, typed_router: bool = True, retriever=None):
        self.R = retriever or Retriever(Corpus(strategy))
        self.typed = TypedRecordAnswerer() if typed_router else None
        self.sel = EvidenceSelector(self.R)
        self.gen = generator
        self.nli = NLI() if use_nli else None

    def citation(self, e) -> Citation:
        r = e.record
        return Citation(evidence_id=e.eid, unit_id=e.unit_id, unit_type=r["unit_type"], project_id=r["project_id"], project_name=r.get("project_name"),
                        document_id=r["document_id"], title=r.get("title"), doc_type=r["doc_type"], document_date=r.get("document_date"),
                        document_version_id=r["document_version_id"], physical_page_start=r["physical_page_start"], physical_page_end=r["physical_page_end"],
                        printed_page_label=r.get("printed_page_label"), section=r.get("section"), table_id=r.get("table_id"),
                        excerpt=e.text[:600], source_url=r.get("source_url"), association=r["association"], page_check=e.page_check,
                        contract_links=r.get("evidence_links", []) or [], plan_version_id=r.get("plan_version_id"))

    def answer(self, question: str, injected_units: list[dict] | None = None) -> AnswerResponse:
        t0 = time.perf_counter()
        plan = understand(question)
        pack = self.sel.select(question, plan, injected_units)
        t_ret = time.perf_counter()
        base = dict(question=question, generator="none (stopped before generation by a guard or required-field check)", generator_is_llm=False,
                    trace={"plan": plan.__dict__, **pack.trace})
        if pack.status != "ok":
            st = "withheld" if pack.status == "withheld_personal_data" else "insufficient_evidence"
            return AnswerResponse(status=st, answer=ABSTAIN_TEXT[st], citations=[], supporting_evidence_ids=[], conflicts=[], limitations=pack.notes,
                                  timings_ms={"retrieval": round(1000 * (t_ret - t0)), "total": round(1000 * (time.perf_counter() - t0))}, **base)
        usable = [e for e in pack.items if not e.quarantined and e.page_check != "unverified"]
        limitations = [f"Evidence {e.eid} ({e.unit_id}) was quarantined: it contains instruction-like text and was not shown to the generator."
                       for e in pack.items if e.quarantined]
        # required fields that must be explicitly present (e.g. bidder counts): checked before generation
        missing = [name for name, rx in plan.required_fields.items() if not any(rx.search(e.text) for e in usable)]
        if missing:
            return AnswerResponse(status="insufficient_evidence", answer=ABSTAIN_TEXT["insufficient_evidence"] + f" Requested field not present in any retrieved evidence: {', '.join(missing)}.",
                                  citations=[], supporting_evidence_ids=[], conflicts=[], limitations=limitations + [f"'{m}' is not recorded in the retrieved plans, contract records or documents; it is not inferred." for m in missing],
                                  timings_ms={"retrieval": round(1000 * (t_ret - t0)), "total": round(1000 * (time.perf_counter() - t0))}, **base)
        gen = self.gen
        if self.typed and any(e.record["unit_type"] == "activity" and e.record["scores"].get("exact_reference_match") for e in usable):
            gen = self.typed      # identifiers, dates and amounts come from typed records, not from a language model
        base["generator"], base["generator_is_llm"] = gen.name, gen.is_llm
        draft, gmeta = gen.generate(question, usable)
        t_gen = time.perf_counter()
        if draft is None:
            return AnswerResponse(status="insufficient_evidence", answer=ABSTAIN_TEXT["insufficient_evidence"], citations=[], supporting_evidence_ids=[], conflicts=[],
                                  limitations=limitations + ["Generation failed to return valid structured output; no answer is shown."],
                                  timings_ms={"retrieval": round(1000 * (t_ret - t0)), "generation": round(1000 * (t_gen - t_ret))},
                                  **{**base, "trace": {**base["trace"], "generator_meta": {**gmeta, "generation_failed": True}}})
        ev = {e.eid: e for e in pack.items}
        kept, removed = [], []
        for c in draft.claims:
            chk = check_claim(c.model_dump(), ev, self.nli, mode="typed" if gen is self.typed and self.typed is not None else "text")
            cr = ClaimResult(text=c.text, evidence_ids=c.evidence_ids, supported=chk["supported"], checks=chk)
            (kept if chk["supported"] else removed).append(cr)
        conflicts = [Conflict(field=x["field"], values=x["values"], source="typed_record") for x in typed_conflicts(usable)]
        for dc in draft.conflicts:   # generator-reported conflicts are kept only if every value is supported by its own evidence
            vals = []
            for v in dc.values:
                e = ev.get(v.evidence_id)
                ok = e is not None and not e.quarantined and (not numbers(v.value) or numbers(v.value) <= numbers(e.text + " " + e.context)) \
                    and v.value.lower().strip(". ") in (e.text + " " + e.context).lower() if not numbers(v.value) else e is not None and numbers(v.value) <= numbers(e.text + " " + e.context)
                if ok:
                    vals.append({"value": v.value, "evidence_id": v.evidence_id, "document_id": e.record["document_id"], "doc_type": e.record["doc_type"],
                                 "document_date": e.record.get("document_date")})
            if len({v["document_id"] for v in vals}) >= 2:
                conflicts.append(Conflict(field=dc.field, values=vals, source="generator_reported"))
        limitations += [f"Removed unsupported claim: {r.text[:160]}" for r in removed]
        limitations += link_status_notes([k.model_dump() for k in kept], usable)
        if conflicts:
            status = "conflicting_evidence"
        elif not kept:
            status = "insufficient_evidence"
        elif removed or draft.status == "partial":
            status = "partial"
        else:
            status = "answered" if draft.status in ("answered", "conflicting_evidence") else draft.status
        if status == "insufficient_evidence" and draft.status != "insufficient_evidence" and draft.claims:
            limitations.append("No generated claim could be verified against the cited evidence.")
        answer = " ".join(k.text for k in kept) if kept else ABSTAIN_TEXT["insufficient_evidence"]
        cited = sorted({i for k in kept for i in k.evidence_ids} | {v["evidence_id"] for c in conflicts for v in c.values}, key=lambda x: int(x[1:]))
        cits = [self.citation(ev[i]) for i in cited if i in ev]
        t_end = time.perf_counter()
        return AnswerResponse(status=status, answer=answer, citations=cits, supporting_evidence_ids=cited, conflicts=conflicts,
                              limitations=list(dict.fromkeys(limitations)), claims=kept, removed_claims=removed,
                              timings_ms={"retrieval_and_selection": round(1000 * (t_ret - t0)), "generation": round(1000 * (t_gen - t_ret)),
                                          "validation": round(1000 * (t_end - t_gen)), "total": round(1000 * (t_end - t0))},
                              **{**base, "trace": {**base["trace"], "generator_meta": gmeta}})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--generator", default="ollama", choices=["ollama", "evidence_only"])
    ap.add_argument("--no-typed-router", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    gen = OllamaGenerator() if a.generator == "ollama" else EvidenceOnlyGenerator()
    res = AnswerPipeline(gen, typed_router=not a.no_typed_router).answer(a.question)
    print(res.model_dump_json(indent=1))
    if a.json:
        Path(a.json).write_text(res.model_dump_json(indent=1))


if __name__ == "__main__":
    main()
