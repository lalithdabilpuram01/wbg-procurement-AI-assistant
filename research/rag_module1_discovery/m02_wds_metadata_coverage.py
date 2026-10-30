"""Module 1 discovery (READ-ONLY, local files only): document availability from WDS metadata already on disk.

Uses discovery/samples/documents/wds_v3_cluster_meta.jsonl.gz (v3, 276 Phase 1 cluster projects, retrieved 2026-10-21)
and wds_cluster_meta.jsonl.gz (v2, same projects; has no_of_pages). No network calls. Restricted to the projects that are
in the 2,000-project cohort (184). Reports per-project document counts by type, page-count distributions by type,
languages, version types, multi-project documents, near-duplicates and metadata-field completeness, plus a list of which
document metadata fields are provided by the source.
"""
from __future__ import annotations

import csv
import gzip
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "discovery" / "samples" / "documents"
OUT = Path(__file__).resolve().parent / "evidence"
COHORT = ROOT / "cohort" / "poc_cohort_2000_manifest.csv"
TYPES = {"Project Appraisal Document": "PAD", "Implementation Status and Results Report": "ISR", "Implementation Completion and Results Report": "ICR",
         "Implementation Completion Report Review": "ICRR", "Procurement Plan": "PP", "Project Paper": "Project Paper",
         "Project Performance Assessment Report": "PPAR"}


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def short(docty: str) -> str:
    return TYPES.get(norm(docty), "Other")


def pct(n: int, d: int) -> float:
    return round(100 * n / d, 1) if d else 0.0


def main() -> None:
    with COHORT.open(newline="", encoding="utf-8") as fh:
        cohort_rows = {r["project_id"]: r for r in csv.DictReader(fh)}
    v3 = [json.loads(ln) for ln in gzip.open(DOCS / "wds_v3_cluster_meta.jsonl.gz", "rt")]
    v2 = {str(r.get("id")): r for r in (json.loads(ln) for ln in gzip.open(DOCS / "wds_cluster_meta.jsonl.gz", "rt"))}
    # seeded projects = the projectid each record was requested for (_extract.filter_projectid); documents can also list
    # related / co-financed projects, which must not be counted as "covered" projects.
    cluster = sorted({r["_extract"]["filter_projectid"] for r in v3})
    in_cohort = [p for p in cluster if p in cohort_rows]
    per = defaultdict(Counter)
    docs = []
    for r in v3:
        pids = [p for p in norm(r.get("projectid")).split(",") if p]
        t = short(r.get("docty"))
        pages = v2.get(str(r.get("id")), {}).get("no_of_pages")
        rec = {"id": r.get("id"), "type": t, "projects": pids, "lang": norm(r.get("lang")), "versiontyp": norm(r.get("versiontyp")) or None,
               "docdt": norm(r.get("docdt"))[:10] or None, "disclosure_date": norm(r.get("disclosure_date"))[:10] or None,
               "pages": int(pages) if str(pages or "").isdigit() else None, "title": norm(r.get("display_title")), "has_pdfurl": bool(r.get("pdfurl"))}
        docs.append(rec)
        seed = r["_extract"]["filter_projectid"]
        rec["seed_project"] = seed
        if seed in cohort_rows:
            per[seed][t] += 1
    cdocs = [d for d in docs if d["seed_project"] in cohort_rows]
    core = ["PAD", "ISR", "ICR", "ICRR", "PP", "Project Paper"]
    has = {t: sum(1 for p in in_cohort if per[p][t] > 0) for t in core}
    pages = {t: [d["pages"] for d in cdocs if d["type"] == t and d["pages"]] for t in core + ["PPAR", "Other"]}
    page_stats = {t: ({"n": len(v), "median": statistics.median(v), "p90": sorted(v)[int(0.9 * (len(v) - 1))], "max": max(v),
                       "ge_100": sum(x >= 100 for x in v)} if v else None) for t, v in pages.items()}
    totals = [sum(per[p].values()) for p in in_cohort]
    narrative = [sum(per[p][t] for t in ("PAD", "ISR", "ICR", "ICRR", "Project Paper")) for p in in_cohort]
    dup = Counter((d["seed_project"], d["type"], d["docdt"], d["title"].lower()) for d in cdocs)
    seen_ids = Counter(d["id"] for d in cdocs)
    combos = Counter(tuple(t for t in ("PAD", "ISR", "ICR", "ICRR") if per[p][t]) for p in in_cohort)
    strata = defaultdict(Counter)
    for p in in_cohort:
        row = cohort_rows[p]
        strata["region"][row["region"]] += 1
        strata["status"][row["status"]] += 1
        strata["fy_band"][row["fy_band"]] += 1
        strata["has_pad_icr"][f"PAD={per[p]['PAD'] > 0},ICR={per[p]['ICR'] > 0}"] += 1
    fields = Counter(k for r in v3 for k, v in r.items() if v not in (None, "", [], {}))
    summary = {
        "source_files": ["wds_v3_cluster_meta.jsonl.gz (retrieved 2026-10-21)", "wds_cluster_meta.jsonl.gz (v2, for no_of_pages)"],
        "cluster_projects": len(cluster), "cluster_projects_in_cohort": len(in_cohort), "documents_v3_all_cluster": len(v3),
        "documents_linked_to_cohort_projects": len(cdocs), "docs_by_type": dict(Counter(d["type"] for d in cdocs)),
        "cohort_projects_with_type": {t: {"projects": n, "pct": pct(n, len(in_cohort))} for t, n in has.items()},
        "type_combinations_PAD_ISR_ICR_ICRR": {"+".join(k) or "none": v for k, v in combos.most_common()},
        "docs_per_project": {"median": statistics.median(totals), "p10": sorted(totals)[len(totals) // 10], "p90": sorted(totals)[int(0.9 * (len(totals) - 1))],
                             "max": max(totals), "min": min(totals)},
        "narrative_docs_per_project (PAD+ISR+ICR+ICRR+Project Paper)": {"median": statistics.median(narrative), "min": min(narrative), "max": max(narrative)},
        "page_stats_by_type": page_stats,
        "estimated_pages_cohort_cluster_narrative": sum(sum(pages[t]) for t in ("PAD", "ISR", "ICR", "ICRR", "Project Paper")),
        "languages": dict(Counter(d["lang"] for d in cdocs).most_common(8)), "versiontyp": dict(Counter(d["versiontyp"] for d in cdocs).most_common(8)),
        "multi_project_docs_pct": pct(sum(len(d["projects"]) > 1 for d in cdocs), len(cdocs)),
        "near_duplicate_groups (same projects,type,docdt,title)": sum(1 for v in dup.values() if v > 1), "near_duplicate_extra_records": sum(v - 1 for v in dup.values() if v > 1),
        "same_document_id_under_several_seed_projects": sum(1 for v in seen_ids.values() if v > 1),
        "pdfurl_present_pct": pct(sum(d["has_pdfurl"] for d in cdocs), len(cdocs)),
        "strata_of_cluster_cohort_projects": {k: dict(v) for k, v in strata.items()},
        "v3_field_completeness_pct": {k: pct(v, len(v3)) for k, v in fields.most_common()},
    }
    (OUT / "wds_metadata_coverage.json").write_text(json.dumps({"summary": summary, "per_project_counts": {p: dict(per[p]) for p in in_cohort}}, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "v3_field_completeness_pct"}, indent=1))


if __name__ == "__main__":
    main()
