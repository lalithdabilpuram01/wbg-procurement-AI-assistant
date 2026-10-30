"""Module 1 v2 (10 countries / 70 projects): first inspection batch, country rationale, completeness checks, v1 comparison.

Read-only: reads the m11/m12 outputs and the previous selection_70/ files; writes only to selection_v2_10_countries/.
No network, no Databricks, no downloads.
"""
from __future__ import annotations

import collections
import csv
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
V2 = HERE / "selection_v2_10_countries"
V1 = HERE / "selection_70"

# Three verified plan-to-contract projects in three countries; the already-local PP_latest files are inspected but not downloaded.
FIRST_BATCH = [
    ("P132833", "PAD_original", "procurement section, implementing agency, packaging vs DS00005 contracts"),
    ("P132833", "ISR_first", "first implementation ratings and procurement rating wording"),
    ("P132833", "ISR_latest", "final ratings; compare with ICR outcome"),
    ("P132833", "ICR", "results/outcome narrative and procurement lessons"),
    ("P132833", "ICRR", "IEG rating; check conflicts with ICR and latest ISR"),
    ("P132833", "PP_earliest", "earliest plan version; STEP references vs 20 verified contract matches"),
    ("P175325", "PAD_original", "active project; planned procurement methods vs awarded"),
    ("P175325", "ISR_latest", "latest ratings for an active project; procurement rating"),
    ("P148850", "PAD_original", "closed project without ICR; direct-selection and foreign-supplier context"),
    ("P148850", "PP_earliest", "earliest plan version; activity table layout for 12 verified matches"),
]


