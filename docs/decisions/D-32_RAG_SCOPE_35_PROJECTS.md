# D-32: The Document RAG Corpus Is Permanently Limited to the 35 Approved Projects

| | |
|---|---|
| Status | **Approved** (2026-10-26, permanent scope decision by the project owner) |
| Supersedes | Any plan to expand the RAG corpus beyond the 35 projects, including the earlier 70-project selections (`selection_70`, `selection_v2_10_countries`) |
| Does not change | D-26: the 2,000-project structured Databricks analytics cohort, with its Bronze, Silver and Gold tables and KPI views |

## Decision

- **The scope is fixed.** The Document RAG corpus contains documents from exactly the **35 approved World Bank projects**:
  5 countries × 7 projects (25 closed, 10 active).
- **The manifests define the scope.**
  - Projects: [`PROJECT_SELECTION_35.csv`](../../rag/module1_discovery/rag_scope_v3_35_projects/PROJECT_SELECTION_35.csv)
    (SHA-256 `7bb630b38b893c52eca17fbe347510ae2a432baabccc9a09ea6fc743e3d5697f`). This file is not to be modified.
  - Documents: [`DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv`](../../rag/module1_discovery/rag_scope_v3_35_projects/DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv).
- **No new projects are added**, whatever the source: new selections, similar-project searches, or user requests.
- **Same projects, new documents:** additional documents or newer revisions (for example a later procurement plan or
  ISR) for one of the same 35 projects may be added. They go through the normal ingestion and validation controls:
  approved manifest entry, checksum, PDF validation, sanitisation, citation checks and the v2 validation gates.
- **Structured analytics is separate.** The 2,000-project structured cohort (D-26) remains unchanged and is not
  limited by this decision. Structured questions about other cohort projects are answered from the Gold tables, not
  from RAG documents.

## Enforcement

[`rag/tests/test_approved_rag_scope.py`](../../rag/tests/test_approved_rag_scope.py) runs offline and read-only. It fails
when:
- the approved project manifest changes, or does not hold exactly 35 unique projects;
- a project ID outside the approved set appears in any of these:
  - the scope manifests or plan-coverage tables;
  - the corpus manifest or downloaded PDF folders;
  - the processed evidence (`rag/corpus_v3/processed/**.jsonl`) or the retrieval benchmark;
- the ingestion configuration stops pointing at the approved manifests;
- a downloaded PDF or an in-scope plan text no longer matches its recorded SHA-256.

## Consequences

- **70-project materials are now history.** The outputs for the other 35 projects (plan texts, 70-project coverage
  tables, `selection_70`) are only lineage. They were archived with verified checksums on 2026-10-26 (see
  [`docs/reports/PROJECT_CLEANUP_REPORT.md`](../reports/PROJECT_CLEANUP_REPORT.md)). After approval, the out-of-scope
  plan texts and six 70-project tables were removed from the working tree on 2026-10-26 (see
  [`PROJECT_CLEANUP_EXECUTION_REPORT.md`](../reports/PROJECT_CLEANUP_EXECUTION_REPORT.md)); `selection_70` and
  `PROPOSED_DOCUMENT_DOWNLOAD_MANIFEST_v3.csv` remain in place.
- **Scripts written for 70 projects need restored inputs.** Scripts with a 70-project default (m09, m10, m12, m16
  without `--projects`, m17) can be re-run only after restoring the archived inputs. The 35-project results (m16
  `--projects`, m18, m21) depend only on in-scope files, which are kept in place.
