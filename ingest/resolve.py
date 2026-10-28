"""Entity extraction and resolution across disjointed keys.

Mentions are found in every chunk (with character spans) and resolved against the reference records:

    E    exact key           a registered project id, a World Bank contract number
    EN   normalised key      a borrower/STEP reference equal after removing spacing and punctuation
    P    probabilistic       a reference within the same project matched by fuzzy similarity (>= FUZZY_MIN), or a
                             contract signed within TEMPORAL_WINDOW days of a supervision/completion report
    K    key only            an identifier with no reference table (loan and credit numbers), kept as its own node
    U    unresolved          recorded with its span, but no edge is drawn

Every link keeps its match class, a confidence and the evidence (rule, span, the compared values), so a reviewer can
see why two records were joined. Links across files (a contract feed and a later procurement plan) are reconciled in
the database by `ingest.store.reconcile`, set-based, so they do not depend on the order files arrive in.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from .model import Link, ParsedDocument
from .reference import Reference, normalise_reference, slug

FUZZY_MIN = 92
TEMPORAL_WINDOW = 180                  # days between a contract signature and an ISR/ICR that may report on it
PROJECT = re.compile(r"\bP\d{6}\b")
STEP_REFERENCE = re.compile(r"\b[A-Z]{2}-[A-Z0-9]{2,12}-\d{2,7}-[A-Z]{2}-[A-Z]{2,6}\b")
LOAN = re.compile(r"\b(?:IDA|IBRD|TF)[- ]?(?:[A-Z][- ]?)?\d{3,6}\b|\b\d{4}-[A-Z]{2}\b")
METHODS = {  # surface form -> canonical procurement method
    "request for bids": "Request for Bids", "rfb": "Request for Bids", "request for quotations": "Request for Quotations",
    "rfq": "Request for Quotations", "request for proposals": "Request for Proposals", "direct selection": "Direct Selection",
    "direct contracting": "Direct Selection", "quality and cost-based selection": "Quality and Cost-Based Selection",
    "quality- and cost-based selection": "Quality and Cost-Based Selection", "qcbs": "Quality and Cost-Based Selection",
    "consultant's qualifications selection": "Consultant's Qualification-Based Selection",
    "consultant qualification selection": "Consultant's Qualification-Based Selection", "cqs": "Consultant's Qualification-Based Selection",
    "least cost selection": "Least Cost Selection", "lcs": "Least Cost Selection",
    "individual consultant selection": "Individual Consultant Selection", "national competitive bidding": "National Competitive Bidding",
    "ncb": "National Competitive Bidding", "international competitive bidding": "International Competitive Bidding",
    "icb": "International Competitive Bidding", "shopping": "Shopping",
}
METHOD = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in METHODS), key=len, reverse=True)) + r")\b", re.I)
CONFIDENCE = {"E": 1.0, "EN": 0.95, "K": 0.9}


@dataclass
class Mention:
    chunk_id: str
    entity_type: str                   # project | step_reference | loan | country | method
    value: str
    node_id: str | None
    match_class: str
    confidence: float
    start: int
    end: int


@dataclass
class Resolution:
    mentions: list[Mention] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    contracts: dict[str, dict] = field(default_factory=dict)       # reference contracts the links point to


def method_name(value: str | None) -> str | None:
    return METHODS.get((value or "").strip().lower(), value.strip() if value else None)


def resolve(doc: ParsedDocument, ref: Reference) -> Resolution:
    res = Resolution()
    country_pattern = _country_pattern(ref)
    for chunk in doc.chunks:
        res.mentions += _mentions(chunk.chunk_id, chunk.text, doc.project_id, ref, country_pattern, res)
    res.links += _temporal(doc, ref, res)
    for link in doc.links:                                              # the extractor's activity -> contract matches
        number = link.dst.split(":", 1)[1]
        if number in ref.contracts_by_number:
            res.contracts[number] = ref.contracts_by_number[number]
    return res


def _mentions(chunk_id: str, text: str, project_id: str | None, ref: Reference, country_pattern, res: Resolution) -> list[Mention]:
    found = []
    for m in PROJECT.finditer(text):
        known = m.group(0) in ref.projects
        found.append(Mention(chunk_id, "project", m.group(0), f"project:{m.group(0)}", "E" if known else "K",
                             CONFIDENCE["E" if known else "K"], m.start(), m.end()))
    for m in STEP_REFERENCE.finditer(text):
        found.append(_reference(chunk_id, m, project_id, ref, res))
    for m in LOAN.finditer(text):
        key = normalise_reference(m.group(0))
        found.append(Mention(chunk_id, "loan", m.group(0), f"loan:{key}", "K", CONFIDENCE["K"], m.start(), m.end()))
    for m in METHOD.finditer(text):
        name = method_name(m.group(0))
        found.append(Mention(chunk_id, "method", name, f"method:{slug(name)}", "EN", CONFIDENCE["EN"], m.start(), m.end()))
    if country_pattern:
        for m in country_pattern.finditer(text):
            code = ref.countries[m.group(0).lower()]
            found.append(Mention(chunk_id, "country", code, f"country:{code}", "E", CONFIDENCE["E"], m.start(), m.end()))
    return found


def _reference(chunk_id: str, m: re.Match, project_id: str | None, ref: Reference, res: Resolution) -> Mention:
    """A STEP/borrower reference: normalised match to a contract of the same project, else fuzzy within the project."""
    printed, norm = m.group(0), normalise_reference(m.group(0))
    exact = ref.contracts_by_reference.get((project_id or "", norm or ""), [])
    if exact:
        contract = exact[0]
        res.contracts[str(contract["wb_contract_number"])] = contract
        return Mention(chunk_id, "step_reference", printed, f"contract:{contract['wb_contract_number']}", "EN", CONFIDENCE["EN"],
                       m.start(), m.end())
    candidates = {r: c for (pid, r), c in ref.contracts_by_reference.items() if pid == project_id}
    best = process.extractOne(norm, list(candidates), scorer=fuzz.ratio) if candidates and norm else None
    if best and best[1] >= FUZZY_MIN:
        contract = candidates[best[0]][0]
        res.contracts[str(contract["wb_contract_number"])] = contract
        return Mention(chunk_id, "step_reference", printed, f"contract:{contract['wb_contract_number']}", "P", round(best[1] / 100, 2),
                       m.start(), m.end())
    return Mention(chunk_id, "step_reference", printed, None, "U", 0.0, m.start(), m.end())


def _temporal(doc: ParsedDocument, ref: Reference, res: Resolution) -> list[Link]:
    """Contracts of the same project signed close to a supervision or completion report: the report may describe them."""
    if doc.doc_type not in ("ISR", "ICR", "ICRR") or not (doc.project_id and doc.document_date):
        return []
    report = dt.date.fromisoformat(doc.document_date)
    links = []
    for contract in ref.contracts:
        if contract["project_id"] != doc.project_id or not contract.get("contract_signing_date"):
            continue
        days = (report - dt.date.fromisoformat(contract["contract_signing_date"][:10])).days
        if abs(days) <= TEMPORAL_WINDOW:
            number = str(contract["wb_contract_number"])
            res.contracts[number] = contract
            links.append(Link("TEMPORALLY_NEAR", f"contract:{number}", f"document:{doc.document_id}", "P",
                              round(1 - abs(days) / TEMPORAL_WINDOW * 0.5, 2),
                              {"rule": "same project, signed within the report window", "days_report_minus_signing": days}))
    return links


def _country_pattern(ref: Reference):
    names = sorted(ref.countries, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I) if names else None
