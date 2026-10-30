"""Unity Catalog managed Volume POC: store ONE approved, public World Bank PDF and verify it, without changing anything else.

Commands (run from procurement-intelligence/, with the Lakebase POC's uv environment, standard library only):
    uv run --project infrastructure/lakebase_poc --locked python infrastructure/uc_volume_poc/uc_volume_pdf_poc.py check
    UC_VOLUME_POC_LIVE=1 uv run --project infrastructure/lakebase_poc --locked python infrastructure/uc_volume_poc/uc_volume_pdf_poc.py run

`check` is offline: approved project and document, corpus checksum and size, zero privacy masking events.
`run` (explicit opt-in) creates the managed Volume if it does not exist, uploads the PDF if it is not there yet (never
overwrites), and verifies size and SHA-256 by listing, by downloading the stored copy, and by reading it on the
workspace's serverless SQL warehouse. No OCR, extraction or model inference.
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
POC_DIR = Path(__file__).resolve().parent
SCOPE = REPO / "rag" / "module1_discovery" / "rag_scope_v3_35_projects"
CORPUS_MANIFEST = REPO / "rag" / "corpus_v3" / "CORPUS_MANIFEST_FIRST10.csv"
MASK_EVENTS = REPO / "rag" / "corpus_v3" / "processed" / "pilot_v2" / "restricted" / "mask_events.jsonl"

CATALOG, SCHEMA, VOLUME = "workspace", "default", "wbg_rag_pdfs_poc"
VOLUME_FULL_NAME = f"{CATALOG}.{SCHEMA}.{VOLUME}"
VOLUME_ROOT = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"
VOLUME_COMMENT = ("POC 2026-10-26: approved public World Bank RAG PDFs (35-project scope, D-32). "
                  "One verification file only; metadata kept outside the Volume.")
DOCUMENT_ID = "40089196"                     # P148850 ISR, 0 privacy masking events in the pilot
PROFILE = os.environ.get("DATABRICKS_CONFIG_PROFILE", "wbg-databricks-free")
LIVE_OPT_IN = "UC_VOLUME_POC_LIVE"
CLI_TIMEOUT_SECONDS = 120


class PocError(RuntimeError):
    """A check failed; the message names the check, never credentials."""


@dataclass(frozen=True)
class DocumentRecord:
    project_id: str
    country_code: str
    document_id: str
    doc_type: str
    title: str
    document_date: str
    disclosure_date: str
    source_url: str
    local_path: str
    size_bytes: int
    sha256: str
    privacy_mask_events: int

    @property
    def filename(self) -> str:
        return Path(self.local_path).name

    @property
    def volume_path(self) -> str:
        """Same <country>/<project>/<original filename> layout as rag/corpus_v3/pdf/."""
        return f"{VOLUME_ROOT}/{self.country_code}/{self.project_id}/{self.filename}"


# ------------------------------------------------------------------------------------------------- offline checks
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def mask_event_count(document_id: str) -> int:
    """Number of personal-data masking events recorded for the document (counts only; content is never read out)."""
    with MASK_EVENTS.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if str(json.loads(line).get("document_id")) == document_id)


def select_document(document_id: str = DOCUMENT_ID) -> DocumentRecord:
    approved = {row["project_id"] for row in read_csv(SCOPE / "PROJECT_SELECTION_35.csv")}
    manifest = {row["document_id"]: row for row in read_csv(SCOPE / "DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv")}
    corpus = {row["document_id"]: row for row in read_csv(CORPUS_MANIFEST)}
    if document_id not in manifest or document_id not in corpus:
        raise PocError("document is not in the approved manifest and the local corpus manifest")
    row, planned = corpus[document_id], manifest[document_id]
    if row["project_id"] not in approved or planned["project_id"] != row["project_id"]:
        raise PocError("document does not belong to an approved project")
    if row["download_status"] != "downloaded" or planned["expected_local_path"] != row["local_path"]:
        raise PocError("document is not downloaded at its approved local path")
    local = REPO / row["local_path"]
    if not local.is_file() or local.is_symlink() or local.suffix.lower() != ".pdf":
        raise PocError("local PDF missing or not a regular .pdf file")
    if local.stat().st_size != int(row["size_bytes"]) or sha256_file(local) != row["sha256"]:
        raise PocError("local PDF does not match its recorded size and SHA-256")
    if not local.read_bytes()[:5] == b"%PDF-":
        raise PocError("local file is not a PDF")
    events = mask_event_count(document_id)
    if events:
        raise PocError("document has personal-data masking events; choose a document without any")
    return DocumentRecord(project_id=row["project_id"], country_code=row["country_code"], document_id=document_id,
                          doc_type=row["doc_type"], title=row["title"], document_date=row["document_date"],
                          disclosure_date=row["disclosure_date"], source_url=row["source_url"], local_path=row["local_path"],
                          size_bytes=int(row["size_bytes"]), sha256=row["sha256"], privacy_mask_events=events)


# ---------------------------------------------------------------------------------------------- Databricks CLI
def databricks_cli() -> str:
    for candidate in [os.environ.get("DATABRICKS_CLI"), shutil.which("databricks"),
                      *sorted(glob.glob(str(Path.home() / ".vscode/extensions/databricks.databricks-*/bin/databricks")), reverse=True)]:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise PocError("Databricks CLI not found; set DATABRICKS_CLI")


def cli(*args: str, ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    completed = subprocess.run([databricks_cli(), *args, "-p", PROFILE], capture_output=True, text=True,
                               timeout=CLI_TIMEOUT_SECONDS, check=False)
    if completed.returncode not in ok_codes:
        first = (completed.stderr.strip().splitlines() or ["unknown error"])[0][:200]
        raise PocError(f"databricks {args[0]} {args[1] if len(args) > 1 else ''} failed: {first}")
    return completed


def cli_json(*args: str) -> Any:
    return json.loads(cli(*args, "-o", "json").stdout or "null")


def existing_volume() -> dict | None:
    for volume in cli_json("volumes", "list", CATALOG, SCHEMA) or []:
        if volume.get("name") == VOLUME:
            return volume
    return None


def ensure_volume() -> tuple[dict, bool]:
    """(volume, created_now). An existing Volume is reused unchanged."""
    volume = existing_volume()
    if volume is not None:
        return volume, False
    cli("volumes", "create", CATALOG, SCHEMA, VOLUME, "MANAGED", "--comment", VOLUME_COMMENT)
    volume = existing_volume()
    if volume is None or volume.get("volume_type") != "MANAGED":
        raise PocError("managed Volume was not created")
    return volume, True


def remote_listing(document: DocumentRecord) -> dict | None:
    folder = f"dbfs:{VOLUME_ROOT}/{document.country_code}/{document.project_id}"
    completed = cli("fs", "ls", folder, "-l", "-o", "json", ok_codes=(0, 1))
    if completed.returncode != 0:
        return None                                       # folder does not exist yet
    for entry in json.loads(completed.stdout or "[]"):
        if Path(entry.get("name", "")).name == document.filename:
            return entry
    return None


def upload(document: DocumentRecord) -> bool:
    """Copy the PDF unless it is already there; never overwrites. Returns True if uploaded now."""
    if remote_listing(document) is not None:
        return False
    cli("fs", "mkdir", f"dbfs:{VOLUME_ROOT}/{document.country_code}/{document.project_id}")   # idempotent, like mkdir -p
    cli("fs", "cp", str(REPO / document.local_path), f"dbfs:{document.volume_path}")
    return True


def download_sha256(document: DocumentRecord) -> tuple[int, str]:
    with tempfile.TemporaryDirectory(prefix="uc_volume_poc_") as scratch:
        target = Path(scratch) / document.filename
        cli("fs", "cp", f"dbfs:{document.volume_path}", str(target))
        return target.stat().st_size, sha256_file(target)


def warehouse_read(document: DocumentRecord) -> dict[str, Any]:
    """Read the stored bytes inside the workspace (serverless SQL warehouse, binaryFile reader) and hash them there."""
    warehouses = cli_json("warehouses", "list") or []
    if not warehouses:
        raise PocError("no SQL warehouse available")
    body = {"warehouse_id": warehouses[0]["id"], "wait_timeout": "50s", "on_wait_timeout": "CONTINUE",
            "statement": "SELECT path, length, sha2(content, 256) AS sha256 FROM read_files(:path, format => 'binaryFile')",
            "parameters": [{"name": "path", "value": document.volume_path, "type": "STRING"}]}
    response = cli_json("api", "post", "/api/2.0/sql/statements", "--json", json.dumps(body))
    started = time.monotonic()
    while response["status"]["state"] in ("PENDING", "RUNNING") and time.monotonic() - started < 600:
        time.sleep(5)
        response = cli_json("api", "get", f"/api/2.0/sql/statements/{response['statement_id']}")
    state = response["status"]["state"]
    if state != "SUCCEEDED":
        message = (response["status"].get("error") or {}).get("message", "")[:200]
        raise PocError(f"workspace read did not succeed ({state}): {message}")
    rows = (response.get("result") or {}).get("data_array") or []
    if len(rows) != 1:
        raise PocError(f"workspace read returned {len(rows)} rows")
    path, length, digest = rows[0]
    return {"warehouse_name": warehouses[0].get("name"), "path": path, "length": int(length), "sha256": digest,
            "elapsed_seconds": round(time.monotonic() - started, 1)}


# --------------------------------------------------------------------------------------------------------- main
def run() -> dict[str, Any]:
    document = select_document()
    volume, created = ensure_volume()
    uploaded = upload(document)
    listing = remote_listing(document) or {}
    downloaded_size, downloaded_sha = download_sha256(document)
    workspace = warehouse_read(document)
    checks = {"listing_size_matches": listing.get("size") == document.size_bytes,
              "download_size_matches": downloaded_size == document.size_bytes,
              "download_sha256_matches": downloaded_sha == document.sha256,
              "workspace_length_matches": workspace["length"] == document.size_bytes,
              "workspace_sha256_matches": workspace["sha256"] == document.sha256}
    return {"run_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "volume": {"full_name": VOLUME_FULL_NAME, "root": VOLUME_ROOT, "volume_type": volume.get("volume_type"),
                       "created_in_this_run": created, "comment": volume.get("comment")},
            "document": {**asdict(document), "volume_path": document.volume_path},
            "upload": {"uploaded_in_this_run": uploaded, "remote_listing_size": listing.get("size")},
            "verification": {"download_sha256": downloaded_sha, "workspace_read": workspace, **checks,
                             "all_passed": all(checks.values())}}


def main(command: str) -> None:
    if command == "check":
        print(json.dumps({**asdict(select_document()), "volume_path": select_document().volume_path}, indent=1))
        return
    if command != "run":
        raise SystemExit(__doc__)
    if os.environ.get(LIVE_OPT_IN) != "1":
        raise SystemExit(f"live run not enabled: set {LIVE_OPT_IN}=1 for this invocation")
    result = run()
    out = POC_DIR / "results" / f"uc_volume_pdf_poc_{result['run_at_utc'].replace(':', '').replace('-', '')[:15]}Z.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    print(f"results written to {out.relative_to(REPO)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
