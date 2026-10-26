"""Activity rows from the WDS text representation of a STEP procurement-plan export (read-only helper).

Two text forms exist:
  * layout  - columns preserved (wide lines, 'Planned      Actual' header with column gaps). Dates can be assigned to a
              stage (e.g. 'Signed Contract | Actual') by character position under the header.
  * flow    - one cell per line. Blank cells collapse, so dates cannot be assigned to stages; they are kept as an
              unassigned list and never used as evidence.
Rows are anchored on the STEP package pattern '-NNNNNN-(CW|GO|CS|NC|TA)-METHOD' found in a whitespace-free copy of the
text (references often wrap across lines), then mapped back to the original text for field extraction. The printed
reference is kept as-is; the normalised form follows wbdisc.normalize.normalize_ref (separators unified, whitespace
removed).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

SECTIONS = {  # printed section heading -> DS00005 procurement_category family
    "WORKS": "WORK", "GOODS": "GOOD", "NON CONSULTING SERVICES": "NONC", "NON-CONSULTING SERVICES": "NONC", "CONSULTING FIRMS": "CONS",
    "INDIVIDUAL CONSULTANTS": "CONS",
    "TRAVAUX": "WORK", "FOURNITURES": "GOOD", "BIENS": "GOOD", "SERVICES AUTRES QUE LES SERVICES DE CONSULTANTS": "NONC",
    "SERVICES AUTRES QUE DES SERVICES DE CONSULTANTS": "NONC", "CABINETS DE CONSULTANTS": "CONS", "BUREAUX D'ETUDES": "CONS",
    "CONSULTANTS INDIVIDUELS": "CONS", "SERVICES DE CONSULTANTS": "CONS",
    "OBRAS": "WORK", "BIENES": "GOOD", "SERVICIOS DE NO CONSULTORÍA": "NONC", "SERVICIOS DE NO CONSULTORIA": "NONC",
    "FIRMAS CONSULTORAS": "CONS", "CONSULTORES INDIVIDUALES": "CONS", "FIRMAS CONSULTORAS / CONSULTORÍA": "CONS",
}
ANCHOR = re.compile(r"-(\d{5,7})-(CW|GO|CS|NC|TA)-([A-Z]+)")
MONEY = re.compile(r"(?<![\d,.])\d{1,3}(?:,\d{3})*\.\d{2}(?!\d)")
DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
REVIEW = re.compile(r"\b(Post|Prior|A\s*posteriori|A\s*priori|Ex[- ]?post|Ex[- ]?ante|Posterior|Previa|Previo)\b", re.I)
REVIEW_MAP = {"post": "post", "prior": "prior", "aposteriori": "post", "apriori": "prior", "expost": "post", "exante": "prior",
              "posterior": "post", "previa": "prior", "previo": "prior"}
METHODS = {  # whitespace-free lower-case printed label -> DS00005 method (squashed)
    "requestforquotations": "requestforquotations", "requestforbids": "requestforbids", "directselection": "directselection",
    "individualconsultantselection": "individualconsultantselection", "qualityandcostbasedselection": "qualityandcostbasedselection",
    "consultantqualificationselection": "consultantqualificationselection", "leastcostselection": "leastcostselection",
    "fixedbudgetselection": "fixedbudgetselection", "qualitybasedselection": "qualitybasedselection",
    "requestforproposals": "requestforproposals", "competitivedialogue": "competitivedialogue",
    "demandedeprix": "requestforquotations", "demandedecotation": "requestforquotations", "appeldoffres": "requestforbids",
    "selectiondirecte": "directselection", "ententedirecte": "directselection", "individuel": "individualconsultantselection",
    "selectionfondeesurlesqualificationsdesconsultants": "consultantqualificationselection",
    "selectionfondeesurlaqualiteetlecout": "qualityandcostbasedselection", "selectionaumoindrecout": "leastcostselection",
    "selectionfondeesurunbudgetdetermine": "fixedbudgetselection", "selectionfondeesurlaqualite": "qualitybasedselection",
    "solicituddecotizaciones": "requestforquotations", "solicituddeofertas": "requestforbids", "contratacióndirecta": "directselection",
    "contrataciondirecta": "directselection", "selecciondirecta": "directselection", "seleccióndirecta": "directselection",
    "seleccióndeconsultoresindividuales": "individualconsultantselection", "selecciondeconsultoresindividuales": "individualconsultantselection",
    "selecciónbasadaenlacalidadyelcosto": "qualityandcostbasedselection", "seleccionbasadaenlacalidadyelcosto": "qualityandcostbasedselection",
    "selecciónbasadaenlascalificacionesdelosconsultores": "consultantqualificationselection",
    "seleccionbasadaenlascalificacionesdelosconsultores": "consultantqualificationselection",
    "selecciónbasadaenelmenorcosto": "leastcostselection", "seleccionbasadaenelmenorcosto": "leastcostselection",
}
STATUSES = {"completed": "completed", "signed": "signed", "canceled": "canceled", "cancelled": "canceled", "underimplementation": "under_implementation",
            "pendingimplementation": "pending_implementation", "underreview": "under_review", "draft": "draft", "terminated": "terminated",
            "annulé": "canceled", "annule": "canceled", "signé": "signed", "signe": "signed", "achevé": "completed", "acheve": "completed",
            "encoursdexécution": "under_implementation", "encoursdexecution": "under_implementation", "enattentedexécution": "pending_implementation",
            "enattentedexecution": "pending_implementation", "résilié": "terminated", "resilie": "terminated", "firmado": "signed", "cancelado": "canceled",
            "completado": "completed", "enejecución": "under_implementation", "enejecucion": "under_implementation",
            "pendientedeejecución": "pending_implementation", "pendientedeejecucion": "pending_implementation", "terminado": "terminated"}
PA_LINE = re.compile(r"^\s*(?:(Planned|Actual|Prévu|Prevu|Réel|Reel|Réalisé|Realise|Planificado|Previsto|Real)\s+){3,}", re.I)
EST_LABELS = ("Estimated", "Montant estim", "Monto estimado", "Costo estimado")
ACT_LABELS = ("Actual Am", "Actual Amount", "Montant r", "Monto real", "Monto adjudicado")
SIGNED_LABELS = ("Signed Contract", "Contrat signé", "Contrat signe", "Signature du contrat", "Contrato firmado", "Firma del contrato")


def squash(x: str) -> str:
    x = unicodedata.normalize("NFKC", x).lower()
    return re.sub(r"[\s\-'’./()]", "", x)


def stripped_with_map(text: str) -> tuple[str, list[int]]:
    """Whitespace-free upper-case copy with separators unified (as normalize_ref), plus index -> original offset."""
    out, idx = [], []
    for i, ch in enumerate(unicodedata.normalize("NFKC", text)):
        if ch.isspace():
            continue
        if ch in "‐‑‒–—―_/\\.":
            ch = "-"
        out.append(ch.upper())
        idx.append(i)
    return "".join(out), idx


@dataclass
class Row:
    ref_printed: str                 # reference as printed (whitespace collapsed), before normalization
    ref_key: str                     # whitespace-free, separators unified; contains the STEP package
    activity_id: str                 # STEP activity number + category + method code, e.g. 204059-CW-RFQ
    section: str | None
    category: str | None
    review: str | None
    method: str | None
    status: str | None
    estimated_amount: float | None
    actual_amount: float | None
    signed_planned: str | None
    signed_actual: str | None
    dates_unassigned: list[str] = field(default_factory=list)
    line_no: int = 0


def text_form(text: str) -> str:
    for line in text.splitlines():
        if PA_LINE.match(line):
            return "layout" if re.search(r"(Planned|Prévu|Planificado|Previsto)\s{3,}(Actual|Réel|Real|Réalisé)", line, re.I) else "flow"
    return "no_step_table"


def _signed_columns(header_lines: list[str], pa_line: str) -> tuple[int, int] | None:
    toks = [(m.start(), m.end()) for m in re.finditer(r"\S+", pa_line)]
    pairs = [(toks[i][0], toks[i + 1][1]) for i in range(0, len(toks) - 1, 2)]
    for hl in header_lines[::-1]:
        for lab in SIGNED_LABELS:
            k = hl.find(lab)
            if k >= 0:
                centre = k + len(lab) / 2
                best = min(pairs, key=lambda p: abs((p[0] + p[1]) / 2 - centre))
                i = pairs.index(best)
                return toks[2 * i][0], toks[2 * i + 1][0]
    return None


def _amount_columns(header_lines: list[str]) -> tuple[bool, bool] | None:
    """Which amount columns the table header has. Older exports have no 'Estimated Amount' column. Header labels are
    left-aligned while numbers are right-aligned, so positions are not used; amounts are read in column order."""
    hdr = " ".join(header_lines)
    est = any(lab in hdr for lab in EST_LABELS)
    act = any(lab in hdr for lab in ACT_LABELS)
    return (est, act) if est or act else None


def parse(text: str, country_prefixes: set[str]) -> tuple[str, list[Row]]:
    form = text_form(text)
    lines = text.splitlines(keepends=True)
    line_start = []
    pos = 0
    for ln in lines:
        line_start.append(pos)
        pos += len(ln)

    def line_of(offset: int) -> int:
        lo, hi = 0, len(line_start) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_start[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo

    # section headings and P/A header lines with their signed-contract columns
    sections, pa = [], []
    for i, ln in enumerate(lines):
        s = re.sub(r"\s+", " ", ln.strip()).upper()
        if s in SECTIONS:
            sections.append((i, s))
        if PA_LINE.match(ln):
            hl = [x.rstrip("\n") for x in lines[max(0, i - 12):i]]
            pa.append((i, _signed_columns(hl, ln.rstrip("\n")) if form == "layout" else None, _amount_columns(hl) if form == "layout" else None))
    flat, idx = stripped_with_map(text)
    anchors = list(ANCHOR.finditer(flat))
    starts = []
    for m in anchors:
        window = flat[max(0, m.start() - 70):m.start()]
        best = -1
        for cc in country_prefixes:
            k = window.rfind(cc + "-")
            best = max(best, k)
        st = m.start() - len(window) + best if best >= 0 else max(0, m.start() - 25)
        starts.append(st)
    rows = []
    for j, m in enumerate(anchors):
        st, en = starts[j], (starts[j + 1] if j + 1 < len(anchors) else len(flat))
        o_st, o_en = idx[st], (idx[en] if en < len(idx) else len(text))
        seg = text[o_st:o_en]
        ref_key = flat[st:m.end()]
        ln0 = line_of(o_st)
        sec = next((s for i, s in reversed(sections) if i <= ln0), None)
        rv = REVIEW.search(seg)
        after = seg[rv.end():] if rv else seg
        money = [float(x.replace(",", "")) for x in MONEY.findall(after)]
        est_amt = act_amt = None
        amt_cols = next((a for i, _, a in reversed(pa) if i <= ln0), None) if form == "layout" else None
        if amt_cols == (False, True):
            act_amt = money[0] if money else None
        elif amt_cols == (True, False):
            est_amt = money[0] if money else None
        elif len(money) >= 2:  # both columns: first is estimated, second actual. A single amount is not assigned.
            est_amt, act_amt = money[0], money[1]
        sq = squash(seg)
        method = next((v for k, v in sorted(METHODS.items(), key=lambda kv: -len(kv[0])) if k in sq), None)
        status = next((v for k, v in sorted(STATUSES.items(), key=lambda kv: -len(kv[0])) if k in sq), None)
        sp = sa = None
        dates = DATE.findall(seg)
        if form == "layout":
            hdr = next((c for i, c, _ in reversed(pa) if i <= ln0), None)
            if hdr:
                for k in range(ln0, line_of(max(o_st, o_en - 1)) + 1):
                    for dm in DATE.finditer(lines[k]):
                        if abs(dm.start() - hdr[0]) <= 4:
                            sp = dm.group(1)
                        elif abs(dm.start() - hdr[1]) <= 4:
                            sa = dm.group(1)
        rows.append(Row(ref_printed=re.sub(r"\s+", " ", text[o_st:idx[m.end() - 1] + 1]).strip(), ref_key=ref_key, section=sec,
                        category=SECTIONS.get(sec) if sec else None,
                        review=REVIEW_MAP.get(squash(rv.group(1))) if rv else None, method=method, status=status,
                        estimated_amount=est_amt, actual_amount=act_amt, activity_id="-".join(m.groups()),
                        signed_planned=sp, signed_actual=sa, dates_unassigned=[] if form == "layout" else dates, line_no=ln0 + 1))
    return form, rows
