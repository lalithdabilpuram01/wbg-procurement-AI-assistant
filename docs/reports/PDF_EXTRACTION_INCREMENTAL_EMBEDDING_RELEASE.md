# PDF Extraction 2.1.0 + Incremental Embeddings: Staged Release (Step 6D)

**Date:** 2026-10-26.

**Status: READY FOR PROMOTION, not promoted.** The corrected corpus (`pilot_v2_1`) and the staged per-unit embedding store passed every integrity, privacy, citation and retrieval acceptance check (§8 and §10).

**Not done:**

- The active defaults are unchanged: the retrieval corpus is `pilot_v2`, the cache mode is `legacy`, and the pipeline profile is `2.0.0`.
- The old corpus and caches are intact.
- Lakebase, Databricks, Bronze/Silver/Gold and local PostgreSQL were not touched, and no hosted service was called.

---

## 1. New processed-data version

| Item | Value |
|---|---|
| Location | `rag/corpus_v3/processed/pilot_v2_1/` (20 MB; same layout as pilot_v2: `shared/`, `strategies/`, `restricted/`, `reference/`, `by_document/`, `run_manifest.json`) |
| Extraction version | `ingest-pilot-2.1.0` (profile `2.1.0`, `stacked_cells: true`) |
| How it is built | `python -m rag.ingestion.pipeline_v2 --profile 2.1.0`, or `python -m rag.release_v2_1.build` (adds a network block, a tokenizer assertion and the default-regression proof) |
| Inputs | the same 10 PDFs and manifests; all 10 PDF SHA-256s are identical to pilot_v2 |
| Tokenizer | `tiktoken:cl100k_base` (local cache; asserted, so there is no silent fallback) |
| Vocabulary SHA-256 | `ab052e4a…` (identical to pilot_v2) |
| Code hash | `cae7261b4979a8ac` (pilot_v2: `4cfa826791cadd26`; the hash covers every `rag/ingestion/*.py`) |
| `run_manifest.json` SHA-256 | `3bd68473…c3c3` |
| `shared/table_rows.jsonl` SHA-256 | `8b94a2a0…` (3,347 rows) |

**Implementation**, integrated into `rag/ingestion` with the default unchanged:

| File | Change |
|---|---|
| `rag/ingestion/stacked_values.py`, `lost_values.py` | **Promoted** from the validated Step 6A modules: identical code, relative import. `rag/ingestion_p1/*` now re-exports them, and all 65 Step 6A tests run against the promoted code. |
| `rag/ingestion/tables.py` | `extract(..., stacked_cells=False)` and `_sanitise_table(..., stacked_cells=False)`. Opt-in only: stacked values keep their line boundaries, rows get `extraction_flags`, and a privacy guard falls back to the default cleaning if a row would carry fewer masks. |
| `rag/ingestion/pipeline_v2.py` | `PROFILES`: `2.0.0` (default → pilot_v2, unchanged) and `2.1.0` (→ pilot_v2_1, `stacked_cells`). The version is threaded through `process` and the vocabulary key. The `--profile` CLI option was added. |
| `rag/ingestion/config.py` | `OUTPUT_V2_1` |

**The default is proven unchanged.** The default profile was re-run into a temporary directory. **All 20 output files are byte-identical** to the SHA-256s recorded in `pilot_v2/run_manifest.json` (shared, strategies and restricted; restricted files were compared by hash only). The vocabulary is identical too. `pilot_v2` itself was not written.

---

## 2. Cell-format change (stacked cells)

| Aspect | Format in 2.1.0 |
|---|---|
| Stacked independent values | Kept in **one cell**, in source order, separated by `\n`. For example `340.00\n18.00\n12.00\n370.00\n0.00\n370.00` (was `340.0018.0012.00370.000.00370.00`). |
| One number wrapped in a narrow column | Still joined: `9,890,912.0⏎7` → `9,890,912.07`, as before |
| Text cells | Unchanged; the same word-break repair as 2.0.0 |
| Logical rows | **Not split.** Alignment between a label column and the stacked values is not established in any affected row. |
| Row flags (`extraction_flags`, new field; text-free) | `stacked_values`: `{columns, format: "newline_separated", reason}`, plus `needs_review: true`. Lost values: `{status: values_detected_not_extracted / values_missing, columns, unassigned_words (count), values_withheld: true, provenance: pdf_source_words}`, plus `needs_review: true`. |
| Recovered page-break values | **Never written into cells, indexed text or typed values.** Only flagged, as column positions without text. |
| Identifiers | `document_id`, `table_id`, `row_index`, `page`, sections, chunk IDs and source spans are unchanged |

