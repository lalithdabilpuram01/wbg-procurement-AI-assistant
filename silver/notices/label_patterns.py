"""Evidence patterns for amendment, re-bid and cancellation signals in procurement-notice text.

Copied verbatim from the ML label feasibility audit (research/discovery/scripts/p09_ml_label_feasibility.py) so the
Silver notices job does not depend on a research script. The patterns are narrow by design: every hit is a candidate
that a person reviews before it can become a label.
"""
from __future__ import annotations

import html
import re

# --- evidence patterns (narrow by design; every hit is reviewed by a person before it can become a label) ---
CONTRACT_AMENDMENT = re.compile(
    r"\bavenant\b|\btermo\s+aditivo\b|\baditivo\s+(?:contratual|de\s+prazo|de\s+valor|ao\s+contrato)"
    r"|\baddendum\s+(?:no\.?\s*\d+\s+)?(?:for|to)\b|\bamendment\s+(?:no\.?\s*\d+\s+)?(?:to|of)\s+(?:the\s+)?(?:\w+\s+){0,3}contract\b"
    r"|\bcontract\s+amendment|\bextension\s+of\s+(?:the\s+)?contract\b|\bprolongation\s+(?:du|de\s+la)\s+(?:contrat|garantie)"
    r"|\badenda\s+(?:al\s+|del\s+|de\s+)?contrato|\benmienda\s+(?:al|del)\s+contrato|\bvariation\s+order|\bcontract\s+variation"
    r"|\bmodificaci[oó]n\s+(?:al|del)\s+contrato", re.I)
REBID_STRONG = re.compile(
    r"\bre-?\s?bid(?:ding|ded)?\b|\bre-?\s?tender(?:ing|ed)?\b|\bre-?\s?advertis\w*|\bre-?\s?invit\w*|\bre-?\s?launch\w*"
    r"|\brelanc\w*|\bsecond\s+call\b|\b2nd\s*(?:call|tender|bid)|\bnouvel\s+appel|\binfructueu\w*|\bdesiert[oa]s?\b"
    r"|\bnueva\s+convocatoria|\bsegunda\s+convocatoria", re.I)
NOTICE_CANCELLED = re.compile(
    r"(?:notice|invitation|tender|bid(?:ding)?(?:\s+process)?|advertisement|publication|procurement|request\s+for\s+\w+|bids\s+received[^.]{0,80})"
    r"\s+(?:has\s+been|have\s+been|is|was|is\s+hereby|stands)\s+(?:cancel+ed|withdrawn|annul+ed)"
    r"|publication\s+cancel+ed|est\s+annul[ée]e?|sont\s+annul[ée]e?s|a\s+[ée]t[ée]\s+annul[ée]e?|ha\s+sido\s+cancelad[oa]"
    r"|queda\s+cancelad[oa]|fue\s+declarad[oa]\s+desiert[oa]", re.I)
BID_DOC_AMENDMENT = re.compile(
    r"\baddend(?:um|a)\b|\bcorrigend(?:um|a)\b|\berratum\b|extension\s+of\s+(?:the\s+)?(?:bid|submission|closing)\s+(?:deadline|date)"
    r"|deadline\s+(?:for\s+\w+\s+)?(?:has\s+been\s+|is\s+)?extended", re.I)
REF_REBID_STRONG = re.compile(r"re-?\s?bid|re-?tender|re-?advert|re-?invit|re-?launch|relance|2\s*(?:nd|ème|eme|e)\s*(?:call|tender|bid|relance)", re.I)


def clean(text: str | None) -> str:
    """Notice text without HTML tags or entities, with whitespace collapsed (missing text becomes "")."""
    text = text if isinstance(text, str) else ""  # missing notice_text arrives as NaN
    t = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", t).strip()

