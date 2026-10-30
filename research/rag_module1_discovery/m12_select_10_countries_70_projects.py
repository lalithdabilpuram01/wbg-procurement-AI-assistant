"""Revised RAG scope v2: exactly 10 countries x 7 projects (READ-ONLY planning; new versioned folder).

Country rule (from m11 country_scoring.json, viable countries only): best score per World Bank region (7 regions), then
the next-highest scores with at most 2 countries per region until 10.
Project rule per country (single-country projects with a validated wdi_country_code; eligible = PAD >= 1, ISR >= 2,
plan >= 1, contracts >= 10, notices >= 10): first projects with >= 5 verified plan->contract matches, then greedy:
new sector (+3), status balance to >= 3 closed and >= 2 active (+2), closed with ICR (+1.5), new approval-FY band (+1),
contract count (log, +<=1); seeded tie-breaks; exactly 7.
Documents (official API metadata; HEAD only): 1 English original PAD; first + latest ISR (re-uploads removed); all
ICR / ICR Review; latest procurement plan, plus the earliest plan for projects with verified matches and for the
largest procurement project (contract count) in each country.
Also computes per-project procurement facts (Gold, read-only) for within-country comparison questions, and the overlap
with the previous 70-project selection. Nothing is downloaded; Databricks is only read.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from pyspark.sql import functions as F

from silver.common.session import get_spark

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EV = HERE / "evidence"
OUT = HERE / "selection_v2_10_countries"
PREV = HERE / "selection_70"
SEED = 20261026
spec = importlib.util.spec_from_file_location("m10", HERE / "m10_document_selection_70.py")
m10 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m10)  # type: ignore[union-attr]


def pick_countries(viable: list[dict]) -> list[dict]:
    best, chosen = {}, []
    for v in sorted(viable, key=lambda v: -v["score"]):
        best.setdefault(v["region"], v)
    chosen = list(best.values())
    per_region = Counter(v["region"] for v in chosen)
    for v in sorted(viable, key=lambda v: -v["score"]):
        if len(chosen) >= 10:
            break
        if v in chosen or per_region[v["region"]] >= 2:
            continue
        chosen.append(v)
        per_region[v["region"]] += 1
    return chosen


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    scoring = json.load(open(OUT / "country_scoring.json"))
    countries = pick_countries(scoring["viable"])
    with (ROOT / "cohort" / "poc_cohort_2000_manifest.csv").open(newline="", encoding="utf-8") as fh:
        cohort = {r["project_id"]: r for r in csv.DictReader(fh)}
    api = {r["project_id"]: r for r in (json.loads(x) for x in (EV / "api_document_counts_by_project.jsonl").read_text().splitlines())}
    cnt = {p: {"PAD": api[p]["by_type"].get("Project Appraisal Document", 0), "ISR": api[p]["by_type"].get("Implementation Status and Results Report", 0),
               "ICR": api[p]["by_type"].get("Implementation Completion and Results Report", 0), "ICRR": api[p]["by_type"].get("Implementation Completion Report Review", 0),
               "PP": api[p]["by_type"].get("Procurement Plan", 0)} for p in api}
    probe = json.load(open(EV / "pp_reference_to_contract_match_probe.json"))["per_project"]
    s = get_spark()
    pf = {r["project_id"]: r.asDict() for r in s.table("workspace.wbg_gold.gold_project_portfolio").select(
        "project_id", "project_name", "wdi_country_code", "source_country_name", "region_name", "approval_fiscal_year", "project_status_snapshot",
        "contract_award_count", "contract_award_amount_usd", "notice_count", "income_group_code_at_approval").collect()}
    rng = random.Random(SEED)
    selected = []
    for c in countries:
        pool = sorted(c["eligible_project_ids"])
        rng.shuffle(pool)
        chosen = [p for p in sorted(pool, key=lambda p: -(probe.get(p) or [0, 0])[1]) if (probe.get(p) or [0, 0])[1] >= 5][:3]
        while len(chosen) < 7:
            sec = Counter(cohort[p]["major_sector"] for p in chosen)
            st = Counter(cohort[p]["status"] for p in chosen)
            fy = Counter(cohort[p]["fy_band"] for p in chosen)
            best, bg = None, -1e9
            for p in pool:
                if p in chosen:
                    continue
                r = cohort[p]
                g = (3 if sec[r["major_sector"]] == 0 else 0) + (2 if (r["status"] == "Closed" and st["Closed"] < 3) or (r["status"] == "Active" and st["Active"] < 2) else 0)
                g += 1.5 if r["status"] == "Closed" and cnt[p]["ICR"] > 0 else 0
                g += 1 if fy[r["fy_band"]] == 0 else 0
                g += min(math.log10(max(pf[p]["contract_award_count"], 1)) / 3, 1)
                if g > bg:
                    best, bg = p, g
            chosen.append(best)
        selected += [(c, p) for p in chosen]
    pids = [p for _, p in selected]
    # per-project procurement facts for comparison questions (Gold, read-only)
    ca = s.table("workspace.wbg_gold.gold_contract_analytics").where(F.col("project_id").isin(pids))
    facts = {r["project_id"]: r.asDict() for r in ca.groupBy("project_id").agg(
        F.count("*").alias("contracts"), F.sum("contract_amount_usd").alias("value_usd"),
        F.expr("percentile_disc(0.5) WITHIN GROUP (ORDER BY contract_amount_usd)").alias("median_value_usd"),
        F.sum((F.col("procurement_method") == "Direct Selection").cast("int")).alias("direct_selection"),
        F.sum((F.col("review_type") == "Prior").cast("int")).alias("prior_review"), F.sum(F.col("review_type").isNotNull().cast("int")).alias("review_known"),
        F.sum((F.col("supplier_country_relation") == "includes_other_country").cast("int")).alias("other_country"),
        F.sum((F.col("supplier_country_relation") != "undetermined").cast("int")).alias("relation_known"),
        F.sum(F.col("signed_before_board_approval").cast("int")).alias("signed_before_approval"),
        F.min("contract_signing_date").alias("first_signing"), F.max("contract_signing_date").alias("last_signing")).collect()}
    top_method = {}
    for r in ca.groupBy("project_id", "procurement_method").count().collect():
        if r["count"] > top_method.get(r["project_id"], ("", 0))[1]:
            top_method[r["project_id"]] = (r["procurement_method"], r["count"])
    nw = s.table("workspace.wbg_gold.gold_procurement_notices").where(F.col("project_id").isin(pids) & F.col("is_tender_invitation"))
    windows = {r["project_id"]: (r["n"], r["med"]) for r in nw.groupBy("project_id").agg(
        F.count("*").alias("n"), F.expr("percentile_disc(0.5) WITHIN GROUP (ORDER BY CASE WHEN deadline_status = 'positive_window' THEN published_response_window_days END)").alias("med")).collect()}
    proj_rows = []
    for c, p in selected:
        r, f = cohort[p], facts.get(p, {})
        m = (probe.get(p) or [None, None, None])
        proj_rows.append({
            "country_code": c["country_code"], "country_name": c["country_name"], "region": c["region"], "income_group_fy2026": c["income_group_fy2026"] or "no_entry_FY2026 (FY2025: L)" if c["country_code"] == "ET" else c["income_group_fy2026"],
            "project_id": p, "project_title": pf[p]["project_name"], "sector": r["major_sector"], "approval_fy": pf[p]["approval_fiscal_year"], "fy_band": r["fy_band"],
            "status": pf[p]["project_status_snapshot"], "contract_count": pf[p]["contract_award_count"], "contract_value_usd": str(pf[p]["contract_award_amount_usd"]),
            "notice_count": pf[p]["notice_count"], "api_pad": cnt[p]["PAD"], "api_isr": cnt[p]["ISR"], "api_icr": cnt[p]["ICR"], "api_icrr": cnt[p]["ICRR"], "api_pp": cnt[p]["PP"],
            "verified_plan_contract_matches": m[1], "plan_references_parsed": m[0],
            "verifiable_contract_document_references": "verified (parsed plan rows match contracts)" if (m[1] or 0) > 0 else
                                                        f"expected (STEP-format reference share in country {c['step_reference_share']:.0%}); to verify after download",
            "top_method": top_method.get(p, ("", 0))[0], "direct_selection_share": round(f.get("direct_selection", 0) / f["contracts"], 3) if f else None,
            "prior_review_share": round(f["prior_review"] / f["review_known"], 3) if f and f["review_known"] else None,
            "other_country_supplier_share": round(f["other_country"] / f["relation_known"], 3) if f and f["relation_known"] else None,
            "median_contract_value_usd": str(f.get("median_value_usd")), "ifb_notices": windows.get(p, (0, None))[0], "ifb_median_window_days": windows.get(p, (0, None))[1],
            "contracts_signed_before_approval": f.get("signed_before_approval"), "first_contract_signing": str(f.get("first_signing")), "last_contract_signing": str(f.get("last_signing"))})
    with (OUT / "PROJECT_SELECTION_MANIFEST_v2.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(proj_rows[0]))
        w.writeheader()
        w.writerows(proj_rows)

    # documents (official metadata; HEAD only)
    evidence_projects = {p for p in pids if (probe.get(p) or [0, 0])[1] >= 5}
    largest_per_country = {max((q for cc, q in selected if cc["country_code"] == c["country_code"]), key=lambda q: pf[q]["contract_award_count"]) for c in countries}
    all_docs, sel, issues = [], [], []
    for p in pids:
        docs = [m10.doc_row(d, p) for d in m10.listing(p)]
        all_docs += docs
        by = defaultdict(list)
        for d in docs:
            by[d["doc_type"]].append(d)
        grp = defaultdict(list)
        for d in docs:
            grp[(d["doc_type"], d["document_date"], d["title"].lower())].append(d)
        dup = set()
        for k, v in grp.items():
            if len(v) > 1 and k[0] != "Other":
                v = sorted(v, key=lambda x: x["document_id"])
                for d in v[:-1]:
                    dup.add(d["document_id"])
                    issues.append({"project_id": p, "document_id": d["document_id"], "doc_type": d["doc_type"], "issue": "duplicate_record", "kept_document_id": v[-1]["document_id"]})
        for d in docs:
            if d["doc_type"] != "Other":
                miss = [x for x in ("document_date", "language", "pdf_url", "version_type", "report_number") if not d[x]]
                if miss:
                    issues.append({"project_id": p, "document_id": d["document_id"], "doc_type": d["doc_type"], "issue": "missing_metadata: " + ",".join(miss)})
                if not d["pdf_url"]:
                    issues.append({"project_id": p, "document_id": d["document_id"], "doc_type": d["doc_type"], "issue": "no_pdf_url"})

        def add(d, role, tier, why):
            sel.append({**d, "role": role, "tier": tier, "selection_reason": why})
        pads = [d for d in by["PAD"] if d["document_id"] not in dup and d["pdf_url"]]
        orig = [d for d in pads if not re.search(r"additional financing|\bAF\b", d["title"], re.I)] or pads
        if orig:
            prim = sorted(orig, key=lambda d: (d["language"] != "English", (d["version_type"] or "").lower() != "final", d["document_date"] or "9999"))[0]
            add(prim, "PAD_original", 1, "original appraisal; English, Final preferred; earliest non-AF")
            for d in pads:
                if d["document_id"] != prim["document_id"]:
                    issues.append({"project_id": p, "document_id": d["document_id"], "doc_type": "PAD",
                                   "issue": ("translation_of_primary_PAD" if d["language"] and d["language"] != "English" else "additional_PAD_not_selected"), "kept_document_id": prim["document_id"]})
        isr = defaultdict(list)
        for d in by["ISR"]:
            if d["document_id"] not in dup and d["pdf_url"]:
                isr[d["isr_sequence"] if d["isr_sequence"] is not None else ("d", d["document_date"])].append(d)
        uniq = []
        for k, v in isr.items():
            v = sorted(v, key=lambda x: x["document_id"])
            uniq.append(v[-1])
            for d in v[:-1]:
                issues.append({"project_id": p, "document_id": d["document_id"], "doc_type": "ISR", "issue": f"isr_reupload_superseded (sequence {k})", "kept_document_id": v[-1]["document_id"]})
        uniq.sort(key=lambda d: (d["document_date"] or "", d["isr_sequence"] or 0))
        if uniq:
            add(uniq[0], "ISR_first", 1, f"{len(uniq)} distinct ISRs; first")
            if len(uniq) > 1:
                add(uniq[-1], "ISR_latest", 1, f"{len(uniq)} distinct ISRs; latest")
        for t in ("ICR", "ICRR"):
            for d in sorted([d for d in by[t] if d["document_id"] not in dup and d["pdf_url"]], key=lambda d: d["document_date"] or ""):
                add(d, t, 1, "completion evidence" if t == "ICR" else "IEG validation")
        pps = sorted([d for d in by["PP"] if d["document_id"] not in dup and d["pdf_url"]], key=lambda d: (d["document_date"] or "", d["document_id"]))
        if pps:
            add(pps[-1], "PP_latest", 1, f"{len(pps)} plan versions; latest")
            if len(pps) > 1 and (p in evidence_projects or p in largest_per_country):
                add(pps[0], "PP_earliest", 1, "earliest plan: plan changes over time / contract-level links (verified matches, or the largest procurement "
                                              "project in its country)")
    inv = json.load(open(EV / "local_pdf_inventory.json"))["documents"]
    local = {d["source_doc_id"] for d in inv if d["source_doc_id"]}
    med = {"PAD": 870010, "ISR": 209218, "ICR": 1219665, "ICRR": 154967, "PP": 163655}
    for d in sel:
        h = m10.head(d["pdf_url"])
        d.update(h)
        cl = h.get("content_length")
        d["size_bytes"] = int(cl) if cl and str(cl).isdigit() else None
        d["size_estimated_bytes"] = d["size_bytes"] or med[d["doc_type"]]
        d["size_basis"] = "content_length" if d["size_bytes"] else "local_type_median"
        d["already_local"] = d["document_id"] in local
        if h.get("http_status") != 200 or "pdf" not in str(h.get("content_type") or ""):
            issues.append({"project_id": d["project_id"], "document_id": d["document_id"], "doc_type": d["doc_type"], "issue": f"pdf_unavailable_or_not_pdf: {h.get('http_status')}"})
    country_of = {p: c["country_code"] for c, p in selected}
    cols = ["country_code", "project_id", "document_id", "doc_type", "role", "tier", "selection_reason", "title", "document_date", "disclosure_date", "version_type",
            "report_number", "isr_sequence", "language", "volume", "total_volumes", "guid", "pdf_url", "final_url", "http_status", "content_type", "size_bytes",
            "size_estimated_bytes", "size_basis", "already_local", "project_ids_listed", "txt_url"]
    for d in sel:
        d["country_code"] = country_of[d["project_id"]]
    with (OUT / "DOCUMENT_DOWNLOAD_MANIFEST_v2.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(sel)
    with (OUT / "DOCUMENT_ISSUES_v2.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["project_id", "document_id", "doc_type", "issue", "kept_document_id"], extrasaction="ignore")
        w.writeheader()
        w.writerows(issues)
    (OUT / "all_documents_metadata_v2.jsonl").write_text("\n".join(json.dumps(d) for d in all_docs) + "\n")
    prev = [r["project_id"] for r in csv.DictReader((PREV / "PROJECT_SELECTION_MANIFEST.csv").open(encoding="utf-8"))]
    new = [d for d in sel if not d["already_local"]]
    summary = {
        "countries": [{k: c[k] for k in ("country_code", "country_name", "region", "income_group_fy2026", "eligible_projects", "with_icr", "step_reference_share",
                                         "probe_plan_matches", "score")} for c in countries],
        "projects": len(pids), "per_country": dict(Counter(country_of.values())),
        "status": dict(Counter(r["status"] for r in proj_rows)), "fy_band": dict(Counter(r["fy_band"] for r in proj_rows)),
        "sectors_total": len({r["sector"] for r in proj_rows}), "sectors_per_country": {c["country_code"]: len({r["sector"] for r in proj_rows if r["country_code"] == c["country_code"]}) for c in countries},
        "verified_match_projects": sorted(evidence_projects), "available_docs": dict(Counter(d["doc_type"] for d in all_docs)), "available_total": len(all_docs),
        "selected": len(sel), "selected_by_role": dict(Counter(d["role"] for d in sel)), "already_local": len(sel) - len(new), "new_downloads": len(new),
        "est_bytes_new": sum(d["size_estimated_bytes"] for d in new), "head_status": dict(Counter(str(d.get("http_status")) for d in sel)),
        "content_length_known": sum(1 for d in sel if d["size_bytes"]), "languages": dict(Counter(d["language"] for d in sel)),
        "issues": dict(Counter(i["issue"].split(":")[0].split(" (")[0] for i in issues)),
        "missing_metadata_selected": dict(Counter(f for i in issues if i["issue"].startswith("missing") and i["document_id"] in {d["document_id"] for d in sel}
                                                 for f in i["issue"].split(": ")[1].split(","))),
        "overlap_with_previous_70": sorted(set(pids) & set(prev)), "seed": SEED}
    (OUT / "selection_v2_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
