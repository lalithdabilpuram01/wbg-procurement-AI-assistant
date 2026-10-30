"""Task 2C: Procurement notices.

Extraction methods:
  1. Global random sample: search.worldbank.org/api/v2/procnotices, default field set,
     seeded random `os` offsets x 100 rows -> samples/procnotices/procnotices_global_sample.jsonl.gz
  2. Project-cluster sample: N projects drawn (seeded, stratified by region) from projects that
     appear in DS00005 contract awards; ALL notices for each project are pulled with
     `project_id=<P>` -> samples/procnotices/procnotices_cluster_sample.jsonl.gz
     (enables exact within-project join rates notice <-> contract)
  3. Finances One DS00979 bulk CSV (population, git-ignored cache).

Personal contact fields (contact_name, contact_email, contact_phone_no) are excluded from
the cluster pull via `fl` (data minimisation). They remain present in the global sample
because that sample documents the default API payload as-is.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from wbdisc.config import SETTINGS  # noqa: E402
from wbdisc.http import download  # noqa: E402
from wbdisc.raw_store import read_jsonl, record_file, write_jsonl  # noqa: E402
from wbdisc.sources import search_all, search_facets, search_page  # noqa: E402

OUT = SETTINGS.samples_dir / "procnotices"
CLUSTER_FIELDS = [
    "id", "notice_type", "noticedate", "notice_lang_name", "notice_status", "submission_deadline_date",
    "submission_deadline_time", "project_ctry_name", "project_id", "project_name", "bid_reference_no",
    "bid_description", "procurement_group", "procurement_method_code", "procurement_method_name",
    "contact_organization", "contact_ctry_name", "submission_date", "notice_text",
]
DS00979_CSV = "DS00979/RS00909/Procurement_Notice.csv"


def cluster_projects() -> list[str]:
    path = SETTINGS.cache_dir / "ds00005_contract_awards.csv"
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig", usecols=["Project ID", "Region"])
    proj = df.drop_duplicates("Project ID")
    proj = proj[proj["Project ID"].str.match(r"^P\d{6}$")]
    per_region = max(1, SETTINGS.cluster_projects // proj["Region"].nunique())
    picks = pd.concat([g.sample(n=min(per_region, len(g)), random_state=SETTINGS.seed) for _, g in proj.groupby("Region")])
    return sorted(picks["Project ID"].tolist())


def main() -> None:
    rng = random.Random(SETTINGS.seed)
    OUT.mkdir(parents=True, exist_ok=True)

    gpath = OUT / "procnotices_global_sample.jsonl.gz"
    if not gpath.exists():
        # The API rejects os > 100000 (Azure Search $skip limit), so offsets are drawn per notice type
        # within [0, min(total,100000)) under two sort orders (newest-first default, oldest-first).
        types = search_facets("procnotices", ["notice_type_exact"])["notice_type_exact"]
        rows = []
        for ntype, total in types.items():
            if total < 50:
                continue
            for sort in ({}, {"srt": "submission_date", "order": "asc"}):
                hi = min(total, 100_000) - 50
                for os_ in sorted(rng.sample(range(0, max(1, hi)), 1 if total < 5000 else 2)):
                    _, recs = search_page("procnotices", {"rows": 50, "os": os_, "notice_type_exact": ntype, **sort},
                                          purpose="procnotices global sample")
                    for r in recs:
                        r["_extract"] = {"notice_type_exact": ntype, "os": os_, "rows": 50,
                                         "sort": sort or "API default", "type_total": total}
                    rows.extend(recs)
        write_jsonl(rows, gpath, source="search.worldbank.org/api/v2/procnotices",
                    method="default fields; per notice_type_exact, seeded random offsets (<100000) x 50 rows "
                           "under default and submission_date-asc sort", notes=f"type totals={types}")

    cpath = OUT / "procnotices_cluster_sample.jsonl.gz"
    if not cpath.exists():
        projects = cluster_projects()
        rows, mismatched = [], 0
        for i, pid in enumerate(projects):
            recs = list(search_all("procnotices", {"project_id": pid, "fl": ",".join(CLUSTER_FIELDS)},
                                   purpose=f"cluster {pid}"))
            for r in recs:
                r["_extract"] = {"filter_project_id": pid}
                mismatched += r.get("project_id") != pid
            rows.extend(recs)
            if i % 25 == 0:
                print(f"  {i}/{len(projects)} projects, {len(rows)} notices")
        write_jsonl(rows, cpath, source="search.worldbank.org/api/v2/procnotices",
                    method=f"project_id=<P> filter for {len(projects)} seeded region-stratified DS00005 projects; "
                           f"fl excludes personal contact fields",
                    notes=f"records whose project_id != filter value: {mismatched}")
        (OUT / "cluster_projects.txt").write_text("\n".join(projects) + "\n")
        print("cluster notices:", len(rows), "project_id mismatches:", mismatched)

    dest = SETTINGS.cache_dir / "ds00979_procurement_notice.csv"
    if not dest.exists() and SETTINGS.allow_bulk:
        n = download(f"{SETTINGS.fone_files_base}/{DS00979_CSV}", dest, purpose="bulk DS00979")
        record_file(dest, source="Finances One DS00979 bulk CSV", method="HTTP GET of published CSV",
                    records=None, notes=f"{n} bytes; git-ignored cache")
    print("global sample:", len(read_jsonl(gpath)))


if __name__ == "__main__":
    main()
