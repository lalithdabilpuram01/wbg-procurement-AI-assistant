"""Select 70 projects for the document RAG scope (READ-ONLY planning; nothing is written to Databricks or the cohort).

Inputs (local): cohort manifest, m05 API document counts per project, the Phase 1 cluster projects (API notice details),
and the procurement-plan reference probe (exact plan-reference -> contract borrower-reference matches).

Eligibility: PAD >= 1, ISR >= 2, Procurement Plan >= 1, contract_count >= 10, notice_count >= 10.
Mandatory: the 2 local parser-control projects (P131049, P145502) and up to 10 'evidence' projects with >= 5 exact
plan-reference -> contract matches (verifiable document-row -> contract links), most regions first.
Fill: greedy, seeded tie-breaks; each step adds the candidate with the highest gain on unmet quotas:
  region (proportional to the cohort, 'Other' max 1), status (>= 30 closed and >= 25 active), approval FY band (>= 8 each),
  major sector (>= 2 each where eligible), new country (+), repeated country (penalty; max 2 per country),
  ICR/ICR Review availability among closed projects (+), API-detail cluster membership (+).
"""
from __future__ import annotations

import csv
import json
import random
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EV = Path(__file__).resolve().parent / "evidence"
OUT = Path(__file__).resolve().parent / "selection_70"
N, SEED = 70, 20261025
CONTROLS = ["P131049", "P145502"]
T = {"PAD": "Project Appraisal Document", "ISR": "Implementation Status and Results Report", "ICR": "Implementation Completion and Results Report",
     "ICRR": "Implementation Completion Report Review", "PP": "Procurement Plan", "PPR": "Project Paper"}


def main() -> None:
    with (ROOT / "cohort" / "poc_cohort_2000_manifest.csv").open(newline="", encoding="utf-8") as fh:
        cohort = {r["project_id"]: r for r in csv.DictReader(fh)}
    api = {r["project_id"]: r for r in (json.loads(x) for x in (EV / "api_document_counts_by_project.jsonl").read_text().splitlines())}
    cnt = {p: {k: api[p]["by_type"].get(v, 0) for k, v in T.items()} for p in api}
    cluster = set(json.load(open(EV / "wds_metadata_coverage.json"))["per_project_counts"])
    probe = json.load(open(EV / "pp_reference_to_contract_match_probe.json"))["per_project"]
    matches = {p: v[1] for p, v in probe.items()}
    ok = lambda p: (cnt[p]["PAD"] >= 1 and cnt[p]["ISR"] >= 2 and cnt[p]["PP"] >= 1 and int(cohort[p]["contract_count"]) >= 10  # noqa: E731
                    and int(cohort[p]["notice_count"]) >= 10)
    eligible = sorted(p for p in cohort if ok(p))
    region_share = Counter(cohort[p]["region"] for p in cohort)
    target_region = {r: (1 if r == "Other" else max(5, round(N * c / len(cohort)))) for r, c in region_share.items()}
    sectors_avail = Counter(cohort[p]["major_sector"] for p in eligible)
    chosen: list[str] = []
    reasons: dict[str, list[str]] = {}
    for p in CONTROLS:
        if p in eligible:
            chosen.append(p)
            reasons[p] = ["local_parser_control (PAD/ISR/ICR/ICRR/PP PDFs already local)"]
    ev = sorted((p for p in eligible if matches.get(p, 0) >= 5 and p not in chosen), key=lambda p: -matches[p])
    seen_regions: set = set()
    for p in sorted(ev, key=lambda p: (cohort[p]["region"] in seen_regions, -matches[p])):
        if len([x for x in chosen if "verifiable" in " ".join(reasons[x])]) >= 10:
            break
        chosen.append(p)
        seen_regions.add(cohort[p]["region"])
        reasons[p] = [f"verifiable_evidence: {matches[p]} procurement-plan references match contract borrower references exactly"]
    rng = random.Random(SEED)
    order = eligible[:]
    rng.shuffle(order)
    while len(chosen) < N:
        cur_r = Counter(cohort[p]["region"] for p in chosen)
        cur_s = Counter(cohort[p]["status"] for p in chosen)
        cur_f = Counter(cohort[p]["fy_band"] for p in chosen)
        cur_sec = Counter(cohort[p]["major_sector"] for p in chosen)
        cur_c = Counter(cohort[p]["country_code"] for p in chosen)
        best, best_gain, best_why = None, -1e9, []
        for p in order:
            if p in chosen:
                continue
            r = cohort[p]
            if cur_c[r["country_code"]] >= 2:
                continue
            g, why = 0.0, []
            if cur_r[r["region"]] < target_region[r["region"]]:
                g += 4 * (target_region[r["region"]] - cur_r[r["region"]]) / target_region[r["region"]]
                why.append("region_quota")
            elif r["region"] == "Other":
                continue
            need_status = {"Closed": 30, "Active": 25}
            if cur_s[r["status"]] < need_status.get(r["status"], 0):
                g += 2
                why.append("status_quota")
            if r["fy_band"] != "Unknown" and cur_f[r["fy_band"]] < 8:
                g += 2
                why.append("fy_band_quota")
            if sectors_avail[r["major_sector"]] >= 2 and cur_sec[r["major_sector"]] < 2:
                g += 1.5
                why.append("sector_coverage")
            g += 1.0 if cur_c[r["country_code"]] == 0 else -2.0
            if cur_c[r["country_code"]] == 0:
                why.append("new_country")
            if r["status"] == "Closed" and cnt[p]["ICR"] > 0:
                g += 1.0
                why.append("has_icr")
            if cnt[p]["ICRR"] > 0:
                g += 0.5
                why.append("has_icr_review")
            if p in cluster:
                g += 0.5
                why.append("api_notice_detail_project")
            if matches.get(p, 0) > 0:
                g += 0.5
                why.append("plan_contract_reference_match")
            if g > best_gain:
                best, best_gain, best_why = p, g, why
        chosen.append(best)
        reasons[best] = best_why
    rows = []
    for p in chosen:
        r = cohort[p]
        rows.append({"project_id": p, "project_name": r["project_name"], "country_code": r["country_code"], "country_name": r["country_name"], "region": r["region"],
                     "country_is_regional": r["country_is_regional"], "major_sector": r["major_sector"], "approval_fy": r["approval_fy"], "fy_band": r["fy_band"],
                     "status": r["status"], "contract_count": r["contract_count"], "contract_amount_usd": r["contract_amount_usd"], "notice_count": r["notice_count"],
                     "api_documents_total": api[p]["total"], **{f"api_{k.lower()}_count": cnt[p][k] for k in T},
                     "api_notice_detail_project": p in cluster, "plan_reference_contract_matches": matches.get(p), "selection_reasons": "; ".join(reasons[p])})
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "PROJECT_SELECTION_MANIFEST.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    mix = {c: dict(Counter(x[c] for x in rows)) for c in ("region", "status", "fy_band", "major_sector", "country_is_regional")}
    summary = {"eligible_pool": len(eligible), "selected": len(rows), "distinct_countries": len({x["country_code"] for x in rows}), "mix": mix,
               "with_icr": sum(x["api_icr_count"] > 0 for x in rows), "with_icr_review": sum(x["api_icrr_count"] > 0 for x in rows),
               "api_notice_detail_projects": sum(x["api_notice_detail_project"] for x in rows),
               "verifiable_evidence_projects": sum(1 for x in rows if (x["plan_reference_contract_matches"] or 0) >= 5),
               "available_docs_selected_projects": {k: sum(x[f"api_{k.lower()}_count"] for x in rows) for k in T}, "seed": SEED}
    (OUT / "project_selection_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
