"""The automated run: every pending file through intake -> parse -> resolve -> graph -> embed -> load, one at a time.

A file that fails any stage is moved to `_quarantine/<folder>/` with a `<name>.reason.json` (the reason code, what to
do, the stage); nothing of it reaches the graph. A loaded file moves to `_processed/<folder>/<date>/`. Every outcome
is appended to `_ledger/ledger.jsonl`, and each run writes `_ledger/runs/<run_id>.json`. Re-dropping a file whose
exact bytes were already loaded is a no-op (`duplicate`).
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from . import PIPELINE_VERSION, detect
from .graph import Graph, build
from .lessons import cards_for
from .layout import Folder, Inbox
from .model import ParsedDocument, Quarantined
from .parsers import common, ocr, pdf, tabular, word
from .reference import Reference
from .reference import load as load_reference
from .resolve import Mention, resolve

Embedder = Callable[[list[str]], Sequence[Sequence[float]]]
RECORD_TYPES = {"contract": "contract", "notice": "notice", "PP": "plan_activity"}


class Sink(Protocol):
    def known(self, sha256: str) -> tuple[str, str] | None: ...          # (status, pipeline version) of these exact bytes
    def load(self, doc: ParsedDocument, graph: Graph, mentions: Sequence[Mention], embeddings, source: dict,
             lessons: Sequence = ()) -> dict[str, int]: ...
    def record_source(self, source: dict) -> None: ...
    def record_run(self, run_id: str, started: dt.datetime, summary: dict, pipeline: str) -> None: ...
    def reconcile(self) -> dict[str, int]: ...


@dataclass
class FileOutcome:
    file: str
    folder: str
    sha256: str
    status: str                         # processed | quarantined | duplicate
    document_id: str | None = None
    doc_type: str | None = None
    modality: str | None = None
    reason: str | None = None
    detail: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    rejected_rows: int = 0
    seconds: float = 0.0


class Pipeline:
    def __init__(self, inbox: Inbox, sink: Sink, embed: Embedder | None = None, reference: Reference | None = None,
                 clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc)):
        self.inbox, self.sink, self.embed, self.clock = inbox, sink, embed, clock
        self._reference = reference

    @property
    def reference(self) -> Reference:
        if self._reference is None:
            self._reference = load_reference()
        return self._reference

    def run(self) -> dict[str, Any]:
        """Process every pending file, then reconcile links across files; returns the run report."""
        self.inbox.create()
        run_id, started = f"run-{self.clock():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}", self.clock()
        outcomes = [self.ingest(folder, path, run_id) for folder, path in self.inbox.pending()]
        reconciled = self.sink.reconcile() if any(o.status == "processed" for o in outcomes) else {}
        summary = {"run_id": run_id, "pipeline": PIPELINE_VERSION, "files": len(outcomes),
                   **{s: sum(o.status == s for o in outcomes) for s in ("processed", "quarantined", "duplicate")},
                   "reconciled_edges": reconciled, "outcomes": [asdict(o) for o in outcomes]}
        self.sink.record_run(run_id, started, {k: v for k, v in summary.items() if k != "outcomes"}, PIPELINE_VERSION)
        runs = self.inbox.folder("_ledger") / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / f"{run_id}.json").write_text(json.dumps(summary, indent=1, default=str))
        return summary

    def ingest(self, folder: Folder, path: Path, run_id: str) -> FileOutcome:
        started = self.clock()
        sha = detect.sha256_of(path)
        outcome = FileOutcome(path.name, folder.name, sha, "processed")
        source = {"sha256": sha, "file_name": path.name, "folder": folder.name, "size_bytes": path.stat().st_size,
                  "pipeline": PIPELINE_VERSION, "run_id": run_id}
        try:
            if self.sink.known(sha) == ("processed", PIPELINE_VERSION):      # an older pipeline's result is redone
                outcome.status = "duplicate"
                self._move(path, folder, "_processed")
                return self._done(outcome, started)
            fmt = detect.sniff(path)
            source["format"] = fmt
            if fmt not in folder.formats:
                raise Quarantined("format_not_accepted", f"{fmt} files do not belong in {folder.name}/ (accepted: {', '.join(folder.formats)})")
            doc = self._parse(folder, path, sha, fmt)
            self._name_after_project(doc)
            res = resolve(doc, self.reference)
            lessons = cards_for(doc, self.reference)                       # proposed lesson cards (ICR, ICRR, PAD)
            graph = build(doc, res, self.reference, lessons)
            vectors = self.embed([c.text for c in doc.chunks]) if self.embed and doc.chunks else None
            outcome.counts = self.sink.load(doc, graph, res.mentions, vectors, source | {
                "modality": doc.modality, "warnings": doc.warnings, "rejected_rows": doc.rejected_rows}, lessons)
            outcome.document_id, outcome.doc_type, outcome.modality = doc.document_id, doc.doc_type, doc.modality
            outcome.warnings, outcome.rejected_rows = doc.warnings, len(doc.rejected_rows)
            self._move(path, folder, "_processed")
        except Quarantined as q:
            self._quarantine(outcome, path, folder, source, q.reason, q.detail)
        except Exception as error:                                       # an unexpected failure is quarantined, never lost
            self._quarantine(outcome, path, folder, source, "pipeline_error", f"{type(error).__name__}: {str(error)[:300]}")
        return self._done(outcome, started)

    # ---------------------------------------------------------------------------------------------- parse
    def _parse(self, folder: Folder, path: Path, sha: str, fmt: str) -> ParsedDocument:
        document_id = common.document_id_for(folder.name, path)
        declared = folder.doc_type if folder.kind == "document" else None
        if fmt in ("csv", "json", "xlsx"):
            return tabular.parse(path, sha, document_id, RECORD_TYPES.get(folder.doc_type or ""), fmt)
        if fmt == "docx":
            return word.parse(path, sha, document_id, declared, self.reference)
        if fmt == "image":
            return ocr.parse(path, sha, document_id, declared, self.reference, "image")
        profile = detect.pdf_profile(path)
        if profile["fully_scanned"]:
            return ocr.parse(path, sha, document_id, declared, self.reference, "pdf")
        head = profile["first_pages"]
        doc_type, warnings = detect.check_type(declared, detect.classify(head))
        project_id, match_class, _ = common.project_for(path.name, head, self.reference)
        if doc_type == "PP" and not project_id:
            raise Quarantined("project_unresolved", "a procurement plan needs its project: put the project id (P + 6 digits) in the file name")
        doc = pdf.parse(path, sha, document_id, doc_type, project_id, self.reference)
        doc.title, doc.document_date, doc.language = common.title_for(head, path.stem), common.document_date(head), detect.language(head)
        if project_id is None:                                       # not on the cover: the ids printed in the body
            project_id, match_class, _ = common.project_for("", " ".join(b.text for b in doc.blocks), self.reference)
            match_class = "body" if project_id else match_class
            doc.project_id = project_id
            doc.country_code = (self.reference.project(project_id) or {}).get("country_code")
        doc.warnings = warnings + doc.warnings + (["project_unresolved"] if not project_id else
                                                  ["project_matched_by_title"] if match_class == "P" else
                                                  ["project_found_in_body_text"] if match_class == "body" else [])
        return doc

    def _name_after_project(self, doc: ParsedDocument) -> None:
        """A cover line that does not name the project ("PROPOSED CREDIT") is replaced by the registered title."""
        project = self.reference.project(doc.project_id)
        if project and not doc.is_feed and not common.NAMES_PROJECT.search(doc.title or ""):
            doc.title = f"{project['title']} ({doc.doc_type})"

    # ---------------------------------------------------------------------------------------------- files
    def _quarantine(self, outcome: FileOutcome, path: Path, folder: Folder, source: dict, reason: str, detail: str) -> None:
        outcome.status, outcome.reason, outcome.detail = "quarantined", reason, detail
        target = self._move(path, folder, "_quarantine")
        target.with_name(target.name + ".reason.json").write_text(json.dumps(
            {"file": path.name, "folder": folder.name, "sha256": outcome.sha256, "reason": reason, "what_to_do": detail,
             "pipeline": PIPELINE_VERSION, "at": self.clock().isoformat()}, indent=1))
        try:
            self.sink.record_source(source | {"status": "quarantined", "reason": f"{reason}: {detail}"[:500]})
        except Exception:                                                 # lineage is best effort; the file is already safe
            outcome.warnings.append("lineage_not_recorded")

    def _move(self, path: Path, folder: Folder, area: str) -> Path:
        directory = self.inbox.folder(area) / folder.name
        if area == "_processed":
            directory = directory / f"{self.clock():%Y-%m-%d}"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / path.name
        if target.exists():
            target = directory / f"{path.stem}.{detect.sha256_of(path)[:8]}{path.suffix}"
        shutil.move(str(path), target)
        return target

    def _done(self, outcome: FileOutcome, started: dt.datetime) -> FileOutcome:
        outcome.seconds = round((self.clock() - started).total_seconds(), 2)
        ledger = self.inbox.folder("_ledger") / "ledger.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a") as fh:
            fh.write(json.dumps(asdict(outcome) | {"at": self.clock().isoformat()}, default=str) + "\n")
        return outcome


class MemorySink:
    """Keeps everything in memory: for `ingest run --dry-run` and the tests."""

    def __init__(self) -> None:
        self.documents: dict[str, tuple[ParsedDocument, Graph, list[Mention], Any]] = {}
        self.lessons: dict[str, list] = {}
        self.sources: dict[str, dict] = {}
        self.runs: list[dict] = []

    def known(self, sha256: str) -> tuple[str, str] | None:
        source = self.sources.get(sha256)
        return (source["status"], source["pipeline"]) if source else None

    def load(self, doc, graph, mentions, embeddings, source, lessons=()) -> dict[str, int]:
        self.documents[doc.document_id] = (doc, graph, list(mentions), embeddings)
        self.lessons[doc.document_id] = list(lessons)
        self.sources[source["sha256"]] = source | {"status": "processed", "document_id": doc.document_id}
        return {"sections": len(doc.sections), "chunks": len(doc.chunks), "nodes": len(graph.nodes), "edges": len(graph.edges),
                "lessons": len(lessons)}

    def record_source(self, source: dict) -> None:
        self.sources[source["sha256"]] = source

    def record_run(self, run_id, started, summary, pipeline) -> None:
        self.runs.append(summary)

    def reconcile(self) -> dict[str, int]:
        return {}
