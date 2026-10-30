"""Question -> evidence demo for the retrieval MVP (no LLM; returns cited passages / rows or an explicit insufficient-evidence result).

Usage (from procurement-intelligence/):
  HF_HUB_OFFLINE=1 /opt/anaconda3/bin/python3 rag/retrieval/demo.py "What procurement method is planned for LK-MIHAPCLG-109016-CW-RFB?"
  ... [--strategy C|A] [--method hybrid|bm25|dense] [--k 5] [--json out.json]
Default configuration = the selected MVP strategy: hybrid (BM25 + gte-base, RRF) over strategy-C chunks, tables and plan rows,
with the scope / privacy / exact-reference guard.
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import Corpus, Retriever  # noqa: E402

STATUS_TEXT = {"insufficient_evidence": "INSUFFICIENT EVIDENCE in the pilot corpus", "withheld_personal_data": "WITHHELD: personal data is not returned"}


def render(res: dict) -> str:
    out = [f"Q: {res['query']}", f"status: {res['status']}  ·  {res['latency_ms']} ms"]
    if res["status"] != "ok":
        out.append(f"→ {STATUS_TEXT.get(res['status'], res['status'])}")
        out += [f"  note: {n}" for n in res["notes"]]
        return "\n".join(out)
    for r in res["results"]:
        page = f"p.{r['physical_page_start']}" + (f"-{r['physical_page_end']}" if r["physical_page_end"] != r["physical_page_start"] else "")
        label = f" (printed {r['printed_page_label']})" if r["printed_page_label"] else ""
        out.append(f"\n[{r['rank']}] {r['doc_type']} {r['document_id']} · {r['project_id']} {r['project_name'] or ''} · {page}{label} · {r['unit_type']}"
                   + (" · EXACT REFERENCE MATCH" if r["scores"].get("exact_reference_match") else ""))
        out.append(f"    {r['title']} · document date {r['document_date']} · version {r['document_version_id']}")
        out.append(f"    section: {r['section'] or '-'}" + (f" · table {r['table_id']}" if r["table_id"] else ""))
        if r["unit_type"] == "activity":
            a = r["activity"]
            out.append(f"    plan {r['plan_version_id']} · {a['activity_reference_normalized']} · {a['method_clean']} / {a['market_approach_clean']} · "
                       f"status {a['process_status_clean']} · est {a['estimated_amount_usd']} · actual {a['actual_amount_usd']} · "
                       f"signing planned {a['signed_contract_planned']} actual {a['signed_contract_actual']}")
            for e in r["evidence_links"]:
                out.append(f"    contract link [{e['evidence_status']}, {e['match_class']}]: WB {e['wb_contract_number']} signed {e['date_recorded_signing']} "
                           f"US${e['contract_amount_usd']}" + (f" · DISAGREES on {e['disagreeing_fields']}" if e["disagreeing_fields"] else ""))
        else:
            out.append("    " + "\n    ".join(textwrap.wrap(r["excerpt"][:420].replace("\n", " "), 110)))
        out.append(f"    association: {r['association']} · source: {r['source_url']}")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--strategy", default="C")
    ap.add_argument("--method", default="hybrid")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--json")
    a = ap.parse_args()
    R = Retriever(Corpus(a.strategy))
    res = R.search(a.question, method=a.method, k=a.k, guarded=True)
    print(render(res))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
