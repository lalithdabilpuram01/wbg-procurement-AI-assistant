"""Module 1: contract-to-document entity-linking feasibility audit for the v2 70-project scope (READ-ONLY).

Inputs (read only):
  * Silver contract_awards (DS00005, signed >= 2019-07-01), contract_awards_legacy (DS01004), notice_contract_links,
    notice_contract_link_candidates, procurement_notice_details; Gold-free.
  * discovery/profiling/procurement_plan_activities.csv: table rows parsed earlier from 40 local plan PDFs (p05).
  * Local plan PDFs already on disk (discovery/samples/documents/pp_files) and the WDS text files of the 32 P178432
    plan versions downloaded in the paused deep dive (discovery/project_deep_dive/raw/P178432/docs_text).
  * v2 selection manifests and WDS metadata (selection_v2_10_countries/), cohort manifest, m05 API counts, m11 scoring.
No network, no downloads, no Databricks writes. Outputs go to entity_linking_audit_v1/ only. Descriptions, supplier names
and supplier IDs are never written.
"""
from __future__ import annotations

import collections
import csv
import json
import re
import statistics
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path

import fitz
from pyspark.sql import functions as F

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "discovery"))
from wbdisc.normalize import normalize_ref  # noqa: E402

from silver.common.session import get_spark  # noqa: E402

V2 = HERE / "selection_v2_10_countries"
OUT = HERE / "entity_linking_audit_v1"
ACTS = ROOT / "discovery" / "profiling" / "procurement_plan_activities.csv"
PP_PDFS = ROOT / "discovery" / "samples" / "documents" / "pp_files"
DD_TEXT = ROOT / "discovery" / "project_deep_dive" / "raw" / "P178432" / "docs_text"
DS00005_START = date(2019, 7, 1)
STEP = re.compile(r"^[A-Z]{2}-[A-Z0-9&()\-]+-\d{5,7}-(CW|GO|CS|NC|TA)-[A-Z]+$")
# STEP package reference inside a longer contract reference: lot / sequence suffixes ("...-GO-RFQ-LOT1", "...-CS-CQS-12")
# and programme prefixes ("CERC-LK-MOE-...") are common in DS00005.
STEP_CORE = re.compile(r"(?<![A-Z0-9])[A-Z]{2}-[A-Z0-9&()]+(?:-[A-Z0-9&()]+)*?-\d{5,7}-(?:CW|GO|CS|NC|TA)-[A-Z]+")
PLAN_REF = re.compile(r"[A-Z]{2}-[A-Z0-9/&.()\-]+?-\d{5,7}-(?:CW|GO|CS|NC|TA)-[A-Z]+")
SEQ = re.compile(r"-(\d{5,7})-(?:CW|GO|CS|NC|TA)-")
SILVER = "workspace.wbg_silver."


def text_key(s: str) -> str:
    """Page/text normalisation consistent with normalize_ref (separators unified, whitespace removed)."""
    s = unicodedata.normalize("NFKC", s).upper()
    s = re.sub(r"[‐-―_/\\.]", "-", s)
    return re.sub(r"\s+", "", s)


def num(x) -> float | None:
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return None


def d(x) -> date | None:
    x = re.sub(r"\s+", "", x or "")
    try:
        return date.fromisoformat(x[:10])
    except ValueError:
        return None


# French STEP exports (Senegal) label methods and review types in French; map to the DS00005 English labels.
FR = {"demandedeprix": "requestforquotations", "appeldoffres": "requestforbids", "individuel": "individualconsultantselection",
      "selectionfondeesurlesqualificationsdesconsultants": "consultantqualificationselection",
      "selectionfondeesurlaqualiteetlecout": "qualityandcostbasedselection", "selectionaumoindrecout": "leastcostselection",
      "selectiondirecte": "directselection", "ententedirecte": "directselection", "aposteriori": "post", "apriori": "prior"}


def cat4(x: str | None) -> str:
    """Plan section (WORKS, GOODS, NON CONSULTING SERVICES, CONSULTING FIRMS, INDIVIDUAL CONSULTANTS) vs DS00005 category."""
    x = re.sub(r"[^A-Z]", "", (x or "").upper())
    return "NONC" if x.startswith("NON") else "CONS" if x.startswith("INDIVIDUAL") else x[:4]


