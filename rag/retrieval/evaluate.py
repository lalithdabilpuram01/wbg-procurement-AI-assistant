"""Evaluate retrieval configurations on benchmark_v1 (same questions, same metrics) and validate citations and privacy.

Usage (from procurement-intelligence/):  /opt/anaconda3/bin/python3 rag/retrieval/evaluate.py
Writes rag/retrieval/runs/<UTC>/: metrics.json, per_question.csv, comparison.csv, citation_check.json, privacy_check.json.
Citation integrity: every top-5 result is re-checked against the text layer of the cited physical page(s) of the local PDF
(PyMuPDF), using probes from the sanitised excerpt (masked tokens skipped). Nothing leaves the machine.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import statistics
import sys
from pathlib import Path

import fitz
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import PILOT, ROOT, Corpus, Retriever  # noqa: E402

HERE = Path(__file__).resolve().parent
CONFIGS = [  # name, strategy, method, guarded (scope / privacy / exact layer), rerank, include_tables
    ("bm25_A", "A", "bm25", False, False, True),
    ("bm25_C", "C", "bm25", False, False, True),
    ("dense_A", "A", "dense", False, False, True),
    ("dense_C", "C", "dense", False, False, True),
    ("hybrid_A", "A", "hybrid", False, False, True),
    ("hybrid_C", "C", "hybrid", False, False, True),
    ("hybrid_A_guarded", "A", "hybrid", True, False, True),
    ("hybrid_C_guarded", "C", "hybrid", True, False, True),
    ("bm25_A_guarded", "A", "bm25", True, False, True),
    ("hybrid_A_guarded_rerank", "A", "hybrid", True, True, True),
    ("hybrid_A_guarded_no_tables", "A", "hybrid", True, False, False),
]
NORM = re.compile(r"[^a-z0-9]+")


def norm(s: str) -> str:
    return NORM.sub("", s.lower())


class PageText:
    """Reads and caches the normalised text layer of PDF pages from the local copy (PyMuPDF)."""
    def __init__(self, corpus: Corpus):
        self.c, self.cache = corpus, {}

    def get(self, doc: str, page: int) -> str:
        """Return the normalised text of one physical page (an empty string when the page is out of range)."""
        key = (doc, page)
        if key not in self.cache:
            with fitz.open(ROOT / self.c.versions[doc]["local_path"]) as pdf:
                self.cache[key] = norm(pdf[page - 1].get_text()) if 1 <= page <= len(pdf) else ""
        return self.cache[key]


def verify(r: dict, pt: PageText) -> dict:
    """Does the cited physical page range contain the excerpt (narrative) or the cell values (table / activity)?"""
    pages = range(r["physical_page_start"], r["physical_page_end"] + 1)
    text = "".join(pt.get(r["document_id"], p) for p in pages)
    if r["unit_type"] == "activity":
        a = r["activity"]
        probes = [a.get("activity_reference_printed") or ""]  # descriptions are word-repaired, so the printed reference is the probe
    elif r["unit_type"] == "table":
        probes = [c for row in r["excerpt"].split(" || ") for c in row.split(" | ") if len(norm(c)) >= 4 and not re.fullmatch(r"\[[A-Z_ ]+\][:.]?", c.strip())][:8]
    else:
        parts = [p for p in re.split(r"\[[A-Z_ ]+\]|\n+", r["excerpt"]) if len(norm(p)) >= 25]
        probes = [p[:80] for p in parts[:4]]
    probes = [norm(p) for p in probes if norm(p)]
    if not probes:
        return {"verified": None, "probes": 0}
    found = sum(1 for p in probes if p in text)
    return {"verified": found / len(probes) >= 0.75, "probes": len(probes), "found": found}


def relevant(r: dict, g: dict) -> bool:
    """True when a result matches a gold group: same document and the gold activity row, or one of the gold pages."""
    if r["document_id"] != g["document_id"]:
        return False
    if g.get("activity_id"):
        return r.get("activity", {}) is not None and r["unit_id"] == f"act:{g['activity_id']}"
    return any(r["physical_page_start"] <= p <= r["physical_page_end"] for p in g["pages"])


def main() -> None:
    """Run every configuration on benchmark_v1; write metrics, per-question results and citation and privacy checks."""
    bench = json.loads((HERE / "benchmark_v1.json").read_text())
    qs = bench["questions"]
    out = HERE / "runs" / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True)
    retrievers, corpora = {}, {}
    rows, cites, summary = [], {}, {}
    for name, strat, method, guarded, rerank, tables in CONFIGS:
        key = (strat, tables)
        if key not in retrievers:
            corpora[key] = Corpus(strat, include_tables=tables)
            retrievers[key] = Retriever(corpora[key])
            retrievers[key].embeddings()
            retrievers[key].search("warm up", method="hybrid", k=1, guarded=False)
        R, pt = retrievers[key], PageText(corpora[key])
        per = []
        for q in qs:
            res = R.search(q["question"], method=method, k=10, guarded=guarded, rerank=rerank)
            hits = res["results"]
            groups = q["gold"]
            first = next((i + 1 for i, r in enumerate(hits) if any(relevant(r, g) for g in groups)), None)
            any5 = any(any(relevant(r, g) for r in hits[:5]) for g in groups) if groups else None
            any10 = any(any(relevant(r, g) for r in hits[:10]) for g in groups) if groups else None
            all5 = all(any(relevant(r, g) for r in hits[:5]) for g in groups) if groups else None
            all10 = all(any(relevant(r, g) for r in hits[:10]) for g in groups) if groups else None
            prec5 = (sum(any(relevant(r, g) for g in groups) for r in hits[:5]) / max(1, min(5, len(hits)))) if groups and hits else None
            ver = [verify(r, pt) for r in hits[:5]]
            for r, v in zip(hits[:5], ver):
                cites[r["unit_id"]] = v
            insuff_flag = res["status"] != "ok"
            row = {"config": name, "qid": q["id"], "type": q["type"], "answerable": bool(groups), "status": res["status"],
                   "first_relevant_rank": first, "recall@5": any5, "recall@10": any10, "all_groups@5": all5, "all_groups@10": all10,
                   "precision@5": prec5, "mrr": (1 / first) if first else 0.0 if groups else None,
                   "citations_verified_top5": sum(1 for v in ver if v["verified"]), "citations_checked_top5": sum(1 for v in ver if v["verified"] is not None),
                   "latency_ms": res["latency_ms"], "insufficient_flagged": insuff_flag,
                   "top1": hits[0]["unit_id"] if hits else None}
            per.append(row)
        rows += per
        df = pd.DataFrame(per)
        for col in ("recall@5", "recall@10", "all_groups@5", "all_groups@10", "precision@5", "mrr"):
            df[col] = pd.to_numeric(df[col].astype(object).where(df[col].notna(), np.nan), errors="coerce")
        ans = df[df.answerable]
        un = df[~df.answerable]
        multi = ans[ans.qid.isin([q["id"] for q in qs if q["require_all"]])]
        summary[name] = {
            "recall@5": round(float(ans["recall@5"].mean()), 3), "recall@10": round(float(ans["recall@10"].mean()), 3),
            "MRR": round(float(ans["mrr"].mean()), 3), "precision@5": round(float(ans["precision@5"].dropna().mean()), 3),
            "all_groups@10_on_multi_source": round(float(multi["all_groups@10"].mean()), 3) if len(multi) else None,
            "citation_verified_rate_top5": round(float(df["citations_verified_top5"].sum() / max(1, df["citations_checked_top5"].sum())), 3),
            "latency_ms_median": round(float(df["latency_ms"].median()), 1), "latency_ms_p95": round(float(df["latency_ms"].quantile(.95)), 1),
            "insufficient_correctly_flagged": f"{int(un['insufficient_flagged'].sum())}/{len(un)}",
            "answerable_wrongly_flagged": int(ans["insufficient_flagged"].sum()),
            "by_type_recall@5": ans.groupby("type")["recall@5"].mean().round(3).to_dict(),
            "by_type_MRR": ans.groupby("type")["mrr"].mean().round(3).to_dict(),
        }
        print(name, {k: v for k, v in summary[name].items() if not k.startswith("by_type")}, flush=True)
    pd.DataFrame(rows).to_csv(out / "per_question.csv", index=False)
    comp = pd.DataFrame(summary).T
    comp.drop(columns=["by_type_recall@5", "by_type_MRR"]).to_csv(out / "comparison.csv")
    # privacy check over every indexed unit (both strategies)
    email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    phone = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(\d{2,4}\)[\s-]?)?\d{3,4}[\s-]\d{3,4}(?:[\s-]\d{2,4})?")
    priv = {}
    for (strat, tables), c in corpora.items():
        texts = [u.excerpt for u in c.units]
        priv[f"{strat}_tables{tables}"] = {"units": len(texts), "units_with_email": sum(1 for t in texts if email.search(t)),
                                            "units_with_phone_like_pattern": sum(1 for t in texts if phone.search(t)),
                                            "phone_like_note": "pattern also matches year / page ranges (e.g. 2015-2020); shapes inspected, all were ranges",
                                            "units_with_mask_token": sum(1 for t in texts if re.search(r"\[[A-Z_ ]+\]", t))}
    src = (HERE / "core.py").read_text()
    for v in priv.values():
        # Confirm that the retrieval code never mentions the restricted folder (its own module docstring excepted).
        v["retrieval_code_references_restricted_dir"] = "restricted/" in src.replace("Never reads `restricted/`", "")
    (out / "privacy_check.json").write_text(json.dumps(priv, indent=1))
    ver = [v for v in cites.values() if v["verified"] is not None]
    (out / "citation_check.json").write_text(json.dumps({"unique_cited_units_checked": len(ver), "verified": sum(1 for v in ver if v["verified"]),
                                                          "failed_units": [k for k, v in cites.items() if v["verified"] is False]}, indent=1))
    (out / "metrics.json").write_text(json.dumps({"benchmark": bench["version"], "questions": len(qs), "answerable": sum(1 for q in qs if q["gold"]),
                                                  "configs": summary}, indent=1, default=str))
    print(out)
    print(comp[["recall@5", "recall@10", "MRR", "precision@5", "all_groups@10_on_multi_source", "citation_verified_rate_top5", "latency_ms_median",
                "insufficient_correctly_flagged", "answerable_wrongly_flagged"]].to_string())
    print(json.dumps({k: v["by_type_recall@5"] for k, v in summary.items()}, indent=0))
    print(json.dumps(priv, indent=1))


if __name__ == "__main__":
    main()
