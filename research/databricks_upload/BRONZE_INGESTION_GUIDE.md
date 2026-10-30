# Bronze Ingestion Guide: 2,000-Project PoC Cohort

Status (2026-10-21): **inputs prepared and validated locally. Nothing has been uploaded, and no Databricks schema or
table has been created.**

**Boundary rule (D-26):** Bronze contains only records whose project ID is one of the 2,000 projects in
[`cohort/poc_cohort_2000_manifest.csv`](../cohort/poc_cohort_2000_manifest.csv).
- **Matching:** exact string equality on the raw source key, with no trimming or case-folding.
- **Scope of the operation:** selection is the only operation. Records are copied byte for byte from the Phase 1
  downloads. That preserves:
  - headers, including the UTF-8 BOM
  - quoting, embedded line breaks and line endings
  - source order and duplicates
  - complete JSON records, including the Phase 1 `_extract` provenance key where present
- **Formats:** no cleaning, typing, deduplication, aggregation or Parquet conversion.

**Reproduce:**
- Extract: `cd discovery && python3 scripts/b01_extract_bronze_inputs.py`. It runs offline and refuses to overwrite an
  existing output.
- Implementation and tests: [`wbdisc/bronze.py`](../discovery/wbdisc/bronze.py) and `tests/test_bronze.py`.

## 1. Files in `databricks_upload/bronze_inputs/`

| Recommended Bronze table | File | Format | Records (of source) | Cohort projects | Size | Coverage |
|---|---|---|---|---|---|---|
| `bronze_projects_v3` | `projects_v3/projects_v3_population__cohort2000.jsonl` | JSONL | **2,000** of 28,193 | 2,000 | 11.3 MB | Complete |
| `bronze_projects_v3_allfields` | `projects_v3_allfields/projects_v3_cluster_allfields__cohort2000.jsonl` | JSONL | 184 of 276 | 184 | 5.6 MB | **Partial** (Phase 1 sample) |
| `bronze_contract_awards_ds00005` | `contract_awards_ds00005/ds00005_contract_awards__cohort2000.csv` | CSV | 186,907 of 295,058 | 2,000 | 72.6 MB | Complete |
| `bronze_contract_awards_legacy_ds01004` | `contract_awards_legacy_ds01004/ds01004_contract_awards_legacy__cohort2000.csv` | CSV | 6,448 of 146,802 | 363 | 2.4 MB | Complete (prior-review contracts only, FY2001–16) |
| `bronze_procurement_notices_ds00979` | `procurement_notices_ds00979/ds00979_procurement_notice__cohort2000.csv` | CSV | 243,533 of 422,557 | 2,000 | 112.3 MB | Complete (metadata only) |
| `bronze_procurement_notices_api` | `procurement_notices_api/procnotices_cluster_sample__cohort2000.jsonl` | JSONL | 20,305 of 30,977 | 184 | 88.6 MB | **Partial** (Phase 1 sample) |
| `bronze_ieg_ratings_ds00053` | `ieg_ratings_ds00053/ds00053_ieg_ratings__cohort2000.csv` | CSV | 750 of 12,629 | 750 | 0.2 MB | Complete |
| `bronze_ibrd_loans_ds00047` | `ibrd_loans_ds00047/ds00047_ibrd_latest__cohort2000.csv` | CSV | 612 of 9,538 | 516 | 0.2 MB | Complete (latest snapshot) |
| `bronze_ida_credits_ds00001` | `ida_credits_ds00001/ds00001_ida_latest__cohort2000.csv` | CSV | 2,073 of 11,486 | 1,129 | 0.6 MB | Complete (latest snapshot) |

Control and provenance files:

| File | Purpose |
|---|---|
| `_control/poc_cohort_2000_manifest.csv` | Byte copy of the approved cohort |
| `_manifest/bronze_inputs_manifest.json` | Per file: source file and SHA-256, selection rule, record counts, sizes, extraction timestamp, output SHA-256, validation results; plus relationship counts |
| `_manifest/bronze_inputs_manifest.csv` | Flat version of the same, loadable as a control table |
| `_manifest/excluded_and_unlinked_sources.json` | Records and sources not extracted, and why |

**Total:** about 296 MB of data files.

## 2. Validation results (all passed)

**Cohort and project table**
- **Cohort manifest:** 2,000 rows, 2,000 unique IDs, all valid P-numbers.
- **`bronze_projects_v3`:** 2,000 records with 2,000 unique `id`s, and the ID set equals the cohort exactly.

**Every extract was checked against an independent read of its source**
- **CSV:**
  - The header bytes are identical to the source header.
  - pandas reads of the filtered source and of the extract are equal, row for row and value for value.
  - Every key is in the cohort.
- **JSONL:** the extract lines equal the selected source lines byte for byte and in order.