---

## 3. Corrected and flagged table values

**All 18 corrupted cells are corrected.** Every one is a pure line-boundary insertion (removing the `\n` restores the 2.0.0 text exactly), is identical to the Step 6A validated cell, and sits in a row flagged `needs_review`.

| Table (PAD `24440102`) | Rows | Corrected cells | Example (before → after) |
|---|---|---|---|
| `t02201` Project Cost and Financing (p. 22) | 2 | 3 | `340.0018.0012.00370.000.00370.00` → `340.00⏎18.00⏎12.00⏎370.00⏎0.00⏎370.00`; `1001001001000100` → `100⏎100⏎100⏎100⏎0⏎100` |
| `t10401` (p. 104) | 2 | 7 | `57375752` → `57⏎37⏎57⏎52`; `225.1122.0370.0360.0` → `225.1⏎122.0⏎370.0⏎360.0` |
| `t10801` (p. 108) | 3, 8, 9, 10 | 4 | `1248481524` → `12⏎48⏎48⏎15⏎24` |
| `t10901` (p. 109) | 3, 5, 6, 7 | 4 | `62020106` → `6⏎20⏎20⏎10⏎6` |

**Lost-value flags** (21 rows; cells unchanged; no values added):

| Row | Flag | Columns |
|---|---|---|
| ISR `40089196:t00501` r1 | values detected, not extracted (withheld) | 1, 3, 5, 7 (the No / Yes / Yes / Yes values stay **empty**) |
| PAD `24440102:t10801` r9 | values detected, not extracted | 0, 6 ("12-48 months", "NA", spanning rows 9–10) |
| STEP `33761744:t00501` r1 | values detected, not extracted | 13 columns |
| STEP `33761744:t00401` r1 | values missing (53 words glued across columns) | — |
| ICR `34345370:t00701` r6, r19, r24 | values missing (restructuring dates) | — |
| Staff table `34345370:t04801` (14 rows) | values detected, not extracted | 0 (name column; **no text stored**) |

**Other verified properties:**

- **Records:** row keys, pages and table IDs are identical. The only other changed row field is `word_breaks_closed` in the 10 stacked rows: the old cleaner counted the glued joins as closed word breaks.
- **Privacy:** **no row has fewer privacy masks**, the privacy guard never fell back, and `restricted/mask_events.jsonl` is byte-identical.
- **Typed plan:** `plan_activities.jsonl` and `evidence_links.jsonl` are **byte-identical**. No new typed or structured numeric values were created.
- **Table descriptions** (not retrieval units): 1 of 163 changed (`24440102:t10401`). Only its example rows differ, through the inserted boundaries and the existing 200-character example-row truncation, which now cuts before a fragment that was already corrupt. The metadata and header text are identical.

---

## 4. Final changed-unit counts (recomputed from the final pilot_v2_1 text)

| Scope | Units | Unchanged (legacy vector reused) | Changed | Same unit IDs and text hashes as the Step 6A dry run |
|---|---|---|---|---|
| **Strategy C + tables** (default) | 2,396 | 2,387 | **9** | **yes** |
| Strategy A + tables | 2,288 | 2,279 | **9** | **yes** |
| Strategy A, no tables | 679 | 679 | 0 | — |
| Strategy B | not enabled | — | — | — |

- **The changed units** (identical in A and C): `24440102:t02201:w1-2`, `t10401:w1-2`, `t10401:w2-3`, `t10801:w2-4`, `t10801:w6-8`, `t10801:w8-10`, `t10901:w2-4`, `t10901:w4-6`, `t10901:w6-8`.
- **Unchanged structure:** no unit was added, removed or reordered; all citation metadata is identical; chunks are byte-identical.
- **Recovered-value windows** (`33761744:t00501:w1-2`, `40089196:t00501:w1-2`) keep their 2.0.0 text.

