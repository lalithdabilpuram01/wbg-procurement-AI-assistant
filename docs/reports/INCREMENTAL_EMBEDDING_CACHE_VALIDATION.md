# Incremental Embedding Cache: Isolated Implementation and Offline Validation (Step 6B)

**Date:** 2026-10-26.

**Status:** a per-retrieval-unit embedding cache has been implemented in isolation (`rag/embedding_cache/`) and validated offline.

- The active retrieval code (`rag/retrieval/core.py`) is **not** switched to it.
- No model was loaded, no embedding was generated, and nothing was downloaded or sent anywhere.
- The legacy caches, pilot_v2, Lakebase, UC Volumes, the PDFs and the manifests are unchanged.

**Next step:** stopping here for approval.

---

## 1. Summary

| Requirement | Result |
|---|---|
| Changed / unchanged units per strategy | Measured exactly (§4). The approved stacked-value fix changes **9 table windows** in each table-indexing scope; the full Step 6A output changes **11**. All other units are unchanged. |
| Unchanged units reuse their original vectors exactly | **Yes.** The store reassembles all three legacy indexes **bit for bit**: A (2,288), A without tables (679), C (2,396). Every cache hit in the Step 6A variants is the identical legacy row. |
| Changed text invalidates only the affected units | **Yes.** In each scope, the set of misses equals the set of changed units, and assembling an incomplete index is refused. |
| A changed model or preprocessing invalidates incompatible entries | **Yes.** All 9 configuration changes tested (revision, weights, remote code, normalisation, max length, prompt, dimension, batch size, library version) reject the store and get 0 hits. |
| Missing or corrupted caches are rejected | **Yes.** 11 damage cases are rejected; the whole store is rejected, never patched. |
| Ordering, metadata and citations stay aligned | **Yes.** Unit order and all citation fields are identical between pilot_v2 and the Step 6A variants in every scope. Vectors are looked up by key, not by position. |
| Verified whole-index embeddings are not overwritten | **Yes.** The SHA-256, size and mtime of the 3 legacy `.npy` files are identical before and after. They are opened read-only. |
| No unsanitised content enters the cache | **Yes.** The store holds IDs, hashes and vectors only, and **no text**: 0 of 3,237 text samples were found in it. Its inputs are sanitised pilot_v2 and Step 6A outputs, never `restricted/`. |
| No silent mixing of model versions, preprocessing or dimensions | **Yes.** A store is bound to one configuration fingerprint. Every vector is checked for dimension, dtype, finiteness and norm on write and on load. |
| Read-only comparison with existing retrieval results | BM25 reproduces the stored run `20261025T164420Z` **31 / 31** for `bm25_A`, `bm25_C` and `bm25_A_guarded`. Dense and hybrid results are identical by construction, because the assembled matrices are byte-identical. |

---

## 2. Current cache behaviour and its limitations

In `rag/retrieval/core.py`, `Retriever.embeddings()` works like this:

```python
key = sha256("\n".join(u.unit_id + u.text for u in units) + EMBED_MODEL)[:16]
file = index_cache/emb_{strategy}_{len(units)}_{key}.npy
if file exists: load it   else: encode ALL units (batch_size=16, normalize_embeddings=True) and save
```

**Actual preprocessing settings**, read from the code and the local model files only:

| Setting | Value |
|---|---|
| Model | `Alibaba-NLP/gte-base-en-v1.5` |
| Revision | `a829fd0e060bb84554da0dfd354d0de0f7712b7f`, the only local snapshot |
| Weights | `model.safetensors`, SHA-256 `c30d96b9…2d2b` (its LFS blob name) |
| Remote code | `Alibaba-NLP/new-impl` revision `40ced75c3017…` (`trust_remote_code=True`) |
| Pooling | CLS |
| `max_seq_length` | 8192 |
| Dimension | 768 |
| Dtype | float32 |
| Normalisation | `normalize_embeddings=True` |
| Prompt / prefix | none: the unit's indexed text (header + text) is encoded as is |
| Encode batch size | 16 |
| Libraries | sentence-transformers 5.4.1, transformers 4.57.3, torch 2.9.1 |

**Existing whole-index caches** (`rag/retrieval/index_cache/`):

