# Module 1: RAG Retrieval MVP (10-PDF pilot)

**Date:** 2026-10-25 · **Scope:** retrieval and citation only (no LLM answer generation) over the sanitised `pilot_v2` outputs of the 10 pilot PDFs.

**Unchanged:** ML models, Databricks, PDFs, ingestion code and the `restricted/` outputs (never read). No services were installed.

## 1. What exists and was reused (Task 1)

| Artefact (`rag/corpus_v3/processed/pilot_v2/`) | Verified content |
|---|---|
| `shared/documents.jsonl`, `document_versions.jsonl` | 10 documents (2 PADs, 3 procurement plans, 2 ISRs, 2 ICRs, 1 ICR Review; projects P148850, P132833, P175325, P163452). WDS document and disclosure dates, `final_url`, version ID = document + PDF SHA, local path |
| `shared/pages.jsonl` | 373 pages. Printed page labels detected on 328; the rest are cited by physical page only |
| `shared/sections.jsonl` | 234 sections with heading paths and physical and printed page ranges |
| `strategies/A`, `strategies/C` | A: 443 section-aware chunks. C: 551 children + 175 parents. Every chunk carries exact `source_spans` (page and character offsets) and its section path |
| `shared/tables.jsonl`, `table_rows.jsonl` | 220 tables, 3,347 cleaned rows. Header and value pairs are often split across consecutive rows |
| `shared/plan_activities.jsonl`, `plan_versions.jsonl` | 236 typed plan rows (reference, method, market approach, status, amounts, stage dates) in 3 plan streams. Each stream is a separate version line (`project:agency:revision-date`) and is never merged with another |
| `shared/evidence_links.jsonl` | 243 plan-row-to-DS00005 links: 55 exact-date supported, 6 amount verified, 38 candidate, 3 conflicting, 141 unmatched |
| Sanitisation | Names and contacts are masked in shared outputs. 0 e-mails in any retrievable unit. Phone-like hits are all year or page ranges (§ privacy report) |

## 2. Retrieval system (Tasks 2 and 5)

**Code:** `rag/retrieval/core.py`. It uses only installed packages (rank_bm25, sentence-transformers, numpy) and models already in the local cache: `Alibaba-NLP/gte-base-en-v1.5` for embeddings and `cross-encoder/ms-marco-MiniLM-L-6-v2` for reranking, both offline.

**PostgreSQL / pgvector:** not needed. The pilot index has 2.3–2.4k units, and in-memory NumPy plus a cached embedding matrix (about 7 MB) is enough. PostgreSQL / pgvector are not installed, and installing them was not required.

**Units:**

| Unit type | Content |
|---|---|
| Narrative chunks | Strategy A, or C children with the parent kept for context |
| Table windows | 3 consecutive cleaned rows, so headers stay attached to values |
| Plan activities | Typed rows, with their evidence links attached |

Each unit gets a short contextual header (document type, project, title, section).

**Methods:**

| Method | Description |
|---|---|
| BM25 | Okapi, reference-preserving tokenizer |
| Dense | gte-base, cosine |
| Hybrid | Reciprocal-rank fusion, k = 60 |
| Optional | Cross-encoder rerank of the top 30 |

**Guarded layer** (applied before ranking):
- **Scope:** a project ID or country outside the corpus → *insufficient evidence*. Named in-scope projects or countries filter the units.
- **Privacy:** requests for contact details → *withheld*.
- **Exact lookup:** STEP and legacy plan references and WB contract numbers are matched against **typed** plan rows and links first. An unknown reference → *no record*.

Identifiers, dates and amounts therefore come from typed records, not from similarity.

**Citation record** (every result):
- Identity: project ID and title, country, document ID, version ID and SHA, document type and role, title, document and disclosure dates.
- Location: physical page range; printed page label with its status ("not detected" → cite the physical page); section path or table ID.
- Content: sanitised excerpt, source URL and local path.
- **Association level:** "project-level document context" for narrative, or "procurement-plan row (typed record)". Plan rows also list their **evidence links with status** (candidate, conflicting, …) and any disagreeing fields, shown unresolved.
- Plan rows keep their `plan_stream_id` and `plan_version_id`.

