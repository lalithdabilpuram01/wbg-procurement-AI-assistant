"""Deep dive (2026-10-23, user request): everything the projects.worldbank.org portal shows for 2 cohort projects.

The portal (projects.worldbank.org/en/projects-operations/project-detail/<P>) renders client-side
from public search APIs, so the same content is fetched from those APIs directly:
  * project record, all fields   search.worldbank.org/api/v3/projects?id=<P>&fl=*
  * procurement notices          search.worldbank.org/api/v2/procnotices?project_id=<P>  (contact fields excluded, as in Phase 1)
  * documents & reports          search.worldbank.org/api/v3/wds?projectid=<P>
  * document files               WDS txturl (text) for procurement-relevant types; PDF for procurement plans
Contract awards (DS00005) are already complete locally and are not re-downloaded.

Scope: 2 cohort projects only. Responses are saved byte-for-byte under raw/<P>/ with an access log and
manifest. Existing files are never overwritten; nothing outside this folder is modified.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
LOG = HERE / "evidence" / "access_log.jsonl"
UA = {"User-Agent": "procurement-intelligence-discovery/0.2 (public-data feasibility study)"}
PROJECTS = ["P178432", "P163870"]
NOTICE_FIELDS = [
    "id", "notice_type", "noticedate", "notice_lang_name", "notice_status", "submission_deadline_date",
    "submission_deadline_time", "project_ctry_name", "project_id", "project_name", "bid_reference_no",
    "bid_description", "procurement_group", "procurement_method_code", "procurement_method_name",
    "contact_organization", "contact_ctry_name", "submission_date", "notice_text",
]
TEXT_TYPES = {
    "Procurement Plan": "PP", "Implementation Status and Results Report": "ISR", "Aide Memoire": "AM",
    "Implementation Completion and Results Report": "ICR", "Implementation Completion Report Review": "ICRR",
    "Project Paper": "PPAPER", "Project Appraisal Document": "PAD", "Minutes": "MIN", "Letter": "LTR",
}
MAX_FILES_PER_PROJECT = 250
manifest: list[dict] = []


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get(url: str, params: dict | None, purpose: str) -> requests.Response:
    for attempt in range(1, 4):
        t0 = time.time()
        try:
            r = requests.get(url, params=params, headers=UA, timeout=120)
        except requests.RequestException as e:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a") as f:
                f.write(json.dumps({"ts": now(), "url": url, "status": None, "error": str(e), "attempt": attempt, "purpose": purpose}) + "\n")
            time.sleep(2 * attempt)
            continue
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps({"ts": now(), "url": r.url, "status": r.status_code, "bytes": len(r.content),
                                "seconds": round(time.time() - t0, 2), "attempt": attempt, "purpose": purpose}) + "\n")
        time.sleep(0.5)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(5 * attempt)
            continue
        return r
    raise SystemExit(f"giving up on {url}")


def save(dest: Path, r: requests.Response, purpose: str) -> Path:
    if dest.exists():
        raise SystemExit(f"refusing to overwrite {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    manifest.append({"file": str(dest.relative_to(HERE)), "url": r.url, "status": r.status_code, "bytes": len(r.content),
                     "sha256": hashlib.sha256(r.content).hexdigest(), "retrieved_utc": now(), "purpose": purpose,
                     "content_type": r.headers.get("Content-Type")})
    return dest


def paged(url: str, params: dict, key: str, dest_prefix: Path, purpose: str, rows: int = 500) -> list[dict]:
    out, offset, page = [], 0, 1
    while True:
        r = get(url, {**params, "format": "json", "rows": rows, "os": offset}, f"{purpose} page {page}")
        r.raise_for_status()
        save(dest_prefix.with_name(f"{dest_prefix.name}_page{page}.json"), r, f"{purpose} page {page}")
        data = r.json()
        payload = data.get(key, {})
        recs = [v for k, v in payload.items() if k != "facets" and isinstance(v, dict)] if isinstance(payload, dict) else list(payload)
        out.extend(recs)
        offset += len(recs)
        if not recs or offset >= int(data.get("total", 0)):
            return out
        page += 1


def main() -> None:
    for pid in PROJECTS:
        base = RAW / pid
        r = get("https://search.worldbank.org/api/v3/projects", {"format": "json", "id": pid, "fl": "*"}, f"project {pid}")
        r.raise_for_status()
        save(base / "project_v3_allfields.json", r, f"project record {pid}")
        notices = paged("https://search.worldbank.org/api/v2/procnotices", {"project_id": pid, "fl": ",".join(NOTICE_FIELDS)},
                        "procnotices", base / "procnotices", f"notices {pid}")
        docs = paged("https://search.worldbank.org/api/v3/wds", {"projectid": pid}, "documents", base / "wds_v3", f"documents {pid}")
        docs = [d for d in docs if pid in str(d.get("projectid", ""))]
        chosen = [d for d in docs if d.get("docty") in TEXT_TYPES][:MAX_FILES_PER_PROJECT]
        print(f"{pid}: notices={len(notices)} documents={len(docs)} relevant={len(chosen)}")
        for d in chosen:
            short = TEXT_TYPES[d["docty"]]
            date = str(d.get("docdt", ""))[:10]
            if d.get("txturl"):
                r = get(d["txturl"], None, f"text {pid} {short} {d.get('id')}")
                if r.ok:
                    save(base / "docs_text" / f"{date}_{short}_{d.get('id')}.txt", r, f"document text {short}")
            if short == "PP" and d.get("pdfurl"):
                r = get(d["pdfurl"], None, f"pdf {pid} PP {d.get('id')}")
                if r.ok and r.content[:5] == b"%PDF-":
                    save(base / "pp_pdf" / f"{date}_PP_{d.get('id')}.pdf", r, "procurement plan PDF")
    (HERE / "raw" / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    print("files:", len(manifest), "bytes:", sum(m["bytes"] for m in manifest))


if __name__ == "__main__":
    sys.exit(main())