| File | Scope | Units | Matches current pilot_v2 units? |
|---|---|---|---|
| `emb_A_2288_15c5eb5b3be5c490.npy` | Strategy A + tables | 2,288 (443 chunks, 1,609 table windows, 236 activities) | Yes, the key verifies |
| `emb_A_679_a6ac726ebf15c0d5.npy` | Strategy A, no tables | 679 | Yes |
| `emb_C_2396_d1afdad97857d7e4.npy` | Strategy C + tables | 2,396 (551 chunks, 1,609, 236) | Yes |
| *(none)* | Strategy B + tables | 2,410 | **Never embedded** |

**Limitations:**

1. **All or nothing.** The key hashes every unit's text, so editing one table window invalidates the whole file, and every unit of the strategy is re-encoded (2,288 or 2,396). This is why Step 6A could not be merged safely.
2. **The key records only the model name.** It does not record the revision, weights, remote code, normalisation, max length, prompt, dimension or library versions. A change to any of these is **silently** served the old vectors.
3. **No integrity check on load.** Shape, dtype, NaN and norm are not verified, and a corrupted or truncated file is used as is.
4. **The key is truncated to 64 bits** and has no manifest, so there is no record of how a file was produced.
5. **Vectors depend on the batch they were encoded in.** This was measured here:
   - The same unit with the same text is **not always bit-identical** across the existing files: 83 of the 1,845 table and activity units shared by A and C differ by at most **2.7e-7** (≈ float32 rounding).
   - sentence-transformers sorts inputs by length into batches, so a unit's neighbours change its last bits.
   - The difference is negligible for ranking, but it means "exact reuse" must be scoped to the index that produced the vector.

---

## 3. Design (implemented in `rag/embedding_cache/`)

### 3.1 Identity

`identity.EmbeddingConfig` is built from **local files only**: the HF cache `refs/main`, the snapshot, the LFS blob names and the JSON configs, plus installed package metadata. No model import.

| Identity component (spec) | How it is captured |
|---|---|
| Retrieval unit ID | `unit_id` in the entry key |
| Exact indexed-text hash | SHA-256 of the exact UTF-8 indexed text (header + body) in the entry key |
| Model identity and revision | `model_id`, `model_revision`, `weights_sha256` and `remote_code_revision` in the config fingerprint |
| Preprocessing configuration | `prompt`, `max_seq_length`, `pooling`, `batch_size`, plus a hash of `config.json`, `modules.json`, `sentence_bert_config.json` and the pooling config; library versions are included as well |
| Dimensionality and normalisation | `dimension`, `dtype`, `normalize`, also enforced on every vector |
| Retrieval / index strategy | **Index scope** (`A+tables`, `A-tables`, `B+tables`, `C+tables`) in the entry key |

**Why the index scope is in the key.** The spec asks for the strategy when it affects the indexed text. The text hash already covers that, because strategy-specific chunks have their own IDs and texts. The scope is included **as well** because of limitation 5: without it, A and C would share one vector for each of their 1,845 common units. Strategy C would then get 83 vectors that are 2.7e-7 off its verified index, instead of its own exact ones.

**Entry key** = SHA-256(config fingerprint ∥ scope ∥ unit ID ∥ text SHA-256).

### 3.2 Store

One directory per store, bound to **one** configuration:

- **`manifest.json`:** schema version, the full config, its fingerprint, the entry count, and the SHA-256 of both data files.
- **`entries.jsonl`:** per entry `key, scope, unit_id, text_sha256, vector_sha256, row, provenance` (e.g. `legacy:emb_C_2396_….npy#517`). **No text field is allowed**: any other field rejects the store.
- **`vectors.npy`:** float32 (N, 768), one row per entry, loaded with `allow_pickle=False`.

| Operation | Behaviour |
|---|---|
| `plan(scope, units)` | Returns hits and misses. A miss means the unit needs (re)embedding. |
| `assemble(scope, units)` | Builds the index matrix **in the caller's unit order** by key lookup. It raises `MissingEmbeddingsError` with the unit IDs if *any* unit misses, so there is never a partial or stale matrix. |
| `put(...)` | Validates the vector (shape, dtype, finite, norm 1 ± 1e-4). It **never replaces** an existing key. |
| `save(dir)` | Writes to a temporary sibling directory, then renames it over the target, so a failed write leaves the old store intact. |
| `load(dir, config)` | Checks the schema, the fingerprint against the requested config, the manifest/config consistency, both file hashes, the matrix shape and dtype, the entry fields, the row order, duplicate keys, each recomputed key and each vector's SHA-256, then validates every vector. **Any failure rejects the whole store.** |

