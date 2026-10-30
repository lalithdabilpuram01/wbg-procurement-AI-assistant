"""Module 1 discovery (READ-ONLY, local files only): template-section anchors per document type and the real WDS
metadata record for each representative PDF (for the sample metadata JSON). Case-insensitive search of each page's text
for the section labels of the World Bank document templates; reports the first physical page where each label appears
outside the table of contents (pages whose text has >= 5 dot-leader lines are treated as TOC pages)."""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "evidence"
D = ROOT / "discovery/samples/documents"
TEMPLATES = {
    "PAD": ["STRATEGIC CONTEXT", "PROJECT DEVELOPMENT OBJECTIVES", "PROJECT DESCRIPTION", "IMPLEMENTATION", "KEY RISKS", "APPRAISAL SUMMARY", "Procurement",
            "Financial Management", "Results Framework", "Economic Analysis"],
    "ISR": ["Key Dates", "Project Development Objectives", "Overall Ratings", "Implementation Status and Key Decisions", "Risks", "Results", "Disbursements",
            "Restructuring History"],
    "ICR": ["PROJECT CONTEXT AND DEVELOPMENT OBJECTIVES", "OUTCOME", "KEY FACTORS THAT AFFECTED IMPLEMENTATION", "BANK PERFORMANCE", "Procurement",
            "LESSONS AND RECOMMENDATIONS", "RESULTS FRAMEWORK", "BORROWER COMMENTS"],
    "ICRR": ["Project Data", "Project Objectives and Components", "Relevance of Objectives", "Efficacy", "Efficiency", "Outcome", "Risk to Development Outcome",
             "Bank Performance", "Quality of Monitoring and Evaluation", "Lessons", "Quality of ICR"],
    "ICR_FR": ["CONTEXTE DU PROJET", "RÉSULTATS", "FACTEURS", "PERFORMANCE DE LA BANQUE", "Passation des marchés", "ENSEIGNEMENTS"],
    "PP": ["General", "Goods and Works", "Selection of Consultants", "Activity Reference", "Procurement Method", "Signed Contract"],
}
DOCS = {"P131049_PAD_17555469.pdf": "PAD", "P145502_PAD_19368603.pdf": "PAD", "P131049_ISR_27391778.pdf": "ISR", "P131049_ICR_32160299.pdf": "ICR",
        "P131049_ICRR_32800471.pdf": "ICRR", "P145502_PP_31837484.pdf": "PP", "P127203_ICRFR_33407968.pdf": "ICR_FR", "P039892_SAR1990_696824.pdf": "PAD"}


def main() -> None:
    meta = {}
    for f in ("wds_v3_cluster_meta.jsonl.gz", "wds_v3_meta_sample_by_type.jsonl.gz"):
        for ln in gzip.open(D / f, "rt"):
            r = json.loads(ln)
            meta.setdefault(str(r.get("id")), {"file": f, "record": r})
    res = {}
    for name, kind in DOCS.items():
        doc = fitz.open(D / "files" / name)
        texts = [pg.get_text("text") for pg in doc]
        toc_pages = {i for i, t in enumerate(texts) if len(re.findall(r"\.{5,}\s*\d+", t)) >= 5}
        found = {}
        for label in TEMPLATES[kind]:
            pat = re.compile(r"(?m)^\s*(?:[IVX]+\.|[A-H]\.|\d+\.)?\s*" + re.escape(label), re.I)
            hits = [i + 1 for i, t in enumerate(texts) if i not in toc_pages and pat.search(t)]
            found[label] = hits[:4]
        docid = name.rsplit("_", 1)[1].split(".")[0]
        m = meta.get(docid)
        res[name] = {"template": kind, "toc_pages": sorted(p + 1 for p in toc_pages), "section_first_pages": found,
                     "wds_metadata_found": bool(m), "wds_metadata_source": m["file"] if m else None,
                     "wds_record": {k: v for k, v in (m["record"].items() if m else []) if k not in ("abstracts", "ml_abstracts")}}
        doc.close()
    (OUT / "template_sections_and_metadata.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    for k, v in res.items():
        print(k, "toc:", v["toc_pages"][:4], "wds:", v["wds_metadata_source"])
        print("   ", {lab: pg for lab, pg in v["section_first_pages"].items()})


if __name__ == "__main__":
    main()
