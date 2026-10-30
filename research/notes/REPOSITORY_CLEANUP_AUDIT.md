# Repository cleanup audit: Procurement Intelligence Platform

2026-10-25. **Read-only audit:** nothing was moved, renamed, deleted or modified, and Databricks was not contacted. This report is the only file created. Only names, sizes and counts were inspected; no secrets or personal data are reproduced.

## Summary

- `procurement-intelligence/` holds **about 4.7 GB in 23,434 files**. 17,165 of those files (390 MB) are a mypy cache. **3.1 GB is one folder of public plan texts.**
- **This is not a git repository:** there is no `.git` here or in the parent folder. The two `.gitignore` files therefore have no effect yet, so "tracked versus ignored" is reported against their patterns.
- **The `.gitignore` misses sensitive and bulky content:**
  - `rag/` (PDFs, 3.1 GB of plan texts, `pilot_v2/restricted/` with unsanitised text);
  - `.mypy_cache/`;
  - `.DS_Store`;
  - the deep-dive PDFs.
- **No secret-like tokens were found:** checked for Databricks, AWS and Slack tokens and private keys. `.env.example` contains only public endpoints and defaults. The four `empty.databrickscfg` files are 0 bytes.
- **Code layout:** the active packages are sound and should **not** move. `discovery/wbdisc` is imported by Silver, Gold and RAG code through 37 `sys.path` inserts. `rag.ingestion` loads `rag/module1_discovery/plan_text_rows.py` and the approved 35-project scope files by path.
- **Biggest wins, all low-risk:**
  - remove regenerable caches (about 405 MB here, plus 390 MB in the parent folder);
  - compress the 3.1 GB plan-text folder (gzip ratio 0.106, so about 330 MB);
  - archive superseded run folders and 70-project outputs.
  - **Potential saving: about 3.5 GB of 4.7 GB.**
- **Precedent:** an earlier cleanup (2026-10-21, `../_cleanup_review_do_not_delete/`) moved files with a SHA-256 manifest and a `restore_moves.py`. The same pattern is proposed here.

## 1. Current inventory

### 1.1 Top level (`procurement-intelligence/`)

| Path | Size | Files | Purpose | Status |
|---|---|---|---|---|
| `rag/` | 3.6 GB | 4,617 | Module 1: discovery (m01–m21), scope v3, corpus PDFs, ingestion pipeline v1/v2 | Active + historical |
| `discovery/` | 699 MB | 1,096 | Phase 1 discovery: `wbdisc` library, `scripts/`, samples, data cache, profiling, reports, deep dive | Active library + historical |
| `.mypy_cache/` | 390 MB | 17,165 | Type-checker cache | Regenerable |
| `databricks_upload/` | 287 MB | 51 | Bronze inputs as uploaded to Databricks volumes, guides, ingest notebook | Active (provenance) |
| `gold/` | 32 MB | 114 | 5 Gold transformations, KPI views (`procurement_kpis`), 21 run folders, tests | Active |
| `silver/` | 4.1 MB | 221 | 12 Silver domains, audit, 46 run folders, tests | Active |
| `cohort/` | 2.1 MB | 4 | Approved 2,000-project cohort (D-26), eligible pool, methodology | Active (approved) |
| `docs/` | 136 KB | 7 | Decisions (D-14, D-14b, D-26, D-28–31, pending), assessment alignment | Active |
| `README.md`, `assessment.md`, `requirements.txt`, `.env.example`, `.gitignore` | small | | README still describes Phase 1 (2026-10-21) | Review |
| `.pytest_cache/`, `.DS_Store` | small | | Caches | Regenerable |

**Outside `procurement-intelligence/`** (in the parent workspace; noted, not in scope):
- `../.mypy_cache/` (390 MB);
- `../_cleanup_review_do_not_delete/` (66 MB of caches moved on 2026-10-21, with a restore script);
- `../MasterPrompt_01.md` (0 bytes);
- `../databricks.yml` and `../.databricks/` (Databricks bundle and CLI state);
- `../.claude/`.