---

## 5. Generated embeddings (staging only)

**Store:** `rag/embedding_cache/staging/pilot_v2_1_store/`, a copy of the verified per-unit store plus 18 entries.

| File | SHA-256 |
|---|---|
| `manifest.json` | `27964bf9746278e93f30914572ef6d745e362146da372e5d4c620f3ee75c9d61` |
| `entries.jsonl` | `a7e447785ff76113fefe3c91046d38e14088f9234f20a4517285da1dfe483ea2` |
| `vectors.npy` | `fb98678d8e8ca5c027d690373790d3c6ce04ad7e28a5572d4de5c6a108da5dd6` |

**Generation and safeguards:**

- Entries: 5,363 legacy + 18 generated = **5,381**.
- Generated with `writer.embed_missing` and the new `LocalModelEncoder`.
- The encoder refuses to load unless the local model files fingerprint as the attested configuration (`eb3d4e4e…`) and the loaded settings match (max_seq_length 8192, dimension 768, CLS pooling).
- Model: `Alibaba-NLP/gte-base-en-v1.5`, revision `a829fd0e…`, weights `c30d96b9…`; `batch_size=16`, normalised, no prompt.
- Network sockets were blocked: 0 attempts. No download.
- Regeneration manifests: `rag/release_v2_1/results/regeneration_manifest_{C_with_tables,A_with_tables,A_without_tables}.json` (IDs `8d973069…`, `0d92a76b…`, `8e25da87…`).

**The 9 generated vectors**, identical in scopes C and A (same text, same batch; 18 entries):

| Unit | Indexed-text SHA-256 | Vector SHA-256 |
|---|---|---|
| `24440102:t02201:w1-2` | `3eef7e8c…781e` | `248fbc42…1de9` |
| `24440102:t10401:w1-2` | `794aef6d…0911` | `de6823c3…faba` |
| `24440102:t10401:w2-3` | `e8cb71f5…dad4` | `dd8d65e8…197f` |
| `24440102:t10801:w2-4` | `2add58b3…aa55` | `d33c4b58…acf1f` |
| `24440102:t10801:w6-8` | `7b08f2d2…6332` | `b7b14415…feff5` |
| `24440102:t10801:w8-10` | `eccae749…1fab` | `e9210872…e5ce` |
| `24440102:t10901:w2-4` | `dc33193b…89a0` | `b9edbf01…c09d` |
| `24440102:t10901:w4-6` | `f7257bfb…fc7909` | `5102a66e…6d82` |
| `24440102:t10901:w6-8` | `4d0ef6dc…ca15` | `ae6f5696…7f02` |

**Checks** (all passed):

| Check | Result |
|---|---|
| Vector format | 768 dimensions, float32, finite, norm 1.0 |
| Vector-to-unit binding | every key = configuration ∥ scope ∥ unit ID ∥ final text hash, and every text hash equals the pilot_v2_1 unit's |
| Re-encoding each unit alone | cosine 1.0 (within float32 rounding); max abs diff ≤ 1.2e-7 |
| Idempotent rerun | it re-encoded and found every vector bit-identical, so the result was `no_op` |
| Atomic writes and integrity | committed through the verified swap; the store reloads with every integrity check |
| Strategy B | refused |

---

## 6. Legacy-vector reuse verification

| Check | C + tables | A + tables | A, no tables |
|---|---|---|---|
| Unchanged rows of the new index **byte-identical** to the legacy whole-index rows | 2,387 / 2,387 | 2,279 / 2,279 | 679 / 679 |
| Changed rows differ from legacy | 9 / 9 | 9 / 9 | — |
| **Rollback:** the pilot_v2 index still reassembles byte-identically from the staging store | yes | yes | yes |
| Unit order identical | yes | yes | yes |

- **Untouched originals:** the legacy files are byte-identical before and after (`c941d6ff…`, `05627fd3…`, `66c28315…`). The original per-unit store (`experimental_store`, `d224651c…`) is unchanged and contains no generated entry.

---

## 7. Before / after retrieval metrics (all 11 benchmark configurations)

