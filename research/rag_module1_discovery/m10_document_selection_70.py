"""Document inventory and recommended download list for the 70 selected projects (READ-ONLY planning).

For each project: full metadata listing from the official Documents & Reports API (search.worldbank.org/api/v3/wds,
projectid=<P>, metadata fields only), then:
  * duplicates: same (project, type, date, normalised title) groups; ISR re-uploads (same sequence number);
  * versions: multiple PADs (original vs additional financing), procurement-plan versions (one per STEP revision);
  * recommended set (rules below), each with role and reason;
  * availability and size of every recommended PDF by HTTP HEAD only (no body downloaded); size is estimated from
    per-type local medians when the server sends no Content-Length;
  * missing metadata per document (docdt, lang, pdfurl, versiontyp, repnb).

Recommended set per project:
  PAD   1  original appraisal: earliest docdt among PADs whose title does not mention additional financing; 'Final' preferred
  ISR   2-3 distinct sequences after de-duplication: first and latest; plus the middle one when >= 5 distinct ISRs
  ICR   all distinct (normally 1)          ICRR  all distinct (normally 1)
  PP    earliest and latest version (temporal comparison); latest only when the project has a single version
Requests are logged to discovery/evidence/access_log.jsonl.
"""
from __future__ import annotations

import csv
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "discovery"))
from wbdisc.http import _log, fetch, session, utcnow  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "selection_70"
API = "https://search.worldbank.org/api/v3/wds"
FL = ("id,docty,majdocty,docdt,disclosure_date,datestored,last_modified_date,display_title,pdfurl,txturl,url,versiontyp,volnb,totvolnb,repnb,"
      "guid,chronical_docm_id,lang,projectid,count")
TYPES = {"Project Appraisal Document": "PAD", "Implementation Status and Results Report": "ISR", "Implementation Completion and Results Report": "ICR",
         "Implementation Completion Report Review": "ICRR", "Procurement Plan": "PP"}
SEQ = re.compile(r"Sequence\s*(?:No|Number)?\.?\s*:?\s*(\d+)", re.I)


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def listing(pid: str) -> list[dict]:
    out, os_ = [], 0
    while True:
        j = fetch(API, {"format": "json", "projectid": pid, "rows": 500, "os": os_, "fl": FL}, purpose="module1 m10: document listing for 70-project plan (metadata only)").json()
        docs = [v for k, v in (j.get("documents") or {}).items() if k != "facets"]
        out += docs
        os_ += 500
        if os_ >= int(j.get("total") or 0) or not docs:
            return out


def head(url: str) -> dict:
    t0 = time.monotonic()
    try:
        r = session().head(url, allow_redirects=True, timeout=30)
        rec = {"http_status": r.status_code, "final_url": r.url, "content_type": r.headers.get("Content-Type"), "content_length": r.headers.get("Content-Length")}
    except Exception as e:  # noqa: BLE001
        rec = {"http_status": None, "error": repr(e)[:160]}
    _log({"ts": utcnow(), "method": "HEAD", "url": url, **rec, "elapsed_s": round(time.monotonic() - t0, 3), "purpose": "module1 m10: availability/size HEAD only"})
    time.sleep(0.4)
    return rec


def doc_row(d: dict, pid: str) -> dict:
    t = TYPES.get(norm(d.get("docty")), "Other")
    title = norm(d.get("display_title"))
    seq = SEQ.search(title)
    return {"project_id": pid, "document_id": str(d.get("id")), "doc_type": t, "docty": norm(d.get("docty")), "title": title,
            "document_date": norm(d.get("docdt"))[:10] or None, "disclosure_date": norm(d.get("disclosure_date"))[:10] or None,
            "stored_date": norm(d.get("datestored"))[:10] or None, "last_modified_date": norm(d.get("last_modified_date"))[:10] or None,
            "version_type": norm(d.get("versiontyp")) or None, "volume": norm(d.get("volnb")) or None, "total_volumes": norm(d.get("totvolnb")) or None,
            "report_number": norm(d.get("repnb")) or None, "guid": norm(d.get("guid")) or None, "language": norm(d.get("lang")) or None,
            "project_ids_listed": norm(d.get("projectid")), "pdf_url": d.get("pdfurl"), "txt_url": d.get("txturl"), "isr_sequence": int(seq.group(1)) if seq else None}


