# Project cleanup report: 2026-10-26

**What was done:**
- **Caches deleted:** only proven-regenerable caches outside the protected areas.
- **Archives created:** verified, compressed copies of historical files, outside the project tree.
- **Scope recorded:** the RAG scope is now an explicit decision (D-32), with a validation test.
- **Regression unchanged:** every suite gave the same result as before the cleanup.

**What was not done:**
- No historical data, PDFs, manifests, evidence, ML artefacts, audit logs or database files were moved or removed.
- Nothing in the Bronze, Silver or Gold pipelines, `discovery/` or `cohort/` was changed, including their caches.
- No database, Databricks, Unity Catalog or other external service was contacted.
- `.env` was not read or changed.

Proposed removals of archived historical data are in [§12](#12-actions-that-need-your-approval). Each one needs your
approval.

Archive and tooling folder (outside the project): `../_archives/2026-10-26_repo_cleanup/`. Its README gives
verification and restore commands.

## 1. Project size before and after

| | Files | Bytes | Excluding `.venv-structured-mcp/` |
|---|---|---|---|
| Before (inventory 2026-10-26, before any change) | 73,983 | 6,699,930,214 (6.70 GB) | 24,321 files, 5.36 GB |
| After the cache cleanup | 56,636 | 6,317,297,983 (6.32 GB) | 6,974 files, 4.97 GB |
| Final (with the new test, decision and report files) | 56,666 | 6,317,540,411 (6.32 GB) | 7,004 files, 4.97 GB |

The final count includes 25 bytecode files (0.24 MB) that the stdio MCP server subprocess recreated in
`structured_agent/` during the post-cleanup tests. Bytecode is always recreated when code runs.

## 2. Files and directories removed

Only caches and Finder metadata were removed: 17,347 files in 38 cache units, 382.64 MB.

| Kind | Units | Files | Size |
|---|---|---|---|
| `.mypy_cache/` (project root) | 1 | 17,165 | 379.88 MB |
| `__pycache__/` in `structured_agent/`, `conversation/`, `rag/`, `ml/`, `app_config/` (only `.pyc` files) | 28 | 168 | 2.60 MB |
| `.pytest_cache/` (project root) | 1 | 6 | 0.08 MB |
| `.DS_Store` (project root, `docs/`, `rag/` incl. `rag/corpus_v3/pdf/` folders) | 8 | 8 | 0.08 MB |

How the removal was done:
- **Method:** `clean_caches.py`. It refused any `__pycache__` holding a non-`.pyc` file and any file whose size had
  changed since the inventory. Files were deleted one by one, then the empty cache directories; no recursive tree
  deletion was used.
- **Exact list:** `REMOVED_CACHES_MANIFEST.csv` in the archive folder, with unit, path, size and SHA-256 for all
  17,347 files (manifest SHA-256 `10c04a9f64cd1cf856d950dbba05c79966ba4bad0f578d2dd898e4e891b98bc8`).
- **Verification:** an inventory diff showed exactly these 17,347 files removed. No other file was added, removed or
  changed.

**Skipped on purpose (protected):** 534 cache files (23.4 MB) inside `silver/`, `gold/`, `databricks_upload/` and
`discovery/`. These are `__pycache__`, `discovery/.mypy_cache` (21.5 MB), `discovery/.pytest_cache` and `.DS_Store`.

## 3. Storage recovered

| | |
|---|---|
| Recovered now | **382.6 MB** (17,347 files; the file count fell by 23%) |
| Recoverable after approval (§12, rows 1–2) | ≈ **2.28 GB** of working tree, kept as 248 MB of verified archives |
| Recoverable outside the project (§12, rows 4–5) | ≈ 489 MB |

## 4. SHA-256 manifests of removed and archived files

| Manifest (in `../_archives/2026-10-26_repo_cleanup/`) | Rows | Manifest SHA-256 |
|---|---|---|
| `REMOVED_CACHES_MANIFEST.csv` (deleted caches) | 17,347 | `10c04a9f64cd1cf856d950dbba05c79966ba4bad0f578d2dd898e4e891b98bc8` |
| `archives/A1_plan_text_out_of_scope_35_projects.manifest.csv` | 2,310 | `1aff964d32f005698fa50c239c44a0165229ca06844fdbe6c5c3ddcf3533fc25` |
| `archives/A2_plan_coverage_v1_70_project_outputs.manifest.csv` | 7 | `d89a1231012a0eacdc84c4c6d856ee2733f15fae0aa4c08ddc652f124aeeafa8` |
| `archives/A3_selection_70.manifest.csv` | 11 | `6149a0100ac782c8bd69014366584e9428536c8bc2d9a692d1c00231142c4822` |
| `archives/A4_entity_linking_audit_v1.manifest.csv` | 8 | `0bea7a23f3e7cdc8bef48d605d38eb4671ae8dd357de25bb5e23b690dff410a3` |
| `archives/B1_backup_plan_text_in_scope_35_projects.manifest.csv` | 2,112 | `2d0c2c7e57d056062c8ee7319d8f8adf76b4cd7e8fcbd0ba4e262f04d42dd5ff` |
| `INVENTORY_BEFORE.csv` (every project file before any change) | 73,983 | `5150b04082a7960ce203aed52e29ad67a74e90b1a0e0a7836f69b0e6cf1c5771` |

## 5. Verified backups and restore instructions

| Archive | Source (originals still in place) | Files | Original | Archive | SHA-256 of archive |
|---|---|---|---|---|---|
| A1 | `rag/module1_discovery/plan_version_coverage_v1/plan_text/<35 non-approved project folders>` | 2,310 | 1,960.6 MB | 205.6 MB | `0c41219e74ac6c87e001b30f16a339c95f7a12d969d176fe027ad23736ad48d0` |
| A2 | `plan_version_coverage_v1/` 70-project tables (`CONTRACT_PLAN_EVIDENCE_LINKS.csv`, `PLAN_ACTIVITIES.csv`, `PLAN_VERSION_INVENTORY.csv`, `PROJECT_PLAN_COVERAGE_70.csv`, `VERSION_SELECTION_MARGINAL_GAINS.csv`, `plan_version_coverage_summary.json`, `PROPOSED_DOCUMENT_DOWNLOAD_MANIFEST_v3.csv`) | 7 | 315.4 MB | 41.8 MB | `529096ea5b1737b929fccf60a98cc2d244a342eed3f91a83ffce1069649622ad` |
| A3 | `rag/module1_discovery/selection_70/` | 11 | 5.8 MB | 0.5 MB | `96489cfd3e44a3ae925a922e585bd2f543541704c2e4726be52c5ebfd8bcde27` |
| A4 | `rag/module1_discovery/entity_linking_audit_v1/` | 8 | 0.1 MB | 0.02 MB | `3226936b3466ef5e178a19bc5e5672b50fb421779989ae01787b1fefcf4d430d` |
| B1 (backup only, not for removal) | `plan_text/<35 approved project folders>` + `fetch_log.jsonl` | 2,112 | 1,330.6 MB | 145.1 MB | `fc3b718b8da3e2451cff50fd5e16763200ed24af01a336ec63eddbd4aa1f55dc` |

**Verification** (`logs/archive_verification.log`). For each archive:
- members match the manifest;
- every extracted file matches its SHA-256 (extraction into a temporary folder outside the project);
- every original is present and unchanged.

**Restore test:** one test per restore behaviour.
- **Full restore:** A3 and A4 restored into an empty folder; `diff -r` showed them identical to the originals.
- **No overwrite:** A2 restored against the live project; all 7 files were skipped as identical.
- **Tampered target:** the restore stopped with nothing overwritten.

**Restore command** (from the workspace folder):

```bash
cd _archives/2026-10-26_repo_cleanup
shasum -a 256 -c archives/SHA256SUMS       # run inside archives/
python tools/archive_tool.py restore ../../procurement-intelligence archives/<archive>.tar.gz
```

The restore puts each file back at its exact original path. It never overwrites, and it verifies every SHA-256.

## 6. Files recommended for later removal but deliberately kept

| Path | Size | Why it stays for now |
|---|---|---|
| `plan_version_coverage_v1/plan_text/<35 non-approved projects>` | 1.96 GB | Archived (A1, verified). Removal needs approval (§12). Only m16 in its default 70-project mode reads them |
| `plan_version_coverage_v1/` 70-project tables | 315 MB | Archived (A2, verified). Superseded by `rag_scope_v3_35_projects/plan_coverage/`. Removal needs approval (§12) |
| `rag/module1_discovery/selection_70/` | 5.8 MB | Archived (A3). Read by m12, m13 and m17, and linked from 3 reports. Too small to be worth the broken lineage |
| `rag/module1_discovery/entity_linking_audit_v1/` | 0.1 MB | Archived (A4). Linked from the scope report and m14. Negligible |
| `rag/corpus_v3/processed/pilot_v2/by_document/` | 10 MB | Restart cache of `pipeline_v2`. The prior audit found it byte-identically regenerable, but it is a current processing output |
| `rag/retrieval/index_cache/` | 16 MB | Embedding cache. The evaluation re-run below reproduced the recorded results with it; regenerating it changes nothing but costs time |
| `rag/retrieval/runs/20261025T163414Z`, `…164121Z` | < 0.2 MB | Superseded evaluation runs (README: final is `…164420Z`); evaluation history |
| `gold/runs/gold_procurement_kpis_dryrun_20261024T211422Z`, `…211846Z` and other superseded Gold/Silver runs | ≈ 20 MB | **Protected (Gold/Silver).** Listed for your review only (prior audit §3.2) |
| `discovery/.mypy_cache`, `__pycache__` in `silver/`, `gold/`, `discovery/` | 23.4 MB | **Protected.** Regenerable, but excluded by instruction |
| `discovery/project_deep_dive/` | 174 MB | **Protected area** (discovery). Contains personal notice data (D-14) and P178432 plan PDFs used by m14/m16 |
| `ml/eda/` | 41 MB | Model-selection evidence behind the frozen models. Kept as provenance |
| `.venv-structured-mcp/` | 1.4 GB | The MCP and NeMo runtime environment. Kept; reinstalling is out of scope |

**Duplicates:** 152 groups of identical non-cache files, 27.9 MB redundant in total. They are nearly all
provenance copies, so none were removed:
- Bronze inputs staged next to their discovery sources;
- the cohort manifest copied into `bronze_inputs/_control/`;
- `kpi_results.json` repeated in two superseded Gold dry runs (10.5 MB);
- repeated EDA charts and models in separate run folders;
- `pilot_v1` and `pilot_v2` sharing `evidence_links.jsonl`.

## 7. Dependencies checked

| Candidate | What was checked | Finding |
|---|---|---|
| Caches | References to `.mypy_cache`, `.pytest_cache`, `__pycache__`, `.DS_Store` in code and configuration | Only `.gitignore` patterns. `cache_dir` in `wbdisc/config.py` is `discovery/data_cache` (protected Bronze source), not a cache |
| `plan_text/` | m15 writes it; m16 and m18 read `TEXTS`; `rag.ingestion` uses `plan_text_rows.py` (code, not texts) | m16 opens texts only for the projects passed with `--projects`, which is how the 35-project coverage was built. m18 defines `TEXTS` but reads only the 35-project coverage. The out-of-scope folders are needed only for m16's default 70-project run |
| 70-project tables | m16 (writes), m17 (reads `PROPOSED_DOCUMENT_DOWNLOAD_MANIFEST_v3.csv`), `rag.ingestion.validate`, m18, m21 | `validate`, m18 and m21 read the **35-project** `plan_coverage/` copies. Only m17, a historical 70-project HEAD check, reads a 70-project table, so that file is kept in place even after approval |
| `selection_70/` | m09/m10 (write), m12, m13, m17 (read); Markdown links | Lineage only; kept |
| `entity_linking_audit_v1/` | m14; links from the scope report | History only; kept |
| Approved scope and corpus | `rag.ingestion.config` paths; PDFs against the corpus-manifest SHA-256; plan texts against the `fetch_log.jsonl` SHA-256 | All intact (§9) |
| Bronze/Silver/Gold, cohort | Inspected only (inventory, checksums) | Unchanged (§10) |
| Code | All 365 Python files parsed (outside the venv) | 0 syntax errors |
| Documentation | Every relative link in 163 Markdown files | 0 broken (after this report was written) |

## 8. Tests run and results

All suites ran offline with no bytecode or pytest-cache writes (`tools/run_regression.sh`). The only live-capable code
paths are guarded by the dual opt-in, which tests remove. No Claude, Grok, Portkey, TypeSafe, Databricks, Hugging Face
or Logfire export call was made.

| Suite | Before cleanup | After cleanup |
|---|---|---|
| venv: Structured Data Agent, NL2SQL, NeMo guardrails, Logfire tracing, resilience, workflow-over-MCP, conversation memory and compression, intent routing, `app_config`, RAG model offline loading | 501 passed | **501 passed** |
| venv: `structured_agent/tests_mcp` (stdio MCP transport, all seven tools) | 41 passed | **41 passed** |
| Base Anaconda: Structured Data Agent, conversation, `app_config`, RAG offline loading | 454 passed, 5 skipped | **454 passed, 5 skipped** |
| Base Anaconda: `rag/ingestion/tests` (ingestion v1/v2 on the local PDFs) | 24 passed | **24 passed** |
| Base Anaconda: `discovery/tests` (read-only run) | 129 passed | **129 passed** |
| **New:** `rag/tests/test_approved_rag_scope.py` | — | **50 passed** |
| **New:** `ml/tests/test_frozen_artifacts.py` | — | **23 passed** |

**RAG retrieval evaluation re-run.** `rag.retrieval.evaluate` was run with its output folder pointed at a scratch
directory, so nothing was written into the project. The results match the final recorded run `20261025T164420Z`,
excluding latency:

| Output | Result |
|---|---|
| `metrics.json`, `citation_check.json`, `privacy_check.json` | Identical |
| `per_question.csv` (341 rows), `comparison.csv` (11 rows) | Identical |

The answering evaluation was not re-run: it is the LLM runtime that is waiting for your approval. Its stored outputs
are byte-identical (§10).

**Not run:**
- `silver/tests` and `gold/tests` need a live Databricks Connect session. Their code and data are byte-identical.
- The ML training and scoring scripts would re-score test sets, which is forbidden. The new integrity test checks the
  frozen artefacts instead.

**Negative check of the scope test:** with one approved project withheld in memory, the scope-table, PDF-folder and
processed-evidence checks failed as intended.

**Observation, not changed:** `structured_agent/tests_mcp` appends events to the real audit trail
`structured_agent/runs/audit_events.jsonl`. It grew by 0.21 MB during the baseline run, which was the only file
changed by any test. Consider pointing that suite at a temporary audit path later; that would be a behaviour change,
so it was not done here.

## 9. The 35 approved projects are intact

| Check | Result |
|---|---|
| `PROJECT_SELECTION_35.csv` | Unchanged, SHA-256 `7bb630b3…697f`; 35 unique projects |
| `DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv` and the rest of `rag_scope_v3_35_projects/` (15 files) | Byte-identical before and after |
| Downloaded PDFs (`rag/corpus_v3/pdf/`, the first batch of 10) | All present; each matches the corpus-manifest SHA-256 |
| Plan texts of the 35 projects (2,111 texts, 1.33 GB) | All present in place; each matches its `fetch_log.jsonl` SHA-256; extra verified backup B1 |
| Plan-version coverage and contract-linking evidence (`rag_scope_v3_35_projects/plan_coverage/`) | Byte-identical |
| Processed evidence, chunk strategies, contracts snapshot, `pilot_v1` benchmark, retrieval benchmark and runs, answering runs | Byte-identical |
| No project outside the 35 | None in the scope tables, corpus manifest, PDF folders, processed records or retrieval benchmark |

The scope is documented in [D-32](../decisions/D-32_RAG_SCOPE_35_PROJECTS.md), and listed under approved decisions in
[PENDING_DECISIONS.md](../decisions/PENDING_DECISIONS.md):
- the RAG corpus is exactly the 35 approved projects, defined by the approved manifests;
- no new projects;
- new documents or revisions for the same projects go through normal ingestion and validation;
- the 2,000-project structured scope (D-26) is unchanged.

## 10. The structured cohort, pipelines and frozen ML models are untouched

Each row compares SHA-256 for every content file, before and after.

| Area | Files compared | Identical |
|---|---|---|
| `cohort/` (2,000-project manifest) | 4 | Yes |
| `databricks_upload/` (Bronze inputs, notebook, manifests) | 48 | Yes |
| `discovery/` (Bronze sources, `wbdisc`, scripts) | 681 | Yes |
| `silver/` (12 domains, runs, audit) | 221 | Yes |
| `gold/` (5 Gold tables, 7 KPI views, runs) | 114 | Yes |
| `ml/final/` (Model 1) | 27 | Yes |
| `ml/final23/` (Models 2 and 3) | 21 | Yes |

- **Frozen-model checksums:** `ml/tests/test_frozen_artifacts.py` confirms every model, prediction, schema,
  configuration and refit-script SHA-256 recorded in `FROZEN.json`. It also confirms the Model 1 freeze record is the
  one that was test-scored (`TEST_SCORED.json`).
- **No writes:** no Databricks job, write, schema change or Delta operation was run.

## 11. Remaining large directories and why they stay

| Directory | Size | Why it is kept |
|---|---|---|
| `rag/module1_discovery/plan_version_coverage_v1/plan_text/` | 3.1 GB | 1.33 GB are the approved 35 projects' plan texts (evidence behind plan-version coverage and contract links). The other 1.96 GB wait for approval (§12) |
| `.venv-structured-mcp/` | 1.4 GB | Isolated runtime for MCP, NeMo Guardrails and Logfire (151 locked packages) |
| `rag/module1_discovery/plan_version_coverage_v1/` tables | 0.32 GB | 70-project tables wait for approval (§12); `fetch_log.jsonl` (4.5 MB) is the integrity record of the plan texts |
| `discovery/data_cache/` | 377 MB | Bronze source files (protected) |
| `databricks_upload/bronze_inputs/` | 281 MB | The exact Bronze inputs uploaded to Databricks (protected) |
| `discovery/project_deep_dive/` | 174 MB | Paused ML-label investigation with personal data (protected area; review item) |
| `rag/module1_discovery/rag_scope_v3_35_projects/` | 138 MB | The approved scope, with coverage and linking evidence |
| `discovery/samples/` | 113 MB | Phase 1 samples; `pp_files` used by m14/m16/m21 (protected area) |
| `ml/` | 80 MB | Frozen models, features, EDA and selection evidence |
| `gold/` | 32 MB | Gold code, KPI views and run evidence (protected) |
| `rag/corpus_v3/` | 33 MB | Approved PDFs, processed evidence, benchmarks |
| `rag/retrieval/` | 16 MB | Retrieval code, benchmark, embedding cache, evaluation runs |

## 12. Actions that need your approval

**Status update (2026-10-26):** rows 1 and 2 were approved and executed. See
[PROJECT_CLEANUP_EXECUTION_REPORT.md](PROJECT_CLEANUP_EXECUTION_REPORT.md). Rows 3–9 have not been done.

| # | Proposed action | Paths | Size | Backup | Effect and risk |
|---|---|---|---|---|---|
| 1 | Remove the out-of-scope plan texts from the working tree | `plan_version_coverage_v1/plan_text/<35 non-approved project folders>` (listed in the A1 manifest) | 1.96 GB | A1, verified, restore tested | m16 in its default 70-project mode would need `restore` first. The 35-project results, tests and RAG are unaffected. Under D-32 these projects will not return |
| 2 | Remove the superseded 70-project tables | `plan_version_coverage_v1/{CONTRACT_PLAN_EVIDENCE_LINKS.csv, PLAN_ACTIVITIES.csv, PLAN_VERSION_INVENTORY.csv, PROJECT_PLAN_COVERAGE_70.csv, VERSION_SELECTION_MARGINAL_GAINS.csv, plan_version_coverage_summary.json}`. `PROPOSED_DOCUMENT_DOWNLOAD_MANIFEST_v3.csv` stays: m17 reads it | 315 MB | A2, verified | Pre-amount-fix outputs; the 35-project copies are authoritative |
| 3 | Optional: also move `selection_70/` and `entity_linking_audit_v1/` out of the tree | as named | 6 MB | A3, A4 | Not recommended: 4 scripts and 3 reports reference them, for negligible saving |
| 4 | Delete the workspace-level mypy cache (outside `procurement-intelligence/`) | `../.mypy_cache/` | 423 MB | None needed (regenerable) | Your editor's mypy recreates it; consider excluding it in the IDE settings |
| 5 | Delete the 2026-10-21 cache holding area and the empty prompt file | `../_cleanup_review_do_not_delete/` (66 MB of caches, with its own restore script), `../MasterPrompt_01.md` (0 bytes) | 66 MB | Its own manifest | Its contents are regenerable caches moved in the earlier cleanup |
| 6 | Review, protected areas: archive superseded Gold/Silver run folders | Listed in `REPOSITORY_CLEANUP_AUDIT.md` §3.2 (e.g. `gold/runs/gold_procurement_kpis_dryrun_20261024T211422Z`, `…211846Z`) | ≈ 20 MB | Not created (protected) | Only if you lift the Bronze/Silver/Gold restriction for these run records |
| 7 | Review, protected areas: caches inside `silver/`, `gold/`, `discovery/` | 534 files | 23.4 MB | Regenerable | Only if you lift the restriction for caches |
| 8 | Review, personal data: `discovery/project_deep_dive/raw/` and `discovery/samples/procnotices/` | as named | 190 MB | Not created (would copy personal data) | Decide retention or restricted storage under D-14 |
| 9 | Optional: test isolation | `structured_agent/tests_mcp` writes to `structured_agent/runs/audit_events.jsonl` | — | — | A test-only change, to stop test runs appending to the real audit trail |

**After approving 1 and 2:** about 2.28 GB of working-tree storage is freed. The verified archives (248 MB) stay in
`../_archives/2026-10-26_repo_cleanup/` until you decide otherwise. I would do the removal with a file-by-file script
driven by the archive manifests, re-check that each file matches its archived checksum before deleting it, and then
re-run the full regression and the scope test.
