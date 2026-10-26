# Project storage assessment: 2026-10-26 (after the approved cleanup)

How every figure here was obtained:
- **Measured** figures come from the local filesystem, read-only, after the approved cleanup in
  [PROJECT_CLEANUP_EXECUTION_REPORT.md](PROJECT_CLEANUP_EXECUTION_REPORT.md).
- **Two size views.** *Apparent* bytes are the sum of file sizes, i.e. what a copy or upload transfers. *Allocated* bytes
  are disk blocks (`st_blocks × 512`), i.e. what the local disk uses. GB = 10⁹ bytes, GiB = 2³⁰ bytes.
- **Estimates and projections** are labelled as such, with their basis.
- **No cloud quota or remote size was queried:** Databricks, Unity Catalog, Lakebase and PostgreSQL were not accessed.

Raw measurements are in `../_archives/2026-10-26_repo_cleanup/execution_2026-10-26/`: `MEASURE_*.json`,
`INVENTORY_*.csv`, `largest_items.txt` and `storage_estimates.txt`.

## A. Overall project storage (measured)

| | Files | Apparent | Allocated |
|---|---|---|---|
| Original baseline, before the first cleanup phase | 73,983 | 6,699,930,214 B = **6.700 GB** (6.240 GiB) | 6.391 GiB (`du`) |
| Before this approved execution (after the cache phase) | 56,667 | 6,317,560,490 B = **6.318 GB** (5.884 GiB) | 6.008 GiB |
| **Now** | **54,351** | 4,041,966,820 B = **4.042 GB** (3.764 GiB) | **3.884 GiB** |
| of which `.venv-structured-mcp/` | 49,662 | 1.345 GB | 1.363 GiB |
| of which project content (everything except the venv) | **4,689** | **2.691 GB** | — |

**Storage freed:**

| | Files | Apparent | Allocated |
|---|---|---|---|
| This execution | 2,316 | 2,275,599,933 B = **2.276 GB** (2.119 GiB) | 2.124 GiB |
| Phase 1 (caches) | 17,347 | 382,638,494 B = 0.383 GB | — |
| **Cumulative (both phases)** | 19,663 | 2,658,238,427 B = **2.658 GB** (2.476 GiB) | ≈ 2.51 GiB (baseline measured with `du`, so approximate) |

**External backup archives** (`../_archives/2026-10-26_repo_cleanup/`):
- the archive folder: 456,363,855 B (0.456 GB, 0.445 GiB allocated);
- of which the archive files themselves (`archives/`): 393,669,513 B (0.394 GB);
- the rest is inventories, manifests, logs and tools.

**Combined footprint, project + external cleanup archives:** 4.498 GB apparent, **4.329 GiB allocated**.

**Outside the project, not counted above (measured):**

| Item | Size | Status |
|---|---|---|
| Workspace `../.mypy_cache/` | 0.406 GB | Not approved for deletion |
| `../_cleanup_review_do_not_delete/` | 0.065 GB | Not approved for deletion |
| Local Hugging Face model cache: retrieval embedding model `gte-base-en-v1.5` | 523 MiB | Needed at runtime; outside the project |
| Local Hugging Face model cache: reranker `ms-marco-MiniLM-L-6-v2` | 88 MiB | Needed at runtime; outside the project |
| Local PostgreSQL data (registry and conversation schemas) | Not measured | Lives in the server's data directory; no database access in this task |

## B. Component breakdown (measured, apparent bytes)

