"""Map ONE approved PDF's existing, sanitised pilot_v2 evidence and its cached embeddings to Lakebase records.

Nothing is re-parsed or re-embedded:
* retrieval units come from the existing retrieval `Corpus` (strategy C + table windows, the approved default);
* vectors come from the existing cache file, which is only used when its name equals the hash of the exact unit IDs,
  texts and model (the retrieval code's own cache key), so row i is proven to belong to unit i;
* only `shared/` and `strategies/` records are read. `restricted/` (unsanitised text) is never opened.
Any identity, checksum or mapping mismatch raises EvidenceError; nothing is guessed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[3]
PILOT = REPO / "rag" / "corpus_v3" / "processed" / "pilot_v2"
SCOPE = REPO / "rag" / "module1_discovery" / "rag_scope_v3_35_projects"
CORPUS_MANIFEST = REPO / "rag" / "corpus_v3" / "CORPUS_MANIFEST_FIRST10.csv"
ALLOWED_SOURCES = ("shared", "strategies")

DOCUMENT_ID = "40089196"
PROJECT_ID = "P148850"
EXPECTED_SHA256 = "c32be7ca2d3d1ce2ea8e2e017cd7ef2cedea0e9aad940711e6f5b4cb7e5a4d24"   # Step 3 upload, verified in UC
VOLUME_PATH = "/Volumes/workspace/default/wbg_rag_pdfs_poc/ET/P148850/ISR_2026-02-28_40089196.pdf"
STRATEGY = "C"                                   # approved default (RAG_RETRIEVAL_MVP_REPORT.md section 5)
DIMENSIONS = 768
PREPROCESSING = {"input": "indexed_text", "normalize_embeddings": True, "batch_size": 16,
                 "library": "sentence-transformers", "trust_remote_code": True}

RESULTS_SCHEMA_ID = "wb_isr_results_indicators_v1"
RESULTS_FIELDS = ("indicator_name", "baseline_value", "baseline_period", "previous_value", "previous_date",
                  "current_value", "current_date", "target_value", "target_period")
UNIT_IN_NAME = re.compile(r"\(([^()]+)\)\s*$")
NUMBER = re.compile(r"^-?\d{1,3}(,\d{3})*(\.\d+)?$|^-?\d+(\.\d+)?$")


class EvidenceError(RuntimeError):
    """The evidence cannot be mapped unambiguously to the approved PDF version."""


@dataclass
class EvidenceBundle:
    """Records per target table (column name -> value), plus the vectors aligned with `unit_embeddings`."""

    records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    vectors: list[np.ndarray] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------------------------------------- helpers
def _jsonl(relative: str) -> list[dict[str, Any]]:
    if relative.split("/")[0] not in ALLOWED_SOURCES:
        raise EvidenceError(f"refusing to read outside sanitised outputs: {relative}")
    with (PILOT / relative).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_import() -> None:
    """Make the existing rag package importable read-only (no bytecode written into rag/)."""
    sys.dont_write_bytecode = True
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))


def _retrieval_core():
    _repo_import()
    from rag.retrieval import core
    return core


def _residual_risk(text: str) -> dict[str, int]:
    """The ingestion pipeline's own post-sanitisation detectors (e-mail; '+'-prefixed or labelled phone numbers)."""
    _repo_import()
    from rag.ingestion.sanitize import TOKEN, residual_risk
    return {**residual_risk(text), "mask_placeholders": len(TOKEN.findall(text))}


# ------------------------------------------------------------------------------------------------ identity
def verify_identity() -> dict[str, Any]:
    approved = {r["project_id"] for r in _read_csv(SCOPE / "PROJECT_SELECTION_35.csv")}
    planned = {r["document_id"]: r for r in _read_csv(SCOPE / "DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv")}
    corpus_row = next((r for r in _read_csv(CORPUS_MANIFEST) if r["document_id"] == DOCUMENT_ID), None)
    version = next((v for v in _jsonl("shared/document_versions.jsonl") if v["document_id"] == DOCUMENT_ID), None)
    document = next((d for d in _jsonl("shared/documents.jsonl") if d["document_id"] == DOCUMENT_ID), None)
    if corpus_row is None or version is None or document is None or DOCUMENT_ID not in planned:
        raise EvidenceError("document missing from a manifest or from the processed outputs")
    local_sha = _sha256_file(REPO / corpus_row["local_path"])
    checks = {"project_approved": PROJECT_ID in approved, "in_approved_document_manifest": planned[DOCUMENT_ID]["project_id"] == PROJECT_ID,
              "corpus_manifest_sha256": corpus_row["sha256"] == EXPECTED_SHA256, "local_pdf_sha256": local_sha == EXPECTED_SHA256,
              "processed_version_sha256": version["sha256"] == EXPECTED_SHA256,
              "processed_manifest_sha256": version["download"]["manifest_sha256"] == EXPECTED_SHA256,
              "document_version_id": version["document_version_id"] == f"{DOCUMENT_ID}:{EXPECTED_SHA256[:12]}",
              "project_matches": document["project_id"] == PROJECT_ID == corpus_row["project_id"],
              "no_validation_errors": not version.get("validation_errors")}
    if not all(checks.values()):
        raise EvidenceError(f"identity mismatch: {[k for k, ok in checks.items() if not ok]}")
    return {"checks": checks, "version": version, "document": document, "corpus_row": corpus_row}


