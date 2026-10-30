"""Task 2D: Documents & Reports (search.worldbank.org/api/v2/wds + documents.worldbank.org files).

Extraction methods:
  1. Metadata sample per document type: docty_exact=<type>, default field set, 2 seeded random
     offsets x (N/2) rows -> samples/documents/wds_meta_sample_by_type.jsonl.gz
  2. Cluster metadata: all documents for the procnotices cluster projects (projectid=<P>,
     restricted fields) -> samples/documents/wds_cluster_meta.jsonl.gz
  3. Files: for up to `pdfs_per_type` cluster projects having PAD+ISR+ICR+ICR Review+Procurement Plan,
     download one PDF of each type, plus the WB-published .txt rendition; plus PPARs from the
     type sample. -> samples/documents/files/ (PDF/TXT git-ignored; checksums in MANIFEST)
"""
from __future__ import annotations

import collections
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wbdisc.config import SETTINGS  # noqa: E402
from wbdisc.http import FetchError, download  # noqa: E402
from wbdisc.raw_store import read_jsonl, record_file, write_jsonl  # noqa: E402
from wbdisc.sources import search_all, search_page  # noqa: E402

OUT = SETTINGS.samples_dir / "documents"
DOC_TYPES = [
    "Project Appraisal Document",
    "Implementation Status and Results Report",
    "Implementation Completion and Results Report",
    "Implementation Completion Report Review",
    "Project Performance Assessment Report",
    "Procurement Plan",
    "Project Paper",
    "Note on Cancelled Operation",
]
CORE = DOC_TYPES[:4] + ["Procurement Plan"]
CLUSTER_FIELDS = [
    "id", "projectid", "docty", "majdocty", "docdt", "disclosure_date", "datestored", "last_modified_date",
    "ext_pub_date", "lang", "available_in", "display_title", "docna", "pdfurl", "txturl", "url", "guid", "docm_id",
    "chronical_docm_id", "versiontyp", "no_of_pages", "count", "count_key", "admreg", "repnb", "seccl", "disclstat",
    "projn", "lndinstr", "subsc", "theme", "prdln",
]
SHORT = {
    "Project Appraisal Document": "PAD", "Implementation Status and Results Report": "ISR",
    "Implementation Completion and Results Report": "ICR", "Implementation Completion Report Review": "ICRR",
    "Project Performance Assessment Report": "PPAR", "Procurement Plan": "PP",
}


def type_sample(rng: random.Random) -> list[dict]:
    rows = []
    half = max(1, SETTINGS.doc_meta_per_type // 2)
    for dt in DOC_TYPES:
        total, _ = search_page("wds", {"rows": 0, "docty_exact": dt}, purpose=f"count {dt}")
        print(f"{dt}: {total}")
        offsets = sorted(rng.sample(range(0, max(1, total - half)), min(2, max(1, total - half))))
        for os_ in offsets:
            _, recs = search_page("wds", {"rows": half, "os": os_, "docty_exact": dt}, purpose=f"type sample {dt}")
            for r in recs:
                r["_extract"] = {"docty_exact": dt, "os": os_, "rows": half, "type_total": total}
            rows.extend(recs)
    return rows


def main() -> None:
    rng = random.Random(SETTINGS.seed)
    OUT.mkdir(parents=True, exist_ok=True)
    tpath = OUT / "wds_meta_sample_by_type.jsonl.gz"
    if not tpath.exists():
        write_jsonl(type_sample(rng), tpath, source="search.worldbank.org/api/v2/wds",
                    method="docty_exact=<type>; default fields; 2 seeded random offsets per type")

    cpath = OUT / "wds_cluster_meta.jsonl.gz"
    projects = (SETTINGS.samples_dir / "procnotices" / "cluster_projects.txt").read_text().split()
    if not cpath.exists():
        rows = []
        for i, pid in enumerate(projects):
            for r in search_all("wds", {"projectid": pid, "fl": ",".join(CLUSTER_FIELDS)}, purpose=f"docs {pid}"):
                r["_extract"] = {"filter_projectid": pid}
                rows.append(r)
            if i % 50 == 0:
                print(f"  docs {i}/{len(projects)}: {len(rows)}")
        write_jsonl(rows, cpath, source="search.worldbank.org/api/v2/wds",
                    method=f"projectid=<P> for {len(projects)} cluster projects; restricted fields")

    # ---- file sample ----
    cluster = read_jsonl(cpath)
    by_proj: dict[str, dict[str, list[dict]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for d in cluster:
        by_proj[d.get("_extract", {}).get("filter_projectid")][(d.get("docty") or "").strip()].append(d)
    complete = sorted(p for p, t in by_proj.items() if all(t.get(x) for x in CORE))
    print("cluster projects with PAD+ISR+ICR+ICRR+PP:", len(complete))
    chosen = rng.sample(complete, min(SETTINGS.pdfs_per_type, len(complete)))
    targets = []
    for pid in chosen:
        for dt in CORE:
            docs = sorted(by_proj[pid][dt], key=lambda d: d.get("docdt") or "")
            targets.append(docs[-1] if dt != "Implementation Status and Results Report" else docs[len(docs) // 2])
    ppars = [d for d in read_jsonl(tpath) if d.get("_extract", {}).get("docty_exact") == "Project Performance Assessment Report"]
    targets += rng.sample(ppars, min(SETTINGS.pdfs_per_type, len(ppars)))

    fdir = OUT / "files"
    for d in targets:
        dt = SHORT.get((d.get("docty") or "").strip(), "DOC")
        stem = f"{d.get('projectid') or 'NOPROJ'}_{dt}_{d['id']}"
        for kind in ("pdfurl", "txturl"):
            url = d.get(kind)
            if not url:
                print(f"  {stem}: no {kind}")
                continue
            url = url.replace("http://", "https://")
            dest = fdir / f"{stem}.{kind[:3]}"
            if dest.exists():
                continue
            try:
                n = download(url, dest, purpose=f"doc file {kind}")
                record_file(dest, source="documents.worldbank.org", method=f"GET {kind} from WDS metadata",
                            records=None, notes=f"url={url}; wds_id={d['id']}; bytes={n}")
                print(f"  {dest.name}: {n} bytes")
            except FetchError as exc:
                print(f"  {stem} {kind} FAILED: {exc}")


if __name__ == "__main__":
    main()
