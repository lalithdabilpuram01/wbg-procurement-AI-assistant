"""Parser for STEP-generated 'Contract Award' procurement-notice text (procnotices.notice_text).

The template is semi-structured HTML (observed labels: 'Loan/Credit/TF Info', 'Bid/Contract
Reference No', 'Notice Version No', 'Date Notification of Award Issued (YYYY/MM/DD)',
'Contract Signature Date (YYYY/MM/DD)', 'Duration of Contract', 'Awarded Bidder(s)',
'Awarded Firm(s)', 'Awarded Firm/Individual', 'Evaluated Bidder(s)', 'Rejected Bidder(s)',
'Signed Contract price', 'Currency: Amount:'). Older/manual notices do not follow it.

The parser is deliberately conservative: a field is returned only when its label is found.
It is a feasibility instrument, not a production extractor.
"""
from __future__ import annotations

import re
from typing import Any

from .normalize import parse_date, strip_html

_LABEL_END = r"(?=\n|\s{2,}|$)"
_DATE_AFTER = r"\(YYYY/MM/DD\)\s*(\d{4}/\d{2}/\d{2})"
_RE = {
    "loan_info": re.compile(r"Loan/Credit/TF Info:\s*([^\n]+)"),
    "reference": re.compile(r"Bid/Contract Reference No:\s*([^\n]+)"),
    "version": re.compile(r"Notice Version No:\s*(\d+)"),
    "award_notification_date": re.compile(r"Date Notification of Award Issued\s*" + _DATE_AFTER),
    "contract_signature_date": re.compile(r"Contract Signature Date\s*" + _DATE_AFTER),
    "duration": re.compile(r"Duration of Contract\s*\n?\s*(\d+)\s*(Month|Day|Week|Year)", re.I),
    "signed_price": re.compile(r"Signed Contract price\s*\n?\s*([A-Z]{3})\s*([\d,]+(?:\.\d+)?)", re.I),
    "currency_amount": re.compile(r"Currency:\s*Amount:\s*(.+?)\s*\(.*?\)\s*([\d,]+(?:\.\d+)?)"),
}
_SECTION = re.compile(
    r"(Awarded Bidder\(s\)|Awarded Firm\(s\)|Awarded Firm/Individual|Evaluated Bidder\(s\)|Evaluated Firm\(s\)|"
    r"Rejected Bidder\(s\)|Rejected Firm\(s\))\s*:"
)
_ENTITY_ID = re.compile(r"\((\d{3,9})\)")


def _sections(text: str) -> dict[str, str]:
    """Split notice text into its awarded / evaluated / rejected sections (the text after each heading)."""
    marks = [(m.start(), m.end(), m.group(1)) for m in _SECTION.finditer(text)]
    out: dict[str, str] = {}
    for i, (_, end, label) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        kind = label.split()[0].lower()  # awarded / evaluated / rejected
        out[kind] = out.get(kind, "") + text[end:stop]
    return out


def parse_award_notice(notice_text: str | None) -> dict[str, Any]:
    """Parse a Contract Award notice into dates, price, duration, version, loans and bidder supplier IDs.
    Fields whose label is missing are left out; bidder counts use distinct STEP supplier IDs, not names."""
    text = strip_html(notice_text or "")
    res: dict[str, Any] = {"is_step_template": "Notice Version No" in text and "Bid/Contract Reference No" in text}
    for key, rx in _RE.items():
        m = rx.search(text)
        if not m:
            continue
        if key in {"award_notification_date", "contract_signature_date"}:
            res[key] = str(parse_date(m.group(1)) or m.group(1))
        elif key == "duration":
            res["duration_value"], res["duration_unit"] = int(m.group(1)), m.group(2).lower()
        elif key in {"signed_price", "currency_amount"}:
            res[f"{key}_currency"], res[f"{key}_amount"] = m.group(1).strip(), float(m.group(2).replace(",", ""))
        elif key == "version":
            res["notice_version_no"] = int(m.group(1))
        elif key == "loan_info":
            res["loan_ids"] = [x.strip() for x in re.split(r"[;,]", m.group(1)) if x.strip()]
        else:
            res["reference_in_text"] = m.group(1).strip()
    secs = _sections(text)
    for kind in ("awarded", "evaluated", "rejected"):
        if kind in secs:
            ids = _ENTITY_ID.findall(secs[kind])
            res[f"{kind}_supplier_ids"] = ids
            res[f"{kind}_count_by_id"] = len(ids)
    if "awarded" in secs or "evaluated" in secs or "rejected" in secs:
        # Bidders = awarded + evaluated-not-awarded + rejected, counted by STEP supplier ids.
        res["bidder_count_by_id"] = len(set(res.get("awarded_supplier_ids", [])) | set(res.get("evaluated_supplier_ids", []))
                                        | set(res.get("rejected_supplier_ids", [])))
    return res