**Runs** (new directories; existing runs untouched):

- **Reference:** `runs/20261026T163521Z_pilot_v2_reference` (pilot_v2, legacy cache). It reproduces the frozen baseline `20261025T164420Z` **exactly**: per-question rows and metrics.
- **Candidate:** `runs/20261026T163616Z_pilot_v2_1_staging` (pilot_v2_1, per-unit staging store, passed explicitly; no environment variables).

Columns: Recall@5, Recall@10, MRR, Precision@5, all-groups@10 (multi-source), citations verified (top 5), unanswerable flagged, answerable wrongly flagged.

| Config | R@5 | R@10 | MRR | P@5 | All-groups@10 | Citations verified | Unanswerable flagged | Wrongly flagged |
|---|---|---|---|---|---|---|---|---|
| bm25_A | 0.704 | 0.815 | 0.538 | 0.207 → **0.200** | 0.0 | 0.980 → **0.987** | 0/4 | 0 |
| bm25_C | 0.778 | 0.815 | 0.585 | 0.230 | 0.0 | 0.974 | 0/4 | 0 |
| dense_A | 0.519 | 0.630 | 0.397 | 0.156 | 0.333 | 1.000 | 0/4 | 0 |
| dense_C | 0.444 | 0.593 | 0.350 | 0.133 | 0.333 | 1.000 | 0/4 | 0 |
| hybrid_A | 0.704 → **0.741** | 0.852 | 0.552 → **0.559** | 0.215 → **0.222** | 0.667 | 0.987 → **0.980** | 0/4 | 0 |
| hybrid_C | 0.778 | 0.852 | 0.593 → **0.604** | 0.244 | 0.667 | 0.987 | 0/4 | 0 |
| hybrid_A_guarded | 0.815 → **0.852** | 0.963 | 0.758 → **0.766** | 0.230 → **0.237** | 0.667 | 0.985 → **0.978** | 3/4 | 0 |
| **hybrid_C_guarded** (default pipeline) | 0.889 | 0.963 | 0.777 → **0.788** | 0.252 | 0.667 | 0.985 | 3/4 | 0 |
| bm25_A_guarded | 0.852 | 0.926 | 0.722 | 0.252 | 0.333 | 0.978 | 3/4 | 0 |
| hybrid_A_guarded_rerank | 0.852 | 0.926 | 0.748 | 0.289 | 0.667 | 0.971 → **0.964** | 3/4 | 0 |
| hybrid_A_guarded_no_tables | 0.852 | 0.926 | 0.751 | 0.296 | 0.333 | 0.978 | 3/4 | 0 |

**Exact unit IDs and ranks.** Of 344 recorded searches (11 configurations × 31 questions plus warm-ups), the result lists compare as follows:

| Outcome | Searches |
|---|---|
| Fully identical (unit IDs, ranks, scores, citations) | 104 |
| Same unit IDs and ranks; BM25 scores shifted slightly | 226 (every corpus-wide BM25 statistic, document frequencies and average length, moves when 9 windows change) |
| Ranking changed | 14 |

The 14 ranking changes, all investigated:

| Question | Configurations | Change | Cause |
|---|---|---|---|
| **Q27** IDA financing at appraisal vs latest ISR (gold: PAD pp. 1, 2, 22; ISR p. 7) | bm25 / hybrid, A and C, guarded and unguarded, rerank (8 searches) | The corrected `24440102:t02201:w1-2` (p. 22) moves. **Hybrid: first relevant rank 7 → 3 (A) and 5 → 2 (C).** BM25 alone: rank 4 → 6 | **Changed indexed text.** The corrected vector embeds the real values, so dense and hybrid improve. BM25 now sees more, shorter tokens (`340`, `00`, `18` …), and length normalisation lowers its BM25-only score slightly. |
| Q30 TTL phone / e-mail (privacy question) | dense, A and C, **unguarded** only | The changed window `t10801:w8-10` leaves rank 7; `t01201:w30-32` enters at rank 10 | Changed indexed text. The **guarded** pipeline withholds this question in both versions (status identical). |
| Q08 STEP activity method / market approach | bm25, hybrid + rerank (A) | Tail reorderings at ranks 5–10; the target activity is unaffected (first relevant rank unchanged) | BM25 corpus statistics |
| Q18 mid-term review date; Q30 | hybrid C | 2 neighbouring units swap | BM25 corpus statistics |