def squash(x: str | None) -> str:
    x = re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", x or "").encode("ascii", "ignore").decode().lower())
    return FR.get(x, x)


def step_core(n: str | None) -> str | None:
    m = STEP_CORE.search(n or "")
    return m.group(0) if m else None


def is_step(r: str | None) -> bool:
    n = normalize_ref(r)
    return bool(n and STEP.match(n))


def main() -> None:
    OUT.mkdir(exist_ok=True)
    projects = list(csv.DictReader(open(V2 / "PROJECT_SELECTION_MANIFEST_v2.csv")))
    sel = {r["project_id"]: r for r in projects}
    docs = [json.loads(x) for x in open(V2 / "all_documents_metadata_v2.jsonl")]
    manifest = list(csv.DictReader(open(V2 / "DOCUMENT_DOWNLOAD_MANIFEST_v2.csv")))
    scoring = json.loads((V2 / "country_scoring.json").read_text())
    with (ROOT / "cohort" / "poc_cohort_2000_manifest.csv").open(newline="", encoding="utf-8") as fh:
        cohort = {r["project_id"]: r for r in csv.DictReader(fh, )}
    api = {r["project_id"]: r for r in (json.loads(x) for x in (HERE / "evidence" / "api_document_counts_by_project.jsonl").read_text().splitlines())}
    countries = {r["country_code"] for r in projects}
    pool = sorted({p for c in scoring["viable"] if c["country_code"] in countries for p in c["eligible_project_ids"]})
    scope = sorted(set(pool) | set(sel))

    s = get_spark()
    ca = [r.asDict() for r in s.table(SILVER + "contract_awards").where(F.col("project_id").isin(scope)).select(
        "project_id", "wb_contract_number", "borrower_contract_reference", "procurement_category", "procurement_method", "review_type",
        "contract_signing_date", "contract_amount_usd", "contracts_sharing_reference_in_project").collect()]
    legacy = {r["project_id"]: r.asDict() for r in s.table(SILVER + "contract_awards_legacy").where(F.col("project_id").isin(scope)).groupBy("project_id").agg(
        F.count("*").alias("legacy_contracts"), F.count("borrower_contract_reference").alias("legacy_with_ref"),
        F.min("contract_signing_date").alias("legacy_first"), F.max("contract_signing_date").alias("legacy_last")).collect()}
    links = {r["project_id"]: r.asDict() for r in s.table(SILVER + "notice_contract_links").where(F.col("project_id").isin(scope)).groupBy("project_id").agg(
        F.countDistinct("wb_contract_number").alias("contracts_with_verified_notice_link"),
        F.countDistinct(F.when(F.col("ambiguity_status") == "unambiguous", F.col("wb_contract_number"))).alias("contracts_with_unambiguous_notice_link")).collect()}
    cands = {r["project_id"]: r["n"] for r in s.table(SILVER + "notice_contract_link_candidates").where(F.col("project_id").isin(scope)).groupBy("project_id").agg(
        F.countDistinct("wb_contract_number").alias("n")).collect()}
    nd = s.table(SILVER + "procurement_notice_details").where(F.col("project_id").isin(scope)).select("project_id", "bid_reference_normalized").collect()
    notice_step = collections.Counter(r["project_id"] for r in nd if step_core(r["bid_reference_normalized"]))
    notice_any = collections.Counter(r["project_id"] for r in nd)

    by_p: dict[str, list[dict]] = collections.defaultdict(list)
    for r in ca:
        r["nref"] = normalize_ref(r["borrower_contract_reference"])
        r["core"] = step_core(r["nref"])
        r["wsref"] = re.sub(r"\s+", "", r["borrower_contract_reference"] or "").upper()
        by_p[r["project_id"]].append(r)

    # ---------- 1. existing matches: parsed plan rows of the local latest plans ----------
    acts = list(csv.DictReader(open(ACTS)))
    probe_projects = sorted({a["_project_id"] for a in acts} & set(sel))
    row_out, unmatched_out, per_probe = [], [], {}
    for p in probe_projects:
        rows = [a for a in acts if a["_project_id"] == p]
        idx_n, idx_ws, idx_raw, idx_core = (collections.defaultdict(list) for _ in range(4))
        for c in by_p[p]:
            idx_n[c["nref"]].append(c)
            if c["core"] and c["core"] != c["nref"]:
                idx_core[c["core"]].append(c)
            idx_ws[c["wsref"]].append(c)
            idx_raw[(c["borrower_contract_reference"] or "").strip()].append(c)
        seqs = collections.defaultdict(list)
        for c in by_p[p]:
            m = SEQ.search(c["nref"] or "")
            if m:
                seqs[m.group(1)].append(c)
        page_ord = collections.Counter()
        parsed = []
        for a in rows:
            page_ord[(a["_wds_id"], a["_page"])] += 1
            cell = a.get("Activity Reference No. / Description") or a.get("Activity Reference No. / Descriptio n:") or ""
            m = PLAN_REF.search(re.sub(r"\s+", "", cell).upper())
            raw_ref = cell.split(" / ")[0].strip()
            parsed.append((a, m.group(0) if m else None, raw_ref, page_ord[(a["_wds_id"], a["_page"])]))
        ref_rows = collections.Counter(normalize_ref(x[1]) for x in parsed if x[1])
        stats = collections.Counter()
        for a, ws, raw_ref, ordinal in parsed:
            stats["plan_rows"] += 1
            if not ws:
                stats["rows_without_step_reference"] += 1
                continue
            stats["rows_with_step_reference"] += 1
            n = normalize_ref(ws)
            hits = idx_n.get(n, []) + idx_core.get(n, [])
            stats["match_exact_raw_string"] += bool(idx_raw.get(raw_ref))
            stats["match_whitespace_uppercase"] += bool(idx_ws.get(ws))
            stats["match_normalize_ref"] += bool(hits)
            status = re.sub(r"\s+", "", a.get("Process Status", "")).lower()  # PDF extraction splits words ("Under Implement ation")
            if not hits:
                sig_act = d(a.get("Signed Contract | Actual"))
                seq = SEQ.search(n)
                variant = [c["wb_contract_number"] for c in seqs.get(seq.group(1), [])] if seq else []
                reason = ("not signed in plan (" + status + "); no contract expected" if status not in ("signed", "completed") else
                          "signed before DS00005 coverage (2019-07-01)" if sig_act and sig_act < DS00005_START else
                          "same sequence number, different reference text in DS00005 (variant)" if variant else
                          "signed in plan; no DS00005 contract with this reference")
                stats["unmatched: " + reason.split(" (")[0]] += 1
                unmatched_out.append({"project_id": p, "plan_document_id": a["_wds_id"], "page": a["_page"], "row_on_page": ordinal, "plan_reference": n,
                                      "process_status": status, "signed_actual": a.get("Signed Contract | Actual", ""), "reason": reason,
                                      "variant_contracts_same_sequence": ";".join(variant)})
                continue
            plan_amt = num(a.get("Actual Amount (US$)"))
            sa, sp = d(a.get("Signed Contract | Actual")), d(a.get("Signed Contract | Planned"))
            lot_dates = [c["contract_signing_date"] for c in hits]
            lot_sum = sum(float(c["contract_amount_usd"] or 0) for c in hits)
            for c in hits:
                amt = float(c["contract_amount_usd"]) if c["contract_amount_usd"] is not None else None
                checks = {
                    "category": cat4(a.get("_category")) == cat4(c["procurement_category"]) if a.get("_category") else None,
                    "method": squash(a.get("Method")) == squash(c["procurement_method"]) if a.get("Method") else None,
                    "review_type": squash(a.get("Review Type")) == squash(c["review_type"]) if a.get("Review Type") else None,
                    "amount": ((abs(plan_amt - amt) <= max(1.0, 0.005 * amt)) if len(hits) == 1 else abs(plan_amt - lot_sum) <= max(1.0, 0.005 * lot_sum))
                    if plan_amt and amt else None,
                }
                agree = [k for k, v in checks.items() if v is True]
                disagree = [k for k, v in checks.items() if v is False]
                # independent corroboration: the reference itself encodes category and method, so only amount and the
                # actual signing date (two separate records of the same event) count as independent evidence
                date_ok = bool(sa and min(lot_dates) - timedelta(7) <= sa <= max(lot_dates) + timedelta(7))
                independent = [k for k, v in (("amount", checks["amount"]), ("actual_signing_date_within_7_days", date_ok)) if v]
                if disagree:
                    vstat = "conflicting (" + ";".join(disagree) + " disagree)"
                elif ref_rows[n] > 1:
                    vstat = f"ambiguous (reference on {ref_rows[n]} plan rows)"
                elif len(hits) == 1:
                    vstat = "verified" if independent else "candidate (reference only; no amount or actual date to corroborate)"
                else:
                    vstat = (f"verified_one_to_many (one plan activity, {len(hits)} lot contracts)" if independent else
                             f"candidate_one_to_many (one plan activity, {len(hits)} contracts; no amount or actual date to corroborate)")
                stats[vstat.split(" (")[0]] += 1
                row_out.append({
                    "project_id": p, "wb_contract_number": c["wb_contract_number"], "borrower_contract_reference": c["borrower_contract_reference"],
                    "plan_reference_extracted": ws, "normalized_reference": n, "plan_document_id": a["_wds_id"], "plan_document_date": a["_docdt"][:10],
                    "page": a["_page"], "row_on_page": ordinal, "section": a.get("_category", ""), "match_method": ("normalized reference equality, same project_id" if c["nref"] == n else
                                                                                                   "package reference + lot/sequence suffix, same project_id"),
                    "exact_raw_string_equal": bool(idx_raw.get(raw_ref)), "corroborating_fields": ";".join(agree), "disagreeing_fields": ";".join(disagree),
                    "independent_corroboration": ";".join(independent), "contracts_for_plan_row": len(hits),
                    "plan_method": a.get("Method", ""), "contract_method": c["procurement_method"], "plan_review": a.get("Review Type", ""),
                    "contract_review": c["review_type"], "plan_actual_amount_usd": plan_amt, "contract_amount_usd": amt,
                    "plan_process_status": a.get("Process Status", ""), "plan_signed_planned": sp, "plan_signed_actual": sa,
                    "ds00005_contract_signing_date": c["contract_signing_date"],
                    "days_plan_actual_minus_ds00005": (sa - c["contract_signing_date"]).days if sa else None,
                    "days_plan_planned_minus_ds00005": (sp - c["contract_signing_date"]).days if sp else None,
                    "contracts_sharing_reference_in_project": c["contracts_sharing_reference_in_project"], "verification_status": vstat})
        contract_step = [c for c in by_p[p] if c["core"]]
        plan_doc = rows[0]["_wds_id"]
        plan_date = d(rows[0]["_docdt"])
        matched_contracts = {r["wb_contract_number"] for r in row_out if r["project_id"] == p}
        not_in_rows = [c for c in contract_step if c["wb_contract_number"] not in matched_contracts]
        per_probe[p] = {"plan_document_id": plan_doc, "plan_document_date": str(plan_date), "parsed_pages": len({a["_page"] for a in rows}), **stats,
                        "contracts": len(by_p[p]), "contracts_step_format": len(contract_step), "contracts_matched": len(matched_contracts),
                        "step_contracts_not_in_parsed_rows": len(not_in_rows),
                        "of_which_signed_after_plan_date": sum(c["contract_signing_date"] > plan_date for c in not_in_rows)}

    # PDF text scan of the same local plan PDFs (all pages), to separate table-parsing misses from absent references
    pdf_scan = {}
    for p in probe_projects:
        pdf = next(PP_PDFS.glob(f"{p}_PP_*.pdf"), None)
        if not pdf:
            continue
        doc = fitz.open(pdf)
        pages = [text_key(pg.get_text()) for pg in doc]
        found = {}
        for c in by_p[p]:
            if c["core"]:
                pg = [i + 1 for i, t in enumerate(pages) if c["core"] in t]
                if pg:
                    found[c["wb_contract_number"]] = pg
        matched = {r["wb_contract_number"] for r in row_out if r["project_id"] == p}
        pdf_scan[p] = {"pdf": str(pdf.relative_to(ROOT)), "pdf_pages": len(pages), "step_contracts": sum(bool(c["core"]) for c in by_p[p]),
                       "contracts_whose_reference_appears_in_pdf_text": len(found), "of_which_also_matched_in_parsed_rows": len(set(found) & matched),
                       "found_in_text_but_not_in_parsed_rows": sorted(set(found) - matched), "matched_rows_not_found_in_text": sorted(matched - set(found))}
        absent = [c for c in by_p[p] if c["core"] and c["wb_contract_number"] not in found]
        plan_date = d(next(a["_docdt"] for a in acts if a["_project_id"] == p))
        pdf_scan[p].update({"step_contracts_absent_from_text": len(absent),
                            "absent_signed_after_plan_date": sum(c["contract_signing_date"] > plan_date for c in absent),
                            "absent_signing_range": [str(min(c["contract_signing_date"] for c in absent)), str(max(c["contract_signing_date"] for c in absent))] if absent else [],
                            "found_signing_range": [str(min(c["contract_signing_date"] for c in by_p[p] if c["wb_contract_number"] in found)),
                                                    str(max(c["contract_signing_date"] for c in by_p[p] if c["wb_contract_number"] in found))] if found else []})
        doc.close()

    # P178432: 32 local plan versions as WDS text (no table parsing): reference occurrence per version
    versions = sorted(DD_TEXT.glob("*_PP_*.txt"))
    p178 = {"project_id": "P178432", "plan_versions_local": len(versions), "contracts": len(by_p["P178432"]),
            "contracts_step_format": sum(bool(c["core"]) for c in by_p["P178432"]), "per_version": []}
    first_seen, seen_any, in_latest = {}, set(), set()
    for v in versions:
        t = text_key(v.read_text(errors="ignore"))
        vd = d(v.name[:10])
        hit = {c["wb_contract_number"] for c in by_p["P178432"] if c["core"] and c["core"] in t}
        signed_before = {c["wb_contract_number"] for c in by_p["P178432"] if c["contract_signing_date"] <= vd}
        for h in hit:
            first_seen.setdefault(h, v.name[:10])
        seen_any |= hit
        in_latest = hit
        p178["per_version"].append({"version_date": v.name[:10], "document_id": v.stem.split("_")[-1], "contracts_referenced": len(hit),
                                    "contracts_signed_by_version_date": len(signed_before), "signed_and_referenced": len(hit & signed_before)})
    p178.update({"contracts_referenced_in_any_version": len(seen_any), "contracts_referenced_in_latest_version": len(in_latest),
                 "referenced_earlier_but_absent_from_latest": len(seen_any - in_latest),
                 "contracts_never_referenced": len({c["wb_contract_number"] for c in by_p["P178432"]} - seen_any),
                 "latest_version_in_v2_manifest": any(m["project_id"] == "P178432" and m["role"] == "PP_latest" and m["document_id"] == versions[-1].stem.split("_")[-1] for m in manifest)})
    # Narrative documents already local for the 70 (deep-dive text of P178432 PAD/ISRs): any contract references?
    narr = {}
    for v in sorted(DD_TEXT.glob("*.txt")):
        if "_PP_" in v.name:
            continue
        t = text_key(v.read_text(errors="ignore"))
        narr[v.name] = sum(1 for c in by_p["P178432"] if c["core"] and c["core"] in t)
    p178["narrative_docs_contract_references"] = narr

    # ---------- 2/5. matching potential for the 70 and the same-country pools ----------
    pp_meta = collections.defaultdict(list)
    for x in docs:
        if x["doc_type"] == "PP" and x["project_id"] in sel:
            pp_meta[x["project_id"]].append(x)
    best_status: dict = {}
    rank = ["verified", "verified_one_to_many", "candidate", "candidate_one_to_many", "ambiguous", "conflicting"]
    for r in row_out:
        k, st = (r["project_id"], r["wb_contract_number"]), r["verification_status"].split(" (")[0]
        best_status[k] = min(best_status.get(k, st), st, key=rank.index)
    count = lambda sts: collections.Counter(k[0] for k, st in best_status.items() if st in sts)  # noqa: E731
    verified_by_p = count({"verified"})
    verified_many_by_p = count({"verified_one_to_many"})
    cand_by_p = count({"candidate", "candidate_one_to_many"})
    amb_by_p = count({"ambiguous", "conflicting"})
    text_cand = {p: v["contracts_whose_reference_appears_in_pdf_text"] for p, v in pdf_scan.items()}
    text_cand["P178432"] = p178["contracts_referenced_in_any_version"]

    def metrics(p: str) -> dict:
        cs = by_p.get(p, [])
        step = [c for c in cs if c["core"]]
        step_exact = [c for c in cs if c["nref"] and STEP.match(c["nref"])]
        refcount = collections.Counter(c["nref"] for c in cs if c["nref"])
        shared = sum(1 for c in cs if c["nref"] and refcount[c["nref"]] > 1)
        sdates = sorted(c["contract_signing_date"] for c in cs)
        pps = pp_meta.get(p, [])
        pp_dates = sorted(x["document_date"] for x in pps if x["document_date"] and x["document_date"] != "None")
        latest = date.fromisoformat(pp_dates[-1]) if pp_dates else None
        lg = legacy.get(p, {})
        m = {
            "contracts_ds00005": len(cs), "contracts_step_exact_format": len(step_exact), "contracts_step_reference": len(step), "step_share": round(len(step) / len(cs), 3) if cs else 0,
            "contracts_with_shared_reference": shared, "distinct_references": len(refcount),
            "first_signing": str(sdates[0]) if sdates else "", "last_signing": str(sdates[-1]) if sdates else "",
            "legacy_contracts_ds01004": lg.get("legacy_contracts", 0),
            "notices_api_detail": notice_any.get(p, 0), "notices_with_step_bid_reference": notice_step.get(p, 0),
            "contracts_with_verified_notice_link": links.get(p, {}).get("contracts_with_verified_notice_link", 0),
            "contracts_with_unambiguous_notice_link": links.get(p, {}).get("contracts_with_unambiguous_notice_link", 0),
            "contracts_with_candidate_notice_link": cands.get(p, 0),
            "api_pp_count": api.get(p, {}).get("by_type", {}).get("Procurement Plan", 0),
            "api_icr": api.get(p, {}).get("by_type", {}).get("Implementation Completion and Results Report", 0),
            "pp_first_date": pp_dates[0] if pp_dates else "", "pp_latest_date": pp_dates[-1] if pp_dates else "",
            "contracts_signed_after_latest_pp": sum(c["contract_signing_date"] > latest for c in cs) if latest else None,
            "step_contracts_signed_by_latest_pp": sum(c["contract_signing_date"] <= latest for c in step) if latest else None,
        }
        return m

    def potential(p: str, m: dict) -> tuple[str, str]:
        if verified_by_p.get(p) or verified_many_by_p.get(p):
            return "verified", (f"{verified_by_p.get(p, 0)} contracts verified one-to-one and {verified_many_by_p.get(p, 0)} as lots of one plan activity "
                                "(local latest plan, parsed rows)")
        if p == "P178432" and p178["contracts_referenced_in_any_version"]:
            return "high", (f"{p178['contracts_referenced_in_any_version']} of {p178['contracts']} contract references found in the text of 32 local plan "
                            "versions (candidates; row-level corroboration needs table parsing)")
        if m["contracts_step_reference"] >= 10 and m["step_share"] >= 0.6:
            return "high", f"{m['contracts_step_reference']} of {m['contracts_ds00005']} contracts carry STEP plan-activity references"
        if m["contracts_step_reference"] >= 3:
            return "medium", f"{m['contracts_step_reference']} of {m['contracts_ds00005']} contracts carry STEP references ({m['step_share']:.0%})"
        return "unknown", "few or no STEP-format contract references in DS00005; plan rows may still carry legacy references; needs plan parsing"

    rows70 = []
    for p, r in sel.items():
        m = metrics(p)
        cls, why = potential(p, m)
        rows70.append({"country_code": r["country_code"], "project_id": p, "sector": r["sector"], "status": r["status"], "approval_fy": r["approval_fy"],
                       "verified_contracts_one_to_one": verified_by_p.get(p, 0), "verified_contracts_lots": verified_many_by_p.get(p, 0),
                       "candidate_contracts_row_level": cand_by_p.get(p, 0), "ambiguous_or_conflicting_contracts": amb_by_p.get(p, 0),
                       "candidate_contracts_reference_in_local_text": text_cand.get(p, 0),
                       "contracts_unassessed": m["contracts_ds00005"] - len({k[1] for k in best_status if k[0] == p} | set(pdf_scan.get(p, {}).get("found_in_text_but_not_in_parsed_rows", [])))
                       if p != "P178432" else m["contracts_ds00005"] - p178["contracts_referenced_in_any_version"],
                       "local_plan_evidence": ("parsed latest plan" if p in per_probe else "32 plan versions (text)" if p == "P178432" else "none local"),
                       **m, "matching_potential": cls, "potential_basis": why})
    rows70.sort(key=lambda x: (x["country_code"], ["verified", "high", "medium", "unknown"].index(x["matching_potential"]), x["project_id"]))

    # swaps: same country, same sector, same status, not selected, stronger potential; keep ICR availability if the selected had one
    swaps = []
    for r in rows70:
        if r["matching_potential"] not in ("unknown", "medium"):
            continue
        for q in pool:
            if q in sel or cohort[q]["country_code"] != r["country_code"] or cohort[q]["major_sector"] != r["sector"] or cohort[q]["status"] != r["status"]:
                continue
            m = metrics(q)
            if m["contracts_step_reference"] < max(10, 2 * r["contracts_step_reference"]) or m["step_share"] < 0.6:
                continue
            swaps.append({"country_code": r["country_code"], "replace": r["project_id"], "replace_potential": r["matching_potential"],
                          "replace_step_contracts": r["contracts_step_reference"], "replace_has_icr": r["api_icr"] > 0, "with": q,
                          "with_name": cohort[q]["project_name"][:60], "sector": r["sector"], "status": r["status"], "approval_fy": cohort[q]["approval_fy"],
                          "with_contracts": m["contracts_ds00005"], "with_step_contracts": m["contracts_step_reference"], "with_step_share": m["step_share"],
                          "with_shared_ref_contracts": m["contracts_with_shared_reference"], "with_verified_notice_links": m["contracts_with_verified_notice_link"],
                          "with_has_icr": m["api_icr"] > 0, "with_api_pp": m["api_pp_count"],
                          "keeps_icr_coverage": (m["api_icr"] > 0) or not (r["api_icr"] > 0)})

    def write(name: str, rows: list[dict]) -> None:
        with (OUT / name).open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

    write("PROJECT_LINKING_POTENTIAL_70.csv", rows70)
    write("PLAN_ROW_CONTRACT_MATCHES_4_PROJECTS.csv", row_out)
    write("PLAN_ROWS_UNMATCHED_4_PROJECTS.csv", unmatched_out)
    if swaps:
        write("SWAP_CANDIDATES_SAME_COUNTRY.csv", swaps)
    lk = {"notice_details_with_reference": [r.asDict() for r in s.table(SILVER + "procurement_notice_details").where(
              "bid_reference_normalized = 'LK-RDA-204059-CW-RFQ' or award_reference_in_text_normalized = 'LK-RDA-204059-CW-RFQ'").select(
              "notice_op_id", "notice_type", "notice_date", "project_id").collect()],
          "notice_contract_links_for_1674110": s.table(SILVER + "notice_contract_links").where("wb_contract_number = '1674110'").count(),
          "plan_row": [r for r in row_out if r["wb_contract_number"] == "1674110"]}
    summary = {"sri_lanka_date_case": lk, "probe_projects": per_probe, "pdf_text_scan": pdf_scan, "p178432_plan_versions": p178,
               "ds00005_coverage_start": str(DS00005_START),
               "potential_counts": dict(collections.Counter(r["matching_potential"] for r in rows70)),
               "potential_by_country": {c: dict(collections.Counter(r["matching_potential"] for r in rows70 if r["country_code"] == c)) for c in sorted(countries)},
               "match_date_differences": {
                   "plan_actual_minus_ds00005_days": collections.Counter(r["days_plan_actual_minus_ds00005"] for r in row_out if r["days_plan_actual_minus_ds00005"] is not None).most_common(),
                   "plan_planned_minus_ds00005_days_median": statistics.median([r["days_plan_planned_minus_ds00005"] for r in row_out if r["days_plan_planned_minus_ds00005"] is not None])},
               "verification_status_counts_pairs": dict(collections.Counter(r["verification_status"].split(" (")[0] for r in row_out)),
               "verification_status_counts_contracts": dict(collections.Counter(best_status.values())),
               "verified_contracts_by_project": {p: [verified_by_p.get(p, 0), verified_many_by_p.get(p, 0), cand_by_p.get(p, 0), amb_by_p.get(p, 0)] for p in per_probe},
               "swap_candidates": len(swaps)}
    (OUT / "entity_linking_audit_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str)[:12000])


if __name__ == "__main__":
    main()
