"""RAG scope v3: 5 countries / 35 projects (READ-ONLY planning; new versioned folder rag_scope_v3_35_projects/).

Inputs (all existing, nothing refetched): v2 project and document manifests, v2 WDS metadata, m15 fetch log, and the m16
35-project plan-coverage outputs in rag_scope_v3_35_projects/plan_coverage/.
Document rules (v2 rules kept): one original English PAD, first and latest ISR, every ICR and ICR Review, the latest
procurement plan. Additional plan versions are added greedily per project, starting from the latest plan, only while the
next version is material: it adds >= max(3, 5% of the project's STEP-reference contracts) contracts with new row-level
reference evidence, or >= 3 newly verified contracts. Versions whose rows are mostly other projects' references
(compilation documents listed under the project) are never added. Coverage is reported for the v2 plan choice (latest + earliest),
the v3 choice, and all available versions, with explicit denominators.
Optional HEAD-only availability check (--head) for plan PDFs added by v3 (no PDF bodies; logged).
"""
from __future__ import annotations

import argparse
import collections
import csv
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
V2 = HERE / "selection_v2_10_countries"
OUT = HERE / "rag_scope_v3_35_projects"
COV = OUT / "plan_coverage"
TEXTS = HERE / "plan_version_coverage_v1"
APPROVED = {
    "LK": ["P132833", "P146314", "P163721", "P178432", "P156019", "P163714", "P159303"],
    "ET": ["P148850", "P164336", "P123531", "P177233", "P176731", "P163452", "P143302"],
    "SN": ["P175325", "P162042", "P125565", "P167681", "P156186", "P124018", "P122841"],
    "ID": ["P165543", "P096532", "P144269", "P180811", "P169548", "P168076", "P157599"],
    "AR": ["P133288", "P170329", "P125151", "P179534", "P175669", "P159929", "P106685"],
}
ROW_LEVEL = {"verified_amount", "verified_exact_date", "candidate", "conflicting", "ambiguous"}
VERIFIED = {"verified_amount", "verified_exact_date"}
LOCAL_DIR = "rag/corpus_v3/pdf/{country}/{project}/{doc_type}_{date}_{doc_id}.pdf"
FIRST10 = [  # (project, role or PP document rule, purpose)
    ("P132833", "PAD_original", "LK closed transport project: PAD procurement arrangements and annexes"),
    ("P132833", "ICR", "LK completion report: results and procurement lessons"),
    ("P132833", "ICRR", "LK IEG review: compare ratings with ICR and latest ISR"),
    ("P132833", "PP_best_historical", "LK historical plan version with the most verified contract rows"),
    ("P148850", "PAD_original", "ET expressway PAD (closed, no ICR in WDS)"),
    ("P148850", "ISR_latest", "ET latest ISR: final ratings without an ICR"),
    ("P148850", "PP_best_historical", "ET historical plan version; legacy and STEP references side by side"),
    ("P163452", "ICR", "ET urban project ICR: table-heavy annexes"),
    ("P175325", "ISR_first", "SN first ISR of an active project (French-language plans elsewhere in the project)"),
    ("P175325", "PP_best_historical", "SN French-label plan version with verified contract rows"),
]