### 1.2 Major code and entry points

There are 92 scripts with `__main__`.

| Package | Modules | Entry points (examples) | Imported by |
|---|---|---|---|
| `discovery/wbdisc/` | config, http (logged client), normalize, procurement_plan, raw_store, sources, countries, cohort, bronze, isr, award_notice, profiling | (library) | Silver, Gold (via Silver), `rag.ingestion`, `rag/module1_discovery` (37 `sys.path` inserts) |
| `discovery/scripts/` | s01–s05 (extraction), p01–p09 (profiling and feasibility), c01 (cohort), b01 (Bronze inputs) | `python discovery/scripts/<x>.py` | Reports in `discovery/reports/` |
| `discovery/country_indicators/` | r01, s01, v01, probes | scripts | `databricks_upload/ref_bronze_inputs` |
| `silver/<12 domains>/` | transform / checks / run_* / verify_* per domain; `common/` (session, dq, write, sourcefiles); `audit/` | `python -m silver.<domain>.run_*` | Gold |
| `gold/<6 areas>/` | transform / checks / run / verify; `common/baseline.py`; `procurement_kpis/` (kpi_queries.py, create_kpi_views.py) | `python -m gold.<area>.run_*`, `python -m gold.procurement_kpis.create_kpi_views` | – |
| `rag/ingestion/` | config, pdf_validate, text_extract, sections, paragraphs, tables, cell_clean, plan_tables, references, evidence, sanitize, chunking, strategies, contracts_snapshot, pipeline (v1), pipeline_v2, validate, validate_v2, privacy_review, samples, samples_v2 | `python -m rag.ingestion.pipeline_v2`, `validate_v2`, `contracts_snapshot` | – |
| `rag/module1_discovery/` | m01–m21 and `plan_text_rows.py` | scripts | `rag.ingestion` imports `plan_text_rows`; m12/m17/m18 load `m10` by file path |
| `databricks_upload/notebooks/01_bronze_ingest.py` | Bronze ingest notebook | Databricks | – |

**KPI views:** the 7 `vw_kpi_*` definitions live in `gold/procurement_kpis/kpi_queries.py`. The reviewed SQL is in the approved dry run `gold/runs/gold_procurement_kpis_dryrun_20261024T212925Z/proposed_kpi_views.sql`, which `create_kpi_views.py --approved-dry-run` reads.

### 1.3 Tests and their dependencies

| Suite | Files | Needs |
|---|---|---|
| `discovery/tests/` (`pytest.ini`: `testpaths = tests`, `pythonpath = .`) | 7 | Local only (69 tests) |
| `rag/ingestion/tests/` | 3 | Local PDFs in `rag/corpus_v3/pdf`, the contracts snapshot, `pilot_v1` and `pilot_v2` outputs (24 tests) |
| `silver/tests/` | 10 (9 use Spark) | **Databricks Connect session** (read-only DataFrame tests; profile `wbg-databricks-free`) |
| `gold/tests/` | 6 (all use Spark) | **Databricks Connect session** |

There is no top-level pytest configuration and no test markers separating local tests from Spark tests.

### 1.4 Data and PDF locations

