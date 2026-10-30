# research/ (not used by the app)

History of how the platform was designed: discovery scripts, data profiling, ML training experiments, earlier RAG
releases, proof-of-concept code and audits. Nothing in the running app, the API, the web UI or the Databricks jobs
imports from here, and `uv run wbg-test` does not test it. Paths inside these scripts still point at the layout they
were written for, so treat them as read-only records rather than runnable tools.

| Folder | What it was |
|---|---|
| discovery/ | Phase 1 public-data discovery: API pulls, profiling, schemas, the cohort selection inputs |
| cohort/ | How the 2,000-project cohort was chosen (the cohort file itself is in data/cohort/) |
| databricks_upload/ | The original manual Bronze upload notebooks and guides (now done by etl/) |
| audits/ | PDF table-quality audit |
| ml/ | Model training stages, experiments, notebooks and reports (the frozen models stay in ml/final*/) |
| rag_module1_discovery/ | How the 35-project RAG scope and the document set were selected |
| rag_release_v2_1/, rag_ingestion_p1/ | Earlier RAG releases and extractor fixes, superseded by rag/release_v3 |
| rag_tools/ | One-off RAG evaluation and demo scripts |
| uc_volume_poc/ | Unity Catalog Volume proof of concept |
| orchestrator_live/ | One-off live end-to-end checks from earlier milestones |
| notes/ | Early assessment and the repository clean-up audit |