def read(path: Path) -> list[dict]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def write(path: Path, rows: list[dict]) -> None:
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--head", action="store_true", help="HEAD-only availability check of added plan PDFs")
    args = ap.parse_args()
    ids = {p: c for c, ps in APPROVED.items() for p in ps}
    projects = [r for r in read(V2 / "PROJECT_SELECTION_MANIFEST_v2.csv") if r["project_id"] in ids]
    assert len(projects) == 35 and all(r["country_code"] == ids[r["project_id"]] for r in projects)
    v2docs = [r for r in read(V2 / "DOCUMENT_DOWNLOAD_MANIFEST_v2.csv") if r["project_id"] in ids]
    meta = {d["document_id"]: d for d in map(json.loads, open(V2 / "all_documents_metadata_v2.jsonl")) if d["project_id"] in ids}
    links = read(COV / "CONTRACT_PLAN_EVIDENCE_LINKS.csv")
    inv = read(COV / "PLAN_VERSION_INVENTORY.csv")
    pcov = {r["project_id"]: r for r in read(COV / "PROJECT_PLAN_COVERAGE_35.csv")}
    local_pp = {p.stem.split("_")[-1] for p in (ROOT / "discovery" / "samples" / "documents" / "pp_files").glob("*.pdf")} | \
               {p.stem.split("_")[-1] for p in (ROOT / "discovery" / "project_deep_dive" / "raw").glob("*/pp_pdf/*.pdf")}

    ev = collections.defaultdict(lambda: collections.defaultdict(dict))  # p -> doc -> contract -> best status
    rank = ["verified_amount", "verified_exact_date", "candidate", "ambiguous", "conflicting", "text_reference_only"]
    for x in links:
        cur = ev[x["project_id"]][x["document_id"]].get(x["contract_id"])
        if cur is None or rank.index(x["evidence_status"]) < rank.index(cur):
            ev[x["project_id"]][x["document_id"]][x["contract_id"]] = x["evidence_status"]

    def cover(p: str, docs: set) -> dict:
        best: dict = {}
        for d in docs:
            for cid, st in ev[p].get(d, {}).items():
                if cid not in best or rank.index(st) < rank.index(best[cid]):
                    best[cid] = st
        c = collections.Counter(best.values())
        return {"verified": c["verified_amount"] + c["verified_exact_date"], "verified_amount": c["verified_amount"],
                "verified_exact_date": c["verified_exact_date"], "row_level": sum(c[s] for s in ROW_LEVEL), "candidate": c["candidate"],
                "conflicting": c["conflicting"] + c["ambiguous"], "text_only": c["text_reference_only"], "any": len(best), "_best": best}

    pp_rows, steps_out, cov_rows, contract_cov = [], [], [], []
    for pr in projects:
        p = pr["project_id"]
        pc = pcov[p]
        versions = [v for v in inv if v["project_id"] == p]
        latest = max(versions, key=lambda v: (v["document_date"] or "", v["document_id"]))
        v2pp = {r["document_id"] for r in v2docs if r["project_id"] == p and r["doc_type"] == "PP"}
        step_contracts = int(pc["contracts_with_step_package"])
        thr = max(3, round(0.05 * step_contracts))
        chosen = [latest["document_id"]]
        base = cover(p, set(chosen))
        steps_out.append({"project_id": p, "step": 0, "document_id": latest["document_id"], "document_date": latest["document_date"], "reason": "latest plan (always kept)",
                          "added_row_level": base["row_level"], "added_verified": base["verified"], "added_text_only": base["text_only"],
                          "cumulative_row_level": base["row_level"], "cumulative_verified": base["verified"], "material": True})
        while True:
            cur = cover(p, set(chosen))
            best_gain = None
            for v in versions:
                d = v["document_id"]
                if d in chosen or d not in ev[p] or v.get("mostly_other_projects") == "True":
                    continue  # compilation documents dominated by other projects' plans are never added
                nxt = cover(p, set(chosen) | {d})
                g = (nxt["row_level"] - cur["row_level"], nxt["verified"] - cur["verified"], nxt["text_only"] - cur["text_only"], v["document_date"] or "", d)
                if best_gain is None or g > best_gain:
                    best_gain = g
            if best_gain is None:
                break
            g_row, g_ver, g_txt, ddate, d = best_gain
            material = g_row >= thr or g_ver >= 3 or g_txt >= thr
            steps_out.append({"project_id": p, "step": len(chosen), "document_id": d, "document_date": ddate, "reason": "greedy marginal coverage",
                              "added_row_level": g_row, "added_verified": g_ver, "added_text_only": g_txt,
                              "cumulative_row_level": cur["row_level"] + g_row, "cumulative_verified": cur["verified"] + g_ver,
                              "material": material, "threshold_contracts": thr})
            if not material:
                break
            chosen.append(d)
        full = cover(p, {v["document_id"] for v in versions})
        v2c = cover(p, v2pp)
        v3c = cover(p, set(chosen))
        ds5 = int(pc["contracts_ds00005"])
        cov_rows.append({"country_code": pr["country_code"], "project_id": p, "status": pr["status"], "approval_fy": pr["approval_fy"],
                         "contracts_ds00005": ds5, "contracts_ds01004_legacy": int(pc["contracts_ds01004_legacy"]), "contracts_with_step_package": step_contracts,
                         "plan_versions_listed": len(versions), "plan_texts_available": int(pc["plan_texts_available"]),
                         "plan_texts_unavailable": int(pc["texts_unavailable"]), "texts_without_step_table": int(pc["texts_without_step_table"]),
                         "unique_plan_activities": int(pc["unique_plan_activities"]), "activities_matched_to_contracts": int(pc["activities_matched_to_contracts"]),
                         "v2_plan_versions": len(v2pp), "v3_plan_versions": len(chosen), "all_versions_for_max": int(pc["recommended_versions"]),
                         **{f"v2_{k}": v for k, v in v2c.items() if k != "_best"}, **{f"v3_{k}": v for k, v in v3c.items() if k != "_best"},
                         **{f"all_{k}": v for k, v in full.items() if k != "_best"}, "materiality_threshold_contracts": thr})
        for cid, st in v3c["_best"].items():
            docs_for = sorted(d for d in chosen if cid in ev[p].get(d, {}))
            contract_cov.append({"project_id": p, "contract_id": cid, "best_status_in_v3_versions": st, "v3_documents_with_evidence": ";".join(docs_for),
                                 "best_status_all_versions": full["_best"].get(cid)})
        for d in chosen:
            md = meta[d]
            src = next((r for r in v2docs if r["document_id"] == d), None)
            stp = next(s for s in steps_out if s["project_id"] == p and s["document_id"] == d)
            role = "PP_latest" if d == latest["document_id"] else "PP_coverage"
            reason = stp["reason"] if role == "PP_latest" else (f"adds {stp['added_row_level']} contracts with row-level reference evidence, "
                                                                f"{stp['added_verified']} verified, {stp['added_text_only']} text-only")
            row = (src or {}) | {
                "country_code": pr["country_code"], "project_id": p, "document_id": d, "doc_type": "PP", "role": role, "tier": "1", "selection_reason": reason,
                "title": md["title"], "document_date": md["document_date"], "disclosure_date": md["disclosure_date"], "version_type": md["version_type"],
                "report_number": md["report_number"], "language": md["language"], "guid": md["guid"], "pdf_url": md["pdf_url"], "txt_url": md["txt_url"],
                "project_ids_listed": md.get("project_ids_listed")}
            if not src:
                row.update(final_url="", http_status="", content_type="", size_bytes="", size_estimated_bytes=163655, size_basis="local_type_median (PP)",
                           already_local=str(d in local_pp), isr_sequence="", volume=md.get("volume"), total_volumes=md.get("total_volumes"))
            row["v3_change"] = "kept from v2" if src else "added"
            pp_rows.append(row)

    docs = [r | {"v3_change": "kept from v2"} for r in v2docs if r["doc_type"] != "PP"] + pp_rows
    dropped = [{"project_id": r["project_id"], "document_id": r["document_id"], "role": r["role"]} for r in v2docs
               if r["doc_type"] == "PP" and r["document_id"] not in {x["document_id"] for x in pp_rows}]
    if args.head:
        spec = importlib.util.spec_from_file_location("m10", HERE / "m10_document_selection_70.py")
        m10 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m10)  # type: ignore[union-attr]
        for r in docs:
            if r["v3_change"] == "added" and r["already_local"] != "True":
                h = m10.head(r["pdf_url"])
                r.update(final_url=h.get("final_url") or "", http_status=h.get("http_status") or "", content_type=h.get("content_type") or "")
                if h.get("content_length"):
                    r.update(size_bytes=h["content_length"], size_basis="content_length")
    for r in docs:
        r["expected_local_path"] = LOCAL_DIR.format(country=r["country_code"], project=r["project_id"], doc_type=r["doc_type"],
                                                    date=r["document_date"] or "nodate", doc_id=r["document_id"])
    order = {"PAD": 0, "ISR": 1, "ICR": 2, "ICRR": 3, "PP": 4}
    docs.sort(key=lambda r: (list(APPROVED).index(r["country_code"]), r["project_id"], order[r["doc_type"]], r["document_date"] or ""))

    # first 10
    first = []
    for p, role, purpose in FIRST10:
        if role == "PP_best_historical":
            cands = [r for r in pp_rows if r["project_id"] == p and r["role"] == "PP_coverage"] or [r for r in pp_rows if r["project_id"] == p]
            r = max(cands, key=lambda r: (sum(st in VERIFIED for st in ev[p].get(r["document_id"], {}).values()), r["document_date"]))
        else:
            r = next(x for x in docs if x["project_id"] == p and x["role"] == role)
        first.append({"batch_no": len(first) + 1, "country_code": r["country_code"], "project_id": p, "document_id": r["document_id"], "doc_type": r["doc_type"],
                      "role": r["role"], "title": r["title"], "document_date": r["document_date"], "disclosure_date": r["disclosure_date"],
                      "language": r["language"], "pdf_url": r["pdf_url"], "final_url": r.get("final_url", ""), "http_status": r.get("http_status", ""),
                      "already_local": r["already_local"], "expected_local_path": r["expected_local_path"],
                      "verified_contract_rows_in_text": sum(st in VERIFIED for st in ev[p].get(r["document_id"], {}).values()) if r["doc_type"] == "PP" else "",
                      "inspection_purpose": purpose})

    write(OUT / "PROJECT_SELECTION_35.csv", projects)
    write(OUT / "DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv", docs)
    write(OUT / "PLAN_VERSION_SELECTION_STEPS_35.csv", steps_out)
    write(OUT / "PLAN_COVERAGE_BY_PROJECT_35.csv", cov_rows)
    write(OUT / "CONTRACT_COVERAGE_BY_DOCUMENT_35.csv", contract_cov)
    write(OUT / "FIRST_BATCH_10_v3.csv", first)

    def tot(prefix: str) -> dict:
        return {k[len(prefix):]: sum(r[k] for r in cov_rows) for k in cov_rows[0] if k.startswith(prefix)}
    new = [r for r in docs if r["already_local"] != "True"]
    size = sum(int(r["size_bytes"] or r["size_estimated_bytes"] or 0) for r in new)
    summary = {
        "projects": len(projects), "countries": list(APPROVED),
        "documents_by_type": collections.Counter(r["doc_type"] for r in docs), "documents_by_role": collections.Counter(r["role"] for r in docs),
        "documents": len(docs), "already_local": len(docs) - len(new), "new_downloads": len(new), "est_bytes_new": size,
        "size_basis": collections.Counter(r["size_basis"] for r in new), "head_status_added": collections.Counter(r["http_status"] for r in docs if r["v3_change"] == "added"),
        "pp_added_vs_v2": sum(r["v3_change"] == "added" for r in docs), "pp_dropped_vs_v2": dropped,
        "denominators": {"contracts_ds00005": sum(r["contracts_ds00005"] for r in cov_rows), "contracts_with_step_package": sum(r["contracts_with_step_package"] for r in cov_rows),
                         "contracts_ds01004_legacy": sum(r["contracts_ds01004_legacy"] for r in cov_rows)},
        "coverage_v2_plan_choice": tot("v2_"), "coverage_v3_plan_choice": tot("v3_"), "coverage_all_versions": tot("all_"),
        "plan_versions": {"listed": sum(r["plan_versions_listed"] for r in cov_rows), "texts_available": sum(r["plan_texts_available"] for r in cov_rows),
                          "texts_unavailable": sum(r["plan_texts_unavailable"] for r in cov_rows), "v2": sum(r["v2_plan_versions"] for r in cov_rows),
                          "v3": sum(r["v3_plan_versions"] for r in cov_rows), "full_cover": sum(r["all_versions_for_max"] for r in cov_rows)},
        "missing_metadata_in_manifest": {f: sum(1 for r in docs if not r.get(f) or r.get(f) == "None") for f in
                                         ("document_date", "disclosure_date", "version_type", "report_number", "language", "pdf_url", "guid")},
        "first_batch": [(f["project_id"], f["role"], f["document_id"]) for f in first],
    }
    (OUT / "scope_v3_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
