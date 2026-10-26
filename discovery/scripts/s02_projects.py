"""Task 2B: Projects & Operations (search.worldbank.org/api/v2/projects).

Extraction methods:
  1. Full-field sample: `fl=*`, seeded random `os` offsets (pages of 50) across the
     whole index -> samples/projects/projects_allfields_sample.jsonl
  2. Population with selected fields (all ~22.7k projects, pages of 1000, sorted by id
     for stable pagination) -> data_cache/projects_population.jsonl.gz
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wbdisc.config import SETTINGS  # noqa: E402
from wbdisc.raw_store import write_jsonl  # noqa: E402
from wbdisc.sources import search_all, search_page  # noqa: E402

POP_FIELDS = [
    "id", "project_name", "countrycode", "countryname", "countryshortname", "regionname", "status",
    "projectstatusdisplay", "last_stage_reached_name", "boardapprovaldate", "approvalfy", "closingdate",
    "recent_close_date1", "p2a_updated_date", "proj_last_upd_date", "lendinginstr", "lendinginstrtype",
    "lendinginstrcode", "prodline", "prodlinetext", "productlinetype", "impagency", "borrower", "totalamt",
    "totalcommamt", "curr_total_commitment", "curr_ibrd_commitment", "curr_ida_commitment", "lendprojectcost",
    "ibrdcommamt", "idacommamt", "grantamt", "sector1", "sector2", "sector3", "mjsector_exact", "sector_exact",
    "themev2_level1_exact", "envassesmentcategorycode", "esrc_ovrl_risk_rate", "rel_loan_ids", "loanid", "mainloan",
    "cancellations", "supplementprojectflg", "parentprojid", "source", "url", "pdo", "cons_serv_reqd_ind",
    "project_gp_info", "ccsa_practice_name",
]
OUT = SETTINGS.samples_dir / "projects"


def main() -> None:
    rng = random.Random(SETTINGS.seed)
    total, _ = search_page("projects", {"rows": 0}, purpose="projects count")
    print("projects total:", total)

    rows = []
    for os_ in sorted(rng.sample(range(0, total - 50), 10)):
        _, recs = search_page("projects", {"rows": 50, "os": os_, "fl": "*", "srt": "id", "order": "asc"},
                              purpose="projects fl=* sample")
        for r in recs:
            r["_extract"] = {"os": os_, "rows": 50, "srt": "id"}
        rows.extend(recs)
    write_jsonl(rows, OUT / "projects_allfields_sample.jsonl.gz", source="search.worldbank.org/api/v2/projects",
                method="fl=*; srt=id asc; 10 seeded random offsets x 50 rows")

    dest = SETTINGS.cache_dir / "projects_population.jsonl.gz"
    if not dest.exists():
        recs = list(search_all("projects", {"fl": ",".join(POP_FIELDS), "srt": "id", "order": "asc"},
                               purpose="projects population"))
        write_jsonl(recs, dest, source="search.worldbank.org/api/v2/projects",
                    method=f"selected fields ({len(POP_FIELDS)}); srt=id asc; rows=1000 os paging")
        print("population records:", len(recs), "unique ids:", len({r.get('id') for r in recs}))


if __name__ == "__main__":
    main()
