"""Automated multimodal ingestion into the procurement Knowledge Graph.

Drop a file into the folder for its type under `ingestion_inbox/` and run `python -m ingest run` (or keep
`python -m ingest watch` running). Each file goes through the same stages, and every stage can stop it with a reason:

    1. intake      fingerprint (SHA-256), real format from the bytes, size limits, duplicates, folder/format rules
    2. classify    the document type from its content, checked against the folder it was dropped in
    3. parse       by modality: text PDF (the validated rag.ingestion extractor), scanned PDF/image (OCR), Word, and
                   tabular feeds (CSV / JSON / Excel) mapped onto canonical schemas with row-level validation
    4. structure   one document model: sections with their hierarchy -> blocks -> chunks; tables -> rows
    5. sanitise    personal data masked (rag.ingestion.sanitize); the raw text is never stored in the graph
    6. resolve     entity mentions linked to reference records: exact key, normalised key, or probabilistic
                   (fuzzy match within a project, temporal proximity), each with a match class and confidence
    7. graph       typed nodes and edges with provenance (ingest.graph), validated against the edge catalog; lessons
                   sections of ICRs, IEG reviews and PADs become proposed lesson cards (Lesson nodes, lesson_card rows)
    8. embed+load  bge-m3 vectors and the document's subgraph written in ONE transaction to Lakebase (schema wbg_kg)

A processed file moves to `_processed/`, a refused one to `_quarantine/` with a `.reason.json` beside it, and every
outcome is appended to `_ledger/ledger.jsonl`. Re-dropping an identical file is a no-op; a changed file replaces its
document's subgraph.
"""

PIPELINE_VERSION = "ingest-1.1.0"          # 1.1: lesson cards (Lesson nodes + lesson_card rows)
