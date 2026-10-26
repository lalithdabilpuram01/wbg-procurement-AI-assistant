# Real PDF evidence → Lakebase pgvector integration POC (Step 4): dry run, 2026-10-26

**Status update (2026-10-26 08:04 UTC): executed after explicit approval.**
- **Write:** 311 records were written to `wbg_rag_pdf_poc` in one transaction.
- **Tests:** all 5 live tests passed, and the rerun inserted 0.
- **Storage:** 0.96 MB measured.
- **Details:** see [PDF_LAKEBASE_LIVE_VALIDATION.md](PDF_LAKEBASE_LIVE_VALIDATION.md). Its §15 lists the safety
  additions made before the write.

Everything below is the original dry-run report.

**Dry-run status (before approval): implemented and validated as a dry run. Nothing was written to Lakebase.**
- The schema definitions, data mapping, write path, retrieval checks and offline tests are in place.
- The real-data write is **waiting for your explicit approval** (§9).
- No Lakebase connection was opened in this step, and no LLM or hosted model was called.

Code: `infrastructure/lakebase_poc/lakebase_poc/pdf_*.py`. Full dry-run output:
`infrastructure/lakebase_poc/results/pdf_dry_run_20261026T075359Z.json`.

## 1. The PDF and its checksum

| Field | Value |
|---|---|
| Volume path (Step 3) | `/Volumes/workspace/default/wbg_rag_pdfs_poc/ET/P148850/ISR_2026-02-28_40089196.pdf` |
| Project / document | **P148850** (Ethiopia Expressway Development Support Project) / **40089196**, ISR (`ISR_latest`), "Disclosable Version of the ISR … Sequence No: 21" |
| SHA-256 | `c32be7ca2d3d1ce2ea8e2e017cd7ef2cedea0e9aad940711e6f5b4cb7e5a4d24` (164,080 B, 8 pages) |
| Document version ID | `40089196:c32be7ca2d3d` (document ID + SHA-256 prefix, as used by the existing pipeline) |
| Extraction version | `ingest-pilot-2.0.0` (pipeline code hash `4cfa826791cadd26`; tokenizer `tiktoken:cl100k_base`; strategy C `C-1.0`) |

All nine identity checks pass, so every record maps unambiguously to this exact PDF version:
- the project is approved, and the document is in the approved download manifest;
- the SHA-256 is identical in the corpus manifest, the local PDF, the processed `document_versions` record and its
  download manifest, and equals the checksum verified in the Unity Catalog Volume;
- the version ID matches;
- the project matches;
- the extraction recorded no validation errors.

## 2. Reusable evidence (existing pilot_v2 outputs; no new parser)

| Evidence | Source (sanitised) | This document |
|---|---|---|
| Narrative child chunks, strategy C (the approved retrieval default) | `strategies/C/chunks.jsonl` | 17, with section paths, physical and printed pages, `source_spans`, sanitisation record |
| Parent chunks (context for children) | `strategies/C/parents.jsonl` | 10 |
| Table windows (3-row retrieval units) | built by the existing retrieval `Corpus` from `shared/table_rows.jsonl` | 22 |
| Tables | `shared/tables.jsonl` | 5 physical parts = 2 logical tables: §6.1 PDO indicators (pp. 3–4) and §6.2 intermediate results indicators (pp. 4–6) |
| Table rows | `shared/table_rows.jsonl` | 42, with headers, cells and pages |
| Searchable table descriptions | `shared/table_descriptions.jsonl` | 2 (one per logical table) |
| Pages / sections | `shared/pages.jsonl`, `shared/sections.jsonl` | 8 (all with printed labels) / 17 |
| Plan activities, contract-evidence links | `shared/plan_activities.jsonl`, `evidence_links.jsonl` | 0: an ISR has no procurement-plan rows |

**`restricted/` (unsanitised text) is never opened.** The reader refuses any path outside `shared/` and `strategies/`,
and a test checks this.

