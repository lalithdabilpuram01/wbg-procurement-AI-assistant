# Approved cleanup execution: 2026-10-26

This report covers the two operations approved after review of
[PROJECT_CLEANUP_REPORT.md](PROJECT_CLEANUP_REPORT.md) §12, rows 1 and 2:

| Operation | Result |
|---|---|
| **A**: remove the plan texts of the 35 projects outside the approved RAG scope | **2,310 files deleted, 1,960,602,532 bytes** |
| **B**: remove six superseded 70-project coverage tables (`PROPOSED_DOCUMENT_DOWNLOAD_MANIFEST_v3.csv` kept, because m17 reads it) | **6 files deleted, 314,997,401 bytes** |
| **Total** | **2,316 files, 2,275,599,933 bytes (2.276 GB, 2.119 GiB). 0 files skipped, 0 discrepancies** |

Nothing else was deleted, moved or modified, apart from the test-driven audit-log append described in §6:
- No databases, Databricks, Unity Catalog or cloud services were accessed.
- Nothing was downloaded or installed.

Storage figures are in [PROJECT_STORAGE_ASSESSMENT.md](PROJECT_STORAGE_ASSESSMENT.md).

Execution records are in `../_archives/2026-10-26_repo_cleanup/execution_2026-10-26/`, outside the project:
- inventories before and after;
- the dry-run and deletion manifests;
- logs of every check below.

## 1. Preflight verification (all passed before anything was deleted)

| Check | Result |
|---|---|
| Report §5, 7, 9, 10, 12 and D-32 re-read | Approved scope and the two operations confirmed |
| `archives/SHA256SUMS` (5 archives, 5 manifests) | 10 of 10 OK |
| A1 full verification: extract to a temporary folder, compare with the manifest and the live files | 2,310 of 2,310 members match the manifest; every extracted SHA-256 matches; all originals present and unchanged |
| A2 full verification | 7 of 7 members match; every extracted SHA-256 matches; all originals present and unchanged |
| `PROJECT_SELECTION_35.csv` | SHA-256 `7bb630b3…697f` as recorded in D-32; 35 rows, 35 unique project IDs |
| `DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv` | 237 documents, all for the 35 approved projects |
| A1 project folders vs the approved set | 35 folders, **0 overlap** with the approved projects. A1 and the in-scope backup B1 share 0 paths. The plan-text folders left afterwards are exactly the 35 approved IDs |
| Dependencies (code, tests, configuration, pipelines) | References only in m15–m18 (historical 70-project scripts, as recorded in report §7) and `rag/tests` (in-scope texts and `fetch_log.jsonl` only). No reference from `silver/`, `gold/`, `databricks_upload/`, `discovery/` or `cohort/`. No code references any out-of-scope project ID. No Markdown link points into the deleted paths |
| Project state since the October 26 report | Inventory diff: only `PROJECT_CLEANUP_REPORT.md` added; nothing else changed |
| Disk space | 163 GiB free; archives readable (verified by full extraction) |

## 2. Method

The tool is `tools/delete_verified.py` (in the archive folder).
- **Explicit allowlist from the verified manifests:**
  - group A: A1 rows, restricted to `plan_text/<project>/<file>` where `<project>` is not an approved project;
  - group B: the six named A2 rows.
- **Startup checks:** the tool stops if the approved manifest SHA-256 or the 35-project count is wrong. It also stops if
  group B is not exactly the six approved files, or if the allowlist contains a preserved file
  (`PROPOSED_DOCUMENT_DOWNLOAD_MANIFEST_v3.csv`, `fetch_log.jsonl`).
- **Checked again immediately before each deletion:**
  - no path traversal;
  - no symbolic link anywhere on the path;
  - the file is inside the project and is a regular file;
  - its size and SHA-256 equal the archive manifest.

  A file that fails any check is skipped and recorded, never deleted. The guards were tested in a scratch folder
  (traversal, symlink, changed content and missing file were all refused).
- **Deletion:** file by file (no recursive deletion). Afterwards only the 35 now-empty out-of-scope project folders were
  removed.
