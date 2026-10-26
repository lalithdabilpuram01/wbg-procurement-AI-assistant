# Real PDF evidence in Lakebase: live validation (Step 4 execution), 2026-10-26

**Result: successful.**
- **Writes:** exactly **311** records for ONE document version were written to the isolated schema `wbg_rag_pdf_poc`,
  in one transaction.
- **Tests:** all **5 approved live tests passed**, and a rerun inserted nothing.
- **Unchanged:** nothing outside that schema was modified.

This was a single-document proof of concept. It is **not** evidence of production-ready retrieval.

Raw evidence, with no identities or credentials, is in `infrastructure/lakebase_poc/results/`:
- `pdf_precheck_offline_20261026T080304Z.json`
- `pdf_precheck_live_20261026T080314Z.json`
- `pdf_live_tests_20261026T080351Z.json`
- `pdf_measure_20261026T080453Z.json`

## 1. Execution timestamp

| Step | UTC |
|---|---|
| Offline pre-checks | 2026-10-26 08:03:04 |
| Live read-only pre-check | 08:03:14 |
| **Approved write + 5 live tests** (the approved command, flags set for that invocation only) | **08:03:51 – 08:04:19** (28 s) |
| Read-only measurements | 08:04:53 |

## 2. Database and schema identification

| Item | Value |
|---|---|
| Lakebase project / branch / endpoint | `projects/wbg-procurement-dev/branches/production/endpoints/primary`. The configured host was confirmed to belong to this endpoint (read-only CLI lookup) |
| Database | `databricks_postgres` (checked with `current_database()`) |
| Target schema | **`wbg_rag_pdf_poc`**: did not exist before; created by the approved run |
| Other schemas present | `__db_system`, `public`, `wbg_rag_poc` (synthetic POC). Not modified (§14) |
| Authentication | Short-lived OAuth database credential, generated per connection through the Databricks CLI; TLS `sslmode=require`; never printed, logged or stored |
| Approval flags | `LAKEBASE_LIVE_TESTS=1 LAKEBASE_PDF_POC_WRITE_APPROVED=1` on the command line of that one invocation only; not in `.env` or shell profiles (checked) |

## 3. PostgreSQL and pgvector versions

| Component | Version |
|---|---|
| PostgreSQL | **18.6** |
| `vector` (standard pgvector) | **0.8.6**. Verified as already installed; the loader no longer creates extensions |
| `lakebase_vector` / Lakebase Search | **Not installed / not enabled** (the loader refuses to run if it is present) |

## 4. Inserted record counts (first write, one transaction)

| Table | Expected | Inserted | Stored after the run |
|---|---|---|---|
| documents | 1 | 1 | 1 |
| pages | 8 | 8 | 8 |
| sections | 17 | 17 | 17 |
| parent_chunks | 10 | 10 | 10 |
| retrieval_units | 39 | 39 | 39 |
| unit_embeddings | 39 | 39 | 39 |
| citations | 39 | 39 | 39 |
| table_schemas | 1 | 1 | 1 |
| pdf_tables | 5 | 5 | 5 |
| pdf_table_rows | 42 | 42 | 42 |
| pdf_table_values | 110 | 110 | 110 |
| **Total** | **311** | **311** | **311** |

Inside the same transaction, after the inserts, every stored row was compared with its prepared record: content,
provenance, foreign keys and the SHA-256 of each embedding. It found no mismatches and no unrelated rows. A mismatch
would have rolled the whole transaction back.

## 5. The 39 real embeddings

**Pre-checks** (offline, before the write):
- 39 vectors, 768 dimensions, all finite, all unit length, no duplicates, aligned 1:1 with the 39 retrieval units;
- each unit's `indexed_text_sha256` matches the retrieval corpus text;
- each `embedding_sha256` matches its vector;
- the cache key `d1afdad97857d7e4`, recomputed from the unit IDs, texts and model, equals the cache file
  `emb_C_2396_d1afdad97857d7e4.npy`;
- strategy C, model `Alibaba-NLP/gte-base-en-v1.5`, preprocessing `{input: indexed_text, normalize_embeddings: true,
  batch_size: 16}`;
- nothing was regenerated.

**After the write:** the stored vectors read back with identical SHA-256 to the prepared vectors, so the
`stored_matches_prepared` check is true.

## 6. HNSW index verification

| Check | Result |
|---|---|
| Catalog (`pg_index`, `pg_am`, `pg_opclass`) | `unit_embeddings_hnsw_cosine`: access method **hnsw**, operator class **`vector_cosine_ops`**, `indisvalid = true`, `indisready = true` |
| Default plan, 39 rows | `Limit → Sort → Seq Scan`. With so few rows the planner prefers a scan, which is expected |
| Plan with `enable_seqscan = off` (`SET LOCAL`, per transaction) | `Limit → Index Scan using unit_embeddings_hnsw_cosine`, so the index is usable for `ORDER BY embedding <=> …` |

