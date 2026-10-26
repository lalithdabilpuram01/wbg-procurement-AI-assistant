"""Benchmark-authoring helper: regex search over SANITISED pilot_v2 units (strategy-A chunks, table rows, plan activities).
Never reads restricted/. Usage: python rag/retrieval/explore.py '<regex>' [doc_id] [width]"""
import json, re, sys
from pathlib import Path
P = Path(__file__).resolve().parents[1] / "corpus_v3" / "processed" / "pilot_v2"
rx = re.compile(sys.argv[1], re.I)
doc = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "-" else None
w = int(sys.argv[3]) if len(sys.argv) > 3 else 220
def rows(f): return (json.loads(l) for l in open(P / f))
for c in rows("strategies/A/chunks.jsonl"):
    if doc and c["document_id"] != doc: continue
    for m in rx.finditer(c["text"]):
        print(f"[A] {c['chunk_id']} p{c['physical_page_start']}-{c['physical_page_end']} | {c['section_path'][:50]} | …{c['text'][max(0,m.start()-w//2):m.end()+w//2]}…".replace("\n", " ")); break
for r in rows("shared/table_rows.jsonl"):
    if doc and r["document_id"] != doc: continue
    t = " | ".join(x for x in r["cells_clean"] if x)
    if rx.search(t): print(f"[ROW] {r['table_id']} r{r['row_index']} p{r['page']} | {t[:w]}")
for a in rows("shared/plan_activities.jsonl"):
    if doc and a["document_id"] != doc: continue
    if rx.search(a["row_text_for_embedding"]): print(f"[ACT] {a['activity_id']} p{a['pdf_page']} | {a['row_text_for_embedding'][:w]}")