**Originals are unchanged**
- Each source's SHA-256 matched its Phase 1 MANIFEST checksum before extraction and was identical after it.
- **Raw format preserved:** all CSVs keep the BOM, LF record terminators and embedded line breaks.
  - DS00979 contains 1,934 CRLF sequences inside quoted fields.
  - DS00005, DS01004 and DS00979 have line breaks inside quoted fields, so Spark must read them with `multiLine`.
  - No record has a field count different from its header.
- **Coverage:** every cohort project has at least one DS00005 contract row and at least one DS00979 notice.
- **Duplicates:** none were present in the cohort rows (0 exact duplicate rows in DS00005 and DS01004). Nothing was
  removed.

## 3. Relationship counts (reported, not materialised in Bronze)

| Measure | Count |
|---|---|
| DS00005 contract rows (contract × supplier lines) | 186,907 |
| DS00005 distinct contracts (`WB Contract Number`) | 178,921 (5,843 contracts have more than one supplier line) |
| DS01004 legacy contracts (one row per contract) | 6,448 |
| **Project-level notice links** (DS00979 rows sharing a cohort Project ID) | 243,533 notices: award 187,070, expressions of interest 31,187, invitations for bids 23,780, general procurement notices 1,239, prequalification 256, small-contracts award 1 |
| Notices with text and reference number (API, 184 projects) | 20,305 (20,148 with `bid_reference_no`) |
| Contracts in scope for contract-to-notice linking (the 184 projects with API notices) | 15,177 distinct contracts |
| Candidate award-notice links: same project and identical normalised reference | 10,097 contracts |
| Candidate tender-notice links (IFB / REOI / prequalification) | 2,495 contracts |
| **Verified contract-to-award-notice links**: reference match and the contract's STEP Supplier ID among the awarded IDs in the notice text | **6,075 contracts** (5,414 matched to exactly one notice) |
| Cohort contracts with no reference-bearing notices yet | 163,744 (their projects need the procnotices API pull) |

DS00979 has no bid reference number, so its 243,533 notices can be linked to projects only, never to individual
contracts. The verified-link method is the Phase 1 method
([`p02_join_analysis.py`](../discovery/scripts/p02_join_analysis.py)). It runs in Silver; Bronze holds no link table.

## 4. Not extracted or not linkable

From `_manifest/excluded_and_unlinked_sources.json`:

| Item | Records for cohort projects | Why it isn't in Bronze |
|---|---|---|
| DS00001 records with Project ID `89101556` (not a P-number) | 4 rows | Can't be linked reliably to a project. Not selected, and not guessed |
| Near matches (an ID that matches only after trimming or case change) | 0 in any source | — |
| v2 projects index | 1,969 | Frozen since about Jan 2025; superseded by v3 (D-01) |
| procnotices global random sample | 378 | Still carries contact name, email and phone fields (D-14). The full API pull for the cohort would return these notices anyway |
| DS00975/6 loan-history samples | 0 | Probe sample only; full history is D-12 |
| DS00005/DS01004 sample files | — | Subsets of the bulk files already extracted (would duplicate records) |
| WGI / CPIA indicators | — | Country-year data with no project key. Linking needs the country crosswalk (D-07), which is a Silver step. Whether to load them as unfiltered reference data is a decision for you |
| WDS document metadata, PDFs, text, parsed procurement plans | — | Outside the five requested domains. WDS project IDs are multi-valued, and the parsed-plan CSV is derived output, not raw |

## 5. Recommended Bronze design

- **Schema:** `workspace.wbg_bronze` (later `wbg_silver`, `wbg_gold`). Table names as listed in §1, plus two control
  tables:
  - `ctl_poc_cohort`, from `_control/`
  - `ctl_bronze_input_manifest`, from `_manifest/*.csv`
- **Table properties** on every table:
  - `delta.columnMapping.mode = 'name'`, with minReaderVersion 2 and minWriterVersion 5. This is required to keep source
    column names that contain spaces or parentheses, such as `Supplier Contract Amount (USD)`.
  - Provenance properties: `source.file`, `source.sha256`, `source.coverage`, `source.selection_rule`, `cohort.id`.

**CSV tables (minimal processing)**