# --------------------------------------------------------------------------------------- retrieval + vectors
def mapped_units_and_vectors() -> tuple[Any, list[Any], np.ndarray, str]:
    """This document's units of the approved corpus and their cached vectors, with the mapping proven by the cache key."""
    core = _retrieval_core()
    corpus = core.Corpus(STRATEGY, include_tables=True)
    key = hashlib.sha256(("\n".join(u.unit_id + u.text for u in corpus.units) + core.EMBED_MODEL).encode()).hexdigest()[:16]
    cache = core.CACHE / f"emb_{corpus.strategy}_{len(corpus.units)}_{key}.npy"
    if not cache.is_file():
        raise EvidenceError("no embedding cache matches the current units, texts and model; refusing to regenerate")
    matrix = np.load(cache)
    if matrix.shape != (len(corpus.units), DIMENSIONS):
        raise EvidenceError("cache shape does not match the unit count and 768 dimensions")
    indices = [i for i, u in enumerate(corpus.units) if u.document_id == DOCUMENT_ID]
    return corpus, [corpus.units[i] for i in indices], matrix[indices].astype(np.float32), cache.name


# -------------------------------------------------------------------------------------------- table values
def indicator_values(table_id: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    """Typed values of one results-indicator row (9-column ISR layout). Rows without a value, header rows, comment
    rows and component titles yield nothing. A name without '(unit)' marks a row split across pages: needs_review."""
    cells = list(row["cells_clean"]) + [""] * (len(RESULTS_FIELDS) - len(row["cells_clean"]))
    name, values = cells[0].strip(), cells[1:len(RESULTS_FIELDS)]
    if row["is_header"] or not name or name == "Indicator Name" or cells[1] in ("Value", "Comments on achieving targets"):
        return []
    if not any(v.strip() for v in values):
        return []
    unit_match = UNIT_IN_NAME.search(name)
    unit = unit_match.group(1) if unit_match else None
    quality = "complete_row" if unit else "needs_review"
    out = [{"table_id": table_id, "row_index": row["row_index"], "field_name": "indicator_name", "value_text": name,
            "value_numeric": None, "unit": unit, "quality": quality}]
    for field_name, raw in zip(RESULTS_FIELDS[1:], values):
        raw = raw.strip()
        if not raw:
            continue
        numeric = float(raw.replace(",", "")) if field_name.endswith("_value") and NUMBER.match(raw) else None
        out.append({"table_id": table_id, "row_index": row["row_index"], "field_name": field_name, "value_text": raw,
                    "value_numeric": numeric, "unit": unit if field_name.endswith("_value") else None, "quality": quality})
    return out


def is_results_indicator_table(table: dict[str, Any]) -> bool:
    header = " ".join(" ".join(r) for r in table.get("header_clean") or [])
    return table["n_columns"] == len(RESULTS_FIELDS) and "Indicator Name" in header and "Closing Period" in header


# ------------------------------------------------------------------------------------------------- bundle
def build_bundle() -> EvidenceBundle:
    identity = verify_identity()
    version, document = identity["version"], identity["document"]
    corpus, units, vectors, cache_name = mapped_units_and_vectors()
    version_id = version["document_version_id"]
    api = document["api"]
    bundle = EvidenceBundle()
    r = bundle.records

    r["documents"] = [{"document_version_id": version_id, "document_id": DOCUMENT_ID, "project_id": PROJECT_ID,
                       "country_code": document["country_code"], "doc_type": document["doc_type"], "document_role": document["role"],
                       "title": api["title"], "document_date": api.get("wds_document_date"),
                       "disclosure_date": api.get("wds_disclosure_date"), "source_url": api["final_url"],
                       "sha256": version["sha256"], "size_bytes": version["size_bytes"], "page_count": version["page_count"],
                       "volume_path": VOLUME_PATH, "extraction_version": version["extraction_version"],
                       "pipeline_code_hash": json.loads((PILOT / "run_manifest.json").read_text())["code_hash"]}]
    r["pages"] = [{"document_version_id": version_id, "page": p["page"], "printed_page_label": p.get("printed_page_label"),
                   "chars": p.get("chars"), "tables_detected": p.get("tables_detected")}
                  for p in _jsonl("shared/pages.jsonl") if p["document_id"] == DOCUMENT_ID]
    r["sections"] = [{"section_id": s["section_id"], "document_version_id": version_id, "section_path": s["section_path"],
                      "heading_text": s.get("heading_text"), "heading_level": s.get("heading_level"), "section_order": s.get("order"),
                      "page_start": s.get("physical_page_start"), "page_end": s.get("physical_page_end"),
                      "printed_page_label_start": s.get("printed_page_label_start"), "printed_page_label_end": s.get("printed_page_label_end")}
                     for s in _jsonl("shared/sections.jsonl") if s["document_id"] == DOCUMENT_ID]
    parents = [p for p in _jsonl("strategies/C/parents.jsonl") if p["document_id"] == DOCUMENT_ID]
    r["parent_chunks"] = [{"parent_id": p["chunk_id"], "document_version_id": version_id, "section_path": p.get("section_path"),
                           "page_start": p["physical_page_start"], "page_end": p["physical_page_end"], "text": p["text"],
                           "child_ids": list(p["child_ids"])} for p in parents]
    chunk_records = {c["chunk_id"]: c for c in _jsonl("strategies/C/chunks.jsonl") if c["document_id"] == DOCUMENT_ID}

    r["retrieval_units"], r["unit_embeddings"], r["citations"] = [], [], []
    for unit, vector in zip(units, vectors):
        source = chunk_records.get(unit.unit_id, {})
        printed = corpus.pages.get((DOCUMENT_ID, unit.page_start), {}).get("printed_page_label")
        r["retrieval_units"].append({
            "unit_id": unit.unit_id, "document_version_id": version_id, "unit_type": unit.unit_type,
            "strategy_version": source.get("strategy_version", "C-1.0") if unit.unit_type == "chunk" else "table-window-1.0",
            "section_id": unit.meta.get("section_id"), "section_path": unit.section, "parent_id": unit.parent_id,
            "table_id": unit.table_id, "page_start": unit.page_start, "page_end": unit.page_end, "printed_page_label": printed,
            "indexed_text": unit.text, "excerpt": unit.excerpt, "indexed_text_sha256": _sha256_text(unit.text),
            "source_spans": source.get("source_spans", [{"table_rows": unit.meta.get("rows")}] if unit.table_id else []),
            "sanitisation": source.get("sanitisation") or unit.meta.get("sanitisation") or {}})
        r["unit_embeddings"].append({"unit_id": unit.unit_id, "model": _retrieval_core().EMBED_MODEL,
                                     "preprocessing": PREPROCESSING, "source_cache": cache_name,
                                     "embedding_sha256": hashlib.sha256(vector.tobytes()).hexdigest()})
        bundle.vectors.append(vector)
        r["citations"].append({"unit_id": unit.unit_id, "document_version_id": version_id, "project_id": PROJECT_ID,
                               "document_id": DOCUMENT_ID, "page_start": unit.page_start, "page_end": unit.page_end,
                               "printed_page_label": printed, "section_path": unit.section, "source_url": api["final_url"],
                               "volume_path": VOLUME_PATH})

    tables = [t for t in _jsonl("shared/tables.jsonl") if t["document_id"] == DOCUMENT_ID]
    descriptions = {tid: d for d in _jsonl("shared/table_descriptions.jsonl") if d["document_id"] == DOCUMENT_ID for tid in d["table_ids"]}
    rows = [row for row in _jsonl("shared/table_rows.jsonl") if row["document_id"] == DOCUMENT_ID]
    results_tables = {t["table_id"] for t in tables if is_results_indicator_table(t)}
    r["table_schemas"] = [{"table_schema_id": RESULTS_SCHEMA_ID, "status": "draft",
                           "description": "World Bank ISR results framework: PDO and intermediate indicators (9-column layout)",
                           "fields": [{"name": n, "type": "numeric" if n.endswith("_value") else "text",
                                       "unit_source": "indicator_name parentheses" if n.endswith("_value") else None}
                                      for n in RESULTS_FIELDS]}] if results_tables else []
    r["pdf_tables"] = [{"table_id": t["table_id"], "document_version_id": version_id,
                        "table_group_id": descriptions.get(t["table_id"], {}).get("table_group_id", t["table_id"]),
                        "table_schema_id": RESULTS_SCHEMA_ID if t["table_id"] in results_tables else None, "page": t["page"],
                        "caption": t.get("caption") or None, "section_path": t.get("section_path"), "header": t.get("header_clean") or [],
                        "n_columns": t["n_columns"], "n_data_rows": t["n_data_rows"], "continuation_of": t.get("continuation_of"),
                        "description": descriptions.get(t["table_id"], {}).get("description"),
                        "extraction_version": version["extraction_version"]} for t in tables]
    r["pdf_table_rows"] = [{"table_id": row["table_id"], "row_index": row["row_index"], "page": row["page"],
                            "is_header": row["is_header"], "cells": list(row["cells_clean"])} for row in rows]
    r["pdf_table_values"] = [value for row in rows if row["table_id"] in results_tables
                             for value in indicator_values(row["table_id"], row)]
    bundle.checks = run_checks(bundle, identity["checks"], cache_name)
    return bundle


# --------------------------------------------------------------------------------------------------- checks
def run_checks(bundle: EvidenceBundle, identity: dict[str, bool], cache_name: str) -> dict[str, Any]:
    r, vectors = bundle.records, np.stack(bundle.vectors) if bundle.vectors else np.zeros((0, DIMENSIONS))
    units = r["retrieval_units"]
    texts = [u["excerpt"] for u in units] + [" ".join(row["cells"]) for row in r["pdf_table_rows"]] + [p["text"] for p in r["parent_chunks"]]
    every_record = [rec for table in r.values() for rec in table]
    checks = {
        "identity": identity,
        "embedding_cache": cache_name,
        "vectors": {"count": len(vectors), "dimensions": int(vectors.shape[1]) if len(vectors) else 0,
                    "all_finite": bool(np.isfinite(vectors).all()),
                    "unit_norm": bool(np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4)),
                    "unique_vectors": len({v.tobytes() for v in vectors}) == len(vectors),
                    "aligned_with_units": len(vectors) == len(units) == len(r["unit_embeddings"])},
        "unique_unit_ids": len({u["unit_id"] for u in units}) == len(units),
        "single_document_version": {rec.get("document_version_id") for rec in every_record if "document_version_id" in rec}
                                   == {r["documents"][0]["document_version_id"]},
        "only_project": sorted({rec["project_id"] for rec in every_record if "project_id" in rec}),
        "unit_ids_belong_to_document": all(u["unit_id"].startswith(DOCUMENT_ID) for u in units),
        "sanitised_sources_only": list(ALLOWED_SOURCES),
        "sanitisation_masks_in_units": sum(int(v) for u in units for v in (u["sanitisation"] or {}).values() if isinstance(v, (int, float))),
        "residual_personal_data": {kind: sum(_residual_risk(t)[kind] for t in texts)
                                   for kind in ("email", "phone", "mask_placeholders")},
        "parents_referenced_exist": {u["parent_id"] for u in units if u["parent_id"]} <= {p["parent_id"] for p in r["parent_chunks"]},
        "table_values_reference_rows": {(v["table_id"], v["row_index"]) for v in r["pdf_table_values"]}
                                       <= {(row["table_id"], row["row_index"]) for row in r["pdf_table_rows"]},
    }
    return checks


def lookup_indicator(bundle: EvidenceBundle, name_prefix: str, field_name: str) -> list[dict[str, Any]]:
    """Exact, deterministic lookup of a typed table value (complete rows only), with its page citation."""
    values = bundle.records["pdf_table_values"]
    rows = {(v["table_id"], v["row_index"]) for v in values
            if v["field_name"] == "indicator_name" and v["quality"] == "complete_row" and v["value_text"].startswith(name_prefix)}
    pages = {(row["table_id"], row["row_index"]): row["page"] for row in bundle.records["pdf_table_rows"]}
    return [{**v, "page": pages[(v["table_id"], v["row_index"])]} for v in values
            if (v["table_id"], v["row_index"]) in rows and v["field_name"] == field_name]