- **Order:**
  1. Dry run: 2,310 + 6 eligible, 0 skipped (`DELETION_DRY_RUN.csv`).
  2. Apply (`DELETION_AUDIT_MANIFEST.csv`).

## 3. Files deleted

`DELETION_AUDIT_MANIFEST.csv` has one row per file, 2,316 rows, with:
- group;
- path;
- size;
- SHA-256 (as archived);
- status `deleted`;
- UTC check time.

**Group A** (2,310 files, 1,960,602,532 B): every file in
`rag/module1_discovery/plan_version_coverage_v1/plan_text/<project>/` for these 35 non-approved projects. The list is
also in `archives/A1_plan_text_out_of_scope_35_projects.manifest.csv`. The 35 now-empty project folders were removed.

**Group B** (6 files, 314,997,401 B), all in `rag/module1_discovery/plan_version_coverage_v1/`:

| File | Bytes |
|---|---|
| `CONTRACT_PLAN_EVIDENCE_LINKS.csv` | 103,388,146 |
| `PLAN_ACTIVITIES.csv` | 209,709,616 |
| `PLAN_VERSION_INVENTORY.csv` | 1,839,264 |
| `PROJECT_PLAN_COVERAGE_70.csv` | 11,011 |
| `VERSION_SELECTION_MARGINAL_GAINS.csv` | 45,496 |
| `plan_version_coverage_summary.json` | 3,868 |

**Kept in `plan_version_coverage_v1/`:**
- `PROPOSED_DOCUMENT_DOWNLOAD_MANIFEST_v3.csv` (read by m17);
- `fetch_log.jsonl`;
- `plan_text/` with the 35 approved projects (2,111 texts).

**Post-deletion inventory diff:**
- the removed set equals the audited deletions exactly;
- 0 files added;
- 0 files changed.

## 4. Retained archives (outside the project)

Location: `../_archives/2026-10-26_repo_cleanup/archives/` (393,669,513 B in total).

| Archive | Bytes | State after execution |
|---|---|---|
| A1, out-of-scope plan texts | 205,583,643 | Complete, re-verified (2,310 of 2,310). **Now the only copy** of these files in this workspace |
| A2, 70-project tables (7 files) | 41,757,803 | Complete, re-verified (7 of 7). 6 originals deleted; `PROPOSED_DOCUMENT_DOWNLOAD_MANIFEST_v3.csv` still in place |
| A3 `selection_70` / A4 `entity_linking_audit_v1` | 544,061 / 23,447 | Unchanged; originals still in place |
| B1, backup of the 35 approved projects' plan texts + `fetch_log.jsonl` | 145,090,968 | Re-verified; originals present and unchanged |

**Restore rehearsal:** A1 and A2 were restored into an empty scratch folder outside the project. All 2,316 deleted files
were reproduced with their original SHA-256 (`restore_rehearsal.log`).

**Recommendation:** A1 and A2 are now the only copies. Keep a second copy of
`../_archives/2026-10-26_repo_cleanup/archives/` on separate storage (an external drive or a private UC Volume) if
these files may still be needed.

## 5. Restore instructions

From the workspace folder (the parent of `procurement-intelligence/`):

```bash
cd _archives/2026-10-26_repo_cleanup
(cd archives && shasum -a 256 -c SHA256SUMS)
python tools/archive_tool.py restore ../../procurement-intelligence archives/A1_plan_text_out_of_scope_35_projects.tar.gz
python tools/archive_tool.py restore ../../procurement-intelligence archives/A2_plan_coverage_v1_70_project_outputs.tar.gz
```

The restore uses the exact original paths and never overwrites:
- an existing identical file is skipped;
- an existing different file stops the restore;
- each restored file is checked against the manifest SHA-256.

A2 skips its seventh file, which is still in place. Restoring is only needed to re-run m16 in its default 70-project
mode or to inspect the old 70-project outputs.

## 6. Regression tests (offline, after the deletion)