## 7. The five live tests (all passed)

| # | Test | Result |
|---|---|---|
| 1 | **Idempotency** | **Passed.** First write: 311 inserted. Rerun: **0** inserted in every table, and all 311 stored rows re-verified identical, with no extra rows |
| 2 | **Vector similarity** | **Passed.** Exact top-10 equals the NumPy reference for all 6 queries, in the same order, with distance difference < 10⁻⁵. HNSW recall in §8 |
| 3 | **Keyword and metadata filtering** | **Passed.** Scope `project_id=P148850`, `document_id=40089196` and `document_version_id=40089196:c32be7ca2d3d` gives 10 results. Another approved project (`P132833`) gives 0, and a wrong version gives 0. `strategy_version=C-1.0` gives only narrative chunks; `unit_type=table` gives only table windows. Keyword `expressway` within P148850 gives 9 results, every one containing the keyword and citing P148850 |
| 4 | **Citations** | **Passed.** Every returned unit maps to document `40089196`, version `40089196:c32be7ca2d3d`, SHA-256 `c32be7ca…4d24`, the approved Volume path and the World Bank `source_url`. Physical pages are within 1–8, the printed page label equals the page record, and source spans are present |
| 5 | **Project isolation** | **Passed.** Only project `P148850` and document `40089196` are present in `documents` and `citations`; 0 retrieval units from any other version |

## 8. Exact search versus HNSW recall

Measured with the 6 existing benchmark questions whose gold evidence is in this document (Q01, Q06, Q17, Q18, Q20,
Q27). Recall is the HNSW top-k overlap with the exact top-k:

| Setting | Recall@5 (6 queries) | Recall@10 (6 queries) |
|---|---|---|
| `hnsw.ef_search` default (40) | 1.0 for all 6 | 1.0 for all 6 |
| `hnsw.ef_search` = 200 | 1.0 for all 6 | 1.0 for all 6 |

**Caveat:** with only 39 vectors, an `ef_search` of 40 already covers the whole graph, so HNSW is effectively
exhaustive here. Perfect recall on this tiny index says nothing about recall at corpus scale. In Step 2, the 500-vector
synthetic test gave 0.9 on 2 of 5 queries at the default and 1.0 at 200.

## 9. Verified PDF and page citations (sample: top results for Q01)

| Unit | Pages (physical) | Printed label | Section |
|---|---|---|---|
| `40089196:t00402:w4-6` | 4 | 4 | 6. RESULTS > 6.2 Intermediate Results Indicators by Components |
| `40089196:t00401:w4-6` | 4 | 4 | 6. RESULTS > 6.1 PDO Indicators by PDO Outcomes |
| `40089196:C:0015` | 8 | 8 | 8. KEY DATES |
| `40089196:C:0008` | 3 | 3 | 6. RESULTS |
| `40089196:C:0014` | 7–8 | 7 | 7. DATA ON FINANCIAL PERFORMANCE > 7.3 Key Dates (by loan) |

Every citation carries the original PDF identity: document `40089196`, SHA-256 `c32be7ca…4d24`,
`/Volumes/workspace/default/wbg_rag_pdfs_poc/ET/P148850/ISR_2026-02-28_40089196.pdf`, and the World Bank
`source_url`.

## 10. Numeric table lookup

This is a parameterised SQL lookup on `pdf_table_values` that uses complete rows only. The same values come from the
deterministic Python lookup.

| Indicator | Field | Value | Unit | Date / period | Source |
|---|---|---|---|---|---|
| Roads constructed (Kilometers) | current_value | **57.40** | Kilometers | current_date **25-Feb-2026** | table `40089196:t00301`, **page 3** |
| | target_value | **57.40** | Kilometers | target_period **Jun/2026** | page 3 |
| | previous_value | 56 | Kilometers | previous_date 30-Jul-2025 | page 3 |
| | baseline_value | 0.00 | Kilometers | baseline_period May/2015 | page 3 |

## 11. Extraction gaps and review flags (preserved)

**Limitation A: split indicator row.**
- Row `40089196:t00301#12`, "Reduction in road accident fatalities along the Addis – Adama and …", spans pages 3–4 and
  its name and unit are incomplete.
- All 5 of its values are stored with `quality = 'needs_review'`.
- The row was not merged, completed or approved, and the lookup only uses `complete_row` values.

**Limitation B: financing amounts on page 7.**
- No table rows, typed values or schema entries were created for them.
- They remain narrative evidence only, in chunk `40089196:C:0013` (page 7).

**Schema registry:** `wb_isr_results_indicators_v1` remains **`draft`**.

## 12. Measured storage (actual, from PostgreSQL)

