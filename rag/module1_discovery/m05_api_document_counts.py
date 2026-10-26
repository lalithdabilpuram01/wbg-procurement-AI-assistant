"""Module 1 discovery (READ-ONLY, metadata API only): document counts per cohort project from the official World Bank
Documents & Reports API (search.worldbank.org/api/v3/wds).

One request per project: projectid=<P>, rows=0, fct=docty_exact,lang_exact,majdocty_exact. Responses carry the total and
facet counts (returned inside `documents.facets`). No document records or PDFs are downloaded. Requests go through the
repository's logged HTTP client (retry/backoff, polite delay, evidence/access_log.jsonl). Results are written
incrementally so the run can resume.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "discovery"))
from wbdisc.http import FetchError, fetch  # noqa: E402

OUT = Path(__file__).resolve().parent / "evidence" / "api_document_counts_by_project.jsonl"
API = "https://search.worldbank.org/api/v3/wds"


def facets(j: dict, key: str) -> dict[str, int]:
    f = (j.get("documents") or {}).get("facets") or {}
    return {v["name"]: int(v["count"]) for v in (f.get(key) or {}).values()}


def main() -> None:
    with (ROOT / "cohort" / "poc_cohort_2000_manifest.csv").open(newline="", encoding="utf-8") as fh:
        projects = [r["project_id"] for r in csv.DictReader(fh)]
    done = {json.loads(ln)["project_id"] for ln in OUT.read_text().splitlines()} if OUT.exists() else set()
    with OUT.open("a") as out:
        for i, p in enumerate(projects):
            if p in done:
                continue
            try:
                j = fetch(API, {"format": "json", "projectid": p, "rows": 0, "fct": "docty_exact,lang_exact,majdocty_exact"},
                          purpose="module1 m05: document facet counts by project (metadata only)").json()
                rec = {"project_id": p, "total": int(j.get("total") or 0), "by_type": facets(j, "docty_exact"), "by_lang": facets(j, "lang_exact"),
                       "by_major_type": facets(j, "majdocty_exact"), "status": "ok"}
            except FetchError as e:
                rec = {"project_id": p, "status": "error", "http_status": e.status}
            out.write(json.dumps(rec) + "\n")
            out.flush()
            if i % 100 == 0:
                print(i, p, rec.get("total"), flush=True)


if __name__ == "__main__":
    main()
