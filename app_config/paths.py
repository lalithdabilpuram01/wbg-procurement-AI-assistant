"""Every input file the app and the Databricks ETL read from the data/ folder, named in one place.

The files under data/ are inputs, not code: the cohort of 2,000 projects, the Bronze extracts uploaded to Databricks,
the checksum manifests that prove where each extract came from, and the RAG document scope. Large extracts are
git-ignored (reproducible); their manifests are tracked.

Paths resolve from this file, so they work on a laptop and inside a Databricks bundle (which keeps the same layout).
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"

# The 2,000-project cohort that bounds every table and answer.
COHORT_MANIFEST = DATA / "cohort" / "poc_cohort_2000_manifest.csv"

# Bronze inputs: cohort-bounded extracts of the public World Bank datasets, uploaded by `uv run wbg-etl-seed`.
BRONZE_INPUTS = DATA / "bronze_inputs"
BRONZE_INPUTS_MANIFEST = BRONZE_INPUTS / "_manifest" / "bronze_inputs_manifest.json"
REF_BRONZE_INPUTS = DATA / "ref_bronze_inputs"
REF_BRONZE_INPUTS_MANIFEST = REF_BRONZE_INPUTS / "_manifest" / "ref_bronze_inputs_manifest.json"

# Checksums of the original downloads the Bronze extracts were cut from (provenance only).
SOURCE_PULLS_CACHE_MANIFEST = DATA / "source_pulls" / "data_cache" / "MANIFEST.jsonl"
SOURCE_PULLS_SAMPLES_MANIFEST = DATA / "source_pulls" / "samples" / "MANIFEST.jsonl"

# The approved RAG scope (35 projects) and the first 10-document pilot used to validate the PDF extractor.
RAG_SCOPE = DATA / "rag_scope" / "rag_scope_v3_35_projects"
RAG_PILOT_FIRST10 = DATA / "rag_scope" / "pilot_first10_v1"
