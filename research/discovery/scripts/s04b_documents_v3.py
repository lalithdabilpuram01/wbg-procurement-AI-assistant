"""Task 2D (revised): document metadata via search.worldbank.org/api/v3/wds, plus a
diversity file sample.

v2 wds reports 549,248 documents vs 619,903 in v3 (2026-10-20) and v2 shows no
`datestored` after early 2025; v3 is the recommended source. v2 samples are kept as evidence.

Extraction methods:
  1. v3 metadata per document type (same types/offset scheme as s04) -> wds_v3_meta_sample_by_type.jsonl.gz
  2. v3 metadata for all cluster projects (projectid=<P>, default v3 fields) -> wds_v3_cluster_meta.jsonl.gz
  3. Diversity files: newest PAD, 2 non-English ICRs (French, Spanish), 1 pre-2000 Staff Appraisal
     Report -> samples/documents/files/
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wbdisc import datasets as D  # noqa: E402
from wbdisc.config import SETTINGS  # noqa: E402
from wbdisc.http import FetchError, download  # noqa: E402
from wbdisc.raw_store import record_file, write_jsonl  # noqa: E402
from wbdisc.sources import search_all, search_page  # noqa: E402

OUT = SETTINGS.samples_dir / "documents"
DOC_TYPES = [
    "Project Appraisal Document", "Implementation Status and Results Report",
    "Implementation Completion and Results Report", "Implementation Completion Report Review",
    "Project Performance Assessment Report", "Procurement Plan", "Project Paper", "Note on Cancelled Operation",
]


def main() -> None:
    rng = random.Random(SETTINGS.seed)
    tpath = OUT / "wds_v3_meta_sample_by_type.jsonl.gz"
    if not tpath.exists():
        rows, half = [], max(1, SETTINGS.doc_meta_per_type // 2)
        for dt in DOC_TYPES:
            total, _ = search_page("v3/wds", {"rows": 0, "docty_exact": dt})
            print(f"v3 {dt}: {total}")
            hi = min(total, 100_000) - half
            for os_ in sorted(rng.sample(range(0, max(1, hi)), min(2, max(1, hi)))):
                _, recs = search_page("v3/wds", {"rows": half, "os": os_, "docty_exact": dt}, purpose=f"v3 type {dt}")
                for r in recs:
                    r["_extract"] = {"docty_exact": dt, "os": os_, "rows": half, "type_total": total}
                rows.extend(recs)
        write_jsonl(rows, tpath, source="search.worldbank.org/api/v3/wds",
                    method="docty_exact=<type>; default fields; 2 seeded random offsets per type")

    cpath = OUT / "wds_v3_cluster_meta.jsonl.gz"
    if not cpath.exists():
        rows = []
        for i, pid in enumerate(D.cluster_projects()):
            for r in search_all("v3/wds", {"projectid": pid}, purpose=f"v3 docs {pid}"):
                r["_extract"] = {"filter_projectid": pid}
                rows.append(r)
            if i % 50 == 0:
                print(f"  v3 docs {i}: {len(rows)}")
        write_jsonl(rows, cpath, source="search.worldbank.org/api/v3/wds",
                    method="projectid=<P> for each cluster project; default v3 fields")

    picks = []
    _, newest_pad = search_page("v3/wds", {"rows": 1, "docty_exact": "Project Appraisal Document", "srt": "docdt", "order": "desc",
                                           "strdate": "2025-01-01", "enddate": "2026-10-20"})
    picks += [("PADNEW", d) for d in newest_pad]
    for lang in ("French", "Spanish"):
        _, recs = search_page("v3/wds", {"rows": 1, "docty_exact": "Implementation Completion and Results Report",
                                         "lang_exact": lang, "srt": "docdt", "order": "desc"})
        picks += [(f"ICR{lang[:2].upper()}", d) for d in recs]
    _, old = search_page("v3/wds", {"rows": 1, "docty_exact": "Staff Appraisal Report", "strdate": "1985-01-01",
                                    "enddate": "1995-12-31"})
    picks += [("SAR1990", d) for d in old]
    fdir = OUT / "files"
    for tag, d in picks:
        pid = (d.get("projectid") or "NOPROJ").replace(",", "+")
        for kind in ("pdfurl", "txturl"):
            url = d.get(kind)
            if not url:
                print(f"  {tag} {d.get('id')}: no {kind}")
                continue
            dest = fdir / f"{pid}_{tag}_{d['id']}.{kind[:3]}"
            if dest.exists():
                continue
            try:
                n = download(url.replace("http://", "https://"), dest, purpose=f"diversity {tag}")
                record_file(dest, source="documents.worldbank.org", method=f"GET {kind} (v3 wds metadata)", records=None,
                            notes=f"url={url}; wds_id={d['id']}; docdt={d.get('docdt')}; lang={d.get('lang')}")
                print(f"  {dest.name}: {n} bytes")
            except FetchError as exc:
                print(f"  {dest.name} FAILED: {exc}")


if __name__ == "__main__":
    main()
