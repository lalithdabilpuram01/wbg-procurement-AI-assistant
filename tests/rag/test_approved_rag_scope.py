"""The Document RAG corpus is limited to the 35 approved World Bank projects (decision D-32). Read-only and offline.

Fails when a project ID outside the approved set appears in a scope manifest, the downloaded corpus, the processed
evidence, the retrieval benchmark or the ingestion configuration; when the approved project manifest itself changes;
or when a downloaded PDF or an in-scope plan text no longer matches its recorded SHA-256.

New documents or newer revisions for the SAME 35 projects are allowed (normal ingestion and validation controls);
new projects are not.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCOPE = REPO / "data" / "rag_scope" / "rag_scope_v3_35_projects"
PROJECT_MANIFEST = SCOPE / "PROJECT_SELECTION_35.csv"
APPROVED_PROJECT_MANIFEST_SHA256 = "7bb630b38b893c52eca17fbe347510ae2a432baabccc9a09ea6fc743e3d5697f"
APPROVED_PROJECT_COUNT = 35

CORPUS = REPO / "rag" / "corpus_v3"
PLAN_TEXTS = REPO / "data" / "rag_scope" / "plan_version_coverage_v1"
SCOPE_TABLES = sorted(SCOPE.glob("*.csv")) + sorted((SCOPE / "plan_coverage").glob("*.csv")) + [CORPUS / "CORPUS_MANIFEST_FIRST10.csv"]
PROCESSED_RECORDS = sorted(p for p in (CORPUS / "processed").rglob("*.jsonl") if "by_document" not in p.parts)
PROJECT_ID = re.compile(r"\bP\d{6}\b")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def approved() -> frozenset[str]:
    return frozenset(row["project_id"] for row in read_csv(PROJECT_MANIFEST))


def outside(ids: set[str], approved: frozenset[str]) -> list[str]:
    return sorted(ids - approved)


# ------------------------------------------------------------------------------------------------- the approved set
def test_approved_project_manifest_is_unchanged():
    assert sha256(PROJECT_MANIFEST) == APPROVED_PROJECT_MANIFEST_SHA256, "the approved 35-project manifest was modified"


def test_exactly_35_unique_approved_projects(approved):
    rows = read_csv(PROJECT_MANIFEST)
    assert len(rows) == APPROVED_PROJECT_COUNT and len(approved) == APPROVED_PROJECT_COUNT
    assert all(PROJECT_ID.fullmatch(p) for p in approved)


def test_ingestion_reads_the_approved_scope_files():
    from rag.ingestion import config

    assert config.PROJECT_MANIFEST == PROJECT_MANIFEST
    assert config.SCOPE_MANIFEST == SCOPE / "DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv"


# --------------------------------------------------------------------------------- no project outside the set
@pytest.mark.parametrize("table", SCOPE_TABLES, ids=lambda p: p.relative_to(REPO).as_posix())
def test_scope_tables_contain_only_approved_projects(table, approved):
    ids = {row["project_id"] for row in read_csv(table) if row.get("project_id")}
    assert ids, f"{table.name} has no project_id values"
    assert outside(ids, approved) == []


def test_downloaded_pdfs_belong_to_approved_projects(approved):
    project_dirs = {d.name for country in (CORPUS / "pdf").iterdir() if country.is_dir() for d in country.iterdir() if d.is_dir()}
    loose = [p.name for p in (CORPUS / "pdf").rglob("*.pdf") if p.parent.name not in project_dirs]
    assert outside(project_dirs, approved) == [] and loose == []


@pytest.mark.parametrize("records", PROCESSED_RECORDS, ids=lambda p: p.relative_to(REPO).as_posix())
def test_processed_evidence_contains_only_approved_projects(records, approved):
    with records.open(encoding="utf-8") as handle:
        ids = {row["project_id"] for row in map(json.loads, handle) if row.get("project_id")}
    assert outside(ids, approved) == []


def test_retrieval_benchmark_mentions_only_approved_projects(approved):
    text = (REPO / "rag" / "retrieval" / "benchmark_v1.json").read_text(encoding="utf-8")
    assert outside(set(PROJECT_ID.findall(text)), approved) == []


# ---------------------------------------------------------------------------------------------- evidence intact
def test_downloaded_pdfs_match_the_corpus_manifest():
    rows = [r for r in read_csv(CORPUS / "CORPUS_MANIFEST_FIRST10.csv") if r["download_status"] == "downloaded"]
    assert rows
    for row in rows:
        path = REPO / row["local_path"]
        assert path.is_file(), f"missing approved PDF: {row['local_path']}"
        assert sha256(path) == row["sha256"], f"approved PDF changed: {row['local_path']}"


def test_in_scope_plan_texts_match_the_fetch_log(approved):
    fetched = [json.loads(line) for line in (PLAN_TEXTS / "fetch_log.jsonl").read_text(encoding="utf-8").splitlines()]
    latest = {e["document_id"]: e for e in fetched}            # last attempt wins, as in m16
    in_scope = [e for e in latest.values() if e["project_id"] in approved and e.get("http_status") == 200 and e.get("path")]
    assert {e["project_id"] for e in in_scope} == set(approved)
    for entry in in_scope:
        path = PLAN_TEXTS / entry["path"]
        assert path.is_file(), f"missing in-scope plan text: {entry['path']}"
        assert sha256(path) == entry["sha256"], f"in-scope plan text changed: {entry['path']}"