**Tables.** Both logical tables are World Bank results-framework tables, with a 9-column layout:

| Column | Holds |
|---|---|
| 1 | Indicator name, with its unit in parentheses |
| 2–9 | Value and date pairs for baseline, previous actual, current actual and closing target |

- **Typed sample:** registered in the draft table schema `wb_isr_results_indicators_v1`.
  - 17 indicator rows are typed, giving 110 field values; numbers are parsed, and units come from the indicator name.
  - Comment rows, component titles and repeated headers are skipped, not invented.
- **Quality:**
  - One row is marked `needs_review`: "Reduction in road accident fatalities along the Addis – Adama and …" is split
    across pages 3 and 4, so its name and unit are incomplete.
  - The §7 financing amounts (IDA-56250, IDA-79000) were not detected as a table. They are in a narrative chunk
    (`40089196:C:0013`, p. 7) and searchable as text, but not typed.
- **Exact numeric lookup** (deterministic Python over the typed values; complete rows only): *"Roads constructed
  (Kilometers)"* → current **57.40 km** on 25-Feb-2026, closing target **57.40 km** for Jun/2026. Cited to physical
  page 3, table `40089196:t00301`. After approval the same lookup can run as parameterised SQL on `pdf_table_values`;
  no LLM-generated SQL is involved.

## 3. Proposed Lakebase schema: `wbg_rag_pdf_poc`

This is a new, isolated schema, separate from the synthetic `wbg_rag_poc` (untouched). There are 11 tables plus an
HNSW index. All DDL is `IF NOT EXISTS`, with no `DROP` or `ALTER`; identifiers are regex-validated and composed with
`psycopg.sql`.

```
documents (document_version_id PK; document_id, project_id, sha256 UNIQUE together; volume_path, source_url,
           dates, extraction_version, pipeline_code_hash)
 ├── pages (document_version_id, page) PK; printed_page_label
 ├── sections (section_id PK; section_path, heading, pages, printed labels)
 ├── parent_chunks (parent_id PK; text, pages, child_ids[])
 ├── retrieval_units (unit_id PK; unit_type chunk|table, strategy_version, section, parent_id → parent_chunks,
 │                    table_id, pages, printed label, indexed_text (exactly what was embedded), excerpt (sanitised),
 │                    indexed_text_sha256, source_spans jsonb, sanitisation jsonb)
 │     ├── unit_embeddings (unit_id PK/FK; model, preprocessing jsonb, source_cache, embedding VECTOR(768),
 │     │                    embedding_sha256)  + HNSW index USING hnsw (embedding vector_cosine_ops)
 │     └── citations (unit_id PK/FK; project_id, document_id, pages, printed label, section, source_url, volume_path)
 ├── pdf_tables (table_id PK; table_group_id, table_schema_id → table_schemas, page, caption, section, header jsonb,
 │               n_columns, n_data_rows, continuation_of, description, extraction_version)
 │     └── pdf_table_rows (table_id, row_index) PK; page, is_header, cells jsonb
 │           └── pdf_table_values (table_id, row_index, field_name) PK; value_text, value_numeric, unit, quality
table_schemas (table_schema_id PK; description, fields jsonb, status draft|approved|retired)
```

PDF tables are stored generically: rows as JSONB cells, typed values as field/value pairs. There is **no physical table
per PDF table**. `table_schemas` is the hook for the future governed PDF-table schema registry, where each PDF table
points to an approved schema version.

## 4. Verified embeddings (reused, not regenerated)

**The mapping is proven.**
- The cache file `rag/retrieval/index_cache/emb_C_2396_d1afdad97857d7e4.npy` is used only because its name equals the
  retrieval code's own cache key: SHA-256 of every unit ID + indexed text + model name, recomputed from the current
  corpus.
- Row *i* is therefore unit *i*. If the units, texts or model ever change, no cache matches, and the code **refuses
  instead of regenerating** (tested).

