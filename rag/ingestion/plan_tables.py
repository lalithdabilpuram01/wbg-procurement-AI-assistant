"""Procurement-plan (STEP export) extraction: plan-version facts from the PDF header and one activity per table row.

Primary extractor: pdfplumber tables. Continuation tables on later pages carry no 'Activity Reference' header; they
inherit the most recent header with the same column count (and the current section). Fallback / cross-check:
pdfplumber layout text parsed by plan_text_rows (the WDS-text parser). Activities are keyed by (page, package
reference); a fallback-only activity is kept with every value flagged uncertain, never silently trusted.
"""
from __future__ import annotations

import collections
import re
from pathlib import Path

import fitz  # type: ignore[import-untyped]
import pdfplumber

from . import config  # noqa: F401
from .references import category_family, header_key, normalize_plan_status, normalize_ref, package_core, package_core_in_text, review_code
import plan_text_rows as T  # noqa: E402  (rag/module1_discovery, via config)

SECTION_LABELS = {"WORKS", "GOODS", "NON CONSULTING SERVICES", "CONSULTING FIRMS", "INDIVIDUAL CONSULTANTS"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _c(x) -> str:
    return re.sub(r"\s+", " ", (x or "")).strip()


def money(x: str | None) -> tuple[float | None, bool]:
    """(value, parse_ok). Empty -> (None, True); unparseable non-empty -> (None, False)."""
    s = re.sub(r"[\s,]", "", x or "")
    if not s:
        return None, True
    return (float(s), True) if re.fullmatch(r"\d+(\.\d+)?", s) else (None, False)


def iso(x: str | None) -> tuple[str | None, bool]:
    s = re.sub(r"\s+", "", x or "")
    if not s:
        return None, True
    return (s, True) if DATE.match(s) else (None, False)


def header_facts(pdf_path: Path) -> dict:
    """Plan-version facts printed in the PDF (STEP 'General Information' block and textual part). Missing -> None."""
    with fitz.open(pdf_path) as doc:  # PyMuPDF: pdfplumber overlaps characters in the STEP header labels
        pages = [p.get_text() or "" for p in doc]
    full = "\n".join(pages)
    gi_page = next((i + 1 for i, t in enumerate(pages) if "General Information" in t), None)
    gi = pages[gi_page - 1] if gi_page else ""

    def grab(rx: str, text: str, flags=0):
        m = re.search(rx, text, flags)
        return _c(m.group(1)) if m else None

    revised = None
    m = re.search(r"Revised Plan Date\(s\).*?(?=Project ID|Project Name|GPN Date|$)", gi, re.S)
    if m:
        revised = sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", m.group(0)))) or None
    agency = grab(r"Executing Agency\(i[a-z]*\)?:?\s*(.+?)(?=\n(?:WORKS|GOODS|NON CONSULTING|CONSULTING FIRMS|INDIVIDUAL|Activity Reference)|\Z)", gi, re.S)
    return {
        "step_header_page": gi_page,
        "project_id_in_header": grab(r"Project ID:\s*(P\d{6})", gi) or grab(r"\(\s*(P\d{6})\s*\)", full) or grab(r"Project\s*(P\d{6})", full),
        "country_in_header": grab(r"Country:\s*([A-Za-z ,'-]+?)(?:\s+Bank|\n)", gi),
        "original_plan_approval_date": grab(r"Original Procurement Plan:?\s*(\d{4}-\d{2}-\d{2})", gi),
        "revised_plan_dates": revised,
        "loan_credit_numbers": grab(r"Loan / Credit No:?\s*((?:[A-Z]+ / [A-Z0-9]+(?:,\s*)?)+)", gi),
        "executing_agency_text": agency or None,
        "textual_part_plan_date_text": grab(r"Date of the Procurement Plan:\s*([^\n]+)", full)
                                       or grab(r"Bank.s approval Date of the procurement Plan:\s*(.+?)(?:\n\d\.|\n[A-Z]{2,})", full, re.S),
        "textual_part_agency_text": grab(r"Project Implementation agency:\s*([^\n]+(?:\n(?!Date)[^\n]+)?)", full),
    }


def table_rows(pdf_path: Path) -> list[dict]:
    rows = []
    headers_by_cols: dict[int, list[str]] = {}
    section = None
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            for tno, table in enumerate(page.extract_tables(), start=1):
                if not table:
                    continue
                first = _c(table[0][0]).upper()
                if first in SECTION_LABELS:
                    section = first
                hi = next((i for i, r in enumerate(table) if r and _c(r[0]).startswith("Activity Reference")), None)
                inherited = False
                if hi is not None and hi + 1 < len(table):
                    header, sub = table[hi], table[hi + 1]
                    if hi > 0 and _c(table[hi - 1][0]).upper() in SECTION_LABELS:
                        section = _c(table[hi - 1][0]).upper()
                    names, stage = [], ""
                    for h, s in zip(header, sub):
                        h, s = _c(h), _c(s)
                        if s in {"Planned", "Actual"}:
                            stage = h or stage
                            names.append(f"{stage} | {s}")
                        else:
                            names.append(h)
                    headers_by_cols[len(header)] = names
                    body, start = table[hi + 2:], hi + 2
                elif len(table[0]) in headers_by_cols and any(re.search(r"\d{5,7}\s*-\s*(?:CW|GO|CS|NC|TA)\s*-", r[0] or "") for r in table if r):
                    names, body, start, inherited = headers_by_cols[len(table[0])], table, 0, True
                else:
                    continue
                for rno, r in enumerate(body, start=start + 1):
                    if not r or not _c(r[0]) or _c(r[0]).startswith("Activity Reference") or _c(r[0]).upper() in SECTION_LABELS:
                        continue
                    cells = {}
                    for n, v in zip(names, r):
                        if n:
                            k = header_key(n)
                            if k not in cells or not cells[k][1]:
                                cells[k] = (n, _c(v))
                    raw_cells = {n: (v or "") for n, v in zip(names, r) if n}
                    rows.append({"page": pno, "table": tno, "row": rno, "section": section, "header_inherited": inherited,
                                 "ref_cell": _c(r[0]), "ref_cell_raw": r[0] or "", "cells": {n: v for n, v in cells.values()}, "cells_raw": raw_cells})
    return rows


def _get(cells: dict, *names: str) -> str | None:
    keys = {header_key(k): k for k in cells}
    for n in names:
        k = keys.get(header_key(n))
        if k and cells[k]:
            return cells[k]
    return None


def fallback_rows(pdf_path: Path, prefixes: set[str]) -> list[dict]:
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            form, rows = T.parse(page.extract_text(layout=True) or "", prefixes)
            for r in rows:
                core = package_core(r.ref_key[-80:])
                out.append({"page": pno, "core": core, "form": form, "actual_amount": r.actual_amount, "estimated_amount": r.estimated_amount,
                            "status": r.status, "review": r.review, "signed_actual": r.signed_actual, "signed_planned": r.signed_planned,
                            "ref_printed": r.ref_printed[:80]})
    return out


def activities(document_id: str, plan_version_id: str, pdf_path: Path, prefixes: set[str], method_codes: set[str]) -> list[dict]:
    """Merged activities with provenance, value checks and extractor agreement flags."""
    acts = []
    seen = set()
    fb = fallback_rows(pdf_path, prefixes)
    # layout text can scramble the method code ('...-CW-RPNPRRFUOOBWVV'), so the cross-check key is the STEP activity
    # number + category code, which survives intact
    fb_by = collections.defaultdict(list)
    for f in fb:
        k = _num_cat(f["core"])
        if k:
            fb_by[k].append(f)
    for r in table_rows(pdf_path):
        c = r["cells"]
        ref_text, _, desc = r["ref_cell"].partition(" / ")
        core, spill = package_core_in_text(ref_text or r["ref_cell"], prefixes, method_codes)
        flags = ["text_before_reference_in_cell"] if spill else []
        est, ok1 = money(_get(c, "Estimated Amount (US$)"))
        act, ok2 = money(_get(c, "Actual Amount (US$)"))
        sp, ok3 = iso(_get(c, "Signed Contract | Planned"))
        sa, ok4 = iso(_get(c, "Signed Contract | Actual"))
        for ok, name in ((ok1, "estimated_amount"), (ok2, "actual_amount"), (ok3, "signed_planned"), (ok4, "signed_actual")):
            if not ok:
                flags.append(f"unparsed_{name}")
        stages = {}
        for k, v in c.items():
            if " | " in k and v:
                st, pa = k.rsplit(" | ", 1)
                d, ok = iso(v)
                stages.setdefault(re.sub(r"\s+", " ", st), {})[pa.lower()] = d if ok else f"UNPARSED:{v[:20]}"
        status_raw = _get(c, "Process Status")
        rec = {
            "activity_id": f"{document_id}:p{r['page']}t{r['table']}r{r['row']}", "plan_version_id": plan_version_id, "document_id": document_id,
            "pdf_page": r["page"], "table_index": r["table"], "row_index": r["row"], "section": r["section"],
            "category_family": category_family(r["section"]), "header_inherited": r["header_inherited"], "extraction_method": "pdf_table",
            "activity_reference_printed": ref_text.strip() or None, "description_present": bool(desc.strip()), "description_text": desc.strip(),
            "activity_reference_normalized": normalize_ref(re.sub(r"\s+", "", ref_text)) if ref_text.strip() else None,
            "package_reference_core": core, "step_reference": core is not None,
            "loan_credit": _get(c, "Loan / Credit No."), "review_type_printed": _get(c, "Review Type"),
            "review_type": review_code(_get(c, "Review Type")), "method_printed": _get(c, "Method"), "market_approach": _get(c, "Market Approach"),
            "process_status_printed": status_raw, "process_status": normalize_plan_status(status_raw),
            "estimated_amount_usd": est, "actual_amount_usd": act, "signed_contract_planned": sp, "signed_contract_actual": sa,
            "_ref_cell_raw": r["ref_cell_raw"], "_cells_raw": r["cells_raw"],
            "stage_dates": stages, "value_flags": flags, "uncertain_fields": [f.replace("unparsed_", "") for f in flags if f.startswith("unparsed_")],
        }
        nk = _num_cat(core)
        f = fb_by.get(nk) if nk else None
        if f:
            f0 = next((x for x in f if x["page"] == r["page"]), f[0])
            agree = {"actual_amount": (f0["actual_amount"] == act) if (f0["actual_amount"] is not None and act is not None) else None,
                     "review": (f0["review"] == rec["review_type"]) if (f0["review"] and rec["review_type"]) else None}
            rec["text_fallback"] = {"found": True, "same_page": f0["page"] == r["page"], "agreement": agree, "actual_amount": f0["actual_amount"]}
            if any(v is False for v in agree.values()):
                rec["value_flags"].append("extractor_disagreement")
        else:
            rec["text_fallback"] = {"found": False}
        if nk:
            seen.add(nk)
        acts.append(rec)
    # fallback-only activities: references the table extractor did not return
    for nk, fs in sorted(fb_by.items()):
        if nk in seen:
            continue
        f0 = fs[0]
        pg, core = f0["page"], package_core(f0["core"], method_codes)
        acts.append({
            "activity_id": f"{document_id}:p{pg}fb{len([a for a in acts if a['pdf_page'] == pg]) + 1}", "plan_version_id": plan_version_id,
            "document_id": document_id, "pdf_page": pg, "table_index": None, "row_index": None, "section": None, "category_family": None,
            "header_inherited": None, "extraction_method": "text_fallback", "activity_reference_printed": f0["ref_printed"],
            "description_present": None, "description_text": "", "activity_reference_normalized": core, "package_reference_core": core, "step_reference": True,
            "loan_credit": None, "review_type_printed": None, "review_type": f0["review"], "method_printed": None, "market_approach": None,
            "process_status_printed": None, "process_status": f0["status"], "estimated_amount_usd": f0["estimated_amount"],
            "actual_amount_usd": f0["actual_amount"], "signed_contract_planned": f0["signed_planned"], "signed_contract_actual": f0["signed_actual"],
            "stage_dates": {}, "value_flags": ["text_fallback_values_unverified"],
            "uncertain_fields": ["review_type", "process_status", "estimated_amount_usd", "actual_amount_usd", "signed_contract_planned",
                                 "signed_contract_actual"], "text_fallback": {"found": True}})
    dup = collections.Counter(a["package_reference_core"] for a in acts if a["package_reference_core"])
    for a in acts:
        if a["package_reference_core"] and dup[a["package_reference_core"]] > 1:
            a["value_flags"].append("reference_on_multiple_rows")
    return acts


def _num_cat(core: str | None) -> tuple[str, str] | None:
    m = re.search(r"-(\d{5,7})-(CW|GO|CS|NC|TA)-", core or "")
    return (m.group(1), m.group(2)) if m else None


def reference_agency_code(acts: list[dict]) -> str | None:
    """Agency segment of the STEP references (e.g. RDA in LK-RDA-204059-CW-RFQ): the plan-stream key."""
    c = collections.Counter()
    for a in acts:
        core = a.get("package_reference_core")
        if core:
            parts = core.split("-")
            num_i = next((i for i, p in enumerate(parts) if re.fullmatch(r"\d{5,7}", p)), None)
            if num_i and num_i >= 2:
                c["-".join(parts[1:num_i])] += 1
    return c.most_common(1)[0][0] if c else None
