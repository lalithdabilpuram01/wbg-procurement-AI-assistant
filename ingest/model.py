"""One document model for every modality: sections (a tree) -> blocks -> chunks; tables -> rows; feed records."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class Quarantined(Exception):
    """The file cannot be ingested as given; `reason` is a short code, `detail` says what to do."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


@dataclass
class Section:
    section_id: str
    parent_id: str | None
    level: int                       # 0 = front matter / document root level
    heading: str
    path: str                        # "3 > 3.2 Procurement arrangements"
    order: int
    kind: str                        # narrative | annex | front_matter | data_sheet | plan_table_* | ...
    topic: str | None = None         # lessons | risks | objective | procurement | results | financing
    page_start: int | None = None
    page_end: int | None = None


@dataclass
class Block:
    block_id: str
    section_id: str
    kind: str                        # paragraph | list_item | heading | table_text
    text: str                        # sanitised
    order: int
    page_start: int | None = None
    page_end: int | None = None


@dataclass
class TableBlock:
    table_id: str
    section_id: str | None
    page: int | None
    caption: str | None
    header: list[str]
    rows: list[list[str]]            # sanitised cells


@dataclass
class Chunk:
    chunk_id: str
    section_id: str
    text: str                        # sanitised; what is embedded and retrieved
    token_count: int
    order: int
    page_start: int | None = None
    page_end: int | None = None


@dataclass
class Record:
    """A structured row: a contract, a notice, or a procurement-plan activity."""

    record_type: str                 # contract | notice | plan_activity
    record_id: str
    project_id: str | None
    fields: dict[str, Any]
    source_row: int | None = None    # 1-based data row of a feed (None for PDF-extracted activities)


@dataclass
class Link:
    """A resolved relationship that the parser itself established (e.g. a plan activity matched to a contract)."""

    edge_type: str
    src: str                         # node ids, e.g. "activity:..." -> "contract:..."
    dst: str
    match_class: str                 # E | EN | P | structural
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    document_id: str
    doc_type: str                    # PAD | ISR | ICR | ICRR | PP | contract_feed | notice_feed
    modality: str                    # pdf_text | pdf_ocr | image_ocr | docx | tabular
    source_sha256: str
    source_name: str
    title: str | None = None
    project_id: str | None = None
    country_code: str | None = None
    document_date: str | None = None
    language: str | None = None
    page_count: int | None = None
    sections: list[Section] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    tables: list[TableBlock] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rejected_rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_feed(self) -> bool:
        return self.modality == "tabular" and self.doc_type in ("contract_feed", "notice_feed")


def document_from_dict(data: dict[str, Any]) -> ParsedDocument:
    """A ParsedDocument back from dataclasses.asdict() (e.g. JSON handed from one job task to the next)."""
    parts = {"sections": Section, "blocks": Block, "tables": TableBlock, "chunks": Chunk, "records": Record, "links": Link}
    return ParsedDocument(**{**data, **{k: [cls(**item) for item in data.get(k, [])] for k, cls in parts.items()}})
