"""The inbox: one folder per document type, and the formats each folder accepts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_INBOX = REPO / "ingestion_inbox"
MANAGED = ("_processed", "_quarantine", "_ledger")


@dataclass(frozen=True)
class Folder:
    """One inbox folder: the document or record type it holds and the formats it accepts."""

    name: str
    kind: str                       # document | records
    doc_type: str | None            # the corpus doc type (PAD, ISR, ICR, PP) or the record type (contract, notice)
    formats: tuple[str, ...]
    description: str


FOLDERS = {f.name: f for f in (
    Folder("pad", "document", "PAD", ("pdf", "docx", "image"), "Project Appraisal Documents"),
    Folder("isr", "document", "ISR", ("pdf", "docx", "image"), "Implementation Status and Results Reports"),
    Folder("icr", "document", "ICR", ("pdf", "docx", "image"),
           "Implementation Completion Reports and ICR Reviews (an IEG review is detected as ICRR)"),
    Folder("procurement_plans", "document", "PP", ("pdf", "docx", "xlsx", "csv", "json"),
           "STEP procurement plans: PDF exports, or spreadsheets with one row per activity"),
    Folder("contracts", "records", "contract", ("csv", "json", "xlsx"), "Contract award feeds: one row per contract"),
    Folder("notices", "records", "notice", ("csv", "json", "xlsx"), "Procurement notice feeds: one row per notice"),
    Folder("other", "auto", None, ("pdf", "docx", "image", "xlsx", "csv", "json"),
           "Anything else: the type is detected from the content"),
)}


@dataclass(frozen=True)
class Inbox:
    """The inbox root: one folder per type, plus the managed folders for processed, quarantined and ledger files."""

    root: Path = DEFAULT_INBOX

    def folder(self, name: str) -> Path:
        return self.root / name

    def create(self) -> list[Path]:
        """Create every folder (and its README) if missing; never touches files already there."""
        created = []
        for folder in FOLDERS.values():
            path = self.folder(folder.name)
            if not path.exists():
                path.mkdir(parents=True)
                created.append(path)
            readme = path / "README.md"
            if not readme.exists():
                readme.write_text(f"# {folder.name}/\n\n{folder.description}.\n\nAccepted formats: "
                                  f"{', '.join(folder.formats)}.\nDrop files here, then run `python -m ingest run` "
                                  "(or keep `python -m ingest watch` running).\n")
        for name in MANAGED:
            self.folder(name).mkdir(parents=True, exist_ok=True)
        return created

    def pending(self) -> list[tuple[Folder, Path]]:
        """Files waiting in the type folders (READMEs and hidden or partial files are ignored)."""
        files = []
        for folder in FOLDERS.values():
            path = self.folder(folder.name)
            if path.is_dir():
                files += [(folder, f) for f in sorted(path.iterdir())
                          if f.is_file() and f.name != "README.md" and not f.name.startswith((".", "~$"))
                          and not f.name.endswith((".part", ".crdownload", ".tmp"))]
        return files