def main() -> None:
    with (OUT / "PROJECT_SELECTION_MANIFEST.csv").open(newline="", encoding="utf-8") as fh:
        projects = [r["project_id"] for r in csv.DictReader(fh)]
    inv = json.load(open(HERE / "evidence" / "local_pdf_inventory.json"))["documents"]
    med_bytes = {}
    for t, key in (("PAD", "PAD"), ("ISR", "ISR"), ("ICR", "ICR"), ("ICRR", "ICRR"), ("PP", "Procurement Plan")):
        v = [x["bytes"] for x in inv if x["doc_type"] == key and x["bytes"] < 50_000_000]
        med_bytes[t] = int(statistics.median(v)) if v else None
    all_rows, selected, issues = [], [], []
    for pid in projects:
        docs = [doc_row(d, pid) for d in listing(pid)]
        all_rows += docs
        by = defaultdict(list)
        for d in docs:
            by[d["doc_type"]].append(d)
        # exact-duplicate groups
        grp = defaultdict(list)
        for d in docs:
            grp[(d["doc_type"], d["document_date"], d["title"].lower())].append(d)
        for k, v in grp.items():
            if len(v) > 1 and k[0] != "Other":
                for d in sorted(v, key=lambda x: x["stored_date"] or "")[:-1]:
                    issues.append({"project_id": pid, "document_id": d["document_id"], "doc_type": d["doc_type"], "issue": "duplicate_record (same type, date, title)",
                                   "kept_document_id": sorted(v, key=lambda x: x["stored_date"] or "")[-1]["document_id"]})
        dup_ids = {i["document_id"] for i in issues if i["project_id"] == pid}
        for d in docs:
            if d["doc_type"] != "Other":
                miss = [f for f in ("document_date", "language", "pdf_url", "version_type", "report_number") if not d[f]]
                if miss:
                    issues.append({"project_id": pid, "document_id": d["document_id"], "doc_type": d["doc_type"], "issue": "missing_metadata: " + ",".join(miss)})

        def pick(d, role, reason):
            selected.append({**d, "role": role, "selection_reason": reason})

        pads = sorted([d for d in by["PAD"] if d["document_id"] not in dup_ids and d["pdf_url"]], key=lambda d: d["document_date"] or "9999")
        orig = [d for d in pads if not re.search(r"additional financing|\bAF\b", d["title"], re.I)] or pads
        if orig:
            # translations are separate WDS records: prefer English, then 'Final', then earliest date
            rank = sorted(orig, key=lambda d: (d["language"] != "English", (d["version_type"] or "").lower() != "final", d["document_date"] or "9999"))
            primary = rank[0]
            pick(primary, "PAD_original", "original appraisal (English preferred, Final preferred, earliest non-AF PAD)")
            for d in pads:
                if d["document_id"] == primary["document_id"]:
                    continue
                if d["language"] and d["language"] != "English" and d["document_date"] and primary["document_date"] and abs(
                        int(d["document_date"][:4]) - int(primary["document_date"][:4])) <= 1 and d in orig:
                    pick(d, "PAD_translation", f"{d['language']} version of the original PAD (multilingual evaluation)")
                    issues.append({"project_id": pid, "document_id": d["document_id"], "doc_type": "PAD", "issue": "translation_of_primary_PAD (optional)",
                                   "kept_document_id": primary["document_id"]})
                else:
                    issues.append({"project_id": pid, "document_id": d["document_id"], "doc_type": "PAD", "issue": "additional_PAD_not_selected (AF or other version)"})
        isr = defaultdict(list)
        for d in by["ISR"]:
            if d["document_id"] not in dup_ids and d["pdf_url"]:
                isr[d["isr_sequence"] if d["isr_sequence"] is not None else ("d", d["document_date"])].append(d)
        uniq = []
        for k, v in isr.items():
            v = sorted(v, key=lambda x: x["stored_date"] or "")
            uniq.append(v[-1])
            for d in v[:-1]:
                issues.append({"project_id": pid, "document_id": d["document_id"], "doc_type": "ISR", "issue": f"isr_reupload_superseded (sequence {k})", "kept_document_id": v[-1]["document_id"]})
        uniq.sort(key=lambda d: (d["document_date"] or "", d["isr_sequence"] or 0))
        if uniq:
            picks = [("ISR_first", uniq[0]), ("ISR_latest", uniq[-1])] if len(uniq) >= 2 else [("ISR_only", uniq[0])]
            if len(uniq) >= 5:
                picks.insert(1, ("ISR_middle", uniq[len(uniq) // 2]))
            for role, d in picks:
                pick(d, role, f"{len(uniq)} distinct ISRs; {role.split('_')[1]} stage")
        for t in ("ICR", "ICRR"):
            for d in sorted([d for d in by[t] if d["document_id"] not in dup_ids and d["pdf_url"]], key=lambda d: d["document_date"] or ""):
                pick(d, t, "completion evidence" if t == "ICR" else "IEG independent validation")
        pps = sorted([d for d in by["PP"] if d["document_id"] not in dup_ids and d["pdf_url"]], key=lambda d: (d["document_date"] or "", d["stored_date"] or ""))
        if pps:
            pick(pps[-1], "PP_latest", f"{len(pps)} plan versions; latest")
            if len(pps) > 1:
                pick(pps[0], "PP_earliest", "earliest plan version (temporal comparison)")
        for t in ("PAD", "ISR", "PP", "ICR", "ICRR"):
            for d in by[t]:
                if not d["pdf_url"]:
                    issues.append({"project_id": pid, "document_id": d["document_id"], "doc_type": t, "issue": "no_pdf_url"})
    evidence = set(json.load(open(OUT / "evidence_projects.json"))["evidence_projects"])
    for s in selected:
        core = s["role"] in ("PAD_original", "ISR_first", "ISR_latest", "ICR", "ICRR", "PP_latest") or (s["role"] == "PP_earliest" and s["project_id"] in evidence)
        s["tier"] = 1 if core else 2
    for s in selected:
        h = head(s["pdf_url"])
        s.update(h)
        cl = h.get("content_length")
        s["size_bytes"] = int(cl) if cl and str(cl).isdigit() else None
        s["size_estimated_bytes"] = s["size_bytes"] if s["size_bytes"] else med_bytes.get(s["doc_type"])
        s["size_basis"] = "content_length" if s["size_bytes"] else "local_type_median"
        if h.get("http_status") != 200 or "pdf" not in str(h.get("content_type") or ""):
            issues.append({"project_id": s["project_id"], "document_id": s["document_id"], "doc_type": s["doc_type"], "issue": f"pdf_unavailable_or_not_pdf: {h.get('http_status')} {h.get('content_type')}"})
    cols = ["tier", "project_id", "document_id", "doc_type", "role", "selection_reason", "title", "document_date", "disclosure_date", "stored_date", "version_type",
            "report_number", "isr_sequence", "language", "volume", "total_volumes", "guid", "pdf_url", "final_url", "http_status", "content_type", "size_bytes",
            "size_estimated_bytes", "size_basis", "project_ids_listed", "txt_url"]
    with (OUT / "DOCUMENT_DOWNLOAD_MANIFEST.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(selected)
    icols = ["project_id", "document_id", "doc_type", "issue", "kept_document_id"]
    with (OUT / "DOCUMENT_ISSUES.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=icols, extrasaction="ignore")
        w.writeheader()
        w.writerows(issues)
    inv_rows = []
    for pid in projects:
        a = Counter(d["doc_type"] for d in all_rows if d["project_id"] == pid)
        s = Counter(d["doc_type"] for d in selected if d["project_id"] == pid)
        inv_rows.append({"project_id": pid, **{f"available_{t}": a[t] for t in ("PAD", "ISR", "ICR", "ICRR", "PP", "Other")},
                         **{f"selected_{t}": s[t] for t in ("PAD", "ISR", "ICR", "ICRR", "PP")}, "available_total": sum(a.values()), "selected_total": sum(s.values())})
    with (OUT / "DOCUMENT_INVENTORY_BY_PROJECT.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(inv_rows[0]))
        w.writeheader()
        w.writerows(inv_rows)
    (OUT / "all_documents_metadata_70_projects.jsonl").write_text("\n".join(json.dumps(d) for d in all_rows) + "\n")
    summary = {
        "projects": len(projects), "documents_available_all_types": len(all_rows),
        "available_by_type": dict(Counter(d["doc_type"] for d in all_rows)), "selected_total": len(selected),
        "selected_by_type": dict(Counter(d["doc_type"] for d in selected)), "selected_by_role": dict(Counter(d["role"] for d in selected)),
        "tier_counts": dict(Counter(d["tier"] for d in selected)),
        "tier_est_bytes": {t: sum(d["size_estimated_bytes"] or 0 for d in selected if d["tier"] == t) for t in (1, 2)},
        "minimal_301_option": sum(1 for d in selected if d["role"] in ("PAD_original", "ISR_first", "ISR_latest", "ICR", "ICRR")),
        "head_status": dict(Counter(str(s.get("http_status")) for s in selected)), "with_content_length": sum(1 for s in selected if s["size_bytes"]),
        "size_bytes_known": sum(s["size_bytes"] or 0 for s in selected), "size_bytes_estimated_total": sum(s["size_estimated_bytes"] or 0 for s in selected),
        "local_type_median_bytes": med_bytes, "issues_by_type": dict(Counter(i["issue"].split(":")[0].split(" (")[0] for i in issues)),
        "languages_selected": dict(Counter(s["language"] for s in selected)),
        "projects_with_pad": sum(1 for r in inv_rows if r["selected_PAD"]), "projects_with_2plus_isr": sum(1 for r in inv_rows if r["selected_ISR"] >= 2),
        "projects_with_icr": sum(1 for r in inv_rows if r["selected_ICR"]), "projects_with_icrr": sum(1 for r in inv_rows if r["selected_ICRR"]),
    }
    (OUT / "document_selection_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
