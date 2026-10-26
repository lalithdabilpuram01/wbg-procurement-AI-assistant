# Incremental Embedding Writer and Strategy-C Opt-in Confirmation (Step 6D Preparation)

**Date:** 2026-10-26.

**Scope:** the two activities you approved.

- **Part A:** an isolated strategy-C retrieval evaluation in both cache modes.
- **Parts B to D:** an offline-tested incremental write path for the per-unit store, plus the Step 6A dry-run.

**Not done:**

- The Step 6A extraction fixes are not merged.
- No real embedding was generated; the writer was tested with synthetic vectors on temporary stores only.
- The default cache is still `legacy`.
- `RAG_EMBEDDING_CACHE` was never set: the mode is passed explicitly to each `Retriever`, and `.env` was not touched.
- Strategy B stays disabled.
- Lakebase and Databricks were not touched.

**Next step:** stopping here for approval.

---

## 1. Summary

| Item | Result |
|---|---|
| Strategy C, whole-index vs per-unit (`embedding_cache="unit"`) | **Exact parity** across all 125 recorded searches (identical unit IDs, ranks, scores and citation records), per-question rows, metrics, citation checks and privacy checks. Both modes are also identical to the stored baseline run `20261025T164420Z`. |
| All methods, guarded and unguarded | bm25, dense and hybrid, each guarded and unguarded, plus hybrid + rerank: **31 / 31** questions identical in each of the 7 combinations |
| Incremental writer | Implemented: dry-run manifest, then all-or-nothing apply, with a crash-safe commit and recovery. **30 new tests** (synthetic vectors), all passing. |
| Strategy C units changed by the stacked-value fix | **9 of 2,396. Confirmed.** 2,387 are reused exactly. The 2 windows with recovered page-break values are **not** in the manifest. |
| Regression suites | All pass: 101 cache tests in total and every existing suite (§5) |
| Generating the corrected embeddings | **Conditional GO.** The writer is ready, but the indexed text of the 9 units is not final yet (§8). |

---

## 2. Part A: strategy-C opt-in benchmark

**Method.** [`rag/embedding_cache/evaluate_strategy_c.py`](../../rag/embedding_cache/evaluate_strategy_c.py) runs the **unchanged** `rag/retrieval/evaluate.py`, restricted to its existing strategy-C configurations: `bm25_C`, `dense_C`, `hybrid_C` and `hybrid_C_guarded`. It runs twice:

- with every `Retriever` created as `Retriever(corpus, embedding_cache="legacy")`;
- then with `embedding_cache="unit"`.

A thin subclass passes the mode and records each search's complete result list. Models load from local files only, every outbound socket is blocked, and 0 connection attempts were made.

**New run directories** (existing runs untouched):

- `rag/retrieval/runs/20261026T161334Z_strategyC_legacy/`
- `rag/retrieval/runs/20261026T161352Z_strategyC_unit/`

Each holds the standard evaluate outputs plus `full_results.json` (sanitised excerpts, as in the citation records).

**Exact comparison** (latency fields excluded, because they are wall-clock):

| Comparison | Result |
|---|---|
| Full result lists, unit vs legacy (125 searches = 4 configs × 31 questions + warm-up) | **125 / 125 identical**: unit IDs, ranking, bm25 / dense / RRF scores, exact-match flags and every citation field (document, version, SHA-256, pages, printed page label, section, table, excerpt, source URL, activity and evidence links) |
| `per_question.csv` (top-1, first relevant rank, recall, precision, MRR, citations verified, insufficient flags) | identical, unit vs legacy, and **both identical to the baseline `20261025T164420Z`** |
| `metrics.json` | identical, unit vs legacy, and both identical to the baseline |
| `citation_check.json`, `privacy_check.json` | identical |
| In-process parity: bm25, dense and hybrid, each guarded and unguarded, plus hybrid + rerank (real query encoding, local model) | **31 / 31** in each of the 7 combinations |

**Strategy-C metrics** (identical in both modes and in the baseline; 27 answerable and 4 unanswerable questions):

| Config | Recall@5 | Recall@10 | MRR | Precision@5 | Citations verified (top 5) | Unanswerable flagged | Answerable wrongly flagged |
|---|---|---|---|---|---|---|---|
| bm25_C | 0.778 | 0.815 | 0.585 | 0.230 | 0.974 | 0/4 | 0 |
| dense_C | 0.444 | 0.593 | 0.350 | 0.133 | 1.000 | 0/4 | 0 |
| hybrid_C | 0.778 | 0.852 | 0.593 | 0.244 | 0.987 | 0/4 | 0 |
| hybrid_C_guarded | 0.889 | 0.963 | 0.777 | 0.252 | 0.985 | 3/4 | 0 |