| Location | Size | Content | `.gitignore` | Regenerable |
|---|---|---|---|---|
| `discovery/data_cache/` | 377 MB | Bulk public files (DS00005, DS00979, DS01004, projects…) + MANIFEST | ignored (except MANIFEST) | Yes, via scripts, but sources change over time |
| `databricks_upload/bronze_inputs/` | 281 MB | Exact Bronze input files uploaded to Databricks | ignored (except `_manifest/`) | Yes, via b01 from `data_cache` |
| `databricks_upload/ref_bronze_inputs/` | 5.5 MB | Country reference inputs | ignored (except `_manifest/`) | Yes |
| `discovery/samples/` | 113 MB | Phase 1 raw samples: documents (88 MB; `pp_files` 12 MB used by m14/m16/m21), procnotices (**personal contact data**, D-14), IEG, projects | partly ignored | Partly |
| `discovery/project_deep_dive/raw/` | 174 MB | Paused ML-label deep dive: P163870 and P178432 notices (**personal data**) and 134 plan PDFs and texts (P178432 PDFs used by m14/m16) | only `procnotices_*.json` ignored | Yes (d01), not byte-identical |
| `rag/module1_discovery/plan_version_coverage_v1/plan_text/` | **3.1 GB** | 4,421 WDS plan texts (public) | **not ignored** | Yes, via m15 (about 75 min, 4,522 requests); WDS content may change |
| `rag/corpus_v3/pdf/` | 7.1 MB | The 10 pilot PDFs (original World Bank files) | **not ignored** | Yes (m19), checksums in `CORPUS_MANIFEST_FIRST10.csv` |
| `rag/corpus_v3/processed/pilot_v2/restricted/` | 1.9 MB | **Unsanitised** original text, cells and mask events | **not ignored** | Yes (`pipeline_v2`) |
| `rag/corpus_v3/processed/reference/` | 112 KB | Contracts snapshot (read-only Silver extract, SHA-checked) | not ignored | Yes, from Databricks |
| `cohort/` | 2.1 MB | Approved cohort manifest (SHA recorded in decisions) | not ignored | Yes (c01), but this is the approved artefact |

### 1.5 Generated reports and experimental outputs

| Location | Count | Notes |
|---|---|---|
| `discovery/reports/` | 9 reports | Phase 1 deliverables |
| `discovery/profiling/` | 82 files, incl. 25 document outlines | Inputs to the Phase 1 reports |
| `silver/runs/` | 46 run folders, 2.4 MB | 10 write runs, 24 dry runs, 7 D-14 probe/gate runs, 3 discovery reports, 2 audits |
| `gold/runs/` | 21 run folders, 31 MB | 6 write/create runs, 6 approved dry runs, 9 superseded attempts (20 MB) |
| `rag/module1_discovery/*/` | 6 report folders | `selection_70` → `selection_v2_10_countries` → `rag_scope_v3_35_projects` (approved); `entity_linking_audit_v1`; `plan_version_coverage_v1` (70-project, superseded); `pilot_first10_v1` |
| `rag/corpus_v3/processed/` | `pilot_v1` (frozen), `pilot_v2` (current) | Each with validation report, metrics, samples, `by_document` cache |

## 2. Essential components (all KEEP, in place)

| Component | Paths |
|---|---|
| Structured discovery and ingestion | `discovery/wbdisc/`, `discovery/scripts/`, `discovery/country_indicators/`, `databricks_upload/` (guides, notebook, `_manifest/`, input files) |
| Bronze, Silver, Gold | `silver/` (code, tests, write runs + approved dry runs, audit), `gold/` (code, tests, write runs + approved dry runs) |
| KPI views | `gold/procurement_kpis/*`, `gold/runs/gold_procurement_kpis_dryrun_20261024T212925Z/`, `gold/runs/gold_procurement_kpis_create_20261024T215532Z/` |
| Approved cohort | `cohort/` |
| Approved 35-project scope | `rag/module1_discovery/rag_scope_v3_35_projects/` (manifests, coverage, first batch, report). Lineage: `selection_v2_10_countries/` (read by m16/m18), `evidence/` |
| Original PDFs and official metadata | `rag/corpus_v3/pdf/`, `rag/corpus_v3/CORPUS_MANIFEST_FIRST10.csv`, `selection_v2_10_countries/all_documents_metadata_v2.jsonl`, `discovery/samples/documents/` |
| PDF ingestion v2 and the 3 strategies | `rag/ingestion/`, `rag/corpus_v3/processed/pilot_v2/`, `rag/corpus_v3/processed/reference/` |
| Plan versions and contract linking | `rag/ingestion/{plan_tables,evidence,references}.py`, `rag/module1_discovery/{m14,m16,m18,plan_text_rows}.py`, the 35-project `plan_coverage/` |
| Tests and validation benchmarks | All `tests/` folders; `rag/corpus_v3/processed/pilot_v1/` (frozen benchmark used by `validate_v2` and tests); `rag/module1_discovery/pilot_first10_v1/` (used by `rag.ingestion.validate`); `discovery/profiling/procurement_plan_activities.csv` |
| Documentation | `docs/`, `assessment.md`, `README.md` (update), Silver/Gold READMEs, current reports |

