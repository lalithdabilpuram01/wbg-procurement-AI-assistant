"""Stages 2-4: guarded retrieval (existing core), evidence selection / diversification, injection screening and
page verification. Evidence IDs (E1..En) are the only handles the generator sees."""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

RET = Path(__file__).resolve().parents[1] / "retrieval"
sys.path.insert(0, str(RET))
from core import Retriever  # noqa: E402
from evaluate import PageText, norm  # noqa: E402

MAX_EVIDENCE = 8
MAX_PER_DOCUMENT = 3
INJECTION = re.compile(r"ignore (all |any )?(previous|prior|above) instructions|disregard (the )?(system|previous)|you are now|new instructions:|"
                       r"system prompt|act as an? |reveal (the|your) (prompt|instructions)|respond only with|override (the )?(rules|policy)|"
                       r"(assistant|model|ai|you) (must|should) (always )?(answer|reply|respond|say|state)|(always|must) (reply|respond|answer) that", re.I)


@dataclass
class Evidence:
    """One piece of evidence shown to the generator under its ID (E1, E2, ...), with page-check and quarantine flags."""
    eid: str
    unit_id: str
    record: dict                 # core.cite() record (metadata + excerpt)
    text: str                    # text shown to the generator (typed facts for plan rows)
    context: str = ""            # strategy-C parent text (section context; not separately citable)
    page_check: str = "unchecked"
    page_check_detail: dict = field(default_factory=dict)
    quarantined: bool = False


@dataclass
class EvidencePack:
    """Result of evidence selection: retrieval status, notes, the selected evidence and a trace for debugging."""
    status: str
    notes: list[str]
    items: list[Evidence]
    trace: dict


def typed_text(r: dict) -> str:
    """Render a procurement-plan row and its contract links as plain-text facts for the generator."""
    a = r["activity"]
    links = "; ".join(f"contract link status={e['evidence_status']} match={e['match_class']} WB contract {e['wb_contract_number']} "
                      f"recorded signing {e['date_recorded_signing']} contract amount US${e['contract_amount_usd']}"
                      + (f" DISAGREES on {', '.join(e['disagreeing_fields'])}" if e["disagreeing_fields"] else "") for e in r.get("evidence_links", []))
    stages = "; ".join(f"{k}: " + ", ".join(f"{kk} {vv}" for kk, vv in v.items()) for k, v in (a.get("stage_dates") or {}).items())
    return (f"Procurement plan row (typed record). Plan version {r['plan_version_id']} (stream {r['plan_stream_id']}). "
            f"Reference {a['activity_reference_normalized']}. Description: {a.get('description_clean')}. Method: {a['method_clean']}. "
            f"Market approach: {a['market_approach_clean']}. Review: {a.get('review_type')}. Process status: {a['process_status_clean']}. "
            f"Estimated amount US$ {a['estimated_amount_usd']}. Actual amount US$ {a['actual_amount_usd']}. "
            f"Signed contract planned {a['signed_contract_planned']}, actual {a['signed_contract_actual']}. Stage dates: {stages}. "
            f"{('Contract links: ' + links + '.') if links else 'No contract link.'}")


def classify_page_check(r: dict, v: dict, pt: PageText) -> tuple[str, dict]:
    """verified / verified_with_text_exception (explained cleaning or reading-order difference) / unverified."""
    if v.get("verified") is None:
        return "not_checkable", v
    if v["verified"]:
        return "verified", v
    ratio = v["found"] / max(1, v["probes"])
    pages = range(r["physical_page_start"], r["physical_page_end"] + 1)
    text = "".join(pt.get(r["document_id"], p) for p in pages)
    if r["unit_type"] == "activity":
        ref = r["activity"].get("package_reference_core") or r["activity"].get("activity_reference_normalized") or ""  # core ref has no spill-over
        parts = [norm(x) for x in re.split(r"[-/ ]", ref) if x]
        if parts and all(p in text for p in parts):
            return "verified_with_text_exception", {**v, "reason": "printed reference has adjacent-cell spill-over; all normalised reference parts are on the cited page"}
    if ratio >= 0.25:
        return "verified_with_text_exception", {**v, "reason": "part of the excerpt differs from the PDF text layer (word repair, hyphenation or reading order); remaining probes found on the cited page"}
    return "unverified", {**v, "reason": "excerpt not found on the cited page"}


