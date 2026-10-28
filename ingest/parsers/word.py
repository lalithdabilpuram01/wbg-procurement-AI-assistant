"""Word documents (.docx): the hierarchy from heading styles (or outline levels), lists, and tables in reading order.

Word has no pages, so locations are block and table positions. A table also becomes one text block where it occurs,
so its content is retrievable in context; its rows are kept as a table as well.
"""
from __future__ import annotations

import re
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from rag.ingestion.sanitize import sanitize

from .. import detect, hierarchy
from ..model import ParsedDocument, TableBlock
from ..reference import Reference
from . import common

HEADING_STYLE = re.compile(r"^(?:Heading|Titre|Überschrift)\s*(\d)$", re.I)
MAX_TABLE_TEXT_ROWS = 40


def parse(path: Path, sha256: str, document_id: str, declared_type: str | None, ref: Reference) -> ParsedDocument:
    try:
        document = docx.Document(str(path))
    except Exception as error:                                          # python-docx raises several error types
        from ..model import Quarantined
        raise Quarantined("corrupt_file", "the Word document cannot be opened") from error
    lines, tables = _read(document)
    text = "\n".join(line.text for line in lines)
    doc_type, warnings = detect.check_type(declared_type, detect.classify(text))
    project_id, match_class, _ = common.project_for(path.name, text, ref)
    sections, blocks = hierarchy.build(document_id, lines)
    section_of = {b.block_id: b.section_id for b in blocks}
    table_blocks = [b for b in blocks if b.kind == "table_text"]
    parsed = ParsedDocument(document_id=document_id, doc_type=doc_type, modality="docx", source_sha256=sha256, source_name=path.name,
                            title=common.title_for(text, path.stem), project_id=project_id,
                            country_code=(ref.project(project_id) or {}).get("country_code"), document_date=common.document_date(text),
                            language=detect.language(text), sections=sections, blocks=blocks, warnings=warnings)
    parsed.tables = [TableBlock(f"{document_id}:t{n:03d}", section_of.get(block.block_id), None, None, header, rows)
                     for n, (block, (header, rows)) in enumerate(zip(table_blocks, tables))]
    parsed.chunks = hierarchy.chunk(document_id, sections, blocks)
    if project_id is None:
        parsed.warnings.append("project_unresolved: no project id or title found")
    elif match_class == "P":
        parsed.warnings.append("project_matched_by_title")
    return parsed


def _read(document) -> tuple[list[hierarchy.Line], list[tuple[list[str], list[list[str]]]]]:
    """Body paragraphs and tables in document order, sanitised."""
    lines, tables = [], []
    for element in document.element.body.iterchildren():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(element, document)
            text = paragraph.text.strip()
            if text:
                lines.append(hierarchy.Line(sanitize(text)[0], None, _level(paragraph), _kind(paragraph), whole=True))
        elif tag == "tbl":
            header, rows = _table(Table(element, document))
            if header or rows:
                tables.append((header, rows))
                rendered = "; ".join(" | ".join(r) for r in ([header] + rows)[:MAX_TABLE_TEXT_ROWS])
                lines.append(hierarchy.Line(f"Table: {rendered}", None, None, "table_text"))
    return lines, tables


def _level(paragraph: Paragraph) -> int | None:
    style = paragraph.style.name if paragraph.style is not None else ""
    if style.lower() == "title":
        return 1
    match = HEADING_STYLE.match(style)
    if match:
        return int(match.group(1))
    outline = paragraph._p.pPr.outlineLvl if paragraph._p.pPr is not None else None
    return int(outline.val) + 1 if outline is not None and int(outline.val) < 9 else None


def _kind(paragraph: Paragraph) -> str:
    style = (paragraph.style.name if paragraph.style is not None else "").lower()
    numbered = paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None
    return "list_item" if "list" in style or numbered else "paragraph"


def _table(table: Table) -> tuple[list[str], list[list[str]]]:
    """Header and body rows; a merged cell's repeated text is kept once per row."""
    rows = []
    for row in table.rows:
        cells, previous = [], None
        for cell in row.cells:
            if cell._tc is not previous:
                cells.append(sanitize(" ".join(cell.text.split()))[0])
            previous = cell._tc
        rows.append(cells)
    return (rows[0], rows[1:]) if rows else ([], [])