**Conclusion:** the strategy-C per-unit opt-in is confirmed. It is exactly equivalent to the whole-index cache.

---

## 3. Part B: writer design and safety checks

**[`rag/embedding_cache/writer.py`](../../rag/embedding_cache/writer.py)** is new. It has no network access and no database connection, and nothing in `core.py` or the pipeline imports it.

| Step | Function | What it guarantees |
|---|---|---|
| 1. Dry run | `dry_run(store_dir, config, scope, units, label)` | Lists exactly the units without a valid vector: their **position in index order**, unit ID, SHA-256 of the exact indexed text, the text hash they replace, and the reason (changed text or new unit). It is bound to the configuration fingerprint, the scope, the **SHA-256 of the store's `manifest.json`**, and a hash of the unit order. The manifest's ID is the SHA-256 of its content, so it is tamper-evident. **It contains no text.** It is written atomically (temporary file, fsync, rename). |
| 2. Apply | `apply(store_dir, config, manifest, [(unit_id, vector)], label)` | Adds **exactly** the manifest's units in **one commit**, or nothing (rejection list below). |
| 2′. Embed | `embed_missing(..., units, encoder, ...)` | Encodes exactly the manifest's units with a supplied encoder, then calls `apply`. Before encoding it requires that the encoder's configuration fingerprints the same as the store and manifest, and that **each unit's current text still hashes to the manifest value**. Tested with a synthetic encoder only; **no real-model encoder is wired**. |

**Rejections.** The whole batch is refused and the store is unchanged (each case is tested) when:

- the vectors cover anything other than exactly the manifest's units (missing, unlisted or duplicated);
- any vector has the wrong dimension or dtype, a NaN, or a norm other than 1;
- an existing key arrives with a **different** vector (conflict). The **same** vector makes the rerun a no-op, so the writer is idempotent;
- the manifest was made for **another configuration**, or the store no longer matches the manifest's base (**stale**);
- the manifest has been tampered with;
- the scope is **B** (or any disabled scope), even with a forged manifest;
- the store is **corrupted** (full integrity load first: a corrupted store is never extended);
- the label is free text: only `[A-Za-z0-9._:-]{1,64}` is allowed, so no document content or credentials can enter the provenance;
- **another writer holds the lock** (`fcntl` exclusive, non-blocking, released on process exit);
- the target is the adapter's **default store** without `allow_default_store=True`, which nothing in the repository sets.

**Binding.** Every new entry is keyed by SHA-256(config fingerprint ∥ scope ∥ unit ID ∥ text SHA-256), the same identity as the reader. Its provenance is `generated:<label>:manifest:<manifest-id prefix>`.

**Crash-safe commit** (`store.save`, rewritten for this step; the **reader `load` is unchanged**):

1. Write and fsync a temporary sibling directory: vectors, entries, manifest.
2. **Verify it loads** with every integrity check before it can become visible.
3. Rename the current store aside (`.old-<ns>`).
4. Rename the new store into place, and fsync the parent directory.
5. Delete the old copy.

On an ordinary error, the swap is rolled back immediately. On a hard crash between steps 3 and 4 there is **no** store. `load` then fails closed (it never finds a partial or old-and-new mix), and `store.recover()` restores the last committed store and removes temporary leftovers.

The previous `save` deleted the old store *before* renaming the new one in. That crash window is now closed.

---

## 4. Parts C and D: Step 6A compatibility and dry-run regeneration manifest

**Changed and unchanged units** for the approved stacked-value fix (variant `p1_stacked`: recovered values reverted to pilot_v2):

| Scope | Units | Reused (exact legacy vector) | To generate | Recovered-value windows (not in the manifest) |
|---|---|---|---|---|
| **C + tables** (default) | 2,396 | **2,387** | **9** | `33761744:t00501:w1-2`, `40089196:t00501:w1-2` |
| A + tables | 2,288 | 2,279 | 9 | the same 2 |
| B | disabled: no manifest |

The count of **9 is confirmed** for strategy C, unchanged from Step 6B. Recovered page-break values are neither in the manifest nor in any indexed text of the approved variant.

**Dry-run regeneration manifest:** `rag/embedding_cache/results/dry_run_manifest_C_p1_stacked.json`.