| Option | Value | Reason |
|---|---|---|
| `header` / `inferSchema` | true / **false** | Every source column is STRING, with values exactly as published (dates like `10/20/2026`, amounts with separators) |
| `multiLine` | **true** | DS00005, DS01004 and DS00979 have line breaks inside quoted fields |
| `quote` / `escape` | `"` / `"` | RFC 4180 doubled quotes (Spark's default escape is a backslash) |
| `encoding` | UTF-8 | Source encoding |
| `nullValue` | `"\u0000"` | Keeps empty fields as empty strings rather than NULL. The post-load check reports the remaining NULL count; confirm it is 0 |
| `mode` / `rescuedDataColumn` | PERMISSIVE / `_rescued_data` | Malformed values are captured, not dropped. The expected count is 0 |

**BOM in the first column name.** All six bulk CSVs start with a UTF-8 BOM. If Spark keeps it in the first column name
(`\ufeffAs of Date`), the notebook removes only that character and records `source.header_bom_removed = true`. This is
the only name change; values are untouched. Please confirm you accept it, or keep the BOM in the column name.

**JSONL tables.** Read with the `text` reader, one line per record.
- `raw_record`: the original line, a STRING and byte-identical to the extract.
- `record`: `parse_json(raw_record)`, a VARIANT for querying.
- `_project_id`: `get_json_object(raw_record, '$.id' | '$.project_id')`, for boundary checks.

The JSON is not flattened, and no types are inferred.

**Ingestion metadata columns (all tables)**
- `_source_file_path`, `_source_file_size`, `_source_file_modified_at` (from `_metadata`)
- `_source_file_sha256` (the extract's SHA-256 from the manifest)
- `_upstream_file`, `_upstream_sha256` (the original Phase 1 file)
- `_extracted_at_utc`
- `_cohort_id` (`D-26/seed-20261021`)
- `_ingested_at`
- `_row_sha256` (a hash of the source columns or raw record, for snapshot diffing later, D-13)
- `_rescued_data` (CSV only)

**Write mode.** `writeTo(...).create()` fails if the table already exists, so Bronze is never silently overwritten.
Future refreshes should append a new extract (a new `_extracted_at_utc`) instead of replacing rows. Refresh policy is
D-13 and not decided here.

**Access.** `bronze_procurement_notices_api.notice_text` contains personal contact details written inside the notice
text. Keep Bronze access restricted, and mask the details in Silver before any indexing (D-14).

The ready-to-review code is [`notebooks/01_bronze_ingest.py`](notebooks/01_bronze_ingest.py), in Databricks
notebook-source format. It has **not been run**.

## 6. Recommended first upload

Upload these to `/Volumes/workspace/default/wbg_procurement_test/bronze_inputs/`, mirroring the local folders. That's
about 85 MB.

| Local file | Volume path |
|---|---|
| `bronze_inputs/_control/poc_cohort_2000_manifest.csv` | `.../bronze_inputs/_control/poc_cohort_2000_manifest.csv` |
| `bronze_inputs/_manifest/bronze_inputs_manifest.json` and `.csv` | `.../bronze_inputs/_manifest/` |
| `bronze_inputs/projects_v3/projects_v3_population__cohort2000.jsonl` | `.../bronze_inputs/projects_v3/` |
| `bronze_inputs/contract_awards_ds00005/ds00005_contract_awards__cohort2000.csv` | `.../bronze_inputs/contract_awards_ds00005/` |
| `bronze_inputs/ieg_ratings_ds00053/ds00053_ieg_ratings__cohort2000.csv` | `.../bronze_inputs/ieg_ratings_ds00053/` |

Why these first:
- They test every risky ingestion path on a small set: the JSONL raw-record path, the multi-line BOM CSV path, column
  mapping and the cohort-boundary checks.
- Their expected values are already fixed:

  | Table | Expected rows | Expected projects |
  |---|---|---|
  | `bronze_projects_v3` | 2,000 | 2,000 |
  | `bronze_contract_awards_ds00005` | 186,907 | 2,000 |
  | `bronze_ieg_ratings_ds00053` | 750 | 750 |

  Also expect 0 IDs outside the cohort and 0 rescued rows.

**Second batch, after the first validates:**
- `procurement_notices_ds00979` (112 MB; the largest multi-line CSV)
- `contract_awards_legacy_ds01004`
- `ibrd_loans_ds00047`
- `ida_credits_ds00001`
- the two partial JSONL tables: `projects_v3_allfields` and `procurement_notices_api` (the latter with access
  restrictions)

**How to upload** (when you choose to):
- **UI:** Catalog Explorer → `workspace` → `default` → volume `wbg_procurement_test` → *Upload to this volume*. Create
  the sub-folders first.
- **CLI:** `databricks fs cp -r databricks_upload/bronze_inputs dbfs:/Volumes/workspace/default/wbg_procurement_test/bronze_inputs`

After uploading, compare each file's SHA-256 with `_manifest/bronze_inputs_manifest.json`.

## 7. Out of scope here

Silver and Gold transformations, notice-to-contract linking, deduplication, type casting, the indicator reference
tables, and the additional downloads listed in
[cohort/COHORT_SELECTION_METHODOLOGY.md §6](../cohort/COHORT_SELECTION_METHODOLOGY.md).
