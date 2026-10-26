# RAG Version Integration: `pilot_v2_1` as an Opt-in Retrieval Version (Step 6E)

**Date:** 2026-10-26.

**Status:** integrated and validated as an **opt-in** version.

- **Default unchanged:** `pilot_v2` with the whole-index cache. Its results are byte-for-byte unchanged.
- **Not done:** no default was switched, nothing was deployed, Lakebase and Databricks were not touched, nothing was re-embedded, no model was downloaded and no hosted LLM was called.
- **Next step:** stopping here.

---

## 1. How to use the improved version (exact configuration)

```python
from rag.retrieval.versions import build_retriever

retriever = build_retriever("pilot_v2_1")                  # strategy C, tables included, per-unit release store
hits = retriever.search(question, method="hybrid", k=10, guarded=True)

# grounded answering, unchanged pipeline (it already accepts a retriever):
from pipeline import AnswerPipeline                        # rag/answering
pipe = AnswerPipeline(generator, retriever=build_retriever("pilot_v2_1"))
```

| Setting | Default (`pilot_v2`) | Opt-in (`pilot_v2_1`) |
|---|---|---|
| Call | `build_retriever()`, identical to `Retriever(Corpus("C"))` | `build_retriever("pilot_v2_1")` |
| Corpus | `rag/corpus_v3/processed/pilot_v2` (extraction 2.0.0) | `rag/corpus_v3/processed/pilot_v2_1` (extraction 2.1.0) |
| Embeddings | whole-index `rag/retrieval/index_cache/emb_C_2396_d1afdad97857d7e4.npy` | per-unit release store `rag/embedding_cache/releases/pilot_v2_1/store/` |
| Strategies | A, C (as today) | **C only**, with tables |
| Strategy B | not offered | not offered |
| Validation | as today | at construction (fail fast) |

**Rules for the opt-in:**

- No environment variable is needed. `RAG_EMBEDDING_CACHE` was never set, and must **not** be put in `.env`.
- The model, retrieval methods, guards, reranker, citation records and document IDs are the same in both versions.

**Release descriptor:** `rag/embedding_cache/releases/pilot_v2_1/release.json` (SHA-256 `69186544…`). It binds together:

| Bound item | Value |
|---|---|
| Corpus | `rag/corpus_v3/processed/pilot_v2_1`, `run_manifest.json` SHA-256 `3bd68473…c3c3`, `extraction_version` `ingest-pilot-2.1.0`, code hash `cae7261b4979a8ac` |
| Indexed text (C + tables) | ordered (unit ID, text SHA-256) fingerprint `e09bd87c…5c6f` |
| Store files | `manifest.json` `27964bf9…`, `entries.jsonl` `a7e44778…`, `vectors.npy` `fb98678d…` (identical to the Step 6D staging store) |
| Embedding configuration | fingerprint `eb3d4e4e…68f6` (gte-base-en-v1.5, revision `a829fd0e…`, weights `c30d96b9…`) |
| Validation evidence | SHA-256 of the Step 6D results (`staging.json`, `evaluation.json`, `corpus_verification.json`, `build.json`) |

---

## 2. Implementation

| File | Change |
|---|---|
| `rag/retrieval/core.py` (`9434752f…` → `f205b5da…`) | **`Corpus(..., processed_dir=None)`**: `None` means the default corpus, read exactly as before (`jl(name)` with `PILOT`). `jl(name, base=None)`. The **whole-index cache now refuses any non-default corpus** (it would otherwise silently re-encode every unit and write a new `.npy` into `index_cache`). No other change: ordering, indexed text, BM25, dense, hybrid, guards, rerank and citations are untouched. |
| `rag/retrieval/versions.py` (**new**, 66 lines) | Version registry (`pilot_v2` default, `pilot_v2_1` opt-in), `build_corpus` and `build_retriever`. Unknown versions, strategy B, strategy A for `pilot_v2_1`, and no-tables mode for `pilot_v2_1` are rejected. The non-default version loads and verifies its embeddings at construction. |
| `rag/embedding_cache/adapter.py` | A non-default corpus is served **only through a registered release** whose descriptor matches **all** of: scope enabled; corpus run-manifest SHA-256; extraction version; indexed-text fingerprint of the scope; store file hashes; configuration fingerprint. On top of that, the existing attestation, store integrity and per-unit completeness checks apply. Any mismatch raises `ReleaseMismatchError` or `UnitCacheNotAllowedError`. The default-corpus path is unchanged. |
| `rag/release_v2_1/install.py` (**new**) | Copies the validated staging store to `releases/pilot_v2_1/store`. It verifies the Step 6D hashes, never overwrites a different release (an identical re-run is a no-op), and writes `release.json`. |
| `rag/embedding_cache/variants.py`, `rag/release_v2_1/stage_embeddings.py`, `tests/test_release.py` | Use `processed_dir` instead of patching `core.PILOT`. The Step 6D staging-content checks now read the store directly, because strategy A on `pilot_v2_1` is no longer served through the adapter. |

