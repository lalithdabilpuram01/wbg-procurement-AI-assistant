"""Task 2B (revised): Projects via search.worldbank.org/api/v3/projects.

Discovery on 2026-10-20 showed the v2 projects index is frozen (no approvals after
2024-12-20; latest update stamps Jan 2025). The live projects website calls v3. v2
output is retained as evidence; v3 is the recommended source.

Extraction methods:
  1. v3 full-field sample: fl=*, cluster projects (incl. loan_transactions)
     -> samples/projects/projects_v3_cluster_allfields.jsonl.gz
  2. v3 population: selected fields (no loan_transactions/indicators/geo), srt=id asc,
     rows=1000 paging -> data_cache/projects_v3_population.jsonl.gz
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wbdisc import datasets as D  # noqa: E402
from wbdisc.config import SETTINGS  # noqa: E402
from wbdisc.raw_store import write_jsonl  # noqa: E402
from wbdisc.sources import search_all, search_page  # noqa: E402

POP_FIELDS = [
    "id", "project_name", "countrycode", "countryname", "countryshortname", "regionname", "status",
    "last_stage_reached_name", "boardapprovaldate", "closingdate", "fiscalyear", "impagency", "borrower",
    "totalamt", "curr_total_commitment", "curr_ibrd_commitment", "curr_ida_commitment", "curr_project_cost",
    "lendprojectcost", "idacommamt", "grantamt", "major_sector_name", "major_sector_code", "major_sectors",
    "sector_exact", "themev2_level1_exact", "envassesmentcategorycode", "milestones", "isr_ratings", "icr_ratings",
    "ieg_ratings", "loan_effective_date", "financers", "public_disclosure_date", "proj_last_upd_date",
    "projectfinancialtype", "prodline_exact", "cons_serv_reqd_ind", "pdo",
]
OUT = SETTINGS.samples_dir / "projects"


def main() -> None:
    total, _ = search_page("v3/projects", {"rows": 0})
    print("v3 projects total:", total)
    cpath = OUT / "projects_v3_cluster_allfields.jsonl.gz"
    if not cpath.exists():
        rows = []
        for pid in D.cluster_projects():
            _, recs = search_page("v3/projects", {"id": pid, "fl": "*", "rows": 5}, purpose=f"v3 project {pid}")
            rows.extend(r for r in recs if r.get("id") == pid)
        write_jsonl(rows, cpath, source="search.worldbank.org/api/v3/projects",
                    method="id=<P>, fl=* for each procnotices cluster project")
        print("cluster v3 records:", len(rows))
    dest = SETTINGS.cache_dir / "projects_v3_population.jsonl.gz"
    if not dest.exists():
        recs = list(search_all("v3/projects", {"fl": ",".join(POP_FIELDS), "srt": "id", "order": "asc"},
                               purpose="v3 projects population"))
        write_jsonl(recs, dest, source="search.worldbank.org/api/v3/projects",
                    method=f"selected fields ({len(POP_FIELDS)}); srt=id asc; rows=1000 os paging",
                    notes=f"reported total={total}")
        print("population:", len(recs), "unique ids:", len({r.get('id') for r in recs}))


if __name__ == "__main__":
    main()
