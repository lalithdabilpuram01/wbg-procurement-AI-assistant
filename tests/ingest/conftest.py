"""Offline fixtures: no external network (local PostgreSQL stays allowed), and small generated input files."""
from __future__ import annotations

from pathlib import Path

import docx
import pytest

from tests.conversation.conftest import offline  # noqa: F401  (autouse: blocks external connections)
from ingest.layout import Inbox

PROJECT = "P148850"


@pytest.fixture
def inbox(tmp_path: Path) -> Inbox:
    box = Inbox(tmp_path / "inbox")
    box.create()
    return box


def write_pad_docx(path: Path, project: str = PROJECT, title: str = "PROJECT APPRAISAL DOCUMENT") -> Path:
    d = docx.Document()
    d.add_heading(title, 0)
    d.add_paragraph(f"Ethiopia Expressway Development Support Project ({project}), May 12, 2015")
    d.add_heading("1. Strategic Context", 1)
    d.add_paragraph("Ethiopia needs a reliable road network to connect markets. " * 25)
    d.add_heading("1.1 Procurement arrangements", 2)
    d.add_paragraph("Works will be procured through Request for Bids under activity ET-ERA-123456-CW-RFB, financed by IDA-5678.")
    d.add_paragraph("Mobilise the contractor early", style="List Bullet")
    d.add_heading("2. Key Risks", 1)
    d.add_paragraph("Contractor capacity is the main risk. " * 10)
    table = d.add_table(rows=2, cols=2)
    for (r, c), text in {(0, 0): "Risk", (0, 1): "Rating", (1, 0): "Fiduciary", (1, 1): "High"}.items():
        table.cell(r, c).text = text
    d.add_heading("Lessons Learned", 1)
    d.add_paragraph("Early design reviews reduced variation orders on similar road works.")
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(path)
    return path


CONTRACTS_CSV = ("Project ID,WB Contract No.,Contract Reference,Contract Amount (USD),Signing Date,Procurement Method,Supplier,"
                 "Supplier Country,Category\n"
                 "P132833,1674058,LK-RDA-257577-GO-RFB,\"197,801.29\",19/04/2021,Request for Bids,ACME Ltd,Sri Lanka,Goods\n"
                 "P132833,9999999,LK-RDA-1-GO-RFB,1.2m,2021-05-01,RFQ,Jane Smith,Sri Lanka,Individual Consultant\n"
                 "BADID,1,,abc,notadate,,,,\n")