| Field | Value |
|---|---|
| Manifest ID | `11c4ab84…179b` |
| Configuration fingerprint | `eb3d4e4e…68f6` |
| Base store | `manifest.json` SHA-256 `d224651c…0a61`, 5,363 entries |
| Unit-order hash | `5766e35f…59d3` |
| Source rows | `experimental_p1/table_rows.jsonl`, SHA-256 `0f532411…30e5` |

| Position | Unit | New text SHA-256 | Replaces text SHA-256 |
|---|---|---|---|
| 664 | `24440102:t02201:w1-2` | `3eef7e8c…` | `feda821a…` |
| 881 | `24440102:t10401:w1-2` | `794aef6d…` | `1235727c…` |
| 882 | `24440102:t10401:w2-3` | `e8cb71f5…` | `99805ac1…` |
| 884 | `24440102:t10801:w2-4` | `2add58b3…` | `14e905c5…` |
| 886 | `24440102:t10801:w6-8` | `7b08f2d2…` | `aa43590f…` |
| 887 | `24440102:t10801:w8-10` | `eccae749…` | `358bdfe0…` |
| 889 | `24440102:t10901:w2-4` | `dc33193b…` | `6be61a35…` |
| 890 | `24440102:t10901:w4-6` | `f7257bfb…` | `879abf57…` |
| 891 | `24440102:t10901:w6-8` | `4d0ef6dc…` | `5c2ed522…` |

The strategy-A manifest (`dry_run_manifest_A_p1_stacked.json`, ID `7d9e6e3c…`) lists the same 9 units. The store was read only: its manifest SHA-256 is identical before and after.

**Important:** these text hashes are for the **experimental** rendering of stacked cells (values separated by `\n` inside a cell). If the merge renders them differently (e.g. `"; "`), the dry run must be re-run. The writer enforces this: `embed_missing` refuses any unit whose text no longer matches the manifest.

---

## 5. Tests and regressions

**New:** `rag/embedding_cache/tests/test_writer.py`, 30 tests, all synthetic vectors on temporary stores (the pilot test copies the real store first):

| Requirement | Tests |
|---|---|
| Atomic addition of missing vectors | apply adds 3 (changed + new); no leftovers; old text still served exactly |
| Idempotent reruns | same manifest and vectors → `no_op`, store hash unchanged |
| Conflicting writes | different vector for an applied entry → rejected; overlapping stale manifest → rejected; store unchanged |
| Exactly the identified units | missing / unlisted / duplicated units → rejected |
| Vector validation | wrong dimension, all-ones (unnormalised), float64, NaN → batch rejected, store unchanged |
| Configuration and identity | other model revision → rejected; dry run under another normalisation → store rejected; tampered manifest → rejected; encoder with another batch size → rejected; unit text changed after the dry run → rejected |
| Stale provenance | store changed after the dry run → rejected |
| Corruption | flipped byte → never extended |
| Interrupted writes | failure while writing the copy → store unchanged; error between the renames → rolled back; **hard crash between the renames** → load fails closed, `recover()` restores, rerun succeeds; partial temporary directory → never loads, cleaned up |
| Concurrency and guard rails | second writer → refused; default store without permission → refused; free-text labels → refused; scope B → refused, even when forged |
| **Pilot copy, strategy C** | dry run = 9 to generate and 2,387 reused; after applying 9 synthetic vectors, every one of the 2,387 unchanged rows equals the legacy vector exactly, the 9 rows equal the synthetic vectors, unit order equals pilot_v2, and the **real store is untouched** |

**Failures found during development** (both fixed):

1. A stale manifest that overlapped an applied one was reported as a "conflict". The batch was still rejected, but the message was wrong. The writer now reports staleness first, unless every unit is already present.
2. A rerun of an applied manifest with *different* vectors was then reported as "stale". It now reports a conflict.

**Suites:**

| Suite | Result |
|---|---|
| `rag/embedding_cache/tests` (writer 30, adapter 23, store / legacy / pilot 48) | **101 passed** |
| `rag/retrieval/test_model_loading_offline.py` | 4 passed |
| `rag/tests` | 52 passed |
| `rag/ingestion/tests` | 24 passed |
| `rag/ingestion_p1/tests` | 65 passed |
| `infrastructure/lakebase_poc/tests` (offline) | 41 passed |
| `ml/tests` | 23 passed |
| `audits/pdf_table_quality/tests` | 12 passed |

**Default retrieval unchanged:**

