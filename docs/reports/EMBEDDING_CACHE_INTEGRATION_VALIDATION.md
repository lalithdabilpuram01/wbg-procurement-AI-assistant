# Embedding Cache: Opt-in Integration and Validation (Step 6C)

**Date:** 2026-10-26.

**Status:** the per-unit embedding cache can now be selected through an **explicit opt-in**. The default is unchanged: the existing whole-index cache.

**What was verified:**

- Model compatibility of the legacy vectors, on 38 units, with the local model files only and the network blocked.
- Bit-exact matrix reconstruction for strategies A and C.
- Identical rankings and citations in both modes.
- Fail-closed behaviour for every mismatch and corruption case tested.

**Not done:**

- The default cache setting is unchanged.
- Step 6A is not merged.
- No embedding was generated for any changed unit.
- Strategy B is not enabled.
- Lakebase and Databricks were not touched.

**Next step:** stopping here for approval.

---

## 1. Opt-in adapter implementation

**`rag/retrieval/core.py`:** the only modified existing file. The complete diff is 14 added lines (2 of them blank) and 1 changed signature:

```python
class Corpus:
    def __init__(self, strategy="A", include_tables=True):
        self.strategy = strategy
+       self.include_tables = include_tables          # needed to name the index scope

+EMBEDDING_CACHE_ENV = "RAG_EMBEDDING_CACHE"
+EMBEDDING_CACHE_MODES = ("legacy", "unit")

class Retriever:
-   def __init__(self, corpus):
+   def __init__(self, corpus, embedding_cache=None):
        ...
+       self.embedding_cache = embedding_cache or os.environ.get(EMBEDDING_CACHE_ENV) or "legacy"
+       if self.embedding_cache not in EMBEDDING_CACHE_MODES: raise ValueError(...)

    def embeddings(self):
+       if self._emb is None and self.embedding_cache == "unit":
+           ...  # add the repository root to sys.path if missing
+           from rag.embedding_cache.adapter import unit_cache_embeddings
+           self._emb = unit_cache_embeddings(self.c)       # returns the verified matrix or raises
        if self._emb is None:
            ... existing whole-index code, unchanged ...
```

| Item | SHA-256 |
|---|---|
| `core.py` before | `1eba5ec5…7e80` |
| `core.py` after | `9434752f…dd49` |

- Chunking, unit order, indexed text, BM25, query encoding, ranking, guards and citations are not touched.
- In unit mode, the matrix is either returned complete or an exception is raised. The legacy branch can't be reached afterwards, because a failed lookup leaves nothing to fall back to and raises again on retry (tested).

**[`rag/embedding_cache/adapter.py`](../../rag/embedding_cache/adapter.py):** new. `unit_cache_embeddings(corpus)` serves the matrix only if all four of these hold:

1. **The scope is enabled.** `A+tables`, `A-tables` and `C+tables` are. **`B+tables` is refused** (decision 4).
2. **A compatibility attestation** (`results/compatibility.json`) has verdict `compatible`, carries **this exact configuration fingerprint**, and marks as compatible **every legacy file the store's vectors came from**. The first test run proved this guard works: it refused the store until the third legacy file (A without tables) had been verified too.
3. **The store loads with every Step 6B integrity check:** configuration fingerprint, file hashes, entry schema with no text, recomputed keys, per-vector hashes, shape, dtype and norms.
4. **Every unit has an entry for its exact indexed text.** Otherwise `MissingEmbeddingsError` lists the unit IDs.

Nothing is encoded, written or taken from another cache.

**How to opt in:**

- per retriever: `Retriever(corpus, embedding_cache="unit")`;
- per process: `RAG_EMBEDDING_CACHE=unit`.

`RAG_UNIT_CACHE_DIR` and `RAG_UNIT_CACHE_ATTESTATION` can point at another store or attestation.

---

## 2. Default behaviour is unchanged

- With no argument and no environment variable, `embedding_cache == "legacy"`, and `embeddings()` runs the original code. The test returns the byte-identical `emb_C_2396_…npy` matrix.
- Unknown values (`"units"`, `"per-unit"`) are rejected instead of being silently treated as legacy.
- No caller was changed (`AnswerPipeline`, `demo.py`, `evaluate.py`, the Lakebase POC). All of them keep the legacy default, and strategy C stays the answering default.
- **Retrieval regressions:**
  - The existing suites pass (§6).
  - BM25 reproduces the stored benchmark run `20261025T164420Z` **31 / 31** questions for `bm25_A`, `bm25_C` and `bm25_A_guarded` with the modified `core.py`.