### 3.3 Legacy import (`legacy.py`)

A legacy `.npy` is accepted for an ordered list of units only if all of these hold:

1. Its file name carries **exactly** the key `core.py` computes for those units, in that order and with that count.
2. It is float32, (N, 768), finite and unit-normalised.
3. Provenance is established:
   - same model ID, normalisation, batch size and no prompt as `core.py`;
   - the **only** local model snapshot (May 10) and the embedding libraries (installed May 7) **predate** the file (October 25).

Row *i* is then stored for unit *i*, because `core.py` encodes units in corpus order. The file is memory-mapped read-only and copied, never written.

---

## 4. Changed and unchanged retrieval units per strategy

The variants are built with the unchanged `core.Corpus`, swapping only the table-rows source in memory:

- **pilot_v2:** the indexed corpus as it is today;
- **P1 stacked-only:** the Step 6A **approved** fix; recovered page-break values are reverted to pilot_v2, per decision 2;
- **P1 full:** the Step 6A output as produced.

| Scope | Units | P1 stacked-only: changed / unchanged | P1 full: changed / unchanged | Cache hits / misses (stacked-only) | Hits identical to legacy vectors |
|---|---|---|---|---|---|
| A + tables | 2,288 | **9** / 2,279 | **11** / 2,277 | 2,279 / **9** | 2,279 / 2,279 |
| A, no tables | 679 | 0 / 679 | 0 / 679 | 679 / 0 | 679 / 679 |
| B + tables | 2,410 | **9** / 2,401 | **11** / 2,399 | 0 / 2,410 (no legacy index) | — |
| C + tables | 2,396 | **9** / 2,387 | **11** / 2,385 | 2,387 / **9** | 2,387 / 2,387 |

**The 9 changed windows (stacked values):** all in `24440102`.

- `t02201` w1-2
- `t10401` w1-2, w2-3
- `t10801` w2-4, w6-8, w8-10
- `t10901` w2-4, w4-6, w6-8

**P1 full adds 2 windows (recovered values):**

- `33761744:t00501` w1-2
- `40089196:t00501` w1-2

**Also confirmed:**

- No unit was added, removed or reordered in any scope.
- The 5 windows that contain only `values_missing` rows keep their text.
- Narrative chunks and plan activities are untouched.

**Correction to the Step 6A report.** It stated "12 of 1,845" windows. The correct figure is **11 of 1,609 table windows**; 1,845 also counted the 236 activities. Its §10 has been corrected.

---

## 5. Legacy cache compatibility

| Legacy file | Key verified | Shape / dtype / norms | Provenance evidence | Imported | Re-assembled vs file |
|---|---|---|---|---|---|
| `emb_A_2288_…` | ✔ | (2288, 768) float32; norms 1.0 | 6 / 6 | 2,288 | **byte-identical** |
| `emb_A_679_…` | ✔ | (679, 768) | 6 / 6 | 679 | **byte-identical** |
| `emb_C_2396_…` | ✔ | (2396, 768) | 6 / 6 | 2,396 | **byte-identical** |
| B | no file | — | — | 0 | — |

- **Store:** 5,363 entries (18 MB) at `rag/embedding_cache/experimental_store/`.
- **Config fingerprint:** `eb3d4e4e8d66…`.
- **Legacy files before and after:** identical SHA-256, size and mtime. For example, `emb_C_2396`'s SHA-256 is `66c28315…5ada` in both.

**Strategy B:** all 2,410 units would need embedding, because B has never been indexed.

- 1,845 of them (table windows and activities) have the same ID and text as units already in A/C.
- Reusing those across scopes would leave only 565 chunks to embed. It would, however, mix batch contexts (≤ 2.7e-7), so it is **not** enabled. It is available as an option if you approve a tolerance policy.

