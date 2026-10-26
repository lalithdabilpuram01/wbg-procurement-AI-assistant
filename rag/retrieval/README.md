# Retrieval MVP (Module 1)

Evidence retrieval with citations over the sanitised `pilot_v2` outputs (10 PDFs). No LLM; no services.

| File | Purpose |
|---|---|
| `core.py` | Units, BM25 / dense (gte-base, offline) / hybrid RRF, optional rerank, guard (scope, privacy, exact reference lookup), citation records |
| `benchmark_v1.json`, `BENCHMARK_V1.md` | 31-question benchmark with verified evidence (v1.1) |
| `evaluate.py` | Runs all configurations on the benchmark; citation re-check against PDF text; privacy scan → `runs/<UTC>/` |
| `demo.py` | Question → cited evidence (or insufficient / withheld); outputs in `demo_outputs/` |
| `explore.py` | Benchmark-authoring search over sanitised units |
| `index_cache/` | Cached embedding matrices (rebuilt automatically if units change) |
| `RAG_RETRIEVAL_MVP_REPORT.md`, `CITATION_AND_PRIVACY_VALIDATION.md` | Results, strategy choice, scaling recommendation |

```bash
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
/opt/anaconda3/bin/python3 rag/retrieval/evaluate.py
/opt/anaconda3/bin/python3 rag/retrieval/demo.py "What procurement method is planned for LK-MIHAPCLG-109016-CW-RFB?"
```
Runs `runs/20261025T163414Z` and `runs/20261025T164121Z` are superseded (benchmark v1; citation-probe fix). Final: `runs/20261025T164420Z`.