---

## 3. Model compatibility: local re-encoding of 38 sampled units

**Method** ([`verify_compatibility.py`](../../rag/embedding_cache/verify_compatibility.py), run separately from normal retrieval):

- **Model loading:** the exact current model, loaded with `core.load_cached_model` (local files only), Hugging Face offline mode, and **every outbound socket blocked**. 0 connection attempts.
- **Loaded settings:** match the configuration (max_seq_length 8192, dimension 768, Transformer + CLS pooling, CPU), and `can_flatten_inputs = False`.
- **Original inference reproduced:**
  - sentence-transformers 5.4.1 orders each strategy's whole unit list by its own length rule and encodes it in batches of 16.
  - The script computed that order with the model's own functions (`_input_length`, `_can_flatten_inputs`) over all 2,288 / 2,396 / 679 units.
  - Each sampled unit was re-encoded **inside its original batch**: the same 16 texts, `batch_size=16`, `normalize_embeddings=True`.
  - Each unit was also encoded **alone**, to measure the batch effect.
- **Thresholds** were fixed before the run:
  - same-batch pass: max abs diff ≤ 1e-5 and cosine ≥ 0.99999;
  - incompatibility indicator: cosine < 0.999.
- **Runtime:** 123 s on CPU.
- **Output:** only checksums and metrics. **No vector was added to any cache.**

**Sample** (deterministic rules; 38 units, 34 from A/C plus 4 for the third legacy file):

