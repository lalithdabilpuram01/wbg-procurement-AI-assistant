# Ingestion inbox

Drop each file into the folder of its type, then run the pipeline from `orchestrator/`:

```bash
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m ingest run     # once
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m ingest watch   # keep running: ingest files as they land
```

| Folder | Put here | Formats |
|---|---|---|
| `pad/` | Project Appraisal Documents | PDF, Word, scanned PDF / image |
| `isr/` | Implementation Status and Results Reports | PDF, Word, scanned PDF / image |
| `icr/` | Implementation Completion Reports and ICR Reviews | PDF, Word, scanned PDF / image |
| `procurement_plans/` | STEP procurement plans | PDF, Word, Excel, CSV, JSON |
| `contracts/` | Contract award feeds (one row per contract) | CSV, JSON, Excel |
| `notices/` | Procurement notice feeds (one row per notice) | CSV, JSON, Excel |
| `other/` | Anything else: the type is detected from the content | all of the above |

Tips:
- Put the project id in the file name (`P148850_plan_2025.pdf`) when the document itself may not show it.
- A file that cannot be ingested moves to `_quarantine/<folder>/` with a `.reason.json` that says what to do.
- Loaded files move to `_processed/<folder>/<date>/`; every outcome is in `_ledger/ledger.jsonl`.
- Dropping the same file again does nothing; a corrected file with the same name replaces the earlier version.
- Scanned files need Tesseract (`brew install tesseract`).

Design: `docs/architecture/INGESTION_AND_KNOWLEDGE_GRAPH.md`.
