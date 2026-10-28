"""The Knowledge Graph topology and the builder that turns one parsed, resolved document into its subgraph.

Node ids are `<type prefix>:<natural key>` (project:P148850, contract:1674058, section:34455066:s012), so the same
real-world entity arriving from different files becomes ONE node: that is how records with disjointed keys meet.
Document-owned nodes (the document, its sections, chunks, tables and plan activities) are replaced when the file is
re-ingested; shared entity nodes (projects, contracts, countries, methods...) are merged.

    Project ─IN_COUNTRY→ Country      Project ─IN_SECTOR→ Sector       Project ─HAS_DOCUMENT→ Document
    Document ─HAS_SECTION→ Section ─PARENT_OF→ Section ─HAS_CHUNK→ Chunk ─NEXT→ Chunk     Section ─HAS_TABLE→ Table
    Section ─ABOUT_TOPIC→ Topic (lessons, risks, objective, procurement, results, financing, implementation)
    Chunk ─MENTIONS→ Project | Contract | Country | ProcurementMethod | Loan
    Document ─LISTS_ACTIVITY→ PlanActivity ─PLANNED_AS→ Contract ─AWARDED_UNDER→ Project
    PlanActivity ─ACTIVITY_OF→ Project     Contract ─AWARDED_TO→ Supplier     Contract|PlanActivity|Notice ─USES_METHOD→ Method
    Notice ─NOTICE_FOR→ Project     Notice ─NOTICE_OF→ PlanActivity     Contract ─TEMPORALLY_NEAR→ Document (ISR/ICR)
    Section ─HAS_LESSON→ Lesson ─LESSON_FOR→ Project     Lesson ─ABOUT_TOPIC→ Topic     Lesson ─FOR_STAGE→ LifecycleStage
    (a lesson's review decision lives in lesson_card and survives re-ingestion; its node is replaced with the document)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lessons.cards import LessonCard

from .model import Link, ParsedDocument, Record
from .reference import Reference, slug
from .resolve import Resolution, method_name

NODE_TYPES = {"project": "Project", "country": "Country", "sector": "Sector", "document": "Document", "section": "Section",
              "chunk": "Chunk", "table": "Table", "activity": "PlanActivity", "contract": "Contract", "notice": "Notice",
              "supplier": "Supplier", "method": "ProcurementMethod", "topic": "Topic", "loan": "Loan", "lesson": "Lesson",
              "stage": "LifecycleStage"}
OWNED = {"document", "section", "chunk", "table", "activity", "lesson"}      # replaced with their document
EDGE_TYPES = {  # edge type -> (allowed source prefixes, allowed target prefixes, storage partition)
    "IN_COUNTRY": ({"project"}, {"country"}, "context"),
    "IN_SECTOR": ({"project"}, {"sector"}, "context"),
    "HAS_DOCUMENT": ({"project"}, {"document"}, "hierarchy"),
    "HAS_SECTION": ({"document"}, {"section"}, "hierarchy"),
    "PARENT_OF": ({"section"}, {"section"}, "hierarchy"),
    "HAS_CHUNK": ({"section"}, {"chunk"}, "hierarchy"),
    "NEXT": ({"chunk"}, {"chunk"}, "hierarchy"),
    "HAS_TABLE": ({"section", "document"}, {"table"}, "hierarchy"),
    "ABOUT_TOPIC": ({"section", "lesson"}, {"topic"}, "semantic"),
    "HAS_LESSON": ({"section"}, {"lesson"}, "hierarchy"),
    "LESSON_FOR": ({"lesson"}, {"project"}, "semantic"),
    "FOR_STAGE": ({"lesson"}, {"stage"}, "semantic"),
    "MENTIONS": ({"chunk"}, {"project", "contract", "country", "method", "loan"}, "semantic"),
    "LISTS_ACTIVITY": ({"document"}, {"activity"}, "procurement"),
    "ACTIVITY_OF": ({"activity"}, {"project"}, "procurement"),
    "PLANNED_AS": ({"activity"}, {"contract"}, "procurement"),
    "AWARDED_UNDER": ({"contract"}, {"project"}, "procurement"),
    "AWARDED_TO": ({"contract"}, {"supplier"}, "procurement"),
    "USES_METHOD": ({"contract", "activity", "notice"}, {"method"}, "procurement"),
    "NOTICE_FOR": ({"notice"}, {"project"}, "procurement"),
    "NOTICE_OF": ({"notice"}, {"activity"}, "procurement"),
    "TEMPORALLY_NEAR": ({"contract"}, {"document"}, "context"),
}


class GraphError(ValueError):
    """An edge breaks the topology (wrong endpoint types or an unknown edge type)."""


@dataclass
class Node:
    node_id: str
    label: str
    props: dict[str, Any] = field(default_factory=dict)

    @property
    def prefix(self) -> str:
        return self.node_id.split(":", 1)[0]

    @property
    def node_type(self) -> str:
        return NODE_TYPES[self.prefix]

    @property
    def owned(self) -> bool:
        return self.prefix in OWNED


@dataclass
class Edge:
    edge_type: str
    src: str
    dst: str
    match_class: str = "structural"
    confidence: float = 1.0
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[tuple[str, str, str], Edge] = field(default_factory=dict)

    def node(self, node_id: str, label: str, **props: Any) -> str:
        existing = self.nodes.get(node_id)
        if existing:
            existing.props.update({k: v for k, v in props.items() if v is not None})
        else:
            self.nodes[node_id] = Node(node_id, label, {k: v for k, v in props.items() if v is not None})
        return node_id

    def edge(self, edge_type: str, src: str, dst: str, match_class: str = "structural", confidence: float = 1.0, **evidence: Any) -> None:
        key = (edge_type, src, dst)
        current = self.edges.get(key)
        if current is None or confidence > current.confidence:          # the strongest evidence for a pair is kept
            self.edges[key] = Edge(edge_type, src, dst, match_class, confidence, evidence)

    def validate(self) -> None:
        for e in self.edges.values():
            if e.edge_type not in EDGE_TYPES:
                raise GraphError(f"unknown edge type {e.edge_type}")
            sources, targets, _ = EDGE_TYPES[e.edge_type]
            if e.src.split(":", 1)[0] not in sources or e.dst.split(":", 1)[0] not in targets:
                raise GraphError(f"{e.edge_type} cannot join {e.src} -> {e.dst}")
            for end in (e.src, e.dst):
                if end not in self.nodes:
                    raise GraphError(f"{e.edge_type} refers to a missing node {end}")


def build(doc: ParsedDocument, res: Resolution, ref: Reference, lessons: list[LessonCard] = ()) -> Graph:
    g = Graph()
    project = _project(g, doc.project_id, ref)
    if not doc.is_feed:
        _document(g, doc, project)
    for card in lessons:
        _lesson(g, card, project)
    for record in doc.records:
        _record(g, doc, record, ref)
    for number, contract in res.contracts.items():
        _contract(g, f"contract:{number}", contract, ref)
    for link in doc.links + res.links:
        _link(g, link)
    for m in res.mentions:
        if m.node_id:
            _mentioned(g, m, ref)
            g.edge("MENTIONS", f"chunk:{m.chunk_id}", m.node_id, m.match_class, m.confidence, entity_type=m.entity_type,
                   span=[m.start, m.end], text=m.value)
    g.validate()
    return g


# --------------------------------------------------------------------------------------------------- pieces
def _project(g: Graph, project_id: str | None, ref: Reference) -> str | None:
    if not project_id:
        return None
    info = ref.project(project_id) or {}
    node = g.node(f"project:{project_id}", info.get("title") or project_id, registered=bool(info), approval_fy=info.get("approval_fy"),
                  region=info.get("region"))
    if info.get("country_code"):
        g.edge("IN_COUNTRY", node, g.node(f"country:{info['country_code']}", info.get("country_name") or info["country_code"]))
    if info.get("sector"):
        g.edge("IN_SECTOR", node, g.node(f"sector:{slug(info['sector'])}", info["sector"]))
    return node


def _document(g: Graph, doc: ParsedDocument, project: str | None) -> None:
    document = g.node(f"document:{doc.document_id}", doc.title or doc.source_name, doc_type=doc.doc_type, modality=doc.modality,
                      document_date=doc.document_date, language=doc.language, page_count=doc.page_count)
    if project:
        g.edge("HAS_DOCUMENT", project, document)
    for s in doc.sections:
        section = g.node(f"section:{s.section_id}", s.heading, level=s.level, path=s.path, kind=s.kind, topic=s.topic,
                         page_start=s.page_start, page_end=s.page_end)
        if s.parent_id:
            g.edge("PARENT_OF", f"section:{s.parent_id}", section)
        else:
            g.edge("HAS_SECTION", document, section)
        if s.topic:
            g.edge("ABOUT_TOPIC", section, g.node(f"topic:{s.topic}", s.topic))
    previous = None
    for c in sorted(doc.chunks, key=lambda c: c.order):
        chunk = g.node(f"chunk:{c.chunk_id}", f"chunk {c.order}", page_start=c.page_start, page_end=c.page_end, tokens=c.token_count)
        g.edge("HAS_CHUNK", f"section:{c.section_id}", chunk)
        if previous:
            g.edge("NEXT", previous, chunk)
        previous = chunk
    known_sections = {s.section_id for s in doc.sections}
    for t in doc.tables:
        table = g.node(f"table:{t.table_id}", t.caption or f"table {t.table_id}", page=t.page, columns=t.header[:20], rows=len(t.rows))
        g.edge("HAS_TABLE", f"section:{t.section_id}" if t.section_id in known_sections else document, table)


def _lesson(g: Graph, card: LessonCard, project: str | None) -> None:
    lesson = g.node(f"lesson:{card.card_id}", card.headline[:200], doc_type=card.doc_type, page_start=card.page_start,
                    page_end=card.page_end, categories=list(card.categories))
    if card.section_id and f"section:{card.section_id}" in g.nodes:
        g.edge("HAS_LESSON", f"section:{card.section_id}", lesson)
    if project:
        g.edge("LESSON_FOR", lesson, project)
    for topic in card.topics:
        g.edge("ABOUT_TOPIC", lesson, g.node(f"topic:{topic}", topic))
    for stage in card.stages:
        g.edge("FOR_STAGE", lesson, g.node(f"stage:{stage}", stage.replace("_", " ")))


def _record(g: Graph, doc: ParsedDocument, r: Record, ref: Reference) -> None:
    f = r.fields
    project = _project(g, r.project_id, ref)
    method = g.node(f"method:{slug(method_name(f['method']))}", method_name(f["method"])) if f.get("method") else None
    if r.record_type == "plan_activity":
        activity = g.node(f"activity:{r.record_id}", f.get("reference") or r.record_id, reference=f.get("reference"),
                          description=f.get("description"), category=f.get("category"), status=f.get("status"),
                          estimated_amount_usd=f.get("estimated_amount_usd"), planned_signing_date=f.get("planned_signing_date"))
        if not doc.is_feed:
            g.edge("LISTS_ACTIVITY", f"document:{doc.document_id}", activity)
        if project:
            g.edge("ACTIVITY_OF", activity, project)
        if method:
            g.edge("USES_METHOD", activity, method)
    elif r.record_type == "contract":
        contract = _contract(g, f"contract:{r.record_id}", f, ref, project)
        if f.get("supplier_name"):
            supplier = g.node(f"supplier:{slug(f['supplier_name'] + ' ' + (f.get('supplier_country') or ''))}", f["supplier_name"],
                              country=f.get("supplier_country"))
            g.edge("AWARDED_TO", contract, supplier, "EN", 0.95, rule="supplier name + country, normalised")
        if method:
            g.edge("USES_METHOD", contract, method)
    elif r.record_type == "notice":
        notice = g.node(f"notice:{r.record_id}", f.get("description") or r.record_id, notice_type=f.get("notice_type"),
                        reference=f.get("reference"), publication_date=f.get("publication_date"), deadline_date=f.get("deadline_date"))
        if project:
            g.edge("NOTICE_FOR", notice, project)
        if method:
            g.edge("USES_METHOD", notice, method)


def _contract(g: Graph, node_id: str, c: dict, ref: Reference, project: str | None = None) -> str:
    amount = c.get("amount_usd", c.get("contract_amount_usd"))
    signed = c.get("signing_date", c.get("contract_signing_date"))
    reference = c.get("reference", c.get("borrower_contract_reference"))
    node = g.node(node_id, reference or node_id.split(":", 1)[1], reference=reference, amount_usd=amount, signing_date=signed,
                  category=c.get("category", c.get("procurement_category")), review_type=c.get("review_type"))
    project = project or _project(g, c.get("project_id"), ref)
    if project:
        g.edge("AWARDED_UNDER", node, project)
    method = c.get("method", c.get("procurement_method"))
    if method:
        g.edge("USES_METHOD", node, g.node(f"method:{slug(method_name(method))}", method_name(method)))
    return node


def _link(g: Graph, link: Link) -> None:
    if link.src in g.nodes and link.dst in g.nodes:
        g.edge(link.edge_type, link.src, link.dst, link.match_class, link.confidence, **link.evidence)


def _mentioned(g: Graph, m, ref: Reference) -> None:
    """Make sure a mentioned entity exists as a node (a known project gets its full context)."""
    prefix, key = m.node_id.split(":", 1)
    if prefix == "project":
        _project(g, key, ref)
    elif m.node_id not in g.nodes:
        g.node(m.node_id, m.value)
