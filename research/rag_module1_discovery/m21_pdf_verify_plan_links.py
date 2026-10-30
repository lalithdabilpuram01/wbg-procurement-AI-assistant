"""PDF-level validation of contract-to-plan evidence for the pilot procurement plans (read-only).

Plans: the 3 plan PDFs downloaded in the pilot plus the already-local latest P132833 plan (34172790, in the approved manifest).
For each PDF table row (pdfplumber; page, table index and row index recorded) the activity reference is matched to Silver
DS00005 contracts of the same project (read-only query) by normalised reference or package reference + lot/sequence suffix.
Evidence tiers are the same as the text analysis and are kept separate: verified_amount, verified_exact_date (exact
'Signed Contract | Actual' = DS00005 signing date, plan actual amount 0/blank), candidate, conflicting, ambiguous. Planned,
actual (plan) and recorded (DS00005) dates are separate columns. Project identity and plan version are read from the PDF's
'General Information' block. Each PDF link is compared with the text-derived link for the same document.
Writes pilot_first10_v1/PDF_PLAN_ROWS.csv, PDF_CONTRACT_LINKS.csv and pdf_link_validation.json.
"""
from __future__ import annotations

import collections
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

import pdfplumber
from pyspark.sql import functions as F

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "discovery"))
from wbdisc.normalize import normalize_ref  # noqa: E402
from wbdisc.procurement_plan import header_key, normalize_plan_status  # noqa: E402

from silver.common.session import get_spark  # noqa: E402

OUT = HERE / "pilot_first10_v1"
PLANS = [  # (project, document_id, local path)
    ("P132833", "33761744", "rag/corpus_v3/pdf/LK/P132833/PP_2022-03-17_33761744.pdf"),
    ("P132833", "34172790", "discovery/samples/documents/pp_files/P132833_PP_34172790.pdf"),
    ("P148850", "40025990", "rag/corpus_v3/pdf/ET/P148850/PP_2025-06-27_40025990.pdf"),
    ("P175325", "40009757", "rag/corpus_v3/pdf/SN/P175325/PP_2025-04-29_40009757.pdf"),
]
TEXT_LINKS = HERE / "rag_scope_v3_35_projects" / "plan_coverage" / "CONTRACT_PLAN_EVIDENCE_LINKS.csv"
STEP_CORE = re.compile(r"(?<![A-Z0-9])[A-Z]{2}-[A-Z0-9&()]+(?:-[A-Z0-9&()]+)*?-\d{5,7}-(?:CW|GO|CS|NC|TA)-[A-Z]+")
FR = {"aposteriori": "post", "apriori": "prior"}


def clean(c) -> str:
    return re.sub(r"\s+", " ", (c or "")).strip()


def squash(x) -> str:
    return re.sub(r"[^a-z]", "", (x or "").lower())


def money(x: str | None) -> float | None:
    x = re.sub(r"[\s,]", "", x or "")
    return float(x) if re.fullmatch(r"\d+(\.\d+)?", x) else None


def iso(x: str | None) -> str | None:
    x = re.sub(r"\s+", "", x or "")
    return x if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x) else None


def cat_family(x: str | None) -> str | None:
    x = re.sub(r"[^A-Z]", "", (x or "").upper())
    if not x:
        return None
    return "NONC" if x.startswith("NON") else "WORK" if x.startswith(("WORK", "CIVIL")) else "CONS" if x.startswith(("CONS", "INDIVIDUAL")) else x[:4]


def get(rec: dict, *names: str) -> str | None:
    keys = {header_key(k): k for k in rec}
    for n in names:
        k = keys.get(header_key(n))
        if k and rec[k]:
            return rec[k]
    return None