**Independent confirmation** (offline, local Hugging Face cache, Anaconda environment): re-embedding this document's 39
units with the same model and settings gave cosine **1.000000** against the cached rows. The best match against 200
other documents' rows was 0.84. The re-embedded vectors were only compared, never stored.

| Check | Result |
|---|---|
| Vectors for this document | 39 (17 chunks + 22 table windows), aligned 1:1 with units |
| Dimensions / finite / unit-norm | 768 / all finite / all ‖v‖ = 1 |
| Duplicates | 0 duplicate unit IDs, 0 duplicate vectors |
| Model and preprocessing | `Alibaba-NLP/gte-base-en-v1.5`; input = `indexed_text` (contextual header + text); `normalize_embeddings=True`; batch size 16; sentence-transformers with `trust_remote_code=True`. Exactly the existing retrieval configuration |

**Query vectors for the post-approval test:** the six existing benchmark questions whose gold evidence is in this
document (Q01, Q06, Q17, Q18, Q20, Q27) were encoded offline with the same model and stored in
`results/pdf_poc_queries.npz`. No document was re-embedded.

## 5. Dry-run record counts

| Table | Records |
|---|---|
| documents | 1 |
| pages | 8 |
| sections | 17 |
| parent_chunks | 10 |
| retrieval_units | 39 |
| unit_embeddings | 39 (VECTOR(768)) |
| citations | 39 |
| table_schemas | 1 (`wb_isr_results_indicators_v1`, draft) |
| pdf_tables | 5 |
| pdf_table_rows | 42 |
| pdf_table_values | 110 |
| **Total** | **311** (write bound: 1,000) |

**Filters for every record:**
- project `P148850`;
- document `40089196`;
- version `40089196:c32be7ca2d3d`;
- SHA-256 `c32be7ca…4d24`;
- strategy C.

**Retrieval preview** (exact cosine ranking computed locally; the live test must reproduce it exactly): all 6 benchmark
questions retrieve a unit on a gold page within the top 5. On a single 39-unit document this is expected, and it says
nothing about production quality at corpus scale.

## 6. Privacy and provenance checks

