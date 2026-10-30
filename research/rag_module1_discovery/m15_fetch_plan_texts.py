"""Fetch the official WDS text representation of every procurement-plan version for the v2 70 projects (text only).

Source: the `txt_url` of each Procurement Plan record in selection_v2_10_countries/all_documents_metadata_v2.jsonl
(search.worldbank.org/api/v3/wds listing). No PDFs are requested. Requests go one at a time through wbdisc.http.fetch
(retry with backoff, 0.5 s polite delay, every request appended to discovery/evidence/access_log.jsonl).
Resumable: documents already saved with HTTP 200 are skipped.

Writes plan_version_coverage_v1/plan_text/<project_id>/<document_id>.txt and plan_version_coverage_v1/fetch_log.jsonl
(one line per document: ids, URLs, dates, HTTP status, content type, bytes, SHA-256, or the reason it is unavailable).
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "discovery"))
from wbdisc.http import FetchError, fetch, utcnow  # noqa: E402

V2 = HERE / "selection_v2_10_countries"
OUT = HERE / "plan_version_coverage_v1"
TEXT = OUT / "plan_text"
LOG = OUT / "fetch_log.jsonl"
KEEP = ("project_id", "document_id", "guid", "title", "document_date", "disclosure_date", "version_type", "report_number", "language",
        "pdf_url", "txt_url")


def main() -> None:
    TEXT.mkdir(parents=True, exist_ok=True)
    with (V2 / "PROJECT_SELECTION_MANIFEST_v2.csv").open(newline="") as fh:
        selected = {r["project_id"] for r in csv.DictReader(fh)}
    plans = [d for d in map(json.loads, open(V2 / "all_documents_metadata_v2.jsonl")) if d["doc_type"] == "PP" and d["project_id"] in selected]
    done = set()
    if LOG.exists():
        done = {e["document_id"] for e in map(json.loads, LOG.read_text().splitlines()) if e.get("http_status") == 200}
    todo = [d for d in sorted(plans, key=lambda d: (d["project_id"], d["document_date"] or "", d["document_id"])) if d["document_id"] not in done]
    print(f"plan versions: {len(plans)}; already fetched: {len(done)}; to fetch: {len(todo)}", flush=True)
    with LOG.open("a") as log:
        for i, d in enumerate(todo, 1):
            rec = {k: d.get(k) for k in KEEP} | {"fetched_at": utcnow()}
            url = d.get("txt_url")
            if not url or url == "None":
                rec |= {"http_status": None, "unavailable_reason": "no txt_url in WDS metadata"}
            else:
                try:
                    r = fetch(url, purpose=f"plan text {d['project_id']} {d['document_id']}")
                    body = r.content
                    ctype = r.headers.get("Content-Type", "")
                    path = TEXT / d["project_id"] / f"{d['document_id']}.txt"
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(body)
                    rec |= {"http_status": r.status_code, "final_url": r.url, "content_type": ctype, "bytes": len(body),
                            "sha256": hashlib.sha256(body).hexdigest(), "path": str(path.relative_to(OUT)),
                            "unavailable_reason": None if body.strip() and "html" not in ctype.lower() else "empty or non-text body"}
                except FetchError as e:
                    rec |= {"http_status": e.status, "unavailable_reason": f"fetch error: {e.message[:200]}"}
            log.write(json.dumps(rec) + "\n")
            log.flush()
            if i % 100 == 0:
                print(f"{i}/{len(todo)}", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
