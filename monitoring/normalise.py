"""Procurement methods and plan stages as printed in STEP plans (English, French, Spanish, and text broken by the PDF
layout, e.g. "Request for Quota tions" or the scrambled "Rtioenqsuest for Quota") mapped onto canonical names."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from rapidfuzz import fuzz

METHODS = {  # canonical method -> the printed forms (any language) after `key()`
    "Request for Bids": ("requestforbids", "appeldoffres", "solicituddeofertas", "rfb"),
    "Request for Quotations": ("requestforquotations", "demandedeprix", "solicituddecotizaciones", "rfq"),
    "Request for Proposals": ("requestforproposals", "demandedepropositions", "solicituddepropuestas", "rfp"),
    "Direct Selection": ("directselection", "adquisiciondirecta", "passationdemarchedegreagre",
                         "selectiondeconsultantsparententedirecte", "selecciondirectadeconsultores", "directcontracting"),
    "Quality and Cost-Based Selection": ("qualityandcostbasedselection", "selectionfondeesurlaqualiteetlecout",
                                         "seleccionbasadaencalidadycosto", "qcbs"),
    "Quality-Based Selection": ("qualitybasedselection", "qbs"),
    "Consultant's Qualification-Based Selection": ("consultantqualificationselection",
                                                   "selectionfondeesurlesqualificationsdesconsultants",
                                                   "seleccionbasadaencalificacionesdeconsultores", "cqs"),
    "Least Cost Selection": ("leastcostselection", "seleccionbasadaenelmenorcosto", "lcs"),
    "Individual Consultant Selection": ("individual", "individuel", "individualconsultantselection", "ics"),
    "UN Agencies": ("unagenciesdirect", "passationdemarchesaupresdesagencesdelonu"),
}
STAGES = {  # stage key -> display name (the keys absorb broken spacing; "teams of reference" is a frequent typo)
    "termsofreference": "Terms of Reference", "teamsofreference": "Terms of Reference",
    "expressionofinterestnotice": "Expression of Interest Notice",
    "shortlistanddraftrequestforproposals": "Short List and Draft Request for Proposals",
    "draftbiddingdocumentjustification": "Draft Bidding Document / Justification",
    "specificprocurementnoticeinvitation": "Specific Procurement Notice / Invitation",
    "proposalsubmissionopeningminutes": "Proposal Submission / Opening / Minutes",
    "openingoftechnicalproposalsminutes": "Opening of Technical Proposals / Minutes",
    "evaluationoftechnicalproposal": "Evaluation of Technical Proposal",
    "bidevaluationreportandrecommendationforaward": "Bid Evaluation Report and Recommendation for Award",
    "combinedevaluationreportanddraftnegotiatedcontract": "Combined Evaluation Report and Draft Negotiated Contract",
    "invitationtoidentifiedselectedconsultant": "Invitation to Identified/Selected Consultant",
    "draftnegotiatedcontract": "Draft Negotiated Contract",
    "signedcontract": "Signed Contract", "contractcompletion": "Contract Completion",
    "draftprequalificationdocuments": "Draft Pre-qualification Documents",
    "prequalificationevaluationreport": "Prequalification Evaluation Report",
}
_ALIASES = {alias: name for name, aliases in METHODS.items() for alias in aliases}


def key(text: str) -> str:
    """Lower case, accents and every non-letter removed: the form that survives broken PDF spacing."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", plain.lower())


@lru_cache(maxsize=4096)
def method(printed: str | None) -> str | None:
    """The canonical method, or None when the printed text cannot be recognised."""
    if not printed:
        return None
    k = key(printed)
    if k in _ALIASES:
        return _ALIASES[k]
    best = max(_ALIASES, key=lambda alias: fuzz.ratio(k, alias))
    if fuzz.ratio(k, best) >= 85:                                       # a missing or extra letter
        return _ALIASES[best]
    letters = "".join(sorted(k))                                       # scrambled text keeps its letters
    scored = max(_ALIASES, key=lambda alias: fuzz.ratio(letters, "".join(sorted(alias))))
    return _ALIASES[scored] if fuzz.ratio(letters, "".join(sorted(scored))) >= 80 else None


def stage(printed: str) -> str:
    return STAGES.get(key(printed), " ".join(printed.split()))