def parse_pdf(path: Path) -> tuple[dict, list[dict]]:
    rows = []
    with pdfplumber.open(path) as pdf:
        head = "\n".join((p.extract_text() or "") for p in pdf.pages[:3])
        ident = {"project_id_in_pdf": (m.group(1) if (m := re.search(r"Project ID:\s*(P\d{6})", head)) else None),
                 "original_plan_approval_date": (m.group(1) if (m := re.search(r"Original Procurement Plan:\s*(\d{4}-\d{2}-\d{2})", head)) else None),
                 "plan_dates_in_header": sorted(set(re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", head)))[:6],
                 "plan_textual_date": (m.group(1) if (m := re.search(r"Date of the Procurement Plan:\s*([A-Za-z]+ \d{1,2}, \d{4})", head)) else None)}
        for pno, page in enumerate(pdf.pages, start=1):
            for tno, table in enumerate(page.extract_tables(), start=1):
                hi = next((i for i, r in enumerate(table) if r and clean(r[0]).startswith("Activity Reference")), None)
                if hi is None or hi + 1 >= len(table):
                    continue
                header, sub = table[hi], table[hi + 1]
                category = clean(table[hi - 1][0]) if hi > 0 else ""
                names, stage = [], ""
                for h, s in zip(header, sub):
                    h, s = clean(h), clean(s)
                    if s in {"Planned", "Actual"}:
                        stage = h or stage
                        names.append(f"{stage} | {s}")
                    else:
                        names.append(h)
                for rno, r in enumerate(table[hi + 2:], start=1):
                    if not r or not clean(r[0]) or clean(r[0]).startswith("Activity Reference"):
                        continue
                    rec = {n: clean(v) for n, v in zip(names, r) if n}
                    rec.update(_page=pno, _table=tno, _row=rno, _category=category)
                    rows.append(rec)
    return ident, rows


def main() -> None:
    OUT.mkdir(exist_ok=True)
    s = get_spark()
    pids = sorted({p for p, _, _ in PLANS})
    cs = [r.asDict() for r in s.table("workspace.wbg_silver.contract_awards").where(F.col("project_id").isin(pids)).select(
        "project_id", "wb_contract_number", "borrower_contract_reference", "procurement_category", "review_type", "contract_signing_date",
        "contract_amount_usd").collect()]
    by_core = collections.defaultdict(list)
    for c in cs:
        c["nref"] = normalize_ref(c["borrower_contract_reference"])
        m = STEP_CORE.search(c["nref"] or "")
        if m:
            by_core[(c["project_id"], m.group(0))].append(c)
    text_links = collections.defaultdict(list)
    with TEXT_LINKS.open(newline="") as fh:
        for x in csv.DictReader(fh):
            if x["document_id"] in {d for _, d, _ in PLANS}:
                text_links[(x["document_id"], x["wb_contract_number"])].append(x)
    plan_rows, links, ident_out = [], [], {}
    for p, did, rel in PLANS:
        ident, rows = parse_pdf(ROOT / rel)
        ident_out[did] = ident | {"project_matches": ident["project_id_in_pdf"] == p, "rows": len(rows)}
        refs = collections.Counter()
        parsed = []
        for r in rows:
            cell = re.sub(r"\s+", "", r.get("Activity Reference No. / Description") or next(iter(r.values()), "")).upper()
            core = STEP_CORE.search(normalize_ref(cell.split("/")[0]) or "")
            key = core.group(0) if core else None
            refs[key] += 1
            parsed.append((r, key))
        for r, key in parsed:
            act_amt = money(get(r, "Actual Amount (US$)"))
            est_amt = money(get(r, "Estimated Amount (US$)"))
            sp, sa = iso(get(r, "Signed Contract | Planned")), iso(get(r, "Signed Contract | Actual"))
            status = normalize_plan_status(get(r, "Process Status"))
            review = squash(get(r, "Review Type"))
            review = FR.get(review, review)
            group = by_core.get((p, key), []) if key else []
            plan_rows.append({"project_id": p, "document_id": did, "page": r["_page"], "table": r["_table"], "row": r["_row"], "section": r["_category"],
                              "activity_reference": key, "review": review or None, "method": get(r, "Method"), "status": status,
                              "estimated_amount_usd": est_amt, "actual_amount_usd": act_amt, "signed_planned": sp, "signed_actual": sa,
                              "matched_contracts": len(group)})
            lot_sum = sum(float(c["contract_amount_usd"] or 0) for c in group)
            for c in group:
                amt = float(c["contract_amount_usd"]) if c["contract_amount_usd"] is not None else None
                flags = []
                if cat_family(r["_category"]) and cat_family(c["procurement_category"]) and cat_family(r["_category"]) != cat_family(c["procurement_category"]):
                    flags.append("category_conflict")
                if review and c["review_type"] and review != squash(c["review_type"]):
                    flags.append("review_conflict")
                if status == "cancelled":
                    flags.append("plan_row_cancelled_in_this_version")
                amount = "unavailable"
                if act_amt:
                    target = amt if len(group) == 1 else lot_sum
                    amount = "agrees" if abs(act_amt - target) <= max(1.0, 0.005 * target) else "differs"
                dd = (date.fromisoformat(sa) - c["contract_signing_date"]).days if sa else None
                if refs[key] > 1:
                    st = "ambiguous"
                elif amount == "differs" or any(f in ("category_conflict", "review_conflict") for f in flags):
                    st = "conflicting"
                elif amount == "agrees":
                    st = "verified_amount"
                elif dd == 0:
                    st = "verified_exact_date"
                else:
                    st = "candidate"
                t = text_links.get((did, c["wb_contract_number"]), [])
                links.append({
                    "project_id": p, "document_id": did, "pdf_path": rel, "pdf_page": r["_page"], "pdf_table": r["_table"], "pdf_table_row": r["_row"],
                    "section": r["_category"], "activity_reference": key, "wb_contract_number": c["wb_contract_number"],
                    "borrower_contract_reference": c["borrower_contract_reference"],
                    "match_class": "exact_or_normalized" if c["nref"] == key else "lot_or_sequence_suffix",
                    "cardinality": "one_row_one_contract" if len(group) == 1 else f"one_row_{len(group)}_contracts",
                    "plan_review": review or None, "contract_review": c["review_type"], "plan_status": status,
                    "plan_estimated_amount_usd": est_amt, "plan_actual_amount_usd": act_amt, "contract_amount_usd": amt,
                    "lot_sum_usd": round(lot_sum, 2) if len(group) > 1 else None, "amount_check": amount,
                    "plan_signed_planned": sp, "plan_signed_actual": sa, "recorded_signing_date": c["contract_signing_date"],
                    "days_plan_actual_minus_recorded": dd, "flags": ";".join(flags), "pdf_evidence_status": st,
                    "text_evidence_status": t[0]["evidence_status"] if t else None, "text_page": t[0]["text_page"] if t else None,
                    "page_agrees_with_text": (str(r["_page"]) == t[0]["text_page"]) if t else None,
                    "status_agrees_with_text": (st == t[0]["evidence_status"]) if t else None})
    for name, rows in (("PDF_PLAN_ROWS.csv", plan_rows), ("PDF_CONTRACT_LINKS.csv", links)):
        with (OUT / name).open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    text_only = {k for k in text_links} - {(x["document_id"], x["wb_contract_number"]) for x in links}
    summary = {"identity": ident_out,
               "per_document": {d: {"pdf_rows": sum(r["document_id"] == d for r in plan_rows),
                                    "rows_with_step_reference": sum(r["document_id"] == d and bool(r["activity_reference"]) for r in plan_rows),
                                    "rows_matched": sum(r["document_id"] == d and r["matched_contracts"] > 0 for r in plan_rows),
                                    "contracts_linked": len({x["wb_contract_number"] for x in links if x["document_id"] == d}),
                                    "pdf_status": dict(collections.Counter(x["pdf_evidence_status"] for x in links if x["document_id"] == d)),
                                    "text_status_for_same_contracts": dict(collections.Counter(x["text_evidence_status"] for x in links if x["document_id"] == d))}
                                for _, d, _ in PLANS},
               "pdf_vs_text": {"links": len(links), "with_text_link": sum(x["text_evidence_status"] is not None for x in links),
                               "status_agrees": sum(bool(x["status_agrees_with_text"]) for x in links),
                               "page_agrees": sum(bool(x["page_agrees_with_text"]) for x in links),
                               "status_pairs_disagreeing": dict(collections.Counter(f"text={x['text_evidence_status']} pdf={x['pdf_evidence_status']}" for x in links
                                                                                    if x["text_evidence_status"] and not x["status_agrees_with_text"])),
                               "text_links_not_found_in_pdf_tables": len(text_only)},
               "date_differences_actual_minus_recorded": dict(collections.Counter(x["days_plan_actual_minus_recorded"] for x in links
                                                                                   if x["days_plan_actual_minus_recorded"] is not None))}
    (OUT / "pdf_link_validation.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
