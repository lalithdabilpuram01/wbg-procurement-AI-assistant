"""Byte-preserving record selection for cohort-bounded Bronze inputs (decision D-26).

The only operation allowed here is *row selection* by project ID. Records are copied exactly as
they appear in the source: header line (including any UTF-8 BOM), field values, quoting,
embedded line breaks, line endings, duplicate rows and source order are all preserved. Nothing
is trimmed, case-folded, re-encoded or re-serialised.

  * CSV: physical lines are accumulated until the double-quote count is even (RFC 4180 records,
    so quoted fields may contain line breaks); the complete raw record text is written unchanged.
    The record is parsed with the csv module only to read the key column.
  * JSON Lines (optionally gzip-compressed source): each raw line is written unchanged
    (decompressed); it is parsed only to read the key field.

Matching is exact string equality between the raw key value and a cohort project ID. Values that
would match only after trimming or case-folding are *not* selected; they are counted as near
matches so they can be reviewed instead of guessed.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any, cast

PROJECT_ID = re.compile(r"P\d{6}")


def iter_csv_records(stream: IO[str]) -> Iterator[str]:
    """Yield raw CSV records (with their original line terminators) from a text stream opened with newline=''."""
    buf: list[str] = []
    quotes = 0
    for line in stream:
        buf.append(line)
        quotes += line.count('"')
        if quotes % 2 == 0:
            yield "".join(buf)
            buf, quotes = [], 0
    if buf:
        yield "".join(buf)


def parse_record(record: str) -> list[str]:
    rows = list(csv.reader(io.StringIO(record, newline="")))
    return rows[0] if rows else []


def _classify(value: Any, keep: set[str], stats: Counter, examples: dict[str, list]) -> bool:
    """True when value is exactly a cohort ID; otherwise count why the record was not selected."""
    if isinstance(value, str) and value in keep:
        return True
    text = "" if value is None else str(value)
    if text.strip().upper() in keep:
        stats["near_match_not_selected"] += 1
        examples.setdefault("near_match_not_selected", []).append(text)
    elif not PROJECT_ID.fullmatch(text):
        stats["key_not_a_project_id"] += 1
        if len(examples.setdefault("key_not_a_project_id", [])) < 5:
            examples["key_not_a_project_id"].append(text)
    else:
        stats["other_project"] += 1
    return False


def select_csv(src: Path, dst: Path, key_column: str, keep: set[str]) -> dict:
    """Copy the header and every record whose `key_column` value is exactly in `keep`."""
    stats: Counter = Counter()
    examples: dict[str, list] = {}
    selected_keys: Counter = Counter()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("r", encoding="utf-8", errors="strict", newline="") as fin, \
            dst.open("w", encoding="utf-8", newline="") as fout:
        records = iter_csv_records(fin)
        header_raw = next(records)
        header = parse_record(header_raw)
        names = [h.lstrip("\ufeff") for h in header]
        idx = names.index(key_column)
        fout.write(header_raw)
        for rec in records:
            fields = parse_record(rec)
            stats["source_records"] += 1
            if len(fields) != len(header):
                stats["field_count_differs_from_header"] += 1
            value = fields[idx] if idx < len(fields) else None
            if _classify(value, keep, stats, examples):
                fout.write(rec)
                stats["selected_records"] += 1
                selected_keys[value] += 1
    return {"stats": dict(stats), "examples": examples, "selected_keys": selected_keys,
            "header_raw": header_raw, "columns": len(header),
            "line_terminator": "CRLF" if header_raw.endswith("\r\n") else "LF",
            "utf8_bom": header_raw.startswith("\ufeff")}


def _open_binary(path: Path) -> IO[bytes]:
    if path.suffix == ".gz":
        return cast(IO[bytes], gzip.open(path, "rb"))
    return path.open("rb")


def select_jsonl(src: Path, dst: Path, key_field: str, keep: set[str]) -> dict:
    """Copy (decompressed) every JSON line whose top-level `key_field` is exactly in `keep`."""
    stats: Counter = Counter()
    examples: dict[str, list] = {}
    selected_keys: Counter = Counter()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with _open_binary(src) as fin, dst.open("wb") as fout:
        for line in fin:
            if not line.strip():
                stats["blank_lines_skipped"] += 1
                continue
            stats["source_records"] += 1
            value = json.loads(line).get(key_field)
            if _classify(value, keep, stats, examples):
                fout.write(line if line.endswith(b"\n") else line + b"\n")
                stats["selected_records"] += 1
                selected_keys[value] += 1
    return {"stats": dict(stats), "examples": examples, "selected_keys": selected_keys}
