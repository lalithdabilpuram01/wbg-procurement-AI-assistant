"""Raw-sample persistence with provenance.

Raw payloads are written exactly as received (JSON records are stored one per
line, unmodified). Each write appends an entry to samples/MANIFEST.jsonl with
source, extraction method, timestamp, record count and SHA-256 checksum.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .config import SETTINGS
from .http import utcnow


def sha256_file(path: Path) -> str:
    """SHA-256 of a file, read in 1 MiB blocks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _manifest(entry: dict[str, Any], manifest_dir: Path) -> None:
    """Append one provenance entry to MANIFEST.jsonl in the given folder."""
    manifest_dir.mkdir(parents=True, exist_ok=True)
    with open(manifest_dir / "MANIFEST.jsonl", "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def write_jsonl(records: Iterable[dict[str, Any]], path: Path, *, source: str, method: str,
                notes: str = "", compress: bool | None = None) -> Path:
    """Write records as JSON Lines (gzip when the name ends in .gz) and record the file in the manifest."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    compress = path.suffix == ".gz" if compress is None else compress
    opener = gzip.open if compress else open
    n = 0
    with opener(path, "wt", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    record_file(path, source=source, method=method, records=n, notes=notes)
    return path


def record_file(path: Path, *, source: str, method: str, records: int | None, notes: str = "") -> None:
    """Append the file's provenance (source, method, time, record count, size, SHA-256) to the samples manifest."""
    path = Path(path)
    root = SETTINGS.samples_dir if SETTINGS.samples_dir in path.parents else path.parent
    _manifest({
        "file": str(path.relative_to(SETTINGS.samples_dir.parent)),
        "source": source,
        "extraction_method": method,
        "extracted_at_utc": utcnow(),
        "records": records,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "notes": notes,
    }, root)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSON Lines file (gzip when the name ends in .gz), skipping blank lines."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
