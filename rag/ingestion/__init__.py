"""Module 1 PDF ingestion pipeline (stage 1: local parsing and metadata; no embeddings, no database).

Schema (see rag/module1_discovery/pilot_first10_v1/METADATA_SCHEMA_v1.json):
  Project -> Document -> DocumentVersion -> Section -> Chunk
  PlanVersion -> PlanActivity -> ContractEvidenceLink
Every record separates API metadata (WDS), PDF-extracted facts and inferred fields.
"""
EXTRACTION_VERSION = "ingest-pilot-1.0.0"