**Unchanged:**

- the legacy whole-index caches (`c941d6ff…`, `05627fd3…`, `66c28315…`);
- `experimental_store` (`d224651c…`);
- `pilot_v2`, `pilot_v1`, the PDFs and manifests;
- Lakebase, Databricks and Bronze/Silver/Gold;
- the ML models;
- the Structured Data Agent, MCP, routing, LangGraph, guardrails and conversation-memory **code**.

---

## 3. Retrieval results: complete strategy-C evaluation of both versions

`evaluate.py` was run unchanged for its strategy-C configurations, through `versions.build_corpus`, recording every full result list. Local models only, sockets blocked (0 attempts).

**New run directories:**

- `runs/20261026T165330Z_6e_pilot_v2`
- `runs/20261026T165346Z_6e_pilot_v2_1`

| Check | Result |
|---|---|
| **Default unchanged**: per-question rows and metrics vs the frozen baseline `20261025T164420Z` | **identical** |
| Default: full result lists (unit IDs, ranks, scores, citations) vs the Step 6D-prep strategy-C legacy run | **identical** (125 / 125 searches) |
| **Improved metrics reproduced**: `pilot_v2_1` per-question rows and metrics vs the Step 6D staged candidate | **identical** |
| `pilot_v2_1` full result lists vs the Step 6D candidate (strategy C) | **identical** (125 / 125) |

| Config | Version | Recall@5 | Recall@10 | MRR | Precision@5 | All-groups@10 | Citations verified (top 5) | Unanswerable flagged | Wrongly flagged |
|---|---|---|---|---|---|---|---|---|---|
| bm25_C | v2 / v2_1 | 0.778 / 0.778 | 0.815 / 0.815 | 0.585 / 0.585 | 0.230 / 0.230 | 0.0 / 0.0 | 0.974 / 0.974 | 0/4 / 0/4 | 0 / 0 |
| dense_C | v2 / v2_1 | 0.444 / 0.444 | 0.593 / 0.593 | 0.350 / 0.350 | 0.133 / 0.133 | 0.333 / 0.333 | 1.000 / 1.000 | 0/4 / 0/4 | 0 / 0 |
| hybrid_C | v2 / v2_1 | 0.778 / 0.778 | 0.852 / 0.852 | 0.593 / **0.604** | 0.244 / 0.244 | 0.667 / 0.667 | 0.987 / 0.987 | 0/4 / 0/4 | 0 / 0 |
| **hybrid_C_guarded** | v2 / v2_1 | 0.889 / 0.889 | 0.963 / 0.963 | 0.777 / **0.788** | 0.252 / 0.252 | 0.667 / 0.667 | 0.985 / 0.985 | 3/4 / 3/4 | 0 / 0 |

The MRR gain comes from Q27 (IDA financing): the corrected Project Cost and Financing table (`24440102:t02201:w1-2`, p. 22) ranks higher. The first relevant rank goes from 5 to 2.

**Citations:**

