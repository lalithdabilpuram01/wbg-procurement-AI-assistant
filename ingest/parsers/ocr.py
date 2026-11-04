"""Scanned PDFs and images through Tesseract OCR: each page is rendered at 300 dpi, recognised in English and (when
installed) French, and the text is structured with the same heading rules as Word documents. Pages recognised with
low confidence are reported; nothing is silently guessed. Without the Tesseract engine the file is quarantined."""
from __future__ import annotations

from pathlib import Path

from rag.ingestion.sanitize import sanitize

from .. import detect, hierarchy
from ..model import ParsedDocument, Quarantined
from ..reference import Reference
from . import common

DPI = 300
LOW_CONFIDENCE = 60                  # mean word confidence (0-100) below which a page is flagged


def parse(path: Path, sha256: str, document_id: str, declared_type: str | None, ref: Reference, fmt: str) -> ParsedDocument:
    """OCR every page, flag pages with low confidence, and structure the text with the same rules as Word documents."""
    if not detect.ocr_available():
        raise Quarantined("needs_ocr", "this file is scanned; install Tesseract (brew install tesseract) and run again")
    import pytesseract
    # French is added only when its Tesseract language pack is installed.
    languages = "+".join(lang for lang in ("eng", "fra") if lang in pytesseract.get_languages(config=""))
    pages = list(_images(path, fmt))
    lines, low = [], []
    for number, image in enumerate(pages, start=1):
        data = pytesseract.image_to_data(image, lang=languages or "eng", output_type=pytesseract.Output.DICT)
        confidences = [float(c) for c in data["conf"] if float(c) >= 0]
        if confidences and sum(confidences) / len(confidences) < LOW_CONFIDENCE:
            low.append(number)
        # The empty line after each page closes the last paragraph, so no paragraph runs across pages.
        lines += [hierarchy.Line(sanitize(text)[0] if text else "", number) for text in _lines(data)] + [hierarchy.Line("", number)]
    text = "\n".join(line.text for line in lines)
    if not text.strip():
        raise Quarantined("no_text_recognised", "OCR found no text; check the scan quality")
    doc_type, warnings = detect.check_type(declared_type, detect.classify(text))
    project_id, _, _ = common.project_for(path.name, text, ref)
    sections, blocks = hierarchy.build(document_id, lines)
    parsed = ParsedDocument(document_id=document_id, doc_type=doc_type, modality="pdf_ocr" if fmt == "pdf" else "image_ocr",
                            source_sha256=sha256, source_name=path.name, title=common.title_for(text, path.stem),
                            project_id=project_id, country_code=(ref.project(project_id) or {}).get("country_code"),
                            document_date=common.document_date(text), language=detect.language(text), page_count=len(pages),
                            sections=sections, blocks=blocks, chunks=hierarchy.chunk(document_id, sections, blocks),
                            warnings=warnings + ([f"low_ocr_confidence_pages: {low[:20]}"] if low else []))
    return parsed


def _images(path: Path, fmt: str):
    """The pages as RGB images: each PDF page rendered at DPI, or every frame of an image file."""
    from PIL import Image, ImageSequence
    if fmt == "pdf":
        import fitz  # type: ignore[import-untyped]
        with fitz.open(path) as doc:
            for page in doc:
                pix = page.get_pixmap(dpi=DPI)
                yield Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return
    with Image.open(path) as image:                                     # a multi-page TIFF yields every frame
        for frame in ImageSequence.Iterator(image):
            yield frame.convert("RGB")


def _lines(data: dict) -> list[str]:
    """Words grouped back into their recognised lines, in reading order; "" marks a paragraph break."""
    grouped: dict[tuple[int, int, int], list[str]] = {}
    for i, word in enumerate(data["text"]):
        if word.strip():
            grouped.setdefault((data["block_num"][i], data["par_num"][i], data["line_num"][i]), []).append(word)
    lines, paragraph = [], None
    for (block, par, _), words in sorted(grouped.items()):
        if paragraph is not None and (block, par) != paragraph:
            lines.append("")
        lines.append(" ".join(words))
        paragraph = (block, par)
    return lines
