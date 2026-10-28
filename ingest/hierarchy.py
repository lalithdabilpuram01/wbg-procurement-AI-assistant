"""Document hierarchy and chunking for the modalities without a dedicated extractor (Word, OCR text).

Headings come from Word heading styles, or, for OCR text, from numbering ("3.2 Procurement", "IV.", "Annex 2") and
short all-capitals lines. Sections form a tree by heading level; every block belongs to exactly one section. Chunks
never cross a section boundary and start with the section path, so a retrieved chunk always says where it sits.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rag.ingestion.chunking import count_tokens

from .model import Block, Chunk, Section

CHUNK_TARGET, CHUNK_MAX, CHUNK_MIN = 500, 650, 80       # tokens, as the validated PDF strategy
TOPICS = [  # (topic, heading pattern): used for every modality, including the PDF sections
    ("lessons", re.compile(r"\blessons?\b", re.I)),
    ("risks", re.compile(r"\brisks?\b|\brisques?\b", re.I)),
    ("objective", re.compile(r"development objective|\bPDO\b|objectifs?\s+de\s+d[ée]veloppement", re.I)),
    ("procurement", re.compile(r"procurement|passation\s+des\s+march", re.I)),
    ("results", re.compile(r"\bresults?\b|\bindicators?\b|\boutcomes?\b|r[ée]sultats", re.I)),
    ("financing", re.compile(r"financing|project\s+costs?|financement", re.I)),
    ("implementation", re.compile(r"implementation\s+(arrangements|status|progress)|mise\s+en\s+[œo]euvre", re.I)),
]
NUMBERED = re.compile(r"^((?:\d{1,2}\.)*\d{1,2}\.?|[IVXL]{1,5}\.|[A-H]\.|(?:ANNEX|Annex)\s+\d{1,2}[:.]?)\s+(\S.*)$")
SENTENCE = re.compile(r"(?<=[.!?;])\s+")


@dataclass
class Line:
    text: str
    page: int | None = None
    level: int | None = None        # set when the source marks a heading (Word styles); else inferred
    kind: str = "paragraph"         # paragraph | list_item | table_text


def topic_of(heading: str) -> str | None:
    return next((topic for topic, pattern in TOPICS if pattern.search(heading)), None)


def heading_level(text: str) -> int | None:
    """The heading level a plain-text line announces, or None for body text."""
    line = text.strip()
    if not 2 < len(line) <= 90 or line.endswith((",", ";")):
        return None
    match = NUMBERED.match(line)
    if match:
        number, words = match.group(1), match.group(2)
        if len(words.split()) > 12 or not words[:1].isupper() or (line.endswith(".") and len(words.split()) > 6):
            return None                                                  # a numbered paragraph, not a heading
        if number.lower().startswith("annex") or re.fullmatch(r"[IVXL]+\.", number):
            return 1
        if re.fullmatch(r"[A-H]\.", number):
            return 2
        return min(len([p for p in number.rstrip(".").split(".") if p]), 4)
    letters = [c for c in line if c.isalpha()]
    if len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.85 and len(line.split()) <= 10:
        return 1
    return None


def build(document_id: str, lines: list[Line], doc_kind: str = "narrative") -> tuple[list[Section], list[Block]]:
    """Sections (a tree) and blocks from ordered lines. Consecutive body lines of one page form one paragraph."""
    root = Section(f"{document_id}:s000", None, 0, "front_matter", "front_matter", 0, "front_matter")
    sections, blocks, stack = [root], [], [root]
    paragraph: list[Line] = []

    def flush() -> None:
        if paragraph:
            text = " ".join(line.text.strip() for line in paragraph if line.text.strip())
            if text:
                pages = [line.page for line in paragraph if line.page is not None]
                blocks.append(Block(f"{document_id}:b{len(blocks):05d}", stack[-1].section_id, paragraph[0].kind, text,
                                    len(blocks), min(pages) if pages else None, max(pages) if pages else None))
            paragraph.clear()

    for line in lines:
        level = line.level if line.level is not None else heading_level(line.text)
        if level is None:
            standalone = line.kind in ("list_item", "table_text")             # one block each, never merged
            if standalone or not line.text.strip():
                flush()
            if line.text.strip():
                paragraph.append(line)
                if standalone:
                    flush()
            continue
        flush()
        while len(stack) > 1 and stack[-1].level >= level:
            stack.pop()
        parent = stack[-1] if stack[-1].level < level else None
        heading = line.text.strip()
        path = f"{parent.path} > {heading}" if parent and parent.level > 0 else heading
        kind = "annex" if heading.lower().startswith("annex") or (parent and parent.kind == "annex") else doc_kind
        section = Section(f"{document_id}:s{len(sections):03d}", parent.section_id if parent and parent.level > 0 else None,
                          level, heading, path, len(sections), kind, topic_of(heading) or (parent.topic if parent else None),
                          line.page, line.page)
        sections.append(section)
        blocks.append(Block(f"{document_id}:b{len(blocks):05d}", section.section_id, "heading", heading, len(blocks), line.page, line.page))
        stack.append(section)
    flush()
    _page_ranges(sections, blocks)
    return [s for s in sections if s is not root or any(b.section_id == root.section_id for b in blocks)], blocks


def _page_ranges(sections: list[Section], blocks: list[Block]) -> None:
    by_section: dict[str, list[int]] = {}
    for block in blocks:
        for page in (block.page_start, block.page_end):
            if page is not None:
                by_section.setdefault(block.section_id, []).append(page)
    for section in sections:
        pages = by_section.get(section.section_id)
        if pages:
            section.page_start, section.page_end = min(pages), max(pages)


def chunk(document_id: str, sections: list[Section], blocks: list[Block]) -> list[Chunk]:
    """Pack each section's body blocks into chunks of about CHUNK_TARGET tokens, prefixed with the section path."""
    chunks: list[Chunk] = []
    for section in sections:
        pieces = [piece for b in blocks if b.section_id == section.section_id and b.kind != "heading" for piece in _pieces(b)]
        header = f"[{section.path}]\n"
        current: list[tuple[str, Block]] = []
        size = count_tokens(header)
        for text, block in pieces:
            tokens = count_tokens(text)
            if current and size + tokens > CHUNK_TARGET and size >= CHUNK_MIN:
                chunks.append(_chunk(document_id, section, header, current, len(chunks)))
                current, size = [], count_tokens(header)
            current.append((text, block))
            size += tokens
        if current:
            chunks.append(_chunk(document_id, section, header, current, len(chunks)))
    return chunks


def _pieces(block: Block) -> list[tuple[str, Block]]:
    """A block, split at sentence boundaries when it alone exceeds the chunk maximum."""
    if count_tokens(block.text) <= CHUNK_MAX:
        return [(block.text, block)]
    pieces, current = [], ""
    for sentence in SENTENCE.split(block.text):
        if current and count_tokens(current + " " + sentence) > CHUNK_TARGET:
            pieces.append((current, block))
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    return pieces + ([(current, block)] if current else [])


def _chunk(document_id: str, section: Section, header: str, parts: list[tuple[str, Block]], order: int) -> Chunk:
    text = header + "\n".join(t for t, _ in parts)
    pages = [p for _, b in parts for p in (b.page_start, b.page_end) if p is not None]
    return Chunk(f"{document_id}:k{order:05d}", section.section_id, text, count_tokens(text), order,
                 min(pages) if pages else None, max(pages) if pages else None)