- 276 unique top-5 units were checked in each version; 271 verified in both. The same 5 fail in both runs, so **0 are newly failed**.
- One of the 5 is `24440102:t02201:w1-2`. That is the known whole-cell probe limitation: the evaluator looks for each cell as one contiguous string, but stacked values are interleaved on the page.
- Value-level check of the 9 changed units: every value is on the cited page (verbatim, or word by word for unchanged multi-line label cells).
- Document and page attribution are unchanged for every unit; only the 9 corrected table windows have new text.

**Guardrails and privacy:**

- The guarded statuses and notes are **identical** for all 125 searches (scope / insufficient-evidence 3/4, privacy withholding, exact-reference layer).
- The privacy check equals the Step 6D candidate.
- The sanitiser's residual-risk scan over all strategy-C units of `pilot_v2_1` finds **0 e-mails and 0 phone numbers**.

**Switch-back:**

- In the same process, after the `pilot_v2_1` run, `build_retriever()` returned the default again: 31 / 31 guarded hybrid searches were identical to the default run.
- `core.PILOT` was still `pilot_v2`, no environment variable was set, and the legacy files were unchanged.
- Switching back is immediate, because no global state is touched.

---

## 4. Answer-level check (no LLM)

The unchanged `rag/answering` `AnswerPipeline` was run over all 31 benchmark questions with the deterministic `EvidenceOnlyGenerator` (no language model, NLI off) for both versions (`results/answer_comparison.json`).

| | answered | partial | conflicting | insufficient | withheld |
|---|---|---|---|---|---|
| `pilot_v2` | 15 | 8 | 2 | 5 | 1 |
| `pilot_v2_1` | 14 | 9 | 2 | 5 | 1 |

- **30 / 31 answers are identical**, and every guarded outcome (withheld, insufficient evidence) is identical.
- **The one difference, Q27, comes from the baseline generator, not from the corpus:**
  - Both versions select the **same 8 evidence units**, all page-verified.
  - In `pilot_v2_1` the corrected table window ranks 3rd, so the evidence-only generator echoes its long excerpt verbatim (truncated to 400 characters).
  - The unchanged validator rejects any claim over 350 characters as an "evidence dump", and it rejects the same echo in **both** versions. In `pilot_v2` that window was 5th, so it was never echoed.
  - As a result, Q27 is `partial` with 2 citations (ISR pp. 1, 7) instead of `answered` with 3 (adding PAD p. 8).
- **For real (LLM-written) claims, the correction improves grounding.** The unchanged claim validator was run on factual claims about the financing table:
  - with `pilot_v2` evidence, the plain values **370.00, 100 and 18.00 are unsupported**, because they were glued;
  - with `pilot_v2_1` they are **all supported**.

---

## 5. Tests

| Suite | Result |
|---|---|
| **New:** `rag/release_v2_1/tests/test_integration.py` | **21 passed** (listed below) |
| `rag/release_v2_1/tests/test_release.py` (Step 6D staging checks, updated) | 9 passed |
| `rag/embedding_cache/tests` | 101 passed |
| `rag/retrieval/test_model_loading_offline.py` | 4 passed |
| `rag/ingestion/tests` | 45 passed |
| `rag/ingestion_p1/tests` | 65 passed |
| `rag/tests` (approved scope) | 72 passed |
| `infrastructure/lakebase_poc/tests` (offline) | 41 passed |
| `ml/tests` | 23 passed |
| `audits/pdf_table_quality/tests` | 12 passed |
| Vetted offline regression (`run_regression.sh`): structured_agent, NL2SQL, guardrails, workflow-MCP, conversation, routing and app_config in the MCP venv | **501 passed** |
| Same runner: stdio MCP transport (7 tools) | 41 passed |
| Same runner: base-Python structured_agent / conversation / app_config | 454 passed, 5 skipped |
| Same runner: discovery | 129 passed |

The 21 integration tests cover:

- the default corpus and version unchanged;
- the improved version differing only in the 9 corrected units;
- switching back leaving no state;
- 5 version and strategy rejections;
- the improved corpus with the whole-index cache rejected, **and nothing written to `index_cache`**;
- the improved corpus with the default store rejected;
- an unregistered corpus rejected;
- changed indexed text rejected;
- 5 tampered-descriptor cases rejected (corpus hash, extraction version, scope, configuration, store hash);
- a corrupted release store rejected;
- the recorded integration and answer-level results.

