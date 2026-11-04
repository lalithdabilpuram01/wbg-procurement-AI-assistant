"""Lesson cards of an ingested completion report (ICR), IEG review (ICRR) or appraisal document (PAD): the blocks of
every section the hierarchy tagged `lessons` (lessons.cards decides which paragraphs are lessons)."""
from __future__ import annotations

from lessons.cards import SOURCES, LessonCard, Paragraph, Source, cards

from .model import ParsedDocument
from .reference import Reference

TEXT_BLOCKS = {"paragraph", "list_item"}


def cards_for(doc: ParsedDocument, ref: Reference) -> list[LessonCard]:
    """Lesson cards from every lessons section of the document (none for types without lessons), each card once."""
    if doc.doc_type not in SOURCES:
        return []
    project = ref.project(doc.project_id) or {}
    out, seen = [], set()
    for section in (s for s in doc.sections if s.topic == "lessons"):
        blocks = sorted((b for b in doc.blocks if b.section_id == section.section_id and b.kind in TEXT_BLOCKS), key=lambda b: b.order)
        source = Source(doc.document_id, doc.doc_type, doc.project_id, project.get("country_name"), project.get("sector"),
                        section.heading[:200], section.section_id)
        found = cards(source, [Paragraph(b.text, b.page_start, b.page_end) for b in blocks])
        out += [c for c in found if c.card_id not in seen]
        seen |= {c.card_id for c in found}
    return out