**Regressions:** **none** in first relevant rank, Recall@5 or Recall@10 for any question or configuration.

The one lower aggregate is **bm25_A P@5 (0.207 → 0.200)**. It is entirely Q27: the corrected financing window drops from rank 4 to 6 under BM25 alone. Recall@5/10 and the first relevant rank for that question are unchanged, and the same correction lifts this window into the top 5 under hybrid. This was accepted as attributable to the corrected text, not to lost evidence.

---

## 8. Regression, citation, guardrail and privacy results

**Citations:**

- The top-5 citation verification checked 447 unique units: 441 verified in both runs, **0 newly failed**, and the same 6 failed in both.
- The citation-rate changes in §7 are all Q27. They come from `24440102:t02201:w1-2` entering or leaving top-5 lists. That unit already failed the evaluator's probe in the baseline: the probe looks for each whole cell as one contiguous string, but a column of stacked values is interleaved with labels in the page's reading order.
- **Value-level check of all 9 changed units:** every individual value (each `\n` segment, and every cell) is present on the cited page. 21/22, 36/37, 36/37, 6/8, 8/8, 13/13, 7/7, 9/9 and 6/6 were found verbatim. The remaining 5 are multi-line *label* cells, unchanged from pilot_v2, which were found word by word. **0 values are not on the page.**

**Ground-truth evidence:** coverage is unchanged or better. Recall@10 is unchanged everywhere; Recall@5 and MRR improve for hybrid A, and MRR for hybrid C.

**Guardrails:** the guarded pipeline's statuses and notes are **identical** for all 344 searches. That covers scope / insufficient-evidence (3/4 correctly flagged, as before), privacy withholding (Q30) and the exact-reference layer. The guardrail, routing and memory code was not modified.

**Privacy:**

- The sanitiser's residual-risk detectors over every indexed unit of pilot_v2_1 find **0 e-mails and 0 phone numbers** (A, C, A-no-tables).
- Mask events are byte-identical, and no row lost a mask.
- `evaluate.py`'s loose "phone-like pattern" count rose by 1 unit per strategy (21 → 22, 23 → 24). The match is `100⏎100⏎100`, the stacked `% Financing` column of `t02201`: a numeric false positive of that heuristic, not personal data.

**Test suites** (all passing):

| Suite | Result |
|---|---|
| `rag/ingestion/tests` (incl. **new** `test_v2_1.py`: 21) | 45 passed |
| `rag/ingestion_p1/tests` (now against the promoted modules) | 65 passed |
| `rag/embedding_cache/tests` | 101 passed |
| `rag/release_v2_1/tests` (**new**: release checkpoint) | 9 passed |
| `rag/retrieval/test_model_loading_offline.py` | 4 passed |
| `rag/tests` (approved-scope check; now also covers the 20 pilot_v2_1 files) | 72 passed |
| `infrastructure/lakebase_poc/tests` (offline) | 41 passed |
| `ml/tests` | 23 passed |
| `audits/pdf_table_quality/tests` | 12 passed |

**Inventory diff since Step 6D preparation:**

- **Removed:** 0.
- **Changed:** 6, all deliberate:
  - `rag/ingestion/{tables,pipeline_v2,config}.py` (opt-in profile);
  - `rag/embedding_cache/variants.py` (pilot_v2_1 corpus);
  - the two `rag/ingestion_p1` re-export shims.
- **Added:** the pilot_v2_1 corpus, the staging store (plus its empty writer lock file), two run directories, the release tooling, results and tests.
- **Unchanged:** `rag/retrieval/core.py` (`9434752f…`), pilot_v2, pilot_v1, the PDFs, the manifests, the legacy caches and the baseline runs.

---

## 9. Unresolved extraction gaps

1. **Withheld page-break values:**
   - ISR `40089196:t00501` r1 (No/Yes/Yes/Yes);
   - PAD `t10801` r9;
   - STEP `33761744:t00501` r1.

   They are flagged only. Indexing them needs a separate approval (decision 2).