## 3. Classification

### 3.1 KEEP

Everything listed in §2, plus:
- `discovery/samples/`, `discovery/profiling/`, `discovery/schemas/`, `discovery/evidence/`;
- `rag/module1_discovery/m01–m21` scripts in place. They are tiny, cited by reports, and m10 is loaded by path by m12/m17/m18;
- discovery scripts superseded within Phase 1, also kept in place: `s02_projects.py` (v2 API, superseded by `s02b`) and `s04_documents.py` (superseded by `s04b`). They are documented lineage for Phase 1 reports and saving is negligible.

### 3.2 ARCHIVE

Moved to `archive/2026-10-25_pre_module2/<original path>` with a SHA-256 manifest.

| Path | Size | Reason | Dependencies | Regenerable |
|---|---|---|---|---|
| `gold/runs/gold_contract_analytics_20261024T064531Z/` | 8 KB | Aborted attempt (baseline only) | None | No (audit record) |
| `gold/runs/gold_contract_analytics_20261024T065059Z/` | 32 KB | Superseded dry run (approved: `…065844Z`) | None | No |
| `gold/runs/gold_financing_analytics_20261024T195959Z/` | 36 KB | Superseded dry run (approved: `…200921Z`) | None | No |
| `gold/runs/gold_procurement_notices_20261024T153851Z/` | 36 KB | Superseded dry run (approved: `…154709Z`) | None | No |
| `gold/runs/gold_procurement_kpis_dryrun_20261024T211422Z/`, `…211846Z/` | 20 MB | Superseded KPI dry runs (approved: `…212925Z`) | None | No |
| `gold/runs/gold_procurement_kpis_create_20261024T214700Z/`, `…215011Z/` | 104 KB | Preflight failed; nothing created | None | No |
| `gold/runs/gold_procurement_kpis_create_20261024T215213Z/` | 52 KB | Preflight only; the create run is `…215532Z` | None | No |
| `silver/runs/{contracts_20261023T155353Z, cpia_20261023T150944Z, ieg_20261023T224049Z, income_class_20261023T143657Z, notices_20261023T164849Z, project_details_20261024T000331Z, project_details_20261024T000753Z, project_details_20261024T001122Z, projects_20261023T152527Z, silver_audit_20261024T043017Z}` | ≈ 0.5 MB | Earlier dry runs. Kept per table: the dry run with the DRY_RUN_REPORT, the last dry run before the write, and the write run. Earlier audit superseded by `…043611Z`. | None in code; final reports cite the write runs | No |
| `rag/module1_discovery/selection_70/` | 5.6 MB | First 70-project / 64-country selection, superseded by v2 → v3 | m09/m10 write here; m12 reads it for the overlap figure (re-running m12 would need the archive path) | Yes (m09/m10; the HEAD and API state may differ) |
| `rag/module1_discovery/plan_version_coverage_v1/{CONTRACT_PLAN_EVIDENCE_LINKS.csv, PLAN_ACTIVITIES.csv, PLAN_VERSION_INVENTORY.csv, PROJECT_PLAN_COVERAGE_70.csv, VERSION_SELECTION_MARGINAL_GAINS.csv, PROPOSED_DOCUMENT_DOWNLOAD_MANIFEST_v3.csv, plan_version_coverage_summary.json}` | ≈ 302 MB | 70-project coverage run produced **before** the amount-parsing fix; superseded by the 35-project run | m17 reads the v3 70-project proposed manifest (historical only) | Yes (m16 over the plan texts) |
| `rag/module1_discovery/entity_linking_audit_v1/` | 128 KB | Feasibility audit superseded by the coverage and pilot results; history | 2 Markdown links | Yes (m14, needs Spark) |