- `core.py` is byte-identical to Step 6C (`9434752f…`).
- The adapter tests confirm that the default is `legacy`, with byte-identical output.
- The legacy caches keep their SHA-256s (`c941d6ff…`, `05627fd3…`, `66c28315…`).
- The experimental store is unchanged (`d224651c…`).

**Inventory diff since Step 6C:**

- **Changed:** 2 files of the experimental package. `store.py` has the safe commit plus `put_hashed`, `has_key`, `validate_vector` and `entries`; `validate_cache.py` pins its comparison baseline to `20261025T164420Z`, so the new runs are never mistaken for it.
- **Added:** writer, dry-run and evaluation modules, tests, results, and the two new run directories.
- **Removed:** nothing.

---

## 6. Remaining compatibility risks

| # | Risk | Status / mitigation |
|---|---|---|
| 1 | **Stacked-cell rendering is not final.** The 9 text hashes depend on it. | Decide the rendering, then re-run the dry run. The writer refuses stale text. |
| 2 | **Batch effect.** The 9 units will be encoded in a batch of their own, not in the batch a full rebuild would put them in. The vectors may differ from a rebuild by about 3e-7 (measured in 6C). | Harmless for ranking. Record them as `generated:` provenance; compare future rebuilds with a tolerance. |
| 3 | **No real-model encoder is wired** (by design for this step). | The next step adds a thin `SentenceTransformer` encoder that reproduces `encode(..., batch_size=16, normalize_embeddings=True)`, verifies its configuration against the store's, and runs offline with sockets blocked. |
| 4 | **The attestation covers legacy vectors only.** Generated vectors are trusted through the writer's checks, not a re-encoding attestation. | After generation, re-encode the 9 units alone once and require cosine ≥ 0.99999 to their stored vectors (a self-consistency check). |
| 5 | **Old entries for the 9 superseded texts remain in the store** (append-only). | Intended: rollback to pilot_v2 text keeps working. Garbage-collect them only after the new version is accepted. |
| 6 | Library or model upgrades invalidate the store and attestation (fingerprint). | Intended. Plan upgrades as full re-embedding and re-verification. |
| 7 | **Lakebase `wbg_rag_pdf_poc`** holds Step 4 units for `40089196`. | None of the 9 units belongs to `40089196`; Lakebase is unaffected and remains unchanged (decision 8). |

---

## 7. Proposed integration and rollback sequence (each step needs your approval)

1. **Fix the stacked-cell rendering** and merge the stacked-only Step 6A fix into a **new** processed version (e.g. `pilot_v2_1`, `ingest-pilot-2.1.0`). pilot_v2 stays as it is.
2. **Re-run the dry run** against the new version's units. Expected: 9 for C (and 9 for A). Any other count stops the process.
3. **Stage:**
   - copy the store;
   - generate the 9 C vectors (and the 9 A vectors) with the real-model encoder into the **copy** (`embed_missing`, local model, sockets blocked);
   - run the self-consistency check (risk 4) and a full `load`.
4. **Evaluate** on the new version with `Retriever(corpus, embedding_cache="unit")`, pointing `RAG_UNIT_CACHE_DIR` at the copy for that process only. Write to a new run directory and compare with `20261025T164420Z`. Expected: changes limited to questions that touch the 9 windows.
5. **Promote:** replace the default store with the verified copy using the same atomic swap, then keep the opt-in for strategy C. Switching the global default is a separate decision.

**Rollback** at any point:

- **Retrieval:** omit `embedding_cache="unit"` (default `legacy`), or point back to pilot_v2. The legacy `.npy` files and pilot_v2 are untouched (hashes recorded), and the baseline results reproduce exactly (shown in §2).
- **Store:** the promoted store still contains every original entry. Keeping the pre-promotion copy allows a byte-identical restore. `store.recover()` handles interrupted swaps.
- **Code:** the writer is not imported by the pipeline, so removing it has no effect on retrieval.

---

## 8. Go / no-go: generating the actual corrected embeddings

| Question | Verdict |
|---|---|
| Is the write path safe and ready? | **Yes.** It is offline-tested (30 tests, including crash recovery and every rejection path) and is exactly bound to unit, text, scope and configuration. |
| Generate the 18 corrected vectors (9 C + 9 A) **now**? | **NO-GO today.** Their indexed text is still the experimental rendering, and the Step 6A merge is not approved. Generating now would produce vectors for text that may never be indexed. |
| Generate them **after** steps 1–2 of §7? | **GO**, on your approval: into a staging copy, with the real-model encoder added under the same offline guards, followed by the self-consistency check and an isolated evaluation before promotion. |
