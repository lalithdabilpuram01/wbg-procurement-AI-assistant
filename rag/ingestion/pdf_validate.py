"""PDF validation, checksums and document identification."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import fitz  # type: ignore[import-untyped]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def validate(path: Path, expected_sha256: str | None = None) -> dict:
    """File-level checks. Never raises for a bad PDF: problems are returned in 'errors'."""
    errors: list[str] = []
    out: dict[str, Any] = {"path": str(path), "exists": path.exists(), "errors": errors}
    if not path.exists():
        errors.append("missing file")
        return out
    data_head = path.read_bytes()[:5]
    out["sha256"] = sha256_file(path)
    out["size_bytes"] = path.stat().st_size
    out["is_pdf_header"] = data_head.startswith(b"%PDF")
    if not out["is_pdf_header"]:
        errors.append("no %PDF header")
    if expected_sha256 and expected_sha256 != out["sha256"]:
        errors.append("sha256 differs from corpus manifest")
    try:
        with fitz.open(path) as doc:
            out["page_count"] = doc.page_count
            out["encrypted"] = doc.is_encrypted
            meta = doc.metadata or {}
            out["pdf_producer"] = meta.get("producer") or None
            out["pdf_creator"] = meta.get("creator") or None
            out["pdf_creation_date"] = _pdf_date(meta.get("creationDate"))
            out["pdf_outline_entries"] = len(doc.get_toc())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"unreadable: {type(exc).__name__}")
    return out


def _pdf_date(v: str | None) -> str | None:
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", v or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def project_ids_in_text(text: str) -> list[str]:
    """Project IDs printed in the document ('P132833', also '( P148850)')."""
    return sorted({f"P{x}" for x in re.findall(r"\bP\s?(\d{6})\b", text or "")})
