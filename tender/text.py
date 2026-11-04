"""Plain text of an uploaded draft (PDF, Word or text), for the user to review before the check runs."""
from __future__ import annotations

import io

MAX_BYTES = 5_000_000
MAX_PAGES = 80
MAX_CHARS = 20_000


class UnreadableDraft(ValueError):
    """The file is not a readable PDF, Word or text document (the message is safe to show)."""


def text_from_file(filename: str, content: bytes) -> str:
    """Plain text of the upload, cut to MAX_CHARS; raises UnreadableDraft with a message safe to show.
    A PDF is recognised by its first bytes; Word needs the zip signature and a .docx name; text files go by name."""
    if len(content) > MAX_BYTES:
        raise UnreadableDraft("The file is larger than 5 MB.")
    name = filename.lower()
    if content.startswith(b"%PDF"):
        text = _pdf(content)
    elif content.startswith(b"PK") and name.endswith(".docx"):
        text = _docx(content)
    elif name.endswith((".txt", ".md")):
        text = content.decode("utf-8", errors="replace")
    else:
        raise UnreadableDraft("Upload a PDF, a Word (.docx) or a text file.")
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not text:
        raise UnreadableDraft("No text was found in the file (a scanned PDF needs OCR first).")
    return text[:MAX_CHARS]


def _pdf(content: bytes) -> str:
    """Text of the first MAX_PAGES pages."""
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages[:MAX_PAGES])
    except Exception as error:                      # a damaged PDF: say so, never crash the request
        raise UnreadableDraft("The PDF could not be read.") from error


def _docx(content: bytes) -> str:
    """Paragraph text, then table cell text; a repeated cell text (merged cells repeat) is kept once."""
    import docx
    try:
        document = docx.Document(io.BytesIO(content))
    except Exception as error:
        raise UnreadableDraft("The Word file could not be read.") from error
    cells = (cell.text for table in document.tables for row in table.rows for cell in row.cells)
    return "\n".join([p.text for p in document.paragraphs] + list(dict.fromkeys(cells)))
