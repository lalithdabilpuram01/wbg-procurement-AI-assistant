"""HEAD-only availability check for plan PDFs added by the proposed v3 manifest (no PDF bodies are requested).

Reuses m10.head (logged to discovery/evidence/access_log.jsonl, 0.4 s delay). Updates final_url, http_status, content_type
and, where the server reports it, size_bytes / size_basis on the 'added' rows of
plan_version_coverage_v1/PROPOSED_DOCUMENT_DOWNLOAD_MANIFEST_v3.csv; nothing else is changed.
"""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATH = HERE / "plan_version_coverage_v1" / "PROPOSED_DOCUMENT_DOWNLOAD_MANIFEST_v3.csv"
spec = importlib.util.spec_from_file_location("m10", HERE / "m10_document_selection_70.py")
m10 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m10)  # type: ignore[union-attr]


def main() -> None:
    with PATH.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    added = [r for r in rows if r["proposal_change"] == "added" and r["already_local"] != "True"]
    print(f"HEAD checks: {len(added)}", flush=True)
    for i, r in enumerate(added, 1):
        h = m10.head(r["pdf_url"])
        r.update(final_url=h.get("final_url") or "", http_status=h.get("http_status") or "", content_type=h.get("content_type") or "")
        if h.get("content_length"):
            r.update(size_bytes=h["content_length"], size_basis="content_length")
        if i % 50 == 0:
            print(f"{i}/{len(added)}", flush=True)
    with PATH.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("done", flush=True)


if __name__ == "__main__":
    main()
