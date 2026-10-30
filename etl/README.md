# The ingestion pipelines: upload a file and watch it flow

There are two Lakeflow jobs in Databricks, each started by a file landing in a Volume:

| Job | You upload | It runs | You see it in |
| --- | --- | --- | --- |
| `wbg_procurement_etl` (structured) | a World Bank data file (contracts, notices, projects, ratings, indicators) into `/Volumes/wbg_etl/etl/landing/<dataset>/` | Bronze → 10 Silver → 5 Gold → KPI views → audit (18 tasks) | the **Dashboard** page of the app (pipeline view) |
| `wbg_document_ingest` (unstructured, RAG) | a PDF or Word report (ICR, PAD, ISR, procurement plan) or a contract/notice sheet into `/Volumes/wbg_etl/etl/inbox/<type>/` | register (Bronze) → parse (Silver) → enrich (Gold) → **embed** (bge-m3) → publish (pgvector on Lakebase) | **chat** (the document is searchable) and the **Review queue** (its lesson cards) |

Both run in their own catalog, `wbg_etl`. They never write to the demo's `workspace` tables.

**What starts them:** `uv run wbg-etl-watch`, on your laptop. Databricks' own file-arrival trigger only works on external
volumes, and every volume on Free Edition is managed, so the trigger is refused. The watcher lists the two Volumes
every 10 seconds with the Files API, which needs no cluster and costs nothing. It starts a job when:
- a file is newer than the job's latest run;
- no run of that job is active;
- the uploads have been quiet for 20 s (documents) or 90 s (data).

It keeps no state, so a file uploaded while the watcher was stopped is picked up when you start it.

## One-time setup (done once, before any demo)

```bash
uv run wbg-etl-seed          # split the demo slice off, fill the landing Volume, upload the parser's reference files
uv run wbg-etl-watch         # it sees the seeded files and starts the first full data run (about 20-30 minutes)
```

`wbg-etl-seed` uploads the cohort extracts in `databricks_upload/` to `/Volumes/wbg_etl/etl/landing/<dataset>/`. One
exception: **DS00005 contract awards signed on or after 1 September 2026** (665 contracts, about US$792 M) are held back
in `demo/structured/ds00005_new_contract_awards.csv`, so that uploading them later is real "new awards arriving".
Use `--dry-run` to see what it would upload.

The bundle itself is already deployed. To redeploy after a code change:

```bash
databricks bundle deploy -t etl -p wbg-databricks-free
```

(The CLI ships with the Databricks VS Code extension, under `~/.vscode/extensions/databricks.databricks-*/bin/`.)

## The demo (about 10 minutes)

Have three things open: a terminal running `uv run wbg-etl-watch`, the app (`uv run wbg-api` + `uv run wbg-web`), and
Databricks (**Jobs & Pipelines**). Sign in to Databricks in the same browser as the app, so the embedded dashboard
loads.

### 1. Structured data → dashboard

1. In the app, open **Dashboard** (pipeline view). FY2027 shows the awards up to 31 August 2026.
2. In Databricks, open **Catalog → wbg_etl → etl → landing → contract_awards_ds00005 → Upload**, and upload
   `demo/structured/ds00005_new_contract_awards.csv`.
3. About 90 s later the watcher prints `[data] new upload … -> started run …`. In **Jobs & Pipelines → WBG
   procurement ETL** the 18 tasks turn green one by one:
   - **bronze:** cuts the file to the cohort and adds its rows;
   - **ten Silver jobs:** the same data-quality gates as the verified build;
   - **five Gold jobs**, then **kpi_views** and **audit**.
4. Refresh the Dashboard page. FY2027 contract value grows by about US$0.8 billion.
5. To show the evidence trail:
   - `wbg_etl.etl.dq_results`: every check of the run;
   - `wbg_etl.etl.table_snapshots`: row counts and Delta versions;
   - `DESCRIBE HISTORY wbg_etl.wbg_gold.gold_contract_analytics`: the table before and after.

