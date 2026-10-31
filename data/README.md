# data/

Input files the app and the Databricks ETL read. Code never builds these paths itself: every one is named in
`app_config/paths.py`.

| Folder | Contents | Tracked in git |
|---|---|---|
| cohort/ | The 2,000-project cohort that bounds every table and answer | yes |
| bronze_inputs/, ref_bronze_inputs/ | Cohort-bounded extracts uploaded to Databricks by `uv run wbg-etl-seed` | manifests only (data is reproducible) |
| source_pulls/ | Checksums of the original World Bank downloads the extracts were cut from | yes |
| rag_scope/ | The approved 35-project RAG scope and the 10-document extractor pilot | yes, except extracted text |
| test_documents/ | Three synthetic samples for trying the app (invented, marked "SAMPLE FOR TESTING ONLY"): a draft tender for Check tender, an ICR for `ingestion_inbox/icr/` (5 lesson cards), a procurement plan CSV for `ingestion_inbox/procurement_plans/` | yes |