| Item | Bytes |
|---|---|
| `databricks_postgres` database total | 13,434,880 (was 12,247,040 after Step 2: +1,187,840) |
| **`wbg_rag_pdf_poc` total** (11 tables incl. TOAST and indexes) | **958,464 (0.96 MB)** |
| `unit_embeddings` total (heap 16,384 + TOAST + primary key + HNSW) | 434,176 |
| HNSW index alone | 172,032 (≈ 4.4 KB per vector) |
| Relational tables (the other 10, total) | 524,288 |

Rows: the counts in §4. Per table:

| Table | Total bytes |
|---|---|
| retrieval_units | 114,688 |
| citations | 65,536 |
| pdf_table_rows | 65,536 |
| pdf_table_values | 65,536 |
| documents | 49,152 |
| pages, sections, parent_chunks, pdf_tables, table_schemas | 32,768 each |

**Compared with the dry-run estimate of ≈ 0.54 MB:** the actual size is **0.96 MB, about 1.8× the estimate**.
- **Vectors:** close. Estimate 330 KB; actual 434 KB including the table's fixed pages.
- **Relational part:** larger. Estimate 208 KB; actual 524 KB.
- **Why:** PostgreSQL allocates whole 8 KB pages, and each of the 11 small tables has a minimum footprint (heap page +
  primary-key index + TOAST) of about 32 KB. At this tiny size, that fixed per-table overhead dominates. It does not
  grow with the data, so the per-record estimate remains the right basis for scaling. The estimate method should add
  about 32 KB per table.

## 13. Rerun and idempotency

| Run | Inserted | Stored rows | Verification |
|---|---|---|---|
| Approved write (fixture) | 311 | 311 | Identical to prepared (inside the transaction) |
| Rerun (test 1) | **0** | 311 | Identical, no extra rows |
| Read-only re-verification (measurement script) | — | 311 | `stored_matches_prepared = true` |

## 14. Protected-component integrity

| Component | Check | Result |
|---|---|---|
| Synthetic schema `wbg_rag_poc` | Read-only fingerprint before and after: row count, MD5 over all rows, relation list | Identical: 500 rows, `a10f8538f6b1500cdf1f90fa1c1b2f7f`, 3 relations |
| Other Lakebase objects | Schemas listed before the run | Only `wbg_rag_pdf_poc` added |
| Local repository | SHA-256 inventory since the dry run | Changes only under `infrastructure/lakebase_poc/` (code, tests, results) |

The local inventory confirms these are unchanged:
- RAG preprocessing and retrieval code, the embedding caches, manifests and original PDFs;
- Bronze/Silver/Gold, the 2,000-project cohort and the frozen ML models;
- MCP, NL2SQL, routing, guardrails and conversation memory;
- the cleanup archives.

**Not contacted in this step:**
- Unity Catalog Volumes and Databricks tables;
- local PostgreSQL;
- any LLM;
- Lakebase Search, Apps, workflows and LangGraph (none used).

**Secrets:** no login identity or token appears in results, code or tests.

## 15. Errors, limitations and recommended next step

**Code changes before the write (all add safety, none relax it):**

| Area | Change |
|---|---|
| Content verification | The loader now verifies every stored row (content, provenance, embedding hash) against the prepared record and aborts on mismatch or unrelated rows. Before, `ON CONFLICT DO NOTHING` accepted any existing key silently |
| Target inspection | The loader inspects the target schema and refuses unexpected objects |
| Extensions | It verifies `vector` instead of creating it, and refuses if `lakebase_vector` is present |
| Transactions | Live connections use autocommit, so every `transaction()` is a real `BEGIN`/`COMMIT`. Before, an implicit read transaction could have turned the write into an uncommitted savepoint and let `SET LOCAL` leak between searches |
| Live tests | Extended to your spec: Recall@5 and @10 at default vs 200 `ef_search`; project, document, version and strategy filters; citations with source URL, printed label, spans and checksum |
| Offline tests | 2 added for the verification logic: **41 offline tests pass** (the 39 earlier tests + 2) |

**Error during the run:** none in the approved write or tests. The read-only measurement script failed once, before
measuring anything, because it hadn't registered the vector type on its new connection. It was fixed and re-run;
nothing was written by it.

**Limitations:**
- one document;
- 39 vectors;
- HNSW recall is not meaningful at this size;
- the planner uses sequential scans for tiny tables;
- the two extraction gaps above;
- query vectors were produced offline (no runtime query-embedding service yet);
- Free Edition Lakebase quotas are still undocumented.

**Rollback (manual, needs approval):** `DROP SCHEMA "wbg_rag_pdf_poc" CASCADE;`.

**Recommended next step** (needs your separate approval):
1. Decide where query embeddings are computed at runtime (for example a model-serving endpoint, or within a future App).
2. Decide whether to run the table-quality review for ISR/PP tables.
3. Then scale the same verified, idempotent loader to a small second batch: for example the other 9 already-processed
   pilot PDFs, still within the 35-project scope, to measure recall and planner behaviour on a larger index.

Stopped here, as instructed.