Same suites and runner as the October 26 report (`tools/run_regression.sh`): no bytecode or pytest-cache writes, and no
live provider, Databricks, Hugging Face or Logfire calls.

| Suite | Baseline | After execution |
|---|---|---|
| venv: Structured Agent, NL2SQL, NeMo Guardrails, Logfire, resilience, workflow-MCP, conversation, routing, `app_config`, RAG offline loading | 501 passed | **501 passed** |
| venv: MCP transport and the seven tools | 41 passed | **41 passed** |
| Base Anaconda suites | 454 passed, 5 skipped | **454 passed, 5 skipped** |
| RAG ingestion | 24 passed | **24 passed** |
| Discovery (read-only run) | 129 passed | **129 passed** |
| Approved RAG scope | 50 passed | **50 passed** |
| Frozen ML artefacts | 23 passed | **23 passed** |

**Other checks:**
- **RAG retrieval evaluation re-run**, with output to a scratch folder and no project writes. Compared with the
  recorded baseline run `20261025T164420Z`, excluding latency, every output is **identical**:
  - `metrics.json`, `citation_check.json`, `privacy_check.json`;
  - `per_question.csv` (341 rows);
  - `comparison.csv` (11 rows).
- **Python and Markdown:** all 365 Python files parse, and none of the 164 Markdown files has a broken relative link.

**Not run:** the Silver/Gold tests, which need a live Databricks session. Their files are byte-identical (§8).

**Audit log limitation:** some MCP tests start the server without an explicit audit path, and the server process gets
its own fixed environment. They therefore append to the real trail `structured_agent/runs/audit_events.jsonl`, and this
could not be prevented without changing test code. It was handled as follows:
- **Snapshot:** the file was copied before the tests (`audit_events.pre_tests.jsonl`, 220,328 B, SHA-256
  `dbd2fc58…c793`).
- **Result:** afterwards the original 220,328 bytes are an exact prefix of the file. 6,263 bytes (15 test events) were
  appended. Nothing was erased or rewritten.

## 7. 35-project RAG scope confirmed

| Item | Result |
|---|---|
| Approved project manifest | Unchanged (SHA-256 as in D-32), 35 projects |
| `rag_scope_v3_35_projects/`, including `plan_coverage/` (15 files) | Byte-identical |
| Approved plan texts | 2,111, byte-identical; the scope test also checks each against its `fetch_log.jsonl` SHA-256 |
| Downloaded approved PDFs (10) | Byte-identical; each matches its corpus-manifest SHA-256 |
| Processed evidence, chunks, tables, contract-linking data, benchmarks and runs (`rag/corpus_v3`, `rag/retrieval`, `rag/answering`) | Byte-identical |
| Out-of-scope projects | None left in the plan-text folders; the scope test still finds no out-of-scope ID anywhere it checks |

## 8. Bronze, Silver, Gold, cohort, models and services unchanged

Every content file was compared by SHA-256 against the pre-execution inventory and against the original October 26
baseline. All identical:

| Area | Files |
|---|---|
| `cohort/` (2,000 projects) | 4 |
| `databricks_upload/` (Bronze inputs, notebook, manifests) | 48 |
| `discovery/` | 681 |
| `silver/` | 221 |
| `gold/` (5 tables, 7 KPI views, runs) | 114 |
| `ml/final/` (Model 1) | 27 |
| `ml/final23/` (Models 2 and 3) | 21 |
| `rag/ingestion` | 26 |
| `structured_agent/` (excluding the audit log) | 121 |
| `conversation/` | 33 |

The frozen-model integrity test checks every model and artefact against `FROZEN.json`.

## 9. Skipped files and discrepancies

- **Skipped:** none. **Discrepancies:** none.
- **Status updates elsewhere:** the October 26 report (§12) and D-32 have a one-line note recording that rows 1 and 2
  were executed. No other documentation was changed.
- **Not done (not approved):**
  - `../.mypy_cache/`, `../_cleanup_review_do_not_delete/` and `../MasterPrompt_01.md` are untouched;
  - all other review items in the October 26 report §12 (rows 3 and 6–9) are unchanged.
