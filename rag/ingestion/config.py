"""Paths and constants for the ingestion pipeline."""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # procurement-intelligence/
CORPUS = REPO / "rag" / "corpus_v3"
CORPUS_MANIFEST = CORPUS / "CORPUS_MANIFEST_FIRST10.csv"
SCOPE_MANIFEST = REPO / "rag" / "module1_discovery" / "rag_scope_v3_35_projects" / "DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv"
PROJECT_MANIFEST = REPO / "rag" / "module1_discovery" / "rag_scope_v3_35_projects" / "PROJECT_SELECTION_35.csv"
OUTPUT = CORPUS / "processed" / "pilot_v1"            # stage-1 outputs (kept unchanged)
OUTPUT_V2 = CORPUS / "processed" / "pilot_v2"         # quality improvements + three chunking strategies
OUTPUT_V2_1 = CORPUS / "processed" / "pilot_v2_1"     # v2 + stacked-value table cells (extraction 2.1.0)
# WBG_RAG_REFERENCE_DIR: the folder holding the contracts snapshots and vocab.json (the document ETL job reads them
# from a Volume); unset, the local corpus folder is used.
REFERENCE = Path(os.environ["WBG_RAG_REFERENCE_DIR"]) if os.environ.get("WBG_RAG_REFERENCE_DIR") else CORPUS / "processed" / "reference"
CONTRACTS_SNAPSHOT = REFERENCE / "contracts_snapshot.jsonl"
CONTRACTS_SNAPSHOT_META = REFERENCE / "contracts_snapshot.meta.json"

# Milestone D: the approved 35-project scope (237 documents), extraction 2.1.0, with its own inputs and outputs
CORPUS_MANIFEST_V3 = CORPUS / "CORPUS_MANIFEST_V3_35.csv"
OUTPUT_V3 = CORPUS / "processed" / "pilot_v3"
CONTRACTS_SNAPSHOT_V3 = REFERENCE / "contracts_snapshot_v3_35.jsonl"
CONTRACTS_SNAPSHOT_V3_META = REFERENCE / "contracts_snapshot_v3_35.meta.json"

CHUNK_TARGET_TOKENS = 500
CHUNK_MAX_TOKENS = 650
CHUNK_MIN_TOKENS = 80

# reuse the Phase 1 helpers (wbdisc) and the plan-text parser rather than re-implementing them
for _p in (REPO / "discovery", REPO / "rag" / "module1_discovery"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