**Inventory diff since Step 6D:**

- **Changed:** `core.py`, `adapter.py`, `variants.py`, two Step 6D release scripts and their test, and the regenerated `evaluation.json` (supplementary checks merged).
- **Added:** `versions.py`, the release (`releases/pilot_v2_1/`), the integration tooling, results and tests, and two run directories.
- **"Removed":** 3 regeneration-manifest files that were renamed in Step 6D.
- **One side effect outside RAG** (§6, item 1).

---

## 6. Remaining issues

1. **Structured-agent audit log appended by the existing test suite.**
   - What happened: running the vetted MCP regression suite (`structured_agent/tests_mcp`) appended **15 test audit events** (6,263 bytes, 16:57–17:01 UTC) to `structured_agent/runs/audit_events.jsonl`. The suite's MCP server subprocess uses the default `WBG_AUDIT_LOG` path.
   - Content: metadata only (tool, status, error codes, hashed principal, `sql_sha256`; no SQL text or result data).
   - Scope: the original 226,591 bytes are unchanged (prefix SHA-256 `a20180bb…`, equal to the pre-run inventory), and no code was changed.
   - I did not edit the audit log. **It can be restored exactly by truncating it to 226,591 bytes, with your approval.**
   - Recommendation: point `WBG_AUDIT_LOG` at a temporary file whenever that suite runs.
2. **Q27 under the evidence-only baseline generator** (§4): `partial` instead of `answered`. This is a property of the no-LLM baseline and the existing 350-character evidence-dump rule, not of the corpus. It needs no change for an LLM generator, but answer evaluations that use the baseline will show this one difference.
3. **Unit scale not in the table evidence context:**
   - The financing table's unit ("US$ million") is in its caption, which the table window does not include.
   - So "US$370 million" style claims fail the numeric check (370000000 is not in the evidence) in **both** versions.
   - This is a pre-existing limitation that matters for LLM answers on tables. Adding the caption or unit to the table-window context would fix it, but that would change indexed text and needs a new release.
4. **Citation probe limitation** (`evaluate.py`): stacked cells are checked as one contiguous string. This is a false negative for `t02201:w1-2` in both versions.
5. **Open from earlier steps** (unchanged):
   - the withheld page-break values;
   - values missing in STEP / ICR rows;
   - the two blank typed STEP activities (deferred);
   - stacked rows not split into separate rows.
6. **Scope:** only strategy C is released for `pilot_v2_1`. Strategy A vectors exist in the store but are not enabled, and strategy B is disabled.

---

## 7. Rollback procedure

| Level | Action | Effect |
|---|---|---|
| Per call (immediate) | Use `build_retriever()` / `Retriever(Corpus("C"))` instead of `build_retriever("pilot_v2_1")` | Default corpus and whole-index cache. Shown in-process to reproduce the default results 31 / 31, and the frozen baseline exactly |
| Disable the version | Delete or rename `rag/embedding_cache/releases/pilot_v2_1/release.json` | `build_retriever("pilot_v2_1")` then fails closed with "no release is registered". The default is unaffected. |
| Remove the release | Delete `rag/embedding_cache/releases/pilot_v2_1/` | Nothing else references it. The staging store (`staging/pilot_v2_1_store`) and `experimental_store` remain. |
| Code | Restore `core.py` to `9434752f…` and `adapter.py` to the Step 6D version, and delete `versions.py` | The default code path is identical in both versions of `core.py`, so default results do not change. |

---

## 8. Conclusion

- **The improved version is available for the LangGraph MVP** through `build_retriever("pilot_v2_1")`.
  - It reproduces the validated Step 6D results exactly (hybrid-C-guarded MRR 0.777 → 0.788).
  - Citations, guardrails and privacy match the default.
  - Incompatible combinations are rejected rather than served.
- **The default is unchanged byte for byte**, and all regression suites pass.
- No default was switched and nothing was deployed.
- Open decision: the audit-log restore (§6, item 1).
