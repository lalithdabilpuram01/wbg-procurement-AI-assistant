"""Stage 1: query understanding (deterministic). Detects comparison intent, document-type hints and requested fields
that the corpus can only answer if the evidence explicitly contains them."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DOC_HINTS = {
    "PAD": r"\bappraisal\b|\bPAD\b|project appraisal document|at approval",
    "ISR": r"\bISR\b|implementation status|latest (status|report)|\bcurrent rating",
    "ICR": r"\bICR\b(?!\s*review)|implementation completion",
    "ICRR": r"\bICR review\b|\bIEG\b|independent evaluation",
    "PP": r"procurement plan|\bplan\b|activity|package",
}
COMPARISON = re.compile(r"\b(and in the|versus|vs\.?|compared?|between .+ and|changed|change|differ|disagree|original and (actual|revised)|both)\b", re.I)
# fields that must be explicitly present in evidence (never inferred)
REQUIRED_FIELDS = {
    "bidder_count": (re.compile(r"how many (bids|bidders|offers|proposals)|number of (bids|bidders|offers|proposals)|bidder count|bids (were )?received", re.I),
                     re.compile(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(bids|bidders|offers|proposals)\s+(were\s+)?(received|submitted)|number of (bids|bidders) received\s*[:\-]?\s*\d+", re.I)),
    "winning_bid_price": (re.compile(r"(winning|evaluated|lowest) bid (price|amount)|bid price", re.I),
                          re.compile(r"(winning|evaluated|lowest) bid (price|amount)\s*[:\-]?\s*(US\$|\$)?\s*[\d,.]+", re.I)),
}


@dataclass
class QueryPlan:
    question: str
    comparison: bool
    doc_type_hints: list[str]
    required_fields: dict = field(default_factory=dict)   # name -> evidence regex that must match


def understand(q: str) -> QueryPlan:
    hints = [k for k, rx in DOC_HINTS.items() if re.search(rx, q, re.I)]
    req = {name: ev for name, (ask, ev) in REQUIRED_FIELDS.items() if ask.search(q)}
    return QueryPlan(q, bool(COMPARISON.search(q)), hints, req)
