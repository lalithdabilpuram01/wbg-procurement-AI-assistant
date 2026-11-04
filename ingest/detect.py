"""Intake checks: the real format from the bytes (never the extension), scanned PDFs, the document type from the
content, and the language."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import zipfile
from pathlib import Path

from .model import Quarantined

MAX_BYTES = 200 * 1024 * 1024
MAX_PAGES = 2_000
SCANNED_SHARE = 0.6                 # a PDF is "scanned" when at least this share of pages has (almost) no text layer
TEXT_CHARS = 100

TYPE_PATTERNS = [  # (doc type, cover-page pattern); first match wins, so the more specific patterns come first
    ("ICRR", re.compile(r"ICR\s+Review|Implementation\s+Completion\s+Report\s+Review|Independent\s+Evaluation\s+Group", re.I)),
    ("ICR", re.compile(r"Implementation\s+Completion\s+(and\s+Results\s+)?Report", re.I)),
    ("ISR", re.compile(r"Implementation\s+Status\s+(&|and)\s+Results", re.I)),
    ("PAD", re.compile(r"Project\s+Appraisal\s+Document", re.I)),
    ("PP", re.compile(r"Procurement\s+Plan|\bSTEP\b.*\bActivit", re.I | re.S)),
]
FRENCH = re.compile(r"\b(le|la|les|des|du|et|pour|dans|est|une|sur|projet|marchés?)\b", re.I)
ENGLISH = re.compile(r"\b(the|and|of|for|in|is|to|project|procurement|with)\b", re.I)


def sha256_of(path: Path) -> str:
    """SHA-256 of the file, read in 1 MB blocks."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sniff(path: Path) -> str:
    """pdf | docx | xlsx | image | json | csv; anything else is quarantined with the reason."""
    size = path.stat().st_size
    if size == 0:
        raise Quarantined("empty_file")
    if size > MAX_BYTES:
        raise Quarantined("too_large", f"{size // (1024 * 1024)} MB; the limit is {MAX_BYTES // (1024 * 1024)} MB")
    head = path.read_bytes()[:8]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return _office(path)
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*")):
        return "image"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        raise Quarantined("legacy_office_format", "save it as .docx or .xlsx and drop it again")
    return _text_format(path)


def _office(path: Path) -> str:
    """docx or xlsx, told apart by the parts inside the ZIP container."""
    try:
        names = set(zipfile.ZipFile(path).namelist())
    except zipfile.BadZipFile as error:
        raise Quarantined("corrupt_file", "the ZIP container cannot be read") from error
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    raise Quarantined("unsupported_format", "a ZIP file that is neither a Word document nor an Excel workbook")


def _text_format(path: Path) -> str:
    """json (including JSON Lines) or csv, judged from the decoded text; anything else is quarantined."""
    text = read_text(path)
    stripped = text.lstrip()
    if stripped[:1] in "[{":
        try:
            json.loads(stripped)
            return "json"
        except json.JSONDecodeError:
            if all(_is_json_object(line) for line in stripped.splitlines()[:20] if line.strip()):
                return "json"                                  # JSON Lines
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        rows = list(csv.reader(io.StringIO(text[:8192]), dialect))
        if len(rows) >= 2 and len(rows[0]) >= 2:
            return "csv"
    except csv.Error:
        pass
    raise Quarantined("unsupported_format", "not a PDF, Word, Excel, image, CSV or JSON file")


def _is_json_object(line: str) -> bool:
    """True when the line parses as a JSON object (used to recognise JSON Lines files)."""
    try:
        return isinstance(json.loads(line), dict)
    except json.JSONDecodeError:
        return False


def read_text(path: Path) -> str:
    """The file decoded with the first encoding that works: UTF-8 (with or without BOM), Windows-1252, then Latin-1."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise Quarantined("unreadable_text", "the text encoding is not supported")


def pdf_profile(path: Path) -> dict:
    """Page count, text-layer coverage and which pages look scanned; refuses encrypted or oversized PDFs."""
    import fitz  # type: ignore[import-untyped]
    try:
        doc = fitz.open(path)
    except Exception as error:                                          # PyMuPDF raises its own error types
        raise Quarantined("corrupt_file", "the PDF cannot be opened") from error
    with doc:
        if doc.needs_pass:
            raise Quarantined("encrypted_pdf", "remove the password and drop it again")
        if doc.page_count > MAX_PAGES:
            raise Quarantined("too_many_pages", f"{doc.page_count} pages; the limit is {MAX_PAGES}")
        chars = [len(page.get_text("text").strip()) for page in doc]
        first_pages = "\n".join(doc[i].get_text("text") for i in range(min(3, doc.page_count)))
    scanned = [n + 1 for n, c in enumerate(chars) if c < TEXT_CHARS]
    return {"pages": len(chars), "scanned_pages": scanned, "first_pages": first_pages,
            "fully_scanned": bool(chars) and len(scanned) / len(chars) >= SCANNED_SHARE}


def classify(text: str) -> str | None:
    """The document type the cover pages announce (None when nothing is recognisable)."""
    head = text[:6000]
    return next((doc_type for doc_type, pattern in TYPE_PATTERNS if pattern.search(head)), None)


def check_type(declared: str | None, detected: str | None) -> tuple[str, list[str]]:
    """The type to ingest as. A clear contradiction between the folder and the content is quarantined."""
    if declared is None:
        if detected is None:
            raise Quarantined("type_unknown", "move the file into the folder of its document type")
        return detected, []
    if detected is None:
        return declared, ["type_unconfirmed: the content does not name its document type"]
    # The icr/ folder holds IEG reviews too, so an ICRR found there is expected, not a mismatch.
    compatible = detected == declared or (declared == "ICR" and detected == "ICRR")
    if not compatible:
        raise Quarantined("type_mismatch", f"the content looks like {detected} but the file is in the {declared} folder")
    return detected, []


def language(text: str) -> str:
    fr, en = len(FRENCH.findall(text[:20000])), len(ENGLISH.findall(text[:20000]))
    return "fr" if fr > en * 1.2 else "en"


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None