class EvidenceSelector:
    """Picks the evidence for one question from guarded retrieval and checks each item before generation."""
    def __init__(self, retriever: Retriever):
        self.R = retriever
        self.c = retriever.c
        self.pt = PageText(self.c)

    def _targeted(self, q: str, doc_ids: list[str]) -> list[int]:
        """Coverage retrieval restricted to given documents: the best unit by fused rank, by dense and by BM25 separately.
        (Fusion alone can bury a unit one signal ranks first, e.g. a numeric disbursement table that BM25 ranks last.)"""
        mask = np.array([u.document_id in doc_ids for u in self.c.units]) & self.R.keep
        if not mask.any():
            return []
        b, d = self.R.rank_bm25(q, mask), self.R.rank_dense(q, mask)
        rb, rd = np.empty(len(b)), np.empty(len(d))
        rb[np.argsort(-b)] = np.arange(1, len(b) + 1)
        rd[np.argsort(-d)] = np.arange(1, len(d) + 1)
        f = 1 / (60 + rb) + 1 / (60 + rd)
        f[~mask] = -np.inf
        out = []
        for s in (f, d, b):
            i = int(np.argmax(s))
            if np.isfinite(s[i]) and i not in out:
                out.append(i)
        return out

    def select(self, q: str, plan, injected_units: list[dict] | None = None) -> EvidencePack:
        """Run guarded hybrid retrieval and return up to MAX_EVIDENCE diverse, screened and page-checked evidence items.
        Items with instruction-like text are kept but flagged as quarantined; injected_units are appended after them."""
        res = self.R.search(q, method="hybrid", k=20, guarded=True)
        trace = {"retrieval_status": res["status"], "retrieved_unit_ids": [r["unit_id"] for r in res["results"]], "targeted": {}}
        if res["status"] != "ok":
            return EvidencePack(res["status"], res["notes"], [], trace)
        recs = list(res["results"])
        projects = {r["project_id"] for r in recs[:10]}
        # comparison / document-type coverage: make sure each hinted (or, for comparisons, each in-scope) document type contributes
        if plan.comparison or len(plan.doc_type_hints) > 1:
            scope = res["results"] and {r["project_id"] for r in res["results"][:3]}
            want = plan.doc_type_hints or sorted({d["doc_type"] for d in self.c.docs.values() if d["project_id"] in scope})
            for dt in want:
                docs = [d for d, m in self.c.docs.items() if m["doc_type"] == dt and m["project_id"] in scope]
                if docs:
                    idx = self._targeted(q, docs)
                    trace["targeted"][dt] = [self.c.units[i].unit_id for i in idx]
                    for i in idx:
                        if all(r["unit_id"] != self.c.units[i].unit_id for r in recs):
                            recs.append(self.c.cite(self.c.units[i], len(recs) + 1, {"targeted_document_coverage": True}))
        # selection: exact typed records first; then diversity across documents; drop near-duplicates
        exact = [r for r in recs if r["scores"].get("exact_reference_match")]
        rest = [r for r in recs if not r["scores"].get("exact_reference_match")]
        targeted = [r for r in rest if r["scores"].get("targeted_document_coverage")]
        ordinary = [r for r in rest if not r["scores"].get("targeted_document_coverage")]
        chosen, per_doc, seen_parent, seen_table_rows = [], {}, set(), {}
        def take(r: dict) -> bool:
            """Add a record unless it repeats a unit, a parent, table rows or a full document; return True when added.
            Exact reference matches are exempt from the per-document limit."""
            if any(c["unit_id"] == r["unit_id"] for c in chosen):
                return False
            if r.get("parent_id") and r["parent_id"] in seen_parent:
                return False
            if r["unit_type"] == "table":
                rows = set(self.c.by_id[r["unit_id"]].meta.get("rows", []))
                if rows & seen_table_rows.get(r["table_id"], set()):
                    return False
            if per_doc.get(r["document_id"], 0) >= MAX_PER_DOCUMENT and not r["scores"].get("exact_reference_match"):
                return False
            chosen.append(r)
            per_doc[r["document_id"]] = per_doc.get(r["document_id"], 0) + 1
            if r.get("parent_id"):
                seen_parent.add(r["parent_id"])
            if r["unit_type"] == "table":
                seen_table_rows.setdefault(r["table_id"], set()).update(self.c.by_id[r["unit_id"]].meta.get("rows", []))
            return True
        for r in exact:
            take(r)
        # interleave: best ordinary hit, then the targeted coverage hits, then the rest
        queue = ordinary[:1] + targeted + ordinary[1:]
        for r in queue:
            if len(chosen) >= MAX_EVIDENCE:
                break
            take(r)
        items = []
        for n, r in enumerate(chosen + (injected_units or []), start=1):
            u = self.c.by_id.get(r["unit_id"])
            text = typed_text(r) if r["unit_type"] == "activity" else r["excerpt"]
            ctx = ""
            # Strategy C: attach the start of the parent section as context (it is shown, but not citable on its own).
            if u is not None and u.parent_id and u.parent_id in self.c.parents:
                ctx = self.c.parents[u.parent_id].get("text", "")[:600]
            e = Evidence(f"E{n}", r["unit_id"], r, text, ctx)
            # Quarantine text that looks like a prompt-injection attempt; the pipeline never shows it to the generator.
            if INJECTION.search(text) or INJECTION.search(ctx):
                e.quarantined = True
            if u is not None:
                from evaluate import verify
                e.page_check, e.page_check_detail = classify_page_check(r, verify(r, self.pt), self.pt)
            else:
                e.page_check = "not_in_index"
            items.append(e)
        trace["selected"] = [(e.eid, e.unit_id, e.record["document_id"], e.page_check, e.quarantined) for e in items]
        trace["projects_in_top10"] = sorted(projects)
        return EvidencePack("ok", res["notes"], items, trace)