| Check | Result |
|---|---|
| Sources read | `pilot_v2/shared/` and `pilot_v2/strategies/` only; `restricted/` refused |
| Sanitisation record on units | 0 masks needed for this document; no personal-data indicators in any of its 17 sections |
| Residual personal data (the pipeline's own `rag.ingestion.sanitize.residual_risk` + mask tokens) over excerpts, table cells and parent texts | **0 emails, 0 phones, 0 `[PERSON]/[EMAIL]/[PHONE]` placeholders** |
| Projects in the records | Only `P148850`; all unit IDs start with `40089196` |
| Provenance kept | document version and SHA-256, Volume path, source URL, extraction version and code hash, strategy version, physical pages + printed page labels, section paths, `source_spans`, table IDs and row indices, embedding model + preprocessing + source cache + vector SHA-256 |

A first, generic regex flagged 25 "phone-like" strings. All of them were numbers in the results and financing tables
(amounts, dates), which is why the check now uses the pipeline's own detectors. Those require a `+` prefix or a
Tel/Phone label.

## 7. Offline tests

The default offline suite has 39 tests, all passing, and makes no connection:

| Group | Tests | Covers |
|---|---|---|
| Environment | 8 | Python version, imports, NumPy and pgvector basics |
| Synthetic POC | 13 | Settings, credential handling, synthetic data, SQL contract |
| **Real-PDF POC** | **18** | Listed below |

The 18 real-PDF tests cover:
- identity;
- one document version and one project;
- 768-d finite, unit-norm, aligned vectors;
- refusal instead of regeneration when no cache matches;
- refusal to read `restricted/`;
- zero residual personal data;
- typed indicator parsing (including `80,000` → 80000);
- split, comment, title and header rows not guessed;
- exact numeric lookup with page;
- the schema is isolated, generic and validated, rollback can't target `wbg_rag_poc`, and DDL has no
  DROP/ALTER/`lakebase_vector`;
- the write gate needs both opt-ins;
- the bundle guard rejects other documents and oversized bundles;
- inserts are `ON CONFLICT DO NOTHING` with placeholders;
- the dry run makes no connection;
- the exact reference ranking hits gold pages.

**Live tests:** `tests_live/test_live_pdf_poc.py` has 5 tests and is **skipped** without approval. It checks:
- a re-run inserts nothing;
- the exact search equals the local reference;
- HNSW recall measured against exact search;
- keyword and metadata filters;
- citations with original pages;
- no other project IDs.

## 8. Expected Lakebase storage

The estimate assumes record JSON size × 1.5, plus the per-vector heap, TOAST and HNSW bytes measured in Step 2:

| Part | Estimate |
|---|---|
| Relational records (text, tables, citations) | ≈ 208 KB |
| 39 vectors (heap + TOAST ≈ 4.4 KB each, HNSW ≈ 4.1 KB each) | ≈ 330 KB |
| **Total** | **≈ 0.54 MB** |

The Lakebase project currently holds about 12.2 MB (Step 2 measurement).

## 9. Approval required before real-data writes

Nothing below has been run. When you approve, the write runs from `infrastructure/lakebase_poc/`:

```bash
LAKEBASE_LIVE_TESTS=1 LAKEBASE_PDF_POC_WRITE_APPROVED=1 ~/.local/bin/uv run --locked pytest tests_live/test_live_pdf_poc.py -q -p no:cacheprovider
```

That one run:
1. **Creates the schema:** `CREATE EXTENSION IF NOT EXISTS vector` (already installed), then the 11 tables and the HNSW
   index in `wbg_rag_pdf_poc`.
2. **Writes the records:** inserts the 311 records in **one transaction** with `ON CONFLICT DO NOTHING`. It refuses
   any other document version or more than 1,000 records.
3. **Runs the retrieval checks:** re-run duplicate test, exact vs HNSW recall, filters, citations and project
   isolation.

**Rollback (manual, needs approval):** `DROP SCHEMA "wbg_rag_pdf_poc" CASCADE;` in `databricks_postgres`. It affects
only this POC schema; the code refuses to build a rollback for any other schema. `wbg_rag_poc`, the `vector`
extension and the project are unaffected.

## 10. Blockers and open questions

There are no blockers for the dry run. Open points:
1. **Strategy:** strategy C plus table windows was used because it is the documented default. The strategy-A cache
   (`emb_A_2288_…`) is also mappable if you prefer A.
2. **Query embeddings at runtime:** the uv environment has no `sentence-transformers`, so the six query vectors were
   created with the Anaconda environment and the existing model. A production design must decide where query embedding
   runs (for example a model-serving endpoint or an App-side model). That needs a separate decision and is not done
   here.
3. **Table extraction gaps:** the §7 financing table was not detected as a table (text only), and one indicator row is
   split across pages (`needs_review`). Typed table coverage for ISRs therefore needs the planned table-quality review
   before corpus-wide use.
4. **Single-document limits:** results from one 39-unit document **do not** demonstrate production retrieval quality,
   HNSW behaviour at scale, or planner choices on large tables.
5. **Free Edition:** Lakebase storage and compute quotas remain undocumented (Step 2). This POC needs under 1 MB.

## Protected components

**Local files:** an inventory comparison (SHA-256 per file) since Step 3 shows changes only under
`infrastructure/lakebase_poc/`:
- 6 new modules, 2 test files, the dry-run report and the query vectors;
- no bytecode written into `rag/`;
- unchanged: the RAG ingestion and retrieval code and outputs, the embedding cache, manifests, the original PDFs,
  Bronze/Silver/Gold, ML models, MCP/NL2SQL/routing/guardrails and the cleanup archives.

**Remote systems:**
- No Lakebase, Databricks, local PostgreSQL or hosted-LLM call was made in this step.
- Lakebase Search was not enabled.
- No App or LangGraph was deployed.