### 3.3 DELETE CANDIDATES

These are regenerable caches with no dependants. They should be moved to a holding folder first, then deleted after approval.

| Path | Size | Reason | Regenerable |
|---|---|---|---|
| `.mypy_cache/` | 390 MB | mypy cache | Automatically |
| all `__pycache__/` (≈ 20 folders) | 2.6 MB | Bytecode | Automatically |
| `.pytest_cache/`, `discovery/.pytest_cache/` | < 0.1 MB | pytest cache | Automatically |
| `.DS_Store` (5) | < 0.1 MB | Finder metadata | Automatically |
| `rag/corpus_v3/processed/pilot_v2/by_document/` | 10 MB | Restart cache; the entity files hold the same records | Yes (`pipeline_v2 --force`, about 80 s, byte-identical, tested) |
| `../.mypy_cache/` (outside scope) | 390 MB | mypy cache | Automatically |

### 3.4 REVIEW

Each of these needs a decision.

| Path | Question |
|---|---|
| `rag/module1_discovery/plan_version_coverage_v1/plan_text/` (3.1 GB) + `fetch_log.jsonl` | Keep as is, compress into the archive (~330 MB, restorable), or move outside the repository? Read by m16/m18 (`TEXTS` path) only to re-run coverage. Re-fetching is possible but not byte-identical. **Recommendation: compressed archive with a verified restore.** |
| `rag/corpus_v3/processed/pilot_v1/by_document/` (2.3 MB) | v1 is frozen and cannot be regenerated with the current code. Keep as part of the frozen artefact (recommended) or archive. |
| `discovery/project_deep_dive/` (174 MB) | Paused ML-label investigation. Contains personal notice data and P178432 plan PDFs used by m14/m16. Keep until the Module 2 label decision, then archive. Needs restricted handling either way. |
| `discovery/samples/procnotices/` (16 MB, personal data) | Governed by D-14 (ignored). Confirm retention, or move to a restricted store. |
| `silver/runs/d14_access_probe_*` (3), `d14_gate_tests_*` (4) | Evidence for D-14/D-14b, which is paused. Keep (recommended) or archive with the decision record. |
| `discovery/data_cache/` (377 MB), `databricks_upload/bronze_inputs/` (281 MB) | Keep uncompressed (inputs of record for Bronze) or compress (~10× on CSV). Already ignored. |
| `README.md` | Outdated (Phase 1). Rewrite as the current entry point. |
| `requirements.txt` | Lacks `tiktoken` and `databricks-connect`/`pyspark`, which the code uses. No dev/test split; versions partly unpinned. |
| `.gitignore` | Missing `rag/**/plan_text/`, `rag/corpus_v3/pdf/`, `**/restricted/`, `**/by_document/`, `.mypy_cache/`, `.DS_Store`, `discovery/project_deep_dive/raw/`. |
| `../_cleanup_review_do_not_delete/` (66 MB), `../MasterPrompt_01.md` (empty) | Outside scope. Delete the previous cache holding area and the empty file? |
| Version control | Initialise git (recommended before any move) so every step is revertable. |

## 4. Proposed target structure

Existing packages keep their paths, so there is no import breakage. Only new folders and an archive are added.