**Lakebase compatibility (no Lakebase access).** The Step 4 `unit_embeddings.embedding_sha256` is `sha256(float32 vector bytes)`, which is the same definition as `vector_sha256` here on little-endian hardware. Store entries can therefore be matched to the Lakebase records later without re-reading the vectors from Lakebase.

---

## 6. Integrity checks (all fail closed)

| Check | When | Validated by |
|---|---|---|
| Config fingerprint equals the requested config, and the manifest is internally consistent | load | 9 config variants rejected (pilot) + 8 (synthetic) |
| File SHA-256 of `vectors.npy` / `entries.jsonl` | load | Flipped byte and truncated entries rejected |
| Matrix shape (N, dim) and dtype | load | Wrong shape (re-hashed manifest) rejected |
| Entry fields exactly whitelisted (no `text`) | load | Added `text` field rejected |
| Row order, duplicate keys, key recomputed from its fields | load | Swapped unit IDs rejected |
| Per-entry vector SHA-256 | load | Replaced vector (consistent re-hashed manifest) rejected |
| Finite, unit norm, dimension, dtype | put + load | NaN, unnormalised, wrong-dim and float64 vectors refused |
| Missing files / invalid JSON | load | Missing manifest, missing vectors, non-JSON manifest rejected |
| No overwrite | put | Existing key never replaced |
| No partial index | assemble | Incomplete unit list → `MissingEmbeddingsError` with IDs |
| Legacy: key, count and order; shape; norms; provenance | import | Other text, reversed order, wrong count, unnormalised, wrong dim and pre-model file date all refused |

---

## 7. Offline tests and regressions

| Suite | Result |
|---|---|
| **New:** `rag/embedding_cache/tests/test_store.py` (synthetic, no pilot data) | **31 passed**. Covers reuse in any order, single-unit invalidation, scope/ID misses, no overwrite, invalid vectors, round trip and atomic save, rejection under 8 config variants, 6 corruption cases, no text in files, legacy import, legacy mismatch / malformed / provenance refusals, and stable hashes. |
| **New:** `rag/embedding_cache/tests/test_pilot_cache.py` (real legacy caches + Step 6A rows, read-only) | **17 passed**. Covers bit-identical reassembly of A, A-no-tables and C; exactly the 9 / 11 changed windows in A, B and C; misses equal changes; hits are the original vectors; aligned order and citation metadata; no B legacy index; recovered values excluded from the approved variant. |
| `rag/ingestion/tests` | 24 passed |
| `rag/ingestion_p1/tests` | 65 passed |
| `rag/tests` | 52 passed |
| `infrastructure/lakebase_poc/tests` (offline) | 41 passed |
| `ml/tests` | 23 passed |
| `audits/pdf_table_quality/tests` | 12 passed |

**Read-only retrieval comparison** against the latest stored run `rag/retrieval/runs/20261025T164420Z`, with no model loaded:

- **Reproduction:**
  - `bm25_A`, `bm25_C` and `bm25_A_guarded` reproduce **31 / 31** questions (same top-1 unit and same first relevant rank).
  - Dense and hybrid configs use the matrix the store reassembles byte-identically, so their scores and rankings are unchanged by construction.
  - Query vectors were not recomputed, because that would mean running the model.
- **Step 6A effect on BM25** (the only retriever that can run without embeddings):

| Config | Variant | Questions whose top-10 changed | First relevant rank changed | Recall@5 | Recall@10 |
|---|---|---|---|---|---|
| bm25_A | stacked-only / full | 2 / 3 | 0 / 0 | 19/27 → 19/27 | 22/27 → 22/27 |
| bm25_C | stacked-only / full | 0 / 1 | 0 / 0 | 21/27 → 21/27 | 22/27 → 22/27 |
| bm25_A_guarded | stacked-only / full | 2 / 3 | 0 / 0 | 23/27 → 23/27 | 25/27 → 25/27 |

Files written:

- `rag/embedding_cache/{__init__, identity, store, legacy, variants, validate_cache}.py`
- the tests
- `experimental_store/`
- `results/validation.json`

**Inventory diff since Step 6A:** 0 changed and 0 removed files. No `__pycache__` or `.pytest_cache` was created.

---

## 8. Expected embeddings needing regeneration

