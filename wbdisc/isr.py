"""Parser for the WB-published text rendition (.txt) of Implementation Status and Results Reports.

Two templates were observed (2026-10-21):

Template A (ISRs archived up to ~mid 2024):
    ... | FY 2013 | Seq No: 8 | ARCHIVED on 24-Apr-2017 | ISR27378 |
    Name                               Previous Rating            Current Rating
    Progress towards achievement of PDO  <G> Moderately Satisfactory  <G> Moderately Satisfactory
     Risk Category        Rating at Approval    Previous Rating    Current Rating
     Fiduciary            --                    Moderate           Moderate

Template B (ISRs from ~late 2024):
    SOUTH ASIA—Sri Lanka—IBRD/IDA—... | FY 2018—Seq No: 17—Archived on 15-Oct-2026—ISR09224
    3. OVERALL RATINGS
    Name Previous Current
    Progress towards achievement of PDO <G>Highly Satisfactory <G>Highly Satisfactory
    5. SYSTEMATIC OPERATIONS RISK-RATING TOOL
    Risk Category / Rating at Approval / ... / Last Approved Rating / ... / Proposed Rating
    Fiduciary <G>Moderate <G>Moderate <G>Moderate

<G> is a private-use glyph (U+F06C, a Wingdings rating icon). Rows are split on the glyph when
present, otherwise on runs of 2+ spaces. Long row names wrap; a values-only line takes the
preceding name fragment, and a short continuation line directly after a values-only row extends
that row's name. A third, vertically flattened layout (each value on its own line, seen in some
2026 renditions) is not parsed: rows come back empty, and the PDF should be used instead. Only values from the WB rating vocabularies are accepted, so narrative
text inside a table region is ignored. The *last* value is the current (or proposed) rating.

Neither template contains the component performance ratings (Procurement, Financial Management,
...) that the v3 projects API exposes for the latest ISR. Only PDO, IP, overall risk and the
SORT risk categories (incl. Fiduciary) are present.
"""
from __future__ import annotations

import re

from .normalize import parse_date

PERFORMANCE = {"Highly Satisfactory", "Satisfactory", "Moderately Satisfactory", "Moderately Unsatisfactory",
               "Unsatisfactory", "Highly Unsatisfactory"}
RISK = {"Low", "Moderate", "Substantial", "High"}
OTHER = {"Not Rated", "Not Applicable", "--"}
VOCAB = PERFORMANCE | RISK | OTHER
_GLYPH = re.compile("[-]")
_SPLIT = re.compile(r"\s{2,}")
_SEQ = re.compile(r"Seq No:\s*(\d+)")
_ARCH = re.compile(r"Archived on\s*(\d{2}-[A-Za-z]{3}-\d{4})", re.I)
_OVERALL_START = re.compile(r"OVERALL RATINGS|Previous Rating\s+Current Rating|^Name\s+Previous\s+Current$")
# Numbered section headings always carry a dot ("4. KEY ISSUES", "6.1 PDO ..."); dates such as
# "26 Apr 2018" inside the template-B risk header must not end the table.
# Multi-line column headers of the template-B risk table ("Rating at Approval", "Approval Package -",
# "26 Apr 2018", "Last Approved Rating", "ISR Seq. 016 - 24 Apr", "2026", "Proposed Rating").
_HEADER_NOISE = re.compile(r"^(Rating at Approval|Approval Package|Last Approved Rating|Proposed Rating|Previous Rating|"
                           r"Current Rating|ISR Seq\.|\d{1,2} [A-Z][a-z]{2} \d{4}$|\d{4}$)")
_SECTION_END = re.compile(r"^(\d+\.(\d+)?\s+[A-Z][A-Za-z &/-]{3,}|Results$|Implementation Status and Key Decisions)")


def _row(line: str) -> tuple[str, list[str]] | None:
    """Return (name, values) when the line is a rating row, else None. Name may be '' (values-only line)."""
    if _GLYPH.search(line):
        parts = [p.strip() for p in _GLYPH.split(line)]
        head = [c for c in _SPLIT.split(parts[0]) if c.strip()]
        # Template A risk rows can carry an un-glyphed '--' (at approval) after the name.
        name = head[0].strip() if head and head[0].strip() not in VOCAB else ""
        vals = [h.strip() for h in head[1:]] + [p for p in parts[1:] if p]
    else:
        cells = [c.strip() for c in _SPLIT.split(line.strip()) if c.strip()]
        if len(cells) < 2:
            return None
        name, vals = ("", cells) if cells[0] in VOCAB else (cells[0], cells[1:])
    if not vals or any(v not in VOCAB for v in vals) or (not name and len(vals) < 2):
        return None
    return name, vals


def parse_isr_text(text: str) -> dict:
    """Parse an ISR text rendition: sequence number, archive date, template, and the overall and risk ratings.
    Each rating is the last value of its row (current or proposed); '--' is stored as None."""
    res: dict = {"ratings": {}, "risk_ratings": {}, "rating_tables_found": 0}
    m = _SEQ.search(text)
    res["seq_no"] = int(m.group(1)) if m else None
    m = _ARCH.search(text)
    res["archived_on"] = str(parse_date(m.group(1))) if m else None
    res["template"] = "B" if "—Seq No:" in text else ("A" if "| Seq No:" in text else None)
    mode, fragment, last_from_fragment = None, "", None
    for raw in text.splitlines():
        stripped = raw.strip()
        if "Risk Category" in stripped:
            mode, fragment = "risk", ""
            res["rating_tables_found"] += 1
            continue
        if _OVERALL_START.search(stripped):
            if mode != "overall":
                res["rating_tables_found"] += 1
            mode, fragment = "overall", ""
            continue
        if not mode or not stripped:
            continue
        if _SECTION_END.match(stripped) and not _GLYPH.search(stripped):
            mode, fragment = None, ""
            continue
        if _HEADER_NOISE.match(stripped):
            fragment, last_from_fragment = "", None
            continue
        target = res["risk_ratings"] if mode == "risk" else res["ratings"]
        row = _row(raw)
        if row is None:
            if last_from_fragment and len(stripped) < 40 and stripped[:1].isupper():
                # continuation of a name whose values were printed between its two halves
                target[f"{last_from_fragment} {stripped}"] = target.pop(last_from_fragment)
                last_from_fragment = None
                continue
            # candidate wrapped row name; long narrative lines reset it
            fragment = (fragment + " " + stripped).strip() if len(stripped) < 80 else ""
            last_from_fragment = None
            continue
        name, vals = row
        last_from_fragment = fragment if (not name and fragment) else None
        name = name or fragment
        fragment = ""
        if name:
            target[name] = None if vals[-1] == "--" else vals[-1]
    return res