### 2. A document → embeddings → pgvector → chat

1. Pick a PDF of one of the 35 projects that is **not** already in the corpus, for example a newer ISR or
   procurement plan from documents.worldbank.org. (A document that is already in the corpus is ingested too, but
   chat already finds it through the corpus, so the demo shows less.)
2. Upload it to **wbg_etl → etl → inbox → isr** (or `icr`, `pad`, `procurement_plans`).
3. The watcher starts **WBG document ingestion**. Five boxes turn green: register → parse → enrich → embed → publish.
   On the first run, the embed task also downloads bge-m3, which takes a few minutes.
4. To show what each stage made, in `wbg_etl.documents`:
   - `bronze_document_files`: the file and its fingerprint;
   - `silver_documents`, `silver_document_sections`, `silver_document_chunks`: what was read;
   - `gold_lesson_cards`, `gold_kg_nodes`, `gold_kg_edges`: what it means;
   - `gold_chunk_embeddings`: one 1024-d vector per chunk;
   - `ingest_outcomes`: one line per file per stage.
5. In the app, ask about it, for example *"What does the latest ISR say about procurement for P148850?"*. The answer
   cites the new document, marked **(uploaded)**.
6. Sign in as `reviewer1` and open **Review queue → Lesson cards**: the document's lessons are waiting for approval.

**Robustness to show:**
- Upload the same file again: nothing happens (`duplicate`).
- Put a spreadsheet in `icr/`: it is quarantined, with the reason, in `ingest_outcomes`.

Scanned PDFs need OCR, which the job doesn't have yet, so use text PDFs or Word files.

## Where the vectors go

- The embed task uses **BAAI/bge-m3 at the same revision as the app's document search**, so new vectors and old
  ones live in one space.
- The publish task writes every chunk with its vector to `wbg_kg.chunk` on the Lakebase **development** branch
  (`halfvec(1024)`, HNSW cosine index), in one transaction per document. The production branch is refused in code.
- The app's document search then also runs a pgvector query over these chunks (`orchestrator/wbg_orchestrator/uploaded_documents.py`):
  - documents already in the corpus release are skipped, so nothing is cited twice;
  - hits are unverified passages, because their pages were not re-checked against the PDF, so their numbers are not
    approved figures.

## Upload folders of the data job

Each dataset has its own folder under `/Volumes/wbg_etl/etl/landing/`. A new file must have the same columns as the
published file, and its rows are cut to the 2,000-project cohort. In the procurement and indicator folders every file
counts (new rows add to what's there). For the country list and the income workbook, the newest upload wins. The job
needs a file in every folder; if one is empty, the first task stops and names it.

| Folder | What goes in it |
| --- | --- |
| `projects_v3/`, `projects_v3_allfields/` | Projects API v3 (`.jsonl`, `.jsonl.gz`) |
| `contract_awards_ds00005/`, `contract_awards_legacy_ds01004/` | contract awards CSVs |
| `procurement_notices_ds00979/`, `procurement_notices_api/` | notices CSV, procnotices API records |
| `ieg_ratings_ds00053/`, `ibrd_loans_ds00047/`, `ida_credits_ds00001/` | Finances One CSVs |
| `wdi_*`, `income_classification_history/`, `wgi_estimates/`, `cpia_ratings/` | country reference data |

**The checks.** Real rules still stop a run:
- unique keys;
- required values;
- links that must resolve;
- the notice-text mask (D-14);
- Bronze equalling its source file.

Checks that only pin the October 2026 snapshot (exact row counts, totals, source dates) become warnings, because new
data is supposed to change them. The list is `SNAPSHOT_CHECKS` in `silver/common/pipeline.py`. Every run's reports
also land in `/Volumes/wbg_etl/etl/runs/<job run id>/`, and Delta keeps every old table version.

## Tests (offline: no Spark, no Databricks)

```bash
uv run python -m pytest -q etl/tests
```
