"""The description (card) of a table: what a question is matched against, and what the registry keeps.

    [PAD 24440102 · Ethiopia Expressway Development Support Project (P148850, ET) · III. Project Description >
     B. Project Financing · page 22]
    Columns: Project Components (text); Project cost (number); IDA Financing (number); % Financing (number)
    Row labels: 1. Construction of high capacity highway; 2. Institutional development and regulatory framework; ...
    Groups: WORKS; GOODS
    Rows: 6
"""
from __future__ import annotations

from dataclasses import dataclass

from .assemble import LogicalTable

MAX_LABELS = 25
MAX_LABEL_CHARS = 80


@dataclass(frozen=True)
class DocumentInfo:
    """The document facts printed in a table card's first line."""
    document_id: str
    doc_type: str
    project_id: str
    project_title: str
    country_code: str


def card_text(table: LogicalTable, doc: DocumentInfo) -> str:
    """Build the card text of a table: source line, caption, typed columns, row labels, groups and row count."""
    pages = f"page {table.pages[0]}" if len(table.pages) == 1 else f"pages {table.pages[0]}-{table.pages[-1]}"
    lines = [f"[{doc.doc_type} {doc.document_id} · {doc.project_title} ({doc.project_id}, {doc.country_code}) · "
             f"{table.section or 'no section'} · {pages}]"]
    if table.caption:
        lines.append(f"Caption: {table.caption}")
    lines.append("Columns: " + "; ".join(f"{c.header} ({c.type})" for c in table.columns))
    labels = list(dict.fromkeys(r.cells[0][:MAX_LABEL_CHARS] for r in table.rows if r.cells and r.cells[0]))
    if labels:
        more = f"; ... ({len(labels) - MAX_LABELS} more)" if len(labels) > MAX_LABELS else ""
        lines.append("Row labels: " + "; ".join(labels[:MAX_LABELS]) + more)
    groups = list(dict.fromkeys(r.group for r in table.rows if r.group))
    if groups:
        lines.append("Groups: " + "; ".join(g[:MAX_LABEL_CHARS] for g in groups[:10]))
    lines.append(f"Rows: {len(table.rows)}")
    return "\n".join(lines)