- **Strategy A + tables and C + tables** (17 each):
  - one median-length chunk per document type (PAD, PP, ISR, ICR, ICRR);
  - the median chunk;
  - the longest chunk (3,639 / 2,807 characters);
  - the shortest chunk (89 characters);
  - the longest, shortest and median table window;
  - the median activity;
  - the first unit in batch order (batch 0, slot 0);
  - the last unit (final batch; C's is a partial batch of 12);
  - 2 arbitrary units (lowest SHA-256 of the unit ID).
- **Shared A/C units:** 3 table/activity units whose two legacy vectors differ (minimum, median and maximum difference: 9.3e-8, 1.3e-7, 2.7e-7), each checked in both A and C.
- **Strategy A without tables:** median chunk, median activity, first and last in batch order.

**Results:**

| Group | Units | Same original batch: bit-identical | Max abs diff | Min cosine | Encoded alone: max abs diff |
|---|---|---|---|---|---|
| **All** | **38** | **38** | **0.0** | **1.0** | 2.7e-7 (15 / 38 still bit-identical) |
| A + tables | 17 | 17 | 0.0 | 1.0 | 2.7e-7 |
| C + tables | 17 | 17 | 0.0 | 1.0 | 1.5e-7 |
| A, no tables | 4 | 4 | 0.0 | 1.0 | 1.5e-7 |
| Chunks / table windows / activities | 19 / 14 / 5 | 19 / 14 / 5 | 0.0 | 1.0 | ≤ 2.7e-7 |
| First / middle / last slot in batch | 5 / 27 / 6 | 5 / 27 / 6 | 0.0 | 1.0 | ≤ 2.7e-7 |

- **Norms:** every legacy and new vector has norm 1.0 (float32).
- **Checksums:** for every unit, the legacy and same-batch SHA-256 are identical. Both are recorded per unit in `results/compatibility.json`.

**Harmless difference vs incompatibility.** The negative controls are the same model with a deliberately different configuration, on 8 units:

| Control | Cosine to the legacy vector |
|---|---|
| Re-encoded in original batch (actual check) | **1.0** (bit-identical) |
| Encoded alone (batch effect only) | ≥ 0.99999994; max abs diff ≤ 2.7e-7 |
| Prompt prefix `"query: "` | 0.972 – 0.997 |
| Truncated to 64 tokens | 0.800 – 1.0 (1.0 only where the text is shorter than 64 tokens) |
| Another unit's legacy vector | 0.38 – 0.65 |
| No normalisation | norm 21.6 – 22.0 instead of 1.0 |

**Interpretation:**

- The legacy vectors are exactly what this model, configuration and batching produce. That is the strongest possible result: equality, not similarity within a tolerance.
- The only deviation is the batch effect (≤ 2.7e-7, at float32 rounding level). It explains the 2.7e-7 A/C differences found in Step 6B exactly: each shared unit encoded alone equals C's legacy vector bit for bit, while A's differs only because A encoded it in a different batch.
- A real configuration or model incompatibility moves cosine to 0.997 or lower, at least 30,000× further from 1.0 than the batch effect does.

**Verdict:** `compatible`. All four checks pass:

- within thresholds;
- loaded settings match;
- the controls are detectable;
- no network attempts.

Legacy files attested:

- `emb_A_2288_…` (SHA-256 `c941d6ff…`);
- `emb_C_2396_…` (`66c28315…`);
- `emb_A_679_…` (`05627fd3…`).

---

## 4. Legacy vector import safety

The import is safe. Every one of the 5,363 store entries comes from a legacy file that:

- passed the Step 6B structural checks (exact key, order and count; shape; dtype; finite values; norms; provenance dates);
- **is now proven compatible** by local re-encoding, as an exact reproduction.

The store itself was not rebuilt or modified in Step 6C (inventory diff: unchanged). If the model snapshot, weights, remote code, preprocessing or any library version changes, the configuration fingerprint changes. Both the attestation and the store are then refused (tested).

---

## 5. Exact array reconstruction

| Scope | Opt-in matrix vs legacy `.npy` | Shape | dtype | Order |
|---|---|---|---|---|
| A + tables | **byte-identical** | (2288, 768) = | float32 = | identical (lookup by unit key, assembled in corpus order) |
| A, no tables | **byte-identical** | (679, 768) = | float32 = | identical |
| C + tables (default strategy) | **byte-identical** | (2396, 768) = | float32 = | identical |

**Rankings and citations.** The model was not loaded for this comparison: the query encoder was replaced by fixed vectors.

| Comparison | Identical |
|---|---|
| Benchmark-wide, A and C each: 31 questions × dense and hybrid × guarded and unguarded | **124 / 124** result lists per scope, including every score and the full citation record (unit, document, version, pages, printed page label, section, table, excerpt, source URL) |
| Unit tests: A and C, 4 queries, `dense`, `hybrid`, `bm25` | **6 / 6** configurations equal |

**Load time:** 4 ms (legacy) vs 71 ms (unit, including full integrity verification) per retriever.

---

## 6. Retrieval regression results

| Suite | Result |
|---|---|
| **New:** `rag/embedding_cache/tests/test_adapter.py` | **23 passed** |
| `rag/embedding_cache/tests` (store, legacy, pilot; Step 6B) | 48 passed (71 in total with the above) |
| `rag/retrieval/test_model_loading_offline.py` (cache-only model loading; sockets blocked) | 4 passed |
| `rag/tests` | 52 passed |
| `rag/ingestion/tests` | 24 passed |
| `rag/ingestion_p1/tests` | 65 passed |
| `infrastructure/lakebase_poc/tests` (offline) | 41 passed |
| `ml/tests` | 23 passed |
| `audits/pdf_table_quality/tests` | 12 passed |
| Stored benchmark run, BM25 reproduction | 31 / 31 × 3 configs |

**Inventory diff since Step 6B:**

- Changed: 1 file (`rag/retrieval/core.py`).
- Added: 5 files (`adapter.py`, `verify_compatibility.py`, `tests/test_adapter.py`, `results/compatibility.json`, and the Step 6B report written after that baseline).
- Removed: 0.
- No `__pycache__` was created.

**Legacy whole-index caches:** byte-identical, with the same SHA-256 as recorded in Step 6B (`c941d6ff…`, `05627fd3…`, `66c28315…`). There are still exactly these 3 files.

---

## 7. Corruption and mismatch tests (all fail closed)

| Case | Result |
|---|---|
| Step 6A text change (stacked-only variant, strategy C) | `MissingEmbeddingsError` naming exactly the 9 changed windows. Nothing encoded, no legacy file written, and the same error on retry |
| Strategy B | `UnitCacheNotAllowedError` |
| Attestation missing, verdict not `compatible`, other fingerprint, or legacy file not covered | `UnitCacheNotAllowedError` |
| Another model configuration (even with its own matching attestation) | `CacheIntegrityError`: the store fingerprint differs |
| Flipped byte in `vectors.npy` | `CacheIntegrityError` |
| Store files missing, or the store directory (via environment) missing | Rejected |
| Unknown mode value | `ValueError` |
| Step 6B store tests (still passing) | 9 config variants; 11 corruption types including tampered vectors with re-hashed manifests, a `text` field, swapped IDs and wrong shapes; legacy key / order / norm / date refusals |

---

## 8. Remaining risks

| # | Risk | Mitigation / status |
|---|---|---|
| 1 | **Unit mode is read-only.** Any indexed-text change (e.g. merging Step 6A) makes it fail until the missing vectors are added. There is no write path yet: embedding misses, appending them and re-attesting are not implemented. | Intended for now (decision 6). Build the write path together with the approved embedding of the 18 units, with a lock and an atomic save. |
| 2 | **Newly embedded units will come from a different batch** than a full rebuild would use (≤ 2.7e-7). | Reuse stays exact. Compare future rebuilds with a tolerance (1e-5) and retrieval metrics, never by hash. |
| 3 | **The verification relies on private sentence-transformers functions** (`_input_length`, `_can_flatten_inputs`) to reproduce batching. | Library versions are part of the fingerprint, so an upgrade invalidates both the attestation and the store, and the verification must be re-run. |
| 4 | **The attestation is a plain JSON file.** It guards against accidental use, not deliberate tampering. | The vectors themselves are hash-checked. If needed, record the attestation's SHA-256 in a reviewed config. |
| 5 | **The environment variable is process-wide.** If it were set in a shell profile or `.env`, every `Retriever` would switch. | For production, opt in through the constructor argument in reviewed code. Keep the environment variable for sessions and tests. Never set it in `.env`. |
| 6 | **The store lives inside the code package** (`rag/embedding_cache/experimental_store/`, 18 MB) and covers pilot_v2 only. | On activation, place it at a stable data path (`RAG_UNIT_CACHE_DIR`). The 35-project corpus will need its own store and attestation. |
| 7 | **The Lakebase POC** (`pdf_evidence.py`) reads the legacy `.npy` directly, not through `Retriever.embeddings()`. | Unaffected. It stays on the legacy path. |

---

## 9. Recommended activation and rollback procedure

**Activation, after your approval (does not change today's results):**

1. Keep the default `legacy`.
2. Opt in where needed with `Retriever(corpus, embedding_cache="unit")`, starting with strategy C (`AnswerPipeline` / `demo.py`), or `RAG_EMBEDDING_CACHE=unit` for a session.
3. **Pre-flight on every start:** the adapter refuses unless all of these hold:
   - the attestation matches the current configuration;
   - the store passes integrity;
   - every unit is present.

   No manual check is needed, but log the store manifest hash and the attestation verdict.
4. Run `evaluate.py` into a **new** `runs/` directory in unit mode. Expected outcome: metrics identical to `20261025T164420Z`, because the matrices are byte-identical.
5. Only later, and separately approved:
   - implement the write path;
   - merge the Step 6A stacked-value fix into a new processed version;
   - embed its 18 changed units (9 for A, 9 for C);
   - re-verify and re-evaluate;
   - then consider switching the default.

**Rollback** (deterministic, takes seconds):

1. Remove the opt-in: drop the argument, or unset `RAG_EMBEDDING_CACHE`.
2. `Retriever.embeddings()` then runs the unchanged legacy code over the untouched legacy files (SHA-256 recorded above).
3. A full code rollback is also possible: restore `core.py` to `1eba5ec5…`. The diff is 15 lines, and no caller depends on the new argument.
4. The store and attestation are separate files and can be deleted without effect.

---

## 10. Go / no-go

| Decision | Verdict |
|---|---|
| Make the per-unit cache **available as an opt-in** for strategies A and C on the pilot corpus | **GO.** Compatibility is established exactly (38/38 bit-identical), reconstruction is byte-identical, rankings and citations are identical, every failure mode fails closed, and the default is unchanged. |
| **Switch the default** to the per-unit cache | **NO-GO for now.** It brings no benefit until changed text needs new vectors, and that requires the write path plus your approval to embed the 18 Step 6A units. |
| Strategy B, cross-strategy reuse, Lakebase | Not enabled (decisions 4, 5 and 8). |
