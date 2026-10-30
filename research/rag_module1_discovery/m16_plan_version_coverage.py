"""Procurement-plan version coverage for the v2 70 projects (READ-ONLY analysis of the fetched WDS plan texts).

For every plan version with a text representation (m15), activity rows are parsed (plan_text_rows) and compared with
the project's structured contracts:
  * DS00005 (Silver contract_awards, signed >= 2019-07-01) and DS01004 (Silver contract_awards_legacy, prior review,
    <= 2016-06-30), read-only.
  * Link key: same project_id AND the contract's STEP package reference (normalize_ref, lot/sequence suffix removed) is
    the package printed in the plan row. Contracts without a STEP package are only searched as whole normalised
    references in the text (>= 8 characters, letters and digits) and can only be 'text_reference_only' candidates.
Evidence tiers per (contract, plan version, row) - project_id alone never creates a link:
  verified_amount        plan 'Actual Amount (US$)' > 0 equals the DS00005 amount (1 row : 1 contract) or the sum of
                         the lot contracts (1 row : n contracts), tolerance max(US$1, 0.5%); no category/review conflict.
  verified_exact_date    layout-form text only: plan 'Signed Contract | Actual' equals the DS00005 contract signing date
                         exactly; plan actual amount absent or 0; no category/review conflict. Reported separately.
  candidate              reference match, no conflict, no independent corroboration (dates that differ are kept,
                         never reconciled; a difference within 7 days is not treated as proof).
  conflicting            plan actual amount > 0 that differs, or a category or review-type conflict. (A row marked
                         cancelled in one version is flagged, not treated as a conflict: status changes between versions.)
  ambiguous              the package appears on two or more rows of one version with different values.
  text_reference_only    non-STEP reference found in the text; no row-level evidence.
Planned dates (plan), actual dates (plan) and recorded dates (DS00005 contract_signing_date, source_as_of_date) are kept in
separate columns. Unique contracts and unique plan activities are counted separately.
Version selection: greedy per project - each step adds the version with the largest gain in verified contracts, then
reference-matched contracts; marginal gains recorded. The latest plan is always kept.
Writes only to plan_version_coverage_v1/. No PDFs, no Databricks writes.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

from pyspark.sql import functions as F

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "discovery"))
sys.path.insert(0, str(HERE))
import plan_text_rows as T  # noqa: E402
from wbdisc.normalize import normalize_ref  # noqa: E402

from silver.common.session import get_spark  # noqa: E402

V2 = HERE / "selection_v2_10_countries"
OUT = HERE / "plan_version_coverage_v1"
TEXTS = HERE / "plan_version_coverage_v1"  # fetch_log.jsonl and plan_text/ from m15 (read only)
SILVER = "workspace.wbg_silver."
STEP_CORE = re.compile(r"(?<![A-Z0-9])[A-Z]{2}-[A-Z0-9&()]+(?:-[A-Z0-9&()]+)*?-\d{5,7}-(?:CW|GO|CS|NC|TA)-[A-Z]+")
VERIFIED = ("verified_amount", "verified_exact_date")
RANK = ["verified_amount", "verified_exact_date", "candidate", "text_reference_only", "ambiguous", "conflicting"]
P05_ROWS = ROOT / "discovery" / "profiling" / "procurement_plan_activities.csv"
LOCAL_PP = {**{p.stem.split("_")[-1]: str(p.relative_to(ROOT)) for p in (ROOT / "discovery" / "samples" / "documents" / "pp_files").glob("*.pdf")},
            **{p.stem.split("_")[-1]: str(p.relative_to(ROOT)) for p in (ROOT / "discovery" / "project_deep_dive" / "raw").glob("*/pp_pdf/*.pdf")}}


def cat_family(x: str | None) -> str | None:
    x = re.sub(r"[^A-Z]", "", (x or "").upper())
    if not x:
        return None
    return "NONC" if x.startswith("NON") else "WORK" if x.startswith(("WORK", "CIVIL")) else "CONS" if x.startswith(("CONS", "INDIVIDUAL")) else x[:4]


def squash(x: str | None) -> str:
    return re.sub(r"[^a-z]", "", (x or "").lower())


def tol_eq(a: float, b: float) -> bool:
    return abs(a - b) <= max(1.0, 0.005 * b)


def main() -> None:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", help="comma-separated project IDs (default: all 70 in the v2 manifest)")
    ap.add_argument("--out", help="output folder (default: plan_version_coverage_v1)")
    ap.add_argument("--no-manifest", action="store_true", help="do not write the proposed v3 manifest")
    args = ap.parse_args()
    if args.out:
        OUT = Path(args.out)
        OUT.mkdir(parents=True, exist_ok=True)
    projects = list(csv.DictReader(open(V2 / "PROJECT_SELECTION_MANIFEST_v2.csv")))
    if args.projects:
        keep = set(args.projects.split(","))
        projects = [r for r in projects if r["project_id"] in keep]
        assert len(projects) == len(keep), "unknown project id"
    sel = {r["project_id"]: r for r in projects}
    manifest = list(csv.DictReader(open(V2 / "DOCUMENT_DOWNLOAD_MANIFEST_v2.csv")))
    meta = {d["document_id"]: d for d in map(json.loads, open(V2 / "all_documents_metadata_v2.jsonl")) if d["doc_type"] == "PP" and d["project_id"] in sel}
    fetch = {}
    for e in map(json.loads, (TEXTS / "fetch_log.jsonl").read_text().splitlines()):
        fetch[e["document_id"]] = e  # last attempt wins

    s = get_spark()
    ca = [r.asDict() for r in s.table(SILVER + "contract_awards").where(F.col("project_id").isin(list(sel))).select(
        "project_id", "wb_contract_number", "borrower_contract_reference", "procurement_category", "procurement_method", "review_type",
        "contract_signing_date", "contract_amount_usd", "source_as_of_date").collect()]
    lg = [r.asDict() for r in s.table(SILVER + "contract_awards_legacy").where(F.col("project_id").isin(list(sel))).select(
        "project_id", F.col("contract_number").alias("wb_contract_number"), "borrower_contract_reference", "procurement_category", "procurement_method",
        F.lit("Prior").alias("review_type"), "contract_signing_date", "contract_amount_usd", "source_as_of_date").collect()]
    contracts = collections.defaultdict(list)
    for src, rows in (("DS00005", ca), ("DS01004", lg)):
        for r in rows:
            r["source"] = src
            r["cid"] = f"{src}:{r['wb_contract_number']}"
            r["nref"] = normalize_ref(r["borrower_contract_reference"])
            m = STEP_CORE.search(r["nref"] or "")
            r["core"] = m.group(0) if m else None
            r["amount"] = float(r["contract_amount_usd"]) if r["contract_amount_usd"] is not None else None
            contracts[r["project_id"]].append(r)
    # STEP method codes seen in contract references; used to cut text glued onto a plan reference by whitespace removal
    # ("...-GO-RFQPENDING" -> "...-GO-RFQ") before matching
    method_codes = {c["core"].rsplit("-", 1)[1] for cs in contracts.values() for c in cs if c["core"]} | {
        "RFQ", "RFB", "RFP", "DIR", "QCBS", "CQS", "LCS", "FBS", "QBS", "INDV", "CDS", "SSS"}

    links, versions, activities = [], [], []
    for p in sorted(sel):
        cs = contracts[p]
        prefixes = {c["core"][:2] for c in cs if c["core"]} | {sel[p]["country_code"]}
        by_core = collections.defaultdict(list)
        for c in cs:
            if c["core"]:
                by_core[c["core"]].append(c)
        legacy_keys = {c["nref"]: c for c in cs if not c["core"] and c["nref"] and len(c["nref"]) >= 8 and re.search(r"\d", c["nref"]) and re.search(r"[A-Z]", c["nref"])}
        for did, d in sorted(meta.items(), key=lambda kv: (kv[1]["document_date"] or "", kv[0])):
            if d["project_id"] != p:
                continue
            f = fetch.get(did, {})
            v = {"project_id": p, "document_id": did, "document_date": d["document_date"], "disclosure_date": d["disclosure_date"],
                 "version_type": d["version_type"], "language": d["language"], "pdf_url": d["pdf_url"], "txt_url": d["txt_url"],
                 "text_status": f.get("http_status"), "text_bytes": f.get("bytes"), "text_sha256": f.get("sha256"),
                 "unavailable_reason": f.get("unavailable_reason") if f else "not fetched", "text_form": None, "rows": 0,
                 "rows_with_actual_amount": 0, "rows_with_signed_actual": 0, "parse_error": None}
            versions.append(v)
            if f.get("http_status") != 200 or f.get("unavailable_reason"):
                continue
            text = (TEXTS / f["path"]).read_text(errors="ignore")
            page_at_line = []  # WDS text keeps PDF page breaks as form feeds: page of the text, to be confirmed on the PDF
            pg = 1
            for ln in text.split("\n"):
                page_at_line.append(pg)
                pg += ln.count("\f")
            try:
                form, rows = T.parse(text, prefixes)
            except Exception as exc:  # noqa: BLE001
                v["parse_error"] = repr(exc)[:200]
                continue
            own_rows = 0
            v.update(text_form=form, rows=len(rows), rows_with_actual_amount=sum(bool(r.actual_amount) for r in rows),
                     rows_with_signed_actual=sum(bool(r.signed_actual) for r in rows))
            flat, _ = T.stripped_with_map(text)
            row_hits = collections.defaultdict(list)
            for i, r in enumerate(rows):
                head, meth = r.ref_key.rsplit("-", 1)
                code = max((c for c in method_codes if meth.startswith(c)), key=len, default=meth)
                rk = f"{head}-{code}"
                hit_cores = [rk[j:] for j in range(max(0, len(rk) - 80), len(rk) - 10)
                             if rk[j:j + 2].isalpha() and rk[j + 2:j + 3] == "-" and rk[j:] in by_core]
                # own-project activity: the reference starts with one of the project's country prefixes (documents listed
                # under a project sometimes compile other projects' plans)
                own = next((rk[j:] for j in range(max(0, len(rk) - 80), len(rk) - 10)
                            if rk[j:j + 2] in prefixes and rk[j + 2:j + 3] == "-"), None)
                own_rows += bool(own)
                activities.append({"project_id": p, "document_id": did, "activity_reference_printed": r.ref_printed[:120],
                                   "activity_key": own or f"other:{r.activity_id}", "own_project_reference": bool(own),
                                   "section": r.section, "status": r.status, "line_no": r.line_no, "matched_contracts": sum(len(by_core[k]) for k in hit_cores)})
                for k in hit_cores:
                    row_hits[k].append((i, r))
            v.update(rows_own_project_reference=own_rows, rows_other_reference=len(rows) - own_rows,
                     mostly_other_projects=len(rows) > 20 and own_rows < 0.5 * len(rows))
            for k, hits in row_hits.items():
                group = by_core[k]
                distinct_rows = {(r.actual_amount, r.signed_actual, r.status) for _, r in hits}
                for i, r in hits:
                    lot_sum = sum(c["amount"] or 0 for c in group)
                    for c in group:
                        flags = []
                        if r.category and cat_family(c["procurement_category"]) and r.category != cat_family(c["procurement_category"]):
                            flags.append("category_conflict")
                        if r.review and c["review_type"] and r.review != squash(c["review_type"]):
                            flags.append("review_conflict")
                        if r.method and squash(c["procurement_method"]) and r.method != squash(c["procurement_method"]):
                            flags.append("method_label_differs (parse-dependent, not evidence)")
                        if r.status == "canceled":
                            flags.append("plan_row_cancelled_in_this_version (status can change between versions; not an identity conflict)")
                        amt = "unavailable"
                        if r.actual_amount:
                            ok = tol_eq(r.actual_amount, c["amount"]) if len(group) == 1 else tol_eq(r.actual_amount, lot_sum)
                            amt = "agrees" if ok else "differs"
                        dd = (date.fromisoformat(r.signed_actual) - c["contract_signing_date"]).days if r.signed_actual else None
                        hard = [x for x in flags if x in ("category_conflict", "review_conflict")]
                        if len(distinct_rows) > 1:
                            st = "ambiguous"
                        elif amt == "differs" or hard:
                            st = "conflicting"
                        elif amt == "agrees":
                            st = "verified_amount"
                        elif dd == 0:
                            st = "verified_exact_date"
                        else:
                            st = "candidate"
                        links.append({
                            "project_id": p, "contract_id": c["cid"], "contract_source": c["source"], "wb_contract_number": c["wb_contract_number"],
                            "borrower_contract_reference": c["borrower_contract_reference"], "contract_reference_normalized": c["nref"],
                            "package_reference": k, "match_method": "normalized reference equality" if c["nref"] == k else "package reference + lot/sequence suffix",
                            "cardinality": "one_row_one_contract" if len(group) == 1 else f"one_row_{len(group)}_contracts",
                            "rows_with_package_in_version": len(hits), "document_id": did, "document_date": d["document_date"],
                            "disclosure_date": d["disclosure_date"], "pdf_url": d["pdf_url"], "txt_url": d["txt_url"], "text_sha256": f.get("sha256"),
                            "text_form": form, "line_no": r.line_no, "text_page": page_at_line[min(r.line_no, len(page_at_line)) - 1],
                            "match_class": ("exact" if c["nref"] == k and (c["borrower_contract_reference"] or "").strip() in r.ref_printed else
                                            "normalized" if c["nref"] == k else "lot_or_sequence_suffix"), "plan_reference_printed": r.ref_printed[:120], "section": r.section,
                            "plan_review": r.review, "contract_review": c["review_type"], "plan_method_label": r.method, "contract_method": c["procurement_method"],
                            "plan_status": r.status, "plan_estimated_amount_usd": r.estimated_amount, "plan_actual_amount_usd": r.actual_amount,
                            "contract_amount_usd": c["amount"], "lot_contracts_amount_sum_usd": round(lot_sum, 2) if len(group) > 1 else None,
                            "amount_check": amt, "plan_signed_planned_date": r.signed_planned, "plan_signed_actual_date": r.signed_actual,
                            "plan_dates_unassigned": ";".join(r.dates_unassigned), "recorded_contract_signing_date": c["contract_signing_date"],
                            "recorded_source_as_of_date": c["source_as_of_date"], "days_plan_actual_minus_recorded": dd,
                            "flags": ";".join(flags), "evidence_status": st})
            for key, c in legacy_keys.items():
                if re.search(re.escape(key) + r"(?!\d)", flat):  # not a prefix of a longer number ("-1" vs "-10")
                    links.append({"project_id": p, "contract_id": c["cid"], "contract_source": c["source"], "wb_contract_number": c["wb_contract_number"],
                                  "borrower_contract_reference": c["borrower_contract_reference"], "contract_reference_normalized": key,
                                  "package_reference": None, "match_method": "non-STEP normalized reference found in text", "cardinality": None,
                                  "rows_with_package_in_version": None, "document_id": did, "document_date": d["document_date"],
                                  "disclosure_date": d["disclosure_date"], "pdf_url": d["pdf_url"], "txt_url": d["txt_url"], "text_sha256": f.get("sha256"),
                                  "text_form": form, "contract_amount_usd": c["amount"], "recorded_contract_signing_date": c["contract_signing_date"],
                                  "recorded_source_as_of_date": c["source_as_of_date"], "evidence_status": "text_reference_only"})

    # ---- per-contract best evidence, version cover, marginal gains ----
    cur_pp = collections.defaultdict(set)
    for m in manifest:
        if m["doc_type"] == "PP":
            cur_pp[m["project_id"]].add(m["document_id"])
    by_pv = collections.defaultdict(lambda: collections.defaultdict(set))  # p -> doc -> {(cid, status)}
    best = {}
    for x in links:
        by_pv[x["project_id"]][x["document_id"]].add((x["contract_id"], x["evidence_status"]))
        k = (x["project_id"], x["contract_id"])
        best[k] = min(best.get(k, x["evidence_status"]), x["evidence_status"], key=RANK.index)
    conflict_any = {(x["project_id"], x["contract_id"]) for x in links if x["evidence_status"] == "conflicting"}

    def cov(p: str, docs: set) -> tuple[set, set, set]:
        ver, ref, txt = set(), set(), set()
        for d in docs:
            for cid, st in by_pv[p].get(d, ()):
                (ver if st in VERIFIED else txt if st == "text_reference_only" else ref).add(cid)
        return ver, ver | ref, ver | ref | txt

    proj_rows, steps, proposed = [], [], []
    for p in sorted(sel):
        vlist = [v for v in versions if v["project_id"] == p]
        alldocs = {v["document_id"] for v in vlist}
        latest = max(vlist, key=lambda v: (v["document_date"] or "", v["document_id"]))["document_id"] if vlist else None
        mv, mr, mt = cov(p, alldocs)
        cv, cr, ct = cov(p, cur_pp[p])
        chosen, gv, gr = [], set(), set()
        while True:
            cand = [(len(cov(p, {d})[0] - gv), len(cov(p, {d})[1] - gr), meta[d]["document_date"] or "", d) for d in alldocs - set(chosen)]
            if not cand:
                break
            bv, br, _, bd = max(cand)
            if bv == 0 and br == 0:
                break
            chosen.append(bd)
            nv, nr, _ = cov(p, {bd})
            gv |= nv
            gr |= nr
            steps.append({"project_id": p, "step": len(chosen), "document_id": bd, "document_date": meta[bd]["document_date"], "added_verified_contracts": bv,
                          "added_reference_matched_contracts": br, "cumulative_verified": len(gv), "cumulative_reference_matched": len(gr),
                          "in_current_v2_manifest": bd in cur_pp[p], "pdf_url": meta[bd]["pdf_url"]})
        rec = list(dict.fromkeys(chosen + ([latest] if latest else [])))
        rv, rr, rt = cov(p, set(rec))
        ds5 = [c for c in contracts[p] if c["source"] == "DS00005"]
        acts = {a["activity_key"] for a in activities if a["project_id"] == p and a["own_project_reference"]}
        matched_acts = {a["activity_key"] for a in activities if a["project_id"] == p and a["own_project_reference"] and a["matched_contracts"]}
        st_counts = collections.Counter(st for (pp, _), st in best.items() if pp == p)
        proj_rows.append({
            "country_code": sel[p]["country_code"], "project_id": p, "status": sel[p]["status"], "approval_fy": sel[p]["approval_fy"],
            "contracts_ds00005": len(ds5), "contracts_ds01004_legacy": len(contracts[p]) - len(ds5),
            "contracts_with_step_package": sum(bool(c["core"]) for c in contracts[p]),
            "plan_versions_listed": len(vlist), "plan_texts_available": sum(v["text_status"] == 200 and not v["unavailable_reason"] for v in vlist),
            "texts_layout": sum(v["text_form"] == "layout" for v in vlist), "texts_flow": sum(v["text_form"] == "flow" for v in vlist),
            "texts_without_step_table": sum(v["text_form"] == "no_step_table" for v in vlist),
            "texts_unavailable": sum(v["text_status"] != 200 or bool(v["unavailable_reason"]) for v in vlist),
            "unique_plan_activities": len(acts), "activities_matched_to_contracts": len(matched_acts),
            "current_versions": len(cur_pp[p]), "current_verified": len(cv), "current_reference_matched": len(cr), "current_incl_text_only": len(ct),
            "all_versions_verified": len(mv), "all_versions_reference_matched": len(mr), "all_versions_incl_text_only": len(mt),
            "recommended_versions": len(rec), "recommended_verified": len(rv), "recommended_reference_matched": len(rr),
            "recommended_incl_text_only": len(rt), "versions_added_vs_current": len(set(rec) - cur_pp[p]), "versions_dropped_vs_current": len(cur_pp[p] - set(rec)),
            "best_verified_amount": st_counts["verified_amount"], "best_verified_exact_date": st_counts["verified_exact_date"],
            "best_candidate": st_counts["candidate"], "best_text_reference_only": st_counts["text_reference_only"], "best_ambiguous": st_counts["ambiguous"],
            "best_conflicting": st_counts["conflicting"], "contracts_with_any_conflicting_evidence": sum(1 for (pp, _) in conflict_any if pp == p),
            "contracts_without_any_document_reference": len(contracts[p]) - len(mt), "recommended_document_ids": ";".join(rec)})
        for d in rec:
            proposed.append((p, d, d == latest, d in chosen))

    def write(name: str, rows: list[dict]) -> None:
        cols = list(dict.fromkeys(k for r in rows for k in r))
        with (OUT / name).open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)

    write("PLAN_VERSION_INVENTORY.csv", versions)
    write("CONTRACT_PLAN_EVIDENCE_LINKS.csv", links)
    write("PLAN_ACTIVITIES.csv", activities)
    write("VERSION_SELECTION_MARGINAL_GAINS.csv", steps)
    write(f"PROJECT_PLAN_COVERAGE_{len(sel)}.csv", proj_rows)

    # ---- proposed manifest v3 (v2 rows kept except PP; PP replaced by the recommended versions) ----
    out, dropped, pp_est = [], [], None
    if not args.no_manifest:
        pp_est = int(next(m["size_estimated_bytes"] for m in manifest if m["doc_type"] == "PP" and m["size_basis"] == "local_type_median"))
        v2_by_doc = {m["document_id"]: m for m in manifest}
        out = [m | {"proposal_change": "unchanged"} for m in manifest if m["doc_type"] != "PP"]
        gains = {(r["project_id"], r["document_id"]): r for r in steps}
        for p, d, is_latest, in_cover in proposed:
            g = gains.get((p, d))
            reason = (f"coverage step {g['step']}: +{g['added_verified_contracts']} verified, +{g['added_reference_matched_contracts']} reference-matched contracts"
                      if g else "") + ("; latest plan (current status)" if is_latest else "")
            if d in v2_by_doc:
                out.append(v2_by_doc[d] | {"role": ("PP_latest" if is_latest else "PP_coverage"), "selection_reason": reason.strip("; "), "proposal_change": "kept from v2"})
            else:
                md = meta[d]
                out.append({"country_code": sel[p]["country_code"], "project_id": p, "document_id": d, "doc_type": "PP",
                            "role": "PP_latest" if is_latest else "PP_coverage", "tier": "1", "selection_reason": reason.strip("; "), "title": md["title"],
                            "document_date": md["document_date"], "disclosure_date": md["disclosure_date"], "version_type": md["version_type"],
                            "report_number": md["report_number"], "isr_sequence": "", "language": md["language"], "volume": md.get("volume"),
                            "total_volumes": md.get("total_volumes"), "guid": md["guid"], "pdf_url": md["pdf_url"], "final_url": "", "http_status": "",
                            "content_type": "", "size_bytes": "", "size_estimated_bytes": pp_est, "size_basis": "local_type_median (PP)",
                            "already_local": str(d in LOCAL_PP), "project_ids_listed": md.get("project_ids_listed"), "txt_url": md["txt_url"],
                            "proposal_change": "added"})
        write("PROPOSED_DOCUMENT_DOWNLOAD_MANIFEST_v3.csv", out)
        dropped = [{"project_id": m["project_id"], "document_id": m["document_id"], "role": m["role"]} for m in manifest
                   if m["doc_type"] == "PP" and (m["project_id"], m["document_id"]) not in {(p, d) for p, d, _, _ in proposed}]

    # ---- parser validation against the PDF-table rows of the 4 local latest plans (p05) ----
    val = collections.Counter()
    p05 = [a for a in csv.DictReader(open(P05_ROWS)) if a["_wds_id"] in {"34172790", "40102923", "40127897", "40134065"}]
    p05_by = collections.defaultdict(list)
    for a in p05:
        cell = re.sub(r"\s+", "", a.get("Activity Reference No. / Description") or "").upper()
        m = STEP_CORE.search(normalize_ref(cell.split("/")[0] if "/" in cell else cell) or "")
        if m:
            p05_by[(a["_wds_id"], m.group(0))].append(a)
    for v in versions:
        if v["document_id"] not in {"34172790", "40102923", "40127897", "40134065"} or v["text_status"] != 200:
            continue
        text = (TEXTS / fetch[v["document_id"]]["path"]).read_text(errors="ignore")
        form, rows = T.parse(text, {c["core"][:2] for c in contracts[v["project_id"]] if c["core"]} | {sel[v["project_id"]]["country_code"]})
        val[f"form:{form}"] += 1
        for r in rows:
            m = STEP_CORE.search(r.ref_key[-60:])
            k = (v["document_id"], m.group(0) if m else None)
            if k not in p05_by:
                val["text_rows_not_in_pdf_table_rows"] += 1
                continue
            a = p05_by[k][0]
            val["rows_compared"] += 1
            pa = a.get("Actual Amount (US$)", "").replace(",", "").strip()
            val["actual_amount_equal"] += (r.actual_amount is not None and pa != "" and abs(r.actual_amount - float(pa)) < 0.01)
            sa = re.sub(r"\s+", "", a.get("Signed Contract | Actual", ""))
            if form == "layout":
                val["layout_signed_actual_compared"] += 1
                val["layout_signed_actual_equal"] += (sa or None) == r.signed_actual
            val["review_equal"] += squash(a.get("Review Type")) in (r.review or "x") or T.REVIEW_MAP.get(squash(a.get("Review Type"))) == r.review
        val["pdf_table_rows_with_step_ref"] += sum(len(x) for kk, x in p05_by.items() if kk[0] == v["document_id"])

    summary = {
        "plan_versions_listed": len(versions), "texts_available": sum(v["text_status"] == 200 and not v["unavailable_reason"] for v in versions),
        "texts_unavailable": collections.Counter(v["unavailable_reason"] or f"http {v['text_status']}" for v in versions
                                                 if v["text_status"] != 200 or v["unavailable_reason"]),
        "text_forms": collections.Counter(v["text_form"] for v in versions if v["text_form"]),
        "parse_errors": sum(bool(v["parse_error"]) for v in versions), "plan_rows": sum(v["rows"] for v in versions),
        "unique_plan_activities": len({(a["project_id"], a["activity_key"]) for a in activities if a["own_project_reference"]}),
        "unique_activities_matched": len({(a["project_id"], a["activity_key"]) for a in activities if a["own_project_reference"] and a["matched_contracts"]}),
        "rows_other_project_references": sum(not a["own_project_reference"] for a in activities),
        "versions_mostly_other_projects": [(v["project_id"], v["document_id"]) for v in versions if v.get("mostly_other_projects")],
        "contracts": {"DS00005": len(ca), "DS01004_legacy": len(lg)},
        "best_evidence_status_per_contract": collections.Counter(best.values()),
        "contracts_with_any_conflicting_evidence": len(conflict_any),
        "totals": {k: sum(r[k] for r in proj_rows) for k in proj_rows[0] if isinstance(proj_rows[0][k], int)},
        "dropped_v2_pp_rows": dropped, "parser_validation_vs_pdf_tables": val,
        "proposed_manifest": {"documents": len(out), "pp": sum(r["doc_type"] == "PP" for r in out),
                              "pp_added": sum(r["proposal_change"] == "added" for r in out),
                              "pp_added_already_local": sum(r["proposal_change"] == "added" and r["already_local"] == "True" for r in out),
                              "pp_estimate_bytes_each": pp_est},
    }
    (OUT / "plan_version_coverage_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: v for k, v in summary.items() if k != "dropped_v2_pp_rows"}, indent=1, default=str))


if __name__ == "__main__":
    main()