| Component | Files | Size |
|---|---|---|
| `rag/` (all) | 2,385 | 1,541 MB |
| `rag/module1_discovery/plan_version_coverage_v1/plan_text/` (**approved 35 projects' plan texts**) | 2,111 | 1,326 MB |
| `rag/module1_discovery/plan_version_coverage_v1/` other (`fetch_log.jsonl`, m17 manifest) | 2 | 5 MB |
| `rag/module1_discovery/rag_scope_v3_35_projects/` (RAG scope manifests and evidence, incl. `plan_coverage/`) | 15 | 144 MB |
| `rag/module1_discovery/` lineage (`selection_v2_10_countries` 7 MB, `selection_70` 6 MB, `evidence` 2 MB, scripts) | 71 | ≈ 15 MB |
| `rag/corpus_v3/pdf/` (original PDFs) | 10 | **7.47 MB** |
| `rag/corpus_v3/processed/` (`pilot_v2` 21 MB incl. `by_document` 11 MB and `restricted` 2 MB; `pilot_v1` 5 MB; reference 0.1 MB) | 64 | 26 MB |
| `rag/retrieval/` (incl. `index_cache/` 16.5 MB) | 40 | 17 MB |
| `rag/answering/`, `rag/ingestion/` (code, runs) | 68 | < 1 MB |
| `discovery/` (`data_cache` 396 MB, `project_deep_dive` 182 MB, `samples` 118 MB) | 1,117 | 730 MB |
| `databricks_upload/` (`bronze_inputs` 295 MB) | 51 | 300 MB |
| `silver/` | 275 | 4 MB |
| `gold/` (`runs` 33 MB) | 155 | 34 MB |
| `cohort/` | 4 | 2 MB |
| `structured_agent/` | 147 | 3 MB |
| `conversation/` | 33 | 0.3 MB |
| `ml/` (`eda` 42 MB, `final23` 18 MB, `final` 6 MB) | 503 | 83 MB |
| `docs/`, `app_config/`, top-level files | — | < 1 MB |
| `.venv-structured-mcp/` | 49,665 | 1,345 MB |

**15 largest remaining files** (names and sizes only):

| Size | File |
|---|---|
| 194.6 MB | `discovery/data_cache/ds00979_procurement_notice.csv` |
| 114.7 MB | `discovery/data_cache/ds00005_contract_awards.csv` |
| 112.3 MB | `databricks_upload/bronze_inputs/procurement_notices_ds00979/ds00979_procurement_notice__cohort2000.csv` |
| 101.4 MB | `discovery/project_deep_dive/raw/P163870/pp_pdf/2021-05-18_PP_33215505.pdf` (not an approved RAG project; paused deep dive) |
| 90.7 MB | `rag/module1_discovery/plan_version_coverage_v1/plan_text/P133288/33218774.txt` |
| 88.6 MB | `databricks_upload/bronze_inputs/procurement_notices_api/procnotices_cluster_sample__cohort2000.jsonl` |
| 82.1 MB | `…/plan_text/P168076/33218099.txt` |
| 81.6 MB | `…/plan_text/P156186/33218102.txt` |
| 81.3 MB | `rag/module1_discovery/rag_scope_v3_35_projects/plan_coverage/PLAN_ACTIVITIES.csv` |
| 79.8 MB | `…/plan_text/P159929/33218035.txt` |
| 78.1 MB | `…/plan_text/P175325/33218024.txt` |
| 77.8 MB | `…/plan_text/P162042/33218007.txt` |
| 74.2 MB | `…/plan_text/P133288/33217814.txt` |
| 72.6 MB | `databricks_upload/bronze_inputs/contract_awards_ds00005/ds00005_contract_awards__cohort2000.csv` |
| 71.9 MB | `…/plan_text/P143302/33217832.txt` |

**15 largest directories** (nested directories are included in their parents):

| Directory | Size |
|---|---|
| `rag/` | 1,541 MB |
| `rag/module1_discovery/` | 1,490 MB |
| `.venv-structured-mcp/` (`lib/python3.12/site-packages/` 1,345 MB) | 1,345 MB |
| `rag/module1_discovery/plan_version_coverage_v1/` | 1,331 MB |
| `…/plan_version_coverage_v1/plan_text/` | 1,326 MB |
| `discovery/` | 730 MB |
| `discovery/data_cache/` | 396 MB |
| `databricks_upload/` | 300 MB |
| `databricks_upload/bronze_inputs/` | 295 MB |
| `discovery/project_deep_dive/` (`raw/` 182 MB, of which `raw/P163870/` 175 MB) | 182 MB |
| `rag/module1_discovery/rag_scope_v3_35_projects/` | 144 MB |
| `discovery/samples/` | 118 MB |
| `ml/` | 83 MB |
| `ml/eda/` | 42 MB |
| `gold/` | 34 MB |

## C. PDF storage for the approved 35 projects

| # | Item | Value | Basis |
|---|---|---|---|
| 1 | Approved projects | **35** | `PROJECT_SELECTION_35.csv` |
| 2 | PDFs in the approved manifest | **237** (PP 85, ISR 70, PAD 35, ICR 25, ICRR 22) | `DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv` |
| 3 | PDFs downloaded locally | **10** (4 projects: PAD 2, ICR 2, ICRR 1, ISR 2, PP 3) | Files present at their `expected_local_path`; all 10 also in `CORPUS_MANIFEST_FIRST10.csv` with matching SHA-256 |
| 4 | Measured size of the downloaded PDFs | **7,471,241 B = 7.47 MB = 7.13 MiB** (7.15 MiB allocated) | Filesystem |
| 5 | Known bytes for PDFs not yet downloaded | **7 PDFs, 152,613 B** | HTTP `Content-Length` recorded in the manifest (`size_basis = content_length`) |
| 6 | PDFs not yet downloaded | **227** (220 with unknown size) | Manifest minus the local files |
| 7a | **Estimate**, full approved corpus | **≈ 93.8 MB (89.5 MiB)** | Measured 7.47 MB + known 0.15 MB + 86.2 MB from the manifest's per-type size estimates (median local sizes per document type) |
| 7b | **Estimate**, alternative | **≈ 129.7 MB** | The 220 unknown sizes estimated from this batch's mean size per type (small sample of 10) |
| 8 | Duplicate original PDFs | **None** | SHA-256 of the 10 downloaded PDFs are distinct; no duplicate document IDs in the manifest |

**Planning range:** about **0.09–0.13 GB** of original PDFs for the full approved corpus. A few very large PADs or ICRs
could exceed it; the size of 220 documents is unknown until they are downloaded.

**Not part of the PDF corpus:**
- **Plan texts:** the 1,326 MB of extracted procurement-plan texts are WDS `.txt` extractions used for plan-version
  discovery. A single plan text reaches 91 MB, while the largest downloaded PDF is 2.18 MB.
- **Discovery PDFs:** `discovery/` holds 197 PDFs (199 MB) from Phase 1 samples and the paused deep dive.
  - Six of them carry document IDs from the approved manifest (1.5 MB, all procurement plans): three for P178432 in
    the deep dive, and one each for P132833, P175325 and P148850 in the samples. They are not in `rag/corpus_v3/pdf/`
    and were left untouched (protected area). They could save six downloads later.
  - Their content was not compared with the manifest, which records no checksums for undownloaded documents.

## D. RAG and vector storage

**Measured:**

| Item | Value |
|---|---|
| Embedding model | `Alibaba-NLP/gte-base-en-v1.5`, **768 dimensions**, float32 (3,072 B per vector) |
| Indexed retrieval units, strategy A with table units | **2,288** |
| Indexed retrieval units, strategy C with table units | **2,396** |
| Indexed retrieval units, strategy A without tables | **679** |
| Chunk records | A 443, B 565, C 551 (+ 175 parents) |
| Local embedding cache (`rag/retrieval/index_cache/`, 3 `.npy` files) | **16,475,520 B = 16.5 MB** |
| Extracted text and tables (`pilot_v2`), shared records | 3.55 MB, of which tables 1.25 MB: 220 tables, 3,347 table rows, 163 table descriptions |
| Extracted text and tables (`pilot_v2`), strategies A/B/C | 5.03 MB |
| Extracted text and tables (`pilot_v2`), restricted unsanitised text (D-14) | 2.01 MB |
| Extracted text and tables (`pilot_v2`), `by_document` restart cache | 10.62 MB |
| Frozen benchmark `pilot_v1` | 5 MB |
| Contracts snapshot | 0.1 MB |

**Projection for the full approved corpus (237 documents).** This scales the 10-document pilot by a factor of ×12.6
(by estimated PDF bytes) to ×23.7 (by document count). Because PP documents carry most table units, the true factor
depends on the document mix.

| Item | Projected |
|---|---|
| Retrieval units, strategy A with tables | 28.7 k – 54.2 k |
| Retrieval units, strategy C with tables | 30.1 k – 56.8 k |
| Raw embeddings (float32), one strategy | 88–174 MB |
| Raw embeddings (float32), the three current caches together | 207–390 MB |
| Raw embeddings, halfvec (16-bit) | about half of the float32 figures |
| Processed JSONL: shared records | 45–84 MB (tables 16–30 MB) |
| Processed JSONL: strategy chunks (A/B/C) | 63–119 MB |
| Processed JSONL: restricted text | 25–48 MB |
| Processed JSONL: `by_document` restart cache | 133–252 MB |

**pgvector in Lakebase PostgreSQL, a projection only.** Embeddings have not been migrated to Lakebase, and nothing was
measured there. This is the projection for **one** indexed strategy (A with tables, float32):

| Part | Assumption | Projected |
|---|---|---|
| Table rows (heap + TOAST) | ≈ 3.4 KB per row: vector 3,080 B, plus tuple header, ids and metadata ≈ 280 B, plus 20% page slack | 116–219 MB |
| HNSW index | ≈ 1.0–1.5 × the raw vector bytes; depends on `m` and `ef_construction` | 88–250 MB |
| **Total for embeddings** | | **≈ 0.2–0.5 GB** |

Indexing a second strategy roughly doubles this; `halfvec` roughly halves it.

## E. Preliminary target cloud allocation

| Target | Content | Size | Kind |
|---|---|---|---|
| Unity Catalog Volume: original PDFs | 237 approved PDFs | 7.5 MB now; **≈ 0.09–0.13 GB** full corpus | Measured / estimate |
| UC Volume: processing artefacts | Processed JSONL (shared + strategies) | ≈ 0.11–0.20 GB | Projection |
| UC Volume: restart caches (optional) | `by_document` cache | ≈ 0.13–0.25 GB | Projection |
| UC Volume (restricted, D-14): unsanitised text | `restricted/` records | ≈ 0.03–0.05 GB | Projection |
| UC Volume (optional): approved plan texts | Plan-version discovery evidence | 1.33 GB | Measured; only needed if plan-version discovery is reproduced in the cloud |
| Lakebase PostgreSQL + pgvector: embeddings + HNSW (one strategy) | Embedding rows and their index | ≈ 0.2–0.5 GB | Projection |
| Lakebase: chunks, PDF-table registry, table rows, citations and evidence links | Text and table records | ≈ 0.15–0.3 GB incl. indexes (JSONL projections ×≈1.5 for row and index overhead) | Projection |
| Lakebase: conversation state | Turns, summaries, decisions | ≈ 2 KB per turn incl. indexes → about 0.2 GB per 100,000 turns | Assumption-based; usage unknown |
| Lakebase: registry metadata (if moved from local PostgreSQL) | Registry tables | Not measured | Unknown |
| Existing Databricks Silver/Gold tables, KPI views | **Do not modify or migrate** | Not measured (no Databricks access). The local Bronze input copies are 0.30 GB, a lower bound for the raw inputs only | Unknown |
| Runtime model files (wherever retrieval runs) | Embedding model + reranker | 523 MiB + 88 MiB | Measured locally |

**Indicative totals for the RAG corpus.** These cover the full 35-project corpus, one indexed strategy, and no plan
texts:

| Store | Projected |
|---|---|
| UC Volumes | ≈ 0.3–0.6 GB |
| Lakebase | ≈ 0.4–0.8 GB, before conversation growth |

All of these are projections from a 10-document pilot.

## F. Outstanding unknowns

- **Cloud quotas are not verified.** Databricks Free Edition quotas for Unity Catalog Volume storage, Lakebase
  availability and limits, and pgvector/HNSW support and version in Lakebase are all unknown. **This assessment does not
  claim that the project fits in Databricks Free Edition.**
- **Remote sizes were not measured:** the actual Delta storage of the 18 Silver tables, 5 Gold tables and 7 KPI views,
  and the size of the local PostgreSQL registry and conversation schemas.
- **Undownloaded PDFs:** 220 of the 237 approved PDFs have unknown sizes. 227 are not downloaded, and no download was
  made.
- **Production indexing choice:** the retrieval strategy (A or C), whether table units are indexed, and float32 versus
  `halfvec` together change the vector storage by up to ×4.
- **Conversation volume:** turns per user and retention are not yet defined.
- **Single copy of deleted data:** the external archives A1 and A2 are now the only copy of the deleted 2.28 GB. They
  sit on the same disk as the project.