```text
procurement-intelligence/
├── README.md                  current status, map, how to run (rewritten)
├── assessment.md
├── requirements.txt           + requirements-dev.txt (pytest, mypy)
├── config/                    NEW: environment templates (.env.example copy), path and Databricks profile notes, no secrets
├── docs/
│   ├── architecture/          NEW: current architecture (medallion, KPI views, RAG v2, ML plan)
│   ├── decisions/             unchanged
│   ├── requirements/          unchanged
│   └── reports/INDEX.md       NEW: index of every current and historical report (links only, no moves)
├── cohort/                    approved source data (unchanged)
├── discovery/                 structured-source discovery and the wbdisc library (unchanged; imported everywhere)
├── databricks_upload/         Bronze inputs, guides, notebook (unchanged)
├── silver/   gold/            transformations, run evidence, tests (unchanged; superseded runs archived)
├── rag/
│   ├── ingestion/             active PDF pipeline (v2) + plan_text_rows.py (moved in, with a shim left behind)
│   ├── corpus_v3/             PDFs + processed outputs (restricted/ ignored and access-controlled)
│   └── module1_discovery/     approved scope v3 + lineage scripts (superseded outputs archived)
├── ml/                        NEW: Module 2 (features/, training/, evaluation/, README)
├── backend/                   NEW placeholder (README)
├── frontend/                  NEW placeholder (README)
├── tests/                     NEW: conftest.py + pytest.ini (markers needs_spark / needs_corpus) that run the existing per-package suites
└── archive/
    └── 2026-10-25_pre_module2/   NEW: superseded runs, selections and outputs (original paths kept), compressed plan texts,
                                  ARCHIVE_MANIFEST.csv (path, size, sha256), restore_archive.py, README
```

**Deliberately not proposed:**
- moving `discovery/wbdisc`, `silver`, `gold` or `rag/ingestion`;
- converting to an installable package. A `pyproject.toml` with an editable install would replace the 37 `sys.path` inserts, but needs software installation and touches many files; it is listed as a later decision.

## 5. Migration plan

Every stage is reversible, and nothing in Databricks is touched.

| Stage | Actions | Verification | Rollback |
|---|---|---|---|
| **A: inventory and dependency map** | (0, recommended) `git init` and commit the current state, with `.gitignore` updated first so the 3.1 GB of texts, PDFs, `restricted/` and caches are not committed. Write `archive/…/INVENTORY.csv` (path, size, sha256, class) and a dependency map (imports, path constants, Markdown links) from this audit. | Inventory row count equals the file count; SHA-256 recorded. | Delete `.git` and the inventory files (nothing else changed). |
| **B: archive historical files** | Move the §3.2 paths into `archive/2026-10-25_pre_module2/<same relative path>` by atomic rename. Compress `plan_text/` (if approved) into `plan_text.tar.gz` inside the archive. Remove the originals only after a test extraction reproduces every SHA-256. Move §3.3 caches to a holding folder (or delete directly if approved). Write `ARCHIVE_MANIFEST.csv` and `restore_archive.py`, modelled on the 2026-10-21 `restore_moves.py`: never overwrites, verifies checksums. | Manifest SHA before = after; `pytest` local suites pass; `pipeline_v2` cached run reproduces output hashes. | `python archive/…/restore_archive.py` (or `git checkout`). |
| **C: reorganise active code (minimal)** | 1) Copy `plan_text_rows.py` into `rag/ingestion/`, point `rag/ingestion/config.py` at it, and leave a one-line re-export shim in `rag/module1_discovery/`. 2) Make `m16`/`m18` read the plan texts from a single `PLAN_TEXT_DIR` constant (archive or original location). 3) Add `tests/conftest.py` with `needs_spark` / `needs_corpus` markers. 4) Create the `ml/`, `backend/`, `frontend/`, `config/` and `docs/architecture/` placeholders. | `python -m pytest` (local markers) green; `pipeline_v2` and `validate_v2` byte-identical outputs. | Revert the commit (or restore the 3 edited files from the archive copy). |
| **D: imports, paths, documentation** | Rewrite `README.md`. Add `docs/reports/INDEX.md`. Fix the Markdown links pointing at archived folders (about 6 files reference `selection_70`, `plan_version_coverage_v1` or `entity_linking_audit_v1`). Update `.gitignore` and `requirements.txt` (pins only; no install). | Link check script (all relative links resolve). | Revert. |
| **E: tests and Databricks unchanged** | Run discovery (69), `rag.ingestion` (24) and the Silver/Gold suites (Spark, read-only DataFrames). Capture `gold.common.baseline.capture()` (read-only) before Stage B and after Stage D; compare table versions, row counts and the 7 views. | 0 failures; identical baselines. | Stage-level revert; nothing in Databricks is written. |
| **F: reproducibility** | From a clean checkout: `pipeline_v2 --force` (byte-identical to the committed `run_manifest`), `validate_v2` gates, cohort SHA check, Bronze input manifests checksum, KPI view DDL equal to the approved dry run (`create_kpi_views` pre-flight, read-only). | All hashes equal. | n/a (verification only). |

