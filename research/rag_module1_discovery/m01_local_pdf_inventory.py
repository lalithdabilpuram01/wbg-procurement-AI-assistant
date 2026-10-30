"""Module 1 discovery (READ-ONLY, local files only): inventory of every PDF already in the repository.

No network calls, no downloads, no Databricks access. For each local PDF records: folder group, project id(s) and document
type parsed from the file name, size, SHA-256 (and whether it equals the download manifest), page count, PDF producer /
creator / dates, outline (bookmarks) presence, text-layer characters per page, likely-scanned pages (< 80 text chars and
at least one image), empty pages, page-1 "Public Disclosure Authorized" watermark, a stop-word language guess, the
download URL and retrieval time from the manifest, and membership of the 2,000-project cohort.

Output: rag/module1_discovery/evidence/local_pdf_inventory.json (one record per PDF) + summary counts.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "evidence"
MANIFESTS = [ROOT / "discovery" / "samples" / "MANIFEST.jsonl"]
COHORT = ROOT / "cohort" / "poc_cohort_2000_manifest.csv"
TYPE_ALIASES = {"PAD": "PAD", "PADNEW": "PAD", "ISR": "ISR", "ICR": "ICR", "ICRFR": "ICR", "ICRES": "ICR", "ICRR": "ICRR", "PP": "Procurement Plan",
                "PPAR": "PPAR", "SAR1990": "SAR", "MIN": "Minutes"}
STOP = {"en": {"the", "and", "of", "to", "in", "for", "is", "project"}, "fr": {"le", "la", "les", "des", "et", "du", "projet", "pour"},
        "es": {"el", "los", "las", "del", "y", "proyecto", "para", "con"}, "pt": {"os", "das", "dos", "e", "projeto", "para", "com", "não"}}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_index() -> dict[str, dict]:
    idx = {}
    for m in MANIFESTS:
        for ln in m.read_text().splitlines():
            r = json.loads(ln)
            if str(r.get("file", "")).endswith(".pdf"):
                url = re.search(r"url=(\S+)", r.get("notes") or "")
                idx[Path(r["file"]).name] = {"sha256": r.get("sha256"), "url": url.group(1) if url else None, "retrieved_at": r.get("extracted_at_utc")}
    return idx


def parse_name(p: Path) -> dict:
    stem = p.stem
    if p.parent.name == "pp_pdf":  # deep dive: <date>_PP_<docid>, project = raw/<P>/
        m = re.match(r"(\d{4}-\d{2}-\d{2})_PP_(\d+)$", stem)
        return {"project_ids": [p.parent.parent.name], "doc_type": "Procurement Plan", "source_doc_id": m.group(2) if m else None,
                "doc_date_from_name": m.group(1) if m else None}
    m = re.match(r"((?:P\d{6},?)+)_([A-Z0-9]+)_(\d+)$", stem)
    if m:
        return {"project_ids": m.group(1).split(","), "doc_type": TYPE_ALIASES.get(m.group(2), m.group(2)), "source_doc_id": m.group(3), "doc_date_from_name": None}
    return {"project_ids": [], "doc_type": "Guidance" if stem.startswith("GUIDANCE") else "Other", "source_doc_id": None, "doc_date_from_name": None}


def lang_guess(text: str) -> str | None:
    words = Counter(w for w in re.findall(r"[a-zà-ÿ]+", text.lower()))
    scores = {lg: sum(words[w] for w in ws) for lg, ws in STOP.items()}
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] >= 20 else None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with COHORT.open(newline="", encoding="utf-8") as fh:
        cohort = {r["project_id"] for r in csv.DictReader(fh)}
    man = manifest_index()
    pdfs = sorted((ROOT / "discovery").rglob("*.pdf"))
    recs = []
    for p in pdfs:
        meta = parse_name(p)
        doc = fitz.open(p)
        chars, scanned, empty, images = [], 0, 0, 0
        for page in doc:
            t = page.get_text("text")
            n_img = len(page.get_images(full=False))
            images += n_img
            chars.append(len(t.strip()))
            if len(t.strip()) < 80 and n_img > 0:
                scanned += 1
            elif len(t.strip()) == 0:
                empty += 1
        first = doc[0].get_text("text") if doc.page_count else ""
        sample = " ".join(doc[i].get_text("text") for i in range(min(5, doc.page_count)))
        md = doc.metadata or {}
        m = man.get(p.name)
        digest = sha256(p)
        recs.append({
            "path": str(p.relative_to(ROOT)), "group": str(p.parent.relative_to(ROOT / "discovery")), **meta,
            "in_cohort": [pid in cohort for pid in meta["project_ids"]], "bytes": p.stat().st_size, "sha256": digest,
            "manifest_sha256_match": (m["sha256"] == digest) if m else None, "source_url": m["url"] if m else None, "retrieved_at": m["retrieved_at"] if m else None,
            "pages": doc.page_count, "pdf_producer": md.get("producer") or None, "pdf_creator": md.get("creator") or None,
            "pdf_creation_date": md.get("creationDate") or None, "pdf_mod_date": md.get("modDate") or None, "pdf_title": md.get("title") or None,
            "has_outline": bool(doc.get_toc()), "text_chars_total": sum(chars), "text_chars_median_per_page": sorted(chars)[len(chars) // 2] if chars else 0,
            "likely_scanned_pages": scanned, "empty_pages": empty, "images": images,
            "disclosure_watermark_p1": first.count("Public Disclosure Authorized"), "language_guess": lang_guess(sample),
            "page_size_pt": [round(doc[0].rect.width), round(doc[0].rect.height)] if doc.page_count else None,
        })
        doc.close()
    by_type = Counter(r["doc_type"] for r in recs)
    projects = Counter(pid for r in recs for pid in r["project_ids"])
    summary = {
        "pdfs": len(recs), "bytes": sum(r["bytes"] for r in recs), "pages": sum(r["pages"] for r in recs), "by_type": dict(by_type),
        "by_group": dict(Counter(r["group"] for r in recs)), "unique_projects": len(projects),
        "unique_projects_in_cohort": len({p for p in projects if p in cohort}),
        "pdfs_per_project": dict(sorted(projects.items(), key=lambda x: -x[1])),
        "pdfs_per_project_distribution": dict(sorted(Counter(projects.values()).items())),
        "pages_by_type": {t: sorted(r["pages"] for r in recs if r["doc_type"] == t) for t in by_type},
        "docs_ge_100_pages": [r["path"] for r in recs if r["pages"] >= 100],
        "docs_with_scanned_pages": {r["path"]: r["likely_scanned_pages"] for r in recs if r["likely_scanned_pages"]},
        "docs_with_outline": sum(r["has_outline"] for r in recs), "manifest_matched": sum(r["manifest_sha256_match"] is not None for r in recs),
        "manifest_sha_mismatch": [r["path"] for r in recs if r["manifest_sha256_match"] is False],
        "producers": dict(Counter(r["pdf_producer"] for r in recs).most_common(15)), "language_guess": dict(Counter(r["language_guess"] for r in recs)),
        "multi_project_docs": [r["path"] for r in recs if len(r["project_ids"]) > 1],
    }
    (OUT / "local_pdf_inventory.json").write_text(json.dumps({"summary": summary, "documents": recs}, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k not in ("pdfs_per_project", "pages_by_type")}, indent=1))


if __name__ == "__main__":
    main()