| Scope | Approved stacked-only fix | Full Step 6A output | Today (whole-index cache) |
|---|---|---|---|
| A + tables | **9** | 11 | 2,288 |
| C + tables | **9** | 11 | 2,396 |
| A, no tables | 0 | 0 | 0 |
| B + tables (never indexed) | 2,410 (or 565 with approved cross-scope reuse) | 2,410 | 2,410 |
| **A + C total** | **18** | 22 | 4,684 |

None of these was generated. Generating the 18 requires your approval, and it runs the local model only (no download).

---

## 9. Risks before switching the active pipeline

| # | Risk | Mitigation |
|---|---|---|
| 1 | **Batch-dependent vectors.** Newly embedded units come from a different batch than a full rebuild would use, so a later full rebuild will not be bit-identical (≤ about 3e-7). | Exact equality is guaranteed for *reuse*. Compare *rebuilds* with a tolerance (e.g. 1e-5 per component) and with retrieval metrics. |
| 2 | **Legacy provenance relies on evidence** (one snapshot, file dates), not a recorded manifest. | Before the switch (approval needed), re-encode a small sample (e.g. 32 units) locally and require a max abs diff ≤ 1e-5 against the imported vectors. |
| 3 | **Library versions are in the identity.** Any torch, transformers or sentence-transformers upgrade invalidates the whole store, which means a full re-embed. | This is intended (no silent mixing). Pin versions, and plan upgrades as re-embedding events. |
| 4 | **Step 6A text rendering is still open.** Stacked cells contain `\n`; the final rendering (e.g. `"; "`) changes the text hashes. | The counts are unaffected (the same 9 / 11 windows). Embed only after the rendering is fixed. |
| 5 | **Table-window IDs depend on row numbers.** A future extraction change that adds or removes rows renames many windows. | The cache detects this correctly as misses, but the cost could be large. Report the miss count before every embedding run. |
| 6 | **No write lock**: a single writer is assumed. | Add a lock file and keep the atomic rename when integrating. |
| 7 | **Store growth**: append-only entries for superseded text accumulate. | Garbage-collect entries not referenced by the current *and* the previous verified index (the previous one is kept for rollback). |
| 8 | **Strategy B has never been embedded.** | Decide whether B is needed. Cross-scope reuse requires an explicit tolerance policy. |
| 9 | The **Lakebase `wbg_rag_pdf_poc`** unit for `40089196:t00501:w1-2` would differ from a re-indexed corpus if the full P1 output were adopted. | It is left unchanged (decision 4). The approved stacked-only variant does not touch any `40089196` unit. |

---

## 10. Safe integration plan, with rollback

1. **Opt-in switch (no behaviour change by default).**
   - Add `embedding_cache="legacy" | "unit"` to `Retriever`, defaulting to `legacy`.
   - In `unit` mode: load the store (a rejected store is an error, never a fallback), then `plan`. If there are misses, raise `MissingEmbeddingsError` unless embedding was explicitly allowed; then `assemble`.
   - Tests: the legacy mode stays byte-identical, and the unit mode equals the legacy matrices for pilot_v2.
2. **Provenance spot-check** (approval: runs the local model on about 32 units, no download). Confirms risk 2.
3. **Merge Step 6A stacked-only into a new processed version** (`ingest-pilot-2.1.0`, a new output directory). pilot_v2 and its caches stay as they are.
4. **Embed only the misses** (approval): 9 for A and 9 for C. Append them to the store, re-verify the store, assemble, then confirm that every hit is byte-identical to the legacy rows.
5. **Re-evaluate** with `evaluate.py` into a **new** `runs/` directory. Compare with `20261025T164420Z`, including the citation and privacy checks.
6. **Switch the default** to `unit` after your review. The legacy `.npy` files remain read-only references.

**Rollback** at any step:

- Set `embedding_cache="legacy"` and point retrieval at pilot_v2. The untouched legacy files (SHA-256 recorded in `results/validation.json`) and pilot_v2 reproduce today's results exactly. The 31/31 BM25 reproduction and the byte-identical matrices show this.
- The store is a separate, append-only directory: removing it affects nothing else.
- Lakebase is never part of this path.

**Out of scope until approved:**

- merging Step 6A into the active pipeline;
- generating any embedding;
- changing Lakebase;
- the two incomplete typed STEP activities (decision 5).