## 3. Benchmark (Task 3)

**File:** `rag/retrieval/benchmark_v1.json` (v1.1; readable summary in `BENCHMARK_V1.md`).

**31 questions:**

| Type | Count |
|---|---:|
| Project facts | 4 |
| Plan activities | 4 |
| Dates and milestones | 4 |
| Contract lookups | 2 |
| Tables | 4 |
| Narrative | 5 |
| Conflicting / version | 4 |
| Insufficient evidence | 4 |

- **Answerable:** 27, with gold document and page groups or exact plan-row IDs. All answers were read from the sanitised text before evaluation.
- **Conflict questions:** 3 need both sources: PAD vs ICR closing dates; ICR "Satisfactory" vs IEG ICR Review "Moderately Satisfactory"; PAD US$370m vs the latest ISR's revised and cancelled amounts.
- **Amendment v1 → v1.1:** the Q26 gold was extended to ICR Review p22 (its ratings table states both ratings). This was found while inspecting demo output and applies to all configurations.

## 4. Results (Task 4)

Run `rag/retrieval/runs/20261025T164420Z/` (`comparison.csv`, `per_question.csv`, `metrics.json`). All configurations use the same 31 questions.

| Configuration | R@5 | R@10 | MRR | P@5 | All sources in top 10 (3 conflict questions) | Citations verified (top 5) | Median latency | Insufficient flagged |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bm25_A | 0.70 | 0.81 | 0.54 | 0.21 | 0/3 | 98.0% | 4 ms | 0/4 |
| bm25_C | 0.78 | 0.81 | 0.59 | 0.23 | 0/3 | 97.4% | 4 ms | 0/4 |
| dense_A | 0.52 | 0.63 | 0.40 | 0.16 | 1/3 | 100% | 80 ms | 0/4 |
| dense_C | 0.44 | 0.59 | 0.35 | 0.13 | 1/3 | 100% | 82 ms | 0/4 |
| hybrid_A | 0.70 | 0.85 | 0.55 | 0.22 | 2/3 | 98.7% | 85 ms | 0/4 |
| hybrid_C | 0.78 | 0.85 | 0.59 | 0.24 | 2/3 | 98.7% | 86 ms | 0/4 |
| **bm25_A + guard** | 0.85 | 0.93 | 0.72 | 0.25 | 1/3 | 97.8% | **4 ms** | 3/4 |
| hybrid_A + guard | 0.81 | **0.96** | 0.76 | 0.23 | 2/3 | 98.5% | 86 ms | 3/4 |
| **hybrid_C + guard** | **0.89** | **0.96** | **0.78** | 0.25 | 2/3 | 98.5% | 82 ms | 3/4 |
| hybrid_A + guard + rerank | 0.85 | 0.93 | 0.75 | 0.29 | 2/3 | 97.1% | 687 ms | 3/4 |
| hybrid_A + guard, no tables | 0.85 | 0.93 | 0.75 | 0.30 | 1/3 | 97.8% | 77 ms | 3/4 |

**Recall@5 by question type (hybrid_C + guard):**

| Type | Recall@5 |
|---|---:|
| Plan activity | 1.0 |
| Contract lookup | 1.0 |
| Dates and milestones | 1.0 |
| Conflicting | 1.0 |
| Narrative | 0.8 |
| Project fact | 0.75 |
| Table | 0.75 |

MRR by type: narrative 0.51, table 0.66, project fact 0.78.