def read(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def num(x: str) -> float | None:
    return float(x) if x not in ("", None) else None


def main() -> None:
    projects = read(V2 / "PROJECT_SELECTION_MANIFEST_v2.csv")
    docs = read(V2 / "DOCUMENT_DOWNLOAD_MANIFEST_v2.csv")
    scoring = json.loads((V2 / "country_scoring.json").read_text())
    summary = json.loads((V2 / "selection_v2_summary.json").read_text())
    v1_projects = read(V1 / "PROJECT_SELECTION_MANIFEST.csv")
    v1_docs = read(V1 / "DOCUMENT_DOWNLOAD_MANIFEST.csv")
    by_pid_role = {(d["project_id"], d["role"]): d for d in docs}

    # 1. first batch
    batch = []
    for i, (pid, role, purpose) in enumerate(FIRST_BATCH, 1):
        d = by_pid_role[(pid, role)]
        batch.append({"batch_no": i, "country_code": d["country_code"], "project_id": pid, "document_id": d["document_id"], "doc_type": d["doc_type"],
                      "role": role, "title": d["title"], "document_date": d["document_date"], "language": d["language"], "version_type": d["version_type"],
                      "pdf_url": d["pdf_url"], "final_url": d["final_url"], "http_status_head": d["http_status"],
                      "size_bytes_or_estimate": d["size_bytes"] or d["size_estimated_bytes"], "size_basis": d["size_basis"],
                      "already_local": d["already_local"], "inspection_purpose": purpose})
    write(V2 / "FIRST_BATCH_10_v2.csv", batch)

    # 2. country rationale
    viable = {c["country_code"]: c for c in scoring["viable"]}
    ranked = sorted(viable.values(), key=lambda c: -c["score"])
    rank = {c["country_code"]: i for i, c in enumerate(ranked, 1)}
    best_per_region = {}
    for c in ranked:
        best_per_region.setdefault(c["region"], c["country_code"])
    rationale = []
    for c in summary["countries"]:
        v = viable[c["country_code"]]
        p = [r for r in projects if r["country_code"] == c["country_code"]]
        dd = [d for d in docs if d["country_code"] == c["country_code"]]
        runner = next((x for x in ranked if x["region"] == v["region"] and x["country_code"] not in {s["country_code"] for s in summary["countries"]}), None)
        rationale.append({
            "country_code": v["country_code"], "country_name": v["country_name"], "region": v["region"],
            "income_group_fy2026": c["income_group_fy2026"] or "no FY2026 entry (FY2025: L)",
            "score": v["score"], "rank_among_61_viable": rank[v["country_code"]],
            "selection_basis": "best score in region" if best_per_region[v["region"]] == v["country_code"] else "next-highest score (max 2 per region)",
            "eligible_projects": v["eligible_projects"], "eligible_closed": v["eligible_closed"], "eligible_active": v["eligible_active"],
            "eligible_with_icr": v["with_icr"], "eligible_with_icrr": v["with_icrr"], "sectors_in_pool": v["sectors"], "methods_in_pool": v["methods"],
            "contracts_in_pool": v["contracts"], "step_reference_share": v["step_reference_share"], "probe_plan_matches": v["probe_plan_matches"],
            "selected_projects": len(p), "selected_closed": sum(r["status"] == "Closed" for r in p), "selected_active": sum(r["status"] == "Active" for r in p),
            "selected_sectors": len({r["sector"] for r in p}), "selected_contracts": sum(int(r["contract_count"]) for r in p),
            "selected_notices": sum(int(r["notice_count"]) for r in p), "selected_documents": len(dd),
            "new_downloads": sum(d["already_local"] != "True" for d in dd),
            "est_new_mb": round(sum(int(d["size_bytes"] or d["size_estimated_bytes"] or 0) for d in dd if d["already_local"] != "True") / 1e6, 1),
            "regional_runner_up": f"{runner['country_code']} ({runner['score']})" if runner else "",
        })
    write(V2 / "COUNTRY_RATIONALE_v2.csv", rationale)

    # 3. completeness and availability
    proj_fields = ["region", "income_group_fy2026", "sector", "approval_fy", "status", "contract_count", "notice_count", "top_method",
                   "direct_selection_share", "prior_review_share", "other_country_supplier_share", "median_contract_value_usd",
                   "ifb_median_window_days", "first_contract_signing"]
    doc_fields = ["document_date", "disclosure_date", "version_type", "report_number", "language", "pdf_url", "guid", "size_bytes"]
    closed = [r for r in projects if r["status"] == "Closed"]
    completeness = {
        "projects": len(projects),
        "project_fields_blank": {f: [r["project_id"] for r in projects if r[f] in ("", None)] for f in proj_fields},
        "income_group_no_fy2026_entry": [r["project_id"] for r in projects if not r["income_group_fy2026"].strip() or r["income_group_fy2026"].startswith("no_entry")],
        "documents": len(docs),
        "document_fields_blank": {f: sum(d[f] in ("", None) for d in docs) for f in doc_fields},
        "document_fields_blank_by_type": {f: dict(collections.Counter(d["doc_type"] for d in docs if d[f] in ("", None))) for f in ("version_type", "report_number")},
        "head_status": dict(collections.Counter(d["http_status"] for d in docs)),
        "content_type": dict(collections.Counter(d["content_type"] for d in docs)),
        "size_basis": dict(collections.Counter(d["size_basis"] for d in docs)),
        "languages": dict(collections.Counter(d["language"] or "blank" for d in docs)),
        "non_english_or_blank": [{k: d[k] for k in ("project_id", "role", "document_id", "language")} for d in docs if d["language"] != "English"],
        "duplicate_document_ids_in_manifest": [k for k, v in collections.Counter(d["document_id"] for d in docs).items() if v > 1],
        "duplicate_pdf_urls_in_manifest": [k for k, v in collections.Counter(d["final_url"] or d["pdf_url"] for d in docs).items() if v > 1],
        "every_project_has": {role: sum((r["project_id"], role) in by_pid_role for r in projects) for role in ("PAD_original", "ISR_first", "ISR_latest", "PP_latest")},
        "isr_first_equals_latest": [r["project_id"] for r in projects
                                    if by_pid_role[(r["project_id"], "ISR_first")]["document_id"] == by_pid_role[(r["project_id"], "ISR_latest")]["document_id"]],
        "closed_projects": len(closed),
        "closed_without_icr": [r["project_id"] for r in closed if int(r["api_icr"]) == 0],
        "closed_without_icrr": [r["project_id"] for r in closed if int(r["api_icrr"]) == 0],
        "projects_with_verified_plan_contract_matches": {r["project_id"]: int(r["verified_plan_contract_matches"]) for r in projects if r["verified_plan_contract_matches"]},
        "projects_with_verifiable_reference_flag": dict(collections.Counter(r["verifiable_contract_document_references"] for r in projects)),
        "issues_all_listed_documents": summary["issues"],
    }
    (V2 / "METADATA_COMPLETENESS_v2.json").write_text(json.dumps(completeness, indent=1))

    # 4. comparison with the previous 70-project selection
    def profile(ps: list[dict], ds: list[dict], status="status", sector_key="sector", contracts="contract_count") -> dict:
        cc = collections.Counter(r["country_code"] for r in ps)
        return {"projects": len(ps), "countries": len(cc), "max_projects_per_country": max(cc.values()),
                "regions": dict(collections.Counter(r["region"] for r in ps)), "status": dict(collections.Counter(r[status] for r in ps)),
                "sectors": len({r[sector_key] for r in ps}), "contracts": sum(int(r[contracts]) for r in ps),
                "median_contracts_per_project": statistics.median(int(r[contracts]) for r in ps), "documents": len(ds),
                "documents_by_type": dict(collections.Counter(d["doc_type"] for d in ds))}
    v1_ids, v2_ids = {r["project_id"] for r in v1_projects}, {r["project_id"] for r in projects}
    comparison = {"previous_70": profile(v1_projects, v1_docs, sector_key="major_sector"), "v2_70": profile(projects, docs),
                  "previous_regional_projects": sum(r["country_is_regional"] == "True" for r in v1_projects),
                  "overlap_project_ids": sorted(v1_ids & v2_ids), "previous_countries_kept": sorted({r["country_code"] for r in v1_projects} & {r["country_code"] for r in projects})}
    (V2 / "COMPARISON_WITH_PREVIOUS_70.json").write_text(json.dumps(comparison, indent=1, default=str))
    print(json.dumps({"first_batch": [(b["project_id"], b["role"], b["document_id"], b["already_local"]) for b in batch],
                      "completeness": {k: v for k, v in completeness.items() if k != "issues_all_listed_documents"}, "comparison": comparison}, indent=1, default=str))


if __name__ == "__main__":
    main()