## 6. Estimated space savings

| Action | Before | After | Saving |
|---|---|---|---|
| Delete caches (`.mypy_cache`, `__pycache__`, pytest, `.DS_Store`, `pilot_v2/by_document`) | ≈ 403 MB | 0 | **≈ 403 MB** |
| Compress `plan_text/` into the archive (measured gzip ratio 0.106) | 3.1 GB | ≈ 330 MB | **≈ 2.8 GB** |
| Compress archived 70-project coverage CSVs (≈ 302 MB, CSV ≈ 10×) | 302 MB | ≈ 30 MB | **≈ 270 MB** |
| Move superseded runs and selections (not compressed) | ≈ 27 MB | ≈ 27 MB (in the archive) | 0 (clarity only) |
| **Total in `procurement-intelligence/`** | ≈ 4.7 GB | ≈ 1.2 GB | **≈ 3.5 GB** |
| Optional, outside scope | `../.mypy_cache` 390 MB, `../_cleanup_review_do_not_delete` 66 MB | | ≈ 456 MB |
| Optional | Compress `data_cache` + `bronze_inputs` (658 MB) | ≈ 90 MB | ≈ 570 MB |

## 7. Dependency and rollback considerations

- **Path-coupled scripts:**
  - m12, m17 and m18 load m10 by file path, so the m-scripts stay together.
  - m16 and m18 read `plan_version_coverage_v1/plan_text` and `fetch_log.jsonl`. If those are archived, Stage C must point the `TEXTS` path at the archive; until then the m16/m18 re-runs need a restore.
  - `rag.ingestion.validate` (v1) reads `pilot_first10_v1/` and `rag_scope_v3_35_projects/plan_coverage/`, so both are kept.
- **Frozen artefacts:** `pilot_v1` and the approved Gold dry run (`…212925Z`) are inputs to tests and to `create_kpi_views`. They must not move.
- **Personal data:** `discovery/samples/procnotices/`, `discovery/project_deep_dive/raw/*/procnotices_*`, and `rag/corpus_v3/processed/pilot_v2/restricted/` contain unsanitised personal data. Archives that include them must be marked restricted, and none of them may be committed.
- **Spark tests** need the Databricks profile. If it is unavailable during Stage E, run local suites only and record the Spark suites as pending; do not skip silently.
- **Rollback layers:**
  1. git (once initialised);
  2. the `ARCHIVE_MANIFEST.csv` + `restore_archive.py` (checksum-verified, no overwrite);
  3. compressed archives verified by test extraction before any original is removed.

## 8. Decisions requiring approval

1. **Initialise git** and adopt the updated `.gitignore` first.
2. **Approve the ARCHIVE list (§3.2):** superseded Gold/Silver runs, `selection_70`, the 70-project coverage outputs, `entity_linking_audit_v1`.
3. **Approve the DELETE candidates (§3.3):** delete directly, or via a holding folder first.
4. **Plan texts (3.1 GB):** compress into the archive (recommended), keep as is, or move outside the repository.
5. **Paused items:** keep `discovery/project_deep_dive/` and the D-14 probe/gate run folders in place until Module 2 decides (recommended), or archive them now.
6. **Stage C code changes:** move `plan_text_rows.py` into `rag/ingestion` with a shim; add a `PLAN_TEXT_DIR` constant; add the test markers.
7. **Placeholders:** create `ml/`, `backend/`, `frontend/`, `config/` and `docs/architecture/` now, or when Module 2 starts.
8. **Outside scope:** `../_cleanup_review_do_not_delete/`, `../.mypy_cache/`, `../MasterPrompt_01.md`.
9. **Later (needs installation):** a `pyproject.toml` with an editable install to replace `sys.path` inserts; a requirements split with pins.

Stopped here. No file has been moved or changed.