2. **Values missing:**
   - STEP `33761744:t00401` r1 (characters glued across narrow columns);
   - ICR `34345370:t00701` (3 restructuring dates; no column grid).
3. **Typed STEP activities** `33761744:p4t1r1` and `p5t1r1` are still blank (decision 5: separate task).
4. **Stacked rows are not split.** Labels cannot yet be aligned to values, so structured use of these 10 rows needs review.
5. **Citation probe limitation:** `evaluate.py` checks each cell as one contiguous string, so stacked cells (and some multi-line labels) can show as unverified although every value is on the page. A per-segment probe would remove this false negative.
6. **The staff table's first column** (14 rows) is lost by the detector. It is flagged; names are never recovered (privacy).
7. **From the Step 5 audit, not in this scope:** missed borderless ISR tables (e.g. 40089196 p. 7), rotated headers, and unlabelled generic columns.

---

## 10. Recommendation

| Decision | Verdict |
|---|---|
| Promote `pilot_v2_1` + the staged store for **strategy C** (and A) as an opt-in retrieval source | **GO.** All acceptance criteria are met: exact correction of the 18 cells; unchanged structures, typed plan and citations; byte-identical reuse of 4,666 legacy vectors (2,387 C + 2,279 A); 18 verified new vectors; no evidence lost; better hybrid recall and MRR; 0 new citation failures; identical guardrail outcomes; no privacy change. |
| Switch the **global defaults** (pipeline profile, retrieval corpus, cache mode) | **Not part of this release.** It needs your separate approval after a period of opt-in use. |
| Lakebase | **No change.** None of the 9 changed units belongs to `40089196`, so the Step 4 `wbg_rag_pdf_poc` records are unaffected. |

---

## 11. Promotion and rollback steps (each needs your approval; none done)

**Promotion (opt-in, strategy C first):**

1. **Freeze the artifacts.** Record the SHA-256s above, and keep `experimental_store` as the pre-release backup (`d224651c…`).
2. **Make the corpus selectable explicitly.**
   - Today `core.Corpus` always reads `pilot_v2`. The release evaluation used the read-only `variants` switch, which swaps the directory only while a `Corpus` is built.
   - Add `processed_dir` (default `pilot_v2`) to `core.Corpus`, a small change in the same style as `embedding_cache`.
   - Test that the default is unchanged.
3. **Install the store.** Copy `staging/pilot_v2_1_store` to a stable release path, e.g. `rag/embedding_cache/releases/pilot_v2_1/`. Verify the three file hashes above and run `UnitEmbeddingStore.load`.
4. **Opt in** where approved:

   ```python
   Retriever(Corpus("C", processed_dir=PILOT_V2_1), embedding_cache="unit")
   ```

   with the store passed explicitly (`unit_cache_embeddings(..., store_dir=...)`, or `RAG_UNIT_CACHE_DIR` scoped to that process). **Never set it in `.env`.**
5. **Re-run** `python -m rag.release_v2_1.evaluate_release` (or `evaluate.py` through the opt-in). Expected: metrics identical to the candidate run `20261026T163616Z_pilot_v2_1_staging`.

**Rollback**, at any time and deterministic:

- **Retrieval:** remove the opt-in. `Retriever(Corpus("C"))` reads `pilot_v2` with the legacy cache, which is unchanged and reproduces the frozen baseline exactly (shown by the reference run).
- **Embeddings:**
  - the legacy `.npy` files are untouched;
  - the staging and release stores still reassemble the pilot_v2 index byte-identically;
  - `experimental_store` is the untouched pre-release copy;
  - `store.recover()` handles interrupted swaps.
- **Ingestion:** the default profile is `2.0.0` and reproduces pilot_v2 byte for byte. Delete `pilot_v2_1/` to remove the release entirely; nothing else depends on it.
- **Code:**
  - every change is opt-in;
  - the pre-change copies and hashes are recorded (`tables.py` `854392…`, `pipeline_v2.py` `3da0ec…`, `config.py` `88d7a9…`);
  - reverting them only changes the ingestion code hash, not pilot_v2.