**Findings:**
1. **The guard layer is the largest single gain:** hybrid_A R@5 0.70 → 0.81, MRR 0.55 → 0.76. Typed exact lookup answers every plan-activity and contract question at rank 1 (MRR 1.0).
2. **Dense alone is weakest** (procurement identifiers and figures are lexical). **Hybrid** gives the best R@10.
3. **C ≥ A by a small margin** (R@5 0.89 vs 0.81 guarded; that is 2 of 27 questions). **C's parents also give the answer layer section context.** With 27 answerable questions, a difference of 1 question is 0.04, so A and C are statistically close.
4. **Reranking** improves precision (0.29) but costs 8× the latency and does not improve recall. It is not adopted.
5. **Table windows did not improve recall** on this benchmark. Most table facts are also in chunk text, and windows add noise (P@5 0.30 without them). They are kept, because they are the only route to multi-row tables (for example ICR Annex 3 costs). Better table units (whole small tables with header context) are a scaling task.
6. **Conflict coverage is incomplete.** Both sources reach the top 10 for 2 of 3 conflict questions; Q27 misses the ISR side. A document-diverse result list (one slot per document type or version) is needed before answer generation.
7. **Insufficient evidence:** 3 of 4 are flagged by rules (out-of-scope country, personal data, unknown reference), and 0 answerable questions were wrongly flagged.
   - Q28 ("how many bids were received") is **not** detectable at the retrieval stage: the documents simply lack the field. The answer layer must check that the requested field exists in the evidence.
   - No score threshold is used: it would have to be calibrated on held-out questions, not on this benchmark.

## 5. Selected strategy

**Default:** hybrid (BM25 + gte-base, RRF) over **strategy C** chunks + table windows + typed plan rows, **with the guard layer**, top-k = 5–10. It is the simplest configuration that reaches R@10 0.96 with verified citations.

**Low-resource fallback:** **BM25 + guard on strategy A** (R@5 0.85, MRR 0.72, 4 ms, no model). It is weaker on paraphrased project facts (R@5 0.5).

## 6. Demo (Task 6)

**Command:** `HF_HUB_OFFLINE=1 /opt/anaconda3/bin/python3 rag/retrieval/demo.py "<question>" [--k 5] [--json out.json]`

**Outputs:** saved in `rag/retrieval/demo_outputs/demo_{1..6}.{txt,json}`:
- typed plan row + candidate contract link;
- narrative evidence (PAD p.87, printed 74);
- ICR vs ICR Review ratings, both retrieved;
- out-of-scope Indonesia → insufficient evidence;
- personal-data request → withheld;
- conflicting plan-vs-contract amount, shown unresolved.

**Latency:** the first query includes model loading (about 3–5 s); warm queries take about 80 ms.

## 7. Recommendation for scaling to the 35-project corpus (237 PDFs; needs approval)

1. **Ingestion:** run `pipeline_v2` over the 227 remaining approved PDFs with the existing validation gates (span verification, privacy residuals, reference recall). The 30-PDF pilot proposed earlier is the gate.
2. **Index:** about 25× the units (roughly 60k). In-memory NumPy and BM25 still work (about 180 MB of embeddings). **FAISS is already installed** if needed. PostgreSQL / pgvector are only justified for multi-user serving or incremental updates, and that is a later decision.
3. **Before scaling, fix:**
   - document-diverse ranking for conflict and version questions;
   - whole-table units with header context;
   - the field-presence check for insufficient evidence (answer layer);
   - plan-version-aware lookup: return all versions of a plan row ordered by revision date within its stream, without merging streams.
4. **Benchmark:** extend to about 80–100 questions over the 35 projects, including multi-version plans, French and Spanish documents and Indonesia / Argentina. Keep a held-out half to calibrate any thresholds.

## 8. Limitations

- **Small benchmark:** 31 questions written by the developer; no SME validation (D-23).
- **Pilot scope:** 10 PDFs and 4 projects. Two plan rows have printed references with adjacent-cell spill-over text.
- **English-only embeddings,** although French plans are in scope (Senegal); lexical retrieval and exact lookup handle the reference codes.
