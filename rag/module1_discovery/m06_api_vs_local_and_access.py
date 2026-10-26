"""Module 1 discovery (READ-ONLY): (a) API document listings (ids/types only) for every project that has local PDFs and a
comparison with the local inventory; (b) public accessibility of PDF links by HTTP HEAD only (no PDF bodies downloaded);
(c) duplicates / versions / multi-volume analysis on the WDS v3 metadata already on disk.

All API and HEAD requests are logged to discovery/evidence/access_log.jsonl (purpose prefix 'module1 m06').
"""
from __future__ import annotations

import gzip
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "discovery"))
from wbdisc.http import _log, fetch, session, utcnow  # noqa: E402

OUT = Path(__file__).resolve().parent / "evidence"
API = "https://search.worldbank.org/api/v3/wds"
FL = "id,docty,docdt,display_title,pdfurl,versiontyp,volnb,totvolnb,repnb,guid,chronical_docm_id,lang,projectid"


def listing(pid: str) -> list[dict]:
    out, os_ = [], 0
    while True:
        j = fetch(API, {"format": "json", "projectid": pid, "rows": 500, "os": os_, "fl": FL}, purpose="module1 m06: id listing by project (metadata only)").json()
        docs = [v for k, v in (j.get("documents") or {}).items() if k != "facets"]
        out += docs
        os_ += 500
        if os_ >= int(j.get("total") or 0) or not docs:
            return out


def head(url: str) -> dict:
    started = time.monotonic()
    try:
        r = session().head(url, allow_redirects=True, timeout=30)
        rec = {"url": url, "status": r.status_code, "final_url": r.url, "redirects": len(r.history), "content_type": r.headers.get("Content-Type"),
               "content_length": r.headers.get("Content-Length")}
    except Exception as e:  # noqa: BLE001
        rec = {"url": url, "status": None, "error": repr(e)[:200]}
    _log({"ts": utcnow(), "method": "HEAD", **rec, "elapsed_s": round(time.monotonic() - started, 3), "purpose": "module1 m06: PDF accessibility HEAD only (no body)"})
    time.sleep(0.5)
    return rec


def main() -> None:
    inv = json.load(open(OUT / "local_pdf_inventory.json"))["documents"]
    local_by_project: dict[str, set] = defaultdict(set)
    for d in inv:
        for p in d["project_ids"]:
            if d["source_doc_id"]:
                local_by_project[p].add(d["source_doc_id"])
    comp, api_docs = {}, {}
    for p in sorted(local_by_project):
        docs = listing(p)
        api_docs[p] = docs
        ids = {str(d.get("id")) for d in docs}
        comp[p] = {"api_total": len(docs), "local_pdfs": len(local_by_project[p]), "local_found_in_api": len(local_by_project[p] & ids),
                   "local_missing_from_api": sorted(local_by_project[p] - ids), "api_by_type": dict(Counter(re.sub(r"\s+", " ", str(d.get("docty") or "")).strip() for d in docs)),
                   "local_coverage_pct_of_api": round(100 * len(local_by_project[p] & ids) / len(docs), 1) if docs else None}
    rng = random.Random(20261024)
    sample = []
    for p in sorted(api_docs):
        cands = [d for d in api_docs[p] if d.get("pdfurl")]
        sample += rng.sample(cands, min(1, len(cands)))
    sample = sample[:20]
    heads = []
    for d in sample:
        u = d["pdfurl"]
        heads.append({"doc_id": d.get("id"), "docty": d.get("docty"), **head(u)})
        if u.startswith("http://"):
            heads.append({"doc_id": d.get("id"), "docty": d.get("docty"), "variant": "https", **head("https://" + u[len("http://"):])})
    v3 = [json.loads(ln) for ln in gzip.open(ROOT / "discovery/samples/documents/wds_v3_cluster_meta.jsonl.gz", "rt")]
    seeded = [r for r in v3]
    vol = Counter((str(r.get("volnb") or ""), str(r.get("totvolnb") or "")) for r in seeded)
    multi_vol = [r for r in seeded if str(r.get("totvolnb") or "1") not in ("", "1")]
    repnb_groups = Counter(r.get("repnb") for r in seeded if r.get("repnb"))
    guid_groups = Counter(r.get("guid") for r in seeded if r.get("guid"))
    title_key = Counter((r["_extract"]["filter_projectid"], re.sub(r"\s+", " ", str(r.get("docty") or "")).strip(), str(r.get("docdt"))[:10],
                         re.sub(r"\s+", " ", str(r.get("display_title") or "")).strip().lower()) for r in seeded)
    isr_seq = Counter((r["_extract"]["filter_projectid"], re.search(r"Sequence No\s*:?\s*(\d+)", str(r.get("display_title") or ""), re.I).group(1))
                      for r in seeded if "Implementation Status" in str(r.get("docty") or "") and re.search(r"Sequence No\s*:?\s*(\d+)", str(r.get("display_title") or ""), re.I))
    versions = {
        "volnb_totvolnb_pairs_top": [[list(k), v] for k, v in vol.most_common(10)], "multi_volume_records": len(multi_vol),
        "multi_volume_types": dict(Counter(re.sub(r"\s+", " ", str(r.get("docty") or "")).strip() for r in multi_vol).most_common(8)),
        "repnb_shared_by_several_records": sum(1 for v in repnb_groups.values() if v > 1), "guid_shared_by_several_records": sum(1 for v in guid_groups.values() if v > 1),
        "same_project_type_date_title_groups": sum(1 for v in title_key.values() if v > 1), "extra_records_in_those_groups": sum(v - 1 for v in title_key.values() if v > 1),
        "isr_sequence_numbers_with_several_records": sum(1 for v in isr_seq.values() if v > 1),
        "versiontyp_values": dict(Counter(re.sub(r"\s+", " ", str(r.get("versiontyp") or "")).strip() or None for r in seeded).most_common(12)),
    }
    res = {"api_vs_local": comp, "pdf_head_checks": heads, "versions_duplicates_volumes_cluster_meta": versions,
           "summary": {"projects_compared": len(comp), "local_pdfs_with_doc_id": sum(len(v) for v in local_by_project.values()),
                       "local_found_in_api": sum(c["local_found_in_api"] for c in comp.values()),
                       "local_missing_from_api": {p: c["local_missing_from_api"] for p, c in comp.items() if c["local_missing_from_api"]},
                       "head_status": dict(Counter(str(h["status"]) for h in heads)), "head_content_types": dict(Counter(str(h.get("content_type")) for h in heads))}}
    (OUT / "api_vs_local_and_access.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res["summary"], indent=1)); print(json.dumps(versions, indent=1))


if __name__ == "__main__":
    main()
