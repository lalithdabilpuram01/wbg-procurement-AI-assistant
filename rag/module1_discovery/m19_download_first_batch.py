"""Download ONLY the 10 approved PDFs in rag_scope_v3_35_projects/FIRST_BATCH_10_v3.csv (pilot, approved 2026-10-25).

Each file goes to its expected_local_path under rag/corpus_v3/pdf/<country>/<project_id>/ via wbdisc.http.download
(retry/backoff, polite delay, every request in discovery/evidence/access_log.jsonl). Existing files are not re-downloaded.
Writes rag/corpus_v3/CORPUS_MANIFEST_FIRST10.csv with the manifest metadata, final URL, content type, SHA-256, size, page
count (PyMuPDF) and download timestamp. Unavailable values are left empty (NULL); nothing is inferred.
"""
from __future__ import annotations

import csv
import hashlib
import sys
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "discovery"))
from wbdisc.http import FetchError, download, utcnow  # noqa: E402

BATCH = HERE / "rag_scope_v3_35_projects" / "FIRST_BATCH_10_v3.csv"
MANIFEST = HERE / "rag_scope_v3_35_projects" / "DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv"
OUT = ROOT / "rag" / "corpus_v3" / "CORPUS_MANIFEST_FIRST10.csv"


def main() -> None:
    with BATCH.open(newline="") as fh:
        batch = list(csv.DictReader(fh))
    assert len(batch) == 10
    with MANIFEST.open(newline="") as fh:
        man = {r["document_id"]: r for r in csv.DictReader(fh)}
    rows = []
    for b in batch:
        m = man[b["document_id"]]
        dest = ROOT / b["expected_local_path"]
        rec = {"batch_no": b["batch_no"], "country_code": b["country_code"], "project_id": b["project_id"], "document_id": b["document_id"],
               "title": m.get("title") or None, "doc_type": b["doc_type"], "role": b["role"], "document_date": m.get("document_date") or None,
               "disclosure_date": m.get("disclosure_date") or None, "language": m.get("language") or None, "version_type": m.get("version_type") or None,
               "report_number": m.get("report_number") or None, "guid": m.get("guid") or None, "source_url": b["pdf_url"],
               "local_path": b["expected_local_path"], "download_status": None, "final_url_head": b.get("final_url") or None, "content_type_head": m.get("content_type") or None, "sha256": None,
               "size_bytes": None, "page_count": None, "downloaded_at": None, "error": None}
        try:
            if dest.exists():
                rec["download_status"] = "already_present"
            else:
                download(b["pdf_url"], dest, purpose=f"module1 pilot first-10 {b['project_id']} {b['doc_type']} {b['document_id']}")
                rec["download_status"] = "downloaded"
                rec["downloaded_at"] = utcnow()
            data = dest.read_bytes()
            rec["sha256"] = hashlib.sha256(data).hexdigest()
            rec["size_bytes"] = len(data)
            if not data.startswith(b"%PDF"):
                rec["error"] = "file does not start with %PDF"
            with fitz.open(dest) as doc:
                rec["page_count"] = doc.page_count
        except FetchError as e:
            rec.update(download_status="failed", error=f"{e.status} {e.message[:150]}")
        rows.append(rec)
        print(rec["batch_no"], rec["project_id"], rec["doc_type"], rec["download_status"], rec["size_bytes"], rec["page_count"], flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
