"""Lesson cards from the processed pilot corpus (pilot_v3): the backfill for documents that arrived before the
automated ingestion. New documents get their cards from the ingestion pipeline (ingest.pipeline)."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from rag.ingestion import config

from .cards import SOURCES, LessonCard, Paragraph, Source, cards

SHARED = config.OUTPUT_V3 / "shared"
LESSONS_HEADING = "lesson"


def _jsonl(directory: Path, name: str):
    """Records of one JSONL file, read lazily; nothing when the file is missing."""
    path = directory / f"{name}.jsonl"
    if path.exists():
        with path.open() as lines:
            yield from (json.loads(line) for line in lines)


def corpus_cards(directory: Path = SHARED) -> list[LessonCard]:
    """Cards from every lessons section of the corpus's ICR, ICRR and PAD documents, each card once."""
    documents = {d["document_id"]: d for d in _jsonl(directory, "documents") if d["doc_type"] in SOURCES}
    projects = {p["project_id"]: p["api"] for p in _jsonl(directory, "projects")}
    sections = {s["section_id"]: s for s in _jsonl(directory, "sections")
                if s["document_id"] in documents and LESSONS_HEADING in s["heading_text"].lower()}
    paragraphs: dict[str, list[Paragraph]] = defaultdict(list)
    for p in _jsonl(directory, "paragraphs"):
        if p["section_id"] in sections and p["kind"] != "heading":
            paragraphs[p["section_id"]].append(Paragraph(p["text"], p.get("page_start"), p.get("page_end")))
    out, seen = [], set()
    for section_id, section in sections.items():
        doc = documents[section["document_id"]]
        project = projects.get(doc.get("project_id"), {})
        source = Source(doc["document_id"], doc["doc_type"], doc.get("project_id"), project.get("country_name"), project.get("sector"),
                        " ".join(section["heading_text"].split())[:200])
        out += [c for c in cards(source, paragraphs[section_id], origin="pilot_v3") if c.card_id not in seen]
        seen |= {c.card_id for c in out}
    return out
