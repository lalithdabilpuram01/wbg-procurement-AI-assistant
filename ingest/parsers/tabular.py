"""Tabular feeds (CSV, JSON, JSON Lines, Excel) mapped onto canonical record schemas with row-level validation.

* Column names are matched through aliases after normalisation ("WB Contract No." == "wb_contract_no"), so feeds
  from different systems need no hand mapping. Unknown columns are kept in `extra`; nothing is dropped silently.
* Values are typed: project ids (P + 6 digits), amounts ("US$ 1,234.5", "1.2m"), dates (ISO, d/m/Y, "14-May-2019",
  Excel dates). An ambiguous day/month date is read day-first and reported.
* Each row is validated; invalid rows are rejected with their reasons. A file missing a required column, or with
  more than half of its rows invalid, is quarantined.
* Personal data: free text is sanitised; the supplier of an individual-consultant contract is replaced by a token.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

from rag.ingestion.sanitize import sanitize

from ..detect import read_text
from ..model import ParsedDocument, Quarantined, Record
from ..reference import normalise_reference
from . import common

ALIASES = {
    "project_id": ("project_id", "projectid", "project", "project_code", "wb_project_id", "project_number"),
    "wb_contract_number": ("wb_contract_number", "wb_contract_no", "contract_number", "contract_no", "contract_id"),
    "reference": ("borrower_contract_reference", "borrower_reference", "contract_reference", "activity_reference", "reference",
                  "reference_no", "procurement_reference", "bid_reference", "package_number", "package_no", "step_reference"),
    "amount_usd": ("contract_amount_usd", "amount_usd", "contract_amount", "signed_amount_usd", "total_contract_amount_usd", "amount",
                   "value_usd", "contract_value_usd"),
    "estimated_amount_usd": ("estimated_amount_usd", "estimated_amount", "estimated_cost_usd", "estimated_cost", "estimate_usd"),
    "actual_amount_usd": ("actual_amount_usd", "actual_amount"),
    "signing_date": ("contract_signing_date", "signing_date", "signed_date", "date_signed", "contract_signature_date"),
    "planned_signing_date": ("planned_signing_date", "signed_contract_planned", "planned_contract_signing", "planned_date"),
    "actual_signing_date": ("actual_signing_date", "signed_contract_actual", "actual_contract_signing"),
    "publication_date": ("publication_date", "published_date", "notice_date", "date_published", "published"),
    "deadline_date": ("deadline_date", "submission_deadline", "bid_deadline", "deadline", "closing_date"),
    "method": ("procurement_method", "method", "selection_method", "procurement_approach"),
    "category": ("procurement_category", "category", "procurement_type"),
    "review_type": ("review_type", "review", "bank_review"),
    "status": ("status", "process_status", "activity_status"),
    "supplier_name": ("supplier_name", "supplier", "contractor", "vendor", "awarded_to", "firm", "awardee"),
    "supplier_country": ("supplier_country", "contractor_country", "vendor_country", "awardee_country"),
    "notice_id": ("notice_id", "notice_no", "notice_number"),
    "notice_type": ("notice_type", "type_of_notice"),
    "description": ("description", "contract_description", "activity_description", "package_description", "title",
                    "notice_title", "subject"),
}
SCHEMAS = {  # record type -> (fields it uses, required: each tuple needs at least one of its fields)
    "contract": (("project_id", "wb_contract_number", "reference", "amount_usd", "signing_date", "method", "category", "review_type",
                  "supplier_name", "supplier_country", "description"), (("project_id",), ("wb_contract_number", "reference"))),
    "notice": (("project_id", "notice_id", "notice_type", "reference", "publication_date", "deadline_date", "method", "category",
                "description"), (("project_id",), ("notice_id", "reference"))),
    "plan_activity": (("project_id", "reference", "description", "method", "category", "review_type", "status",
                       "estimated_amount_usd", "actual_amount_usd", "planned_signing_date", "actual_signing_date"),
                      (("project_id",), ("reference",))),
}
DOC_TYPES = {"contract": "contract_feed", "notice": "notice_feed", "plan_activity": "PP"}
MAX_INVALID_SHARE = 0.5
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%m-%Y", "%d.%m.%Y")
AMOUNT = re.compile(r"^(-?\d+(?:\.\d+)?)\s*([kmb])?$", re.I)


def parse(path: Path, sha256: str, document_id: str, record_type: str | None, fmt: str) -> ParsedDocument:
    """Map the columns, validate every row and keep the valid records. A file missing a required column, or with too
    many invalid rows, is quarantined."""
    rows = _rows(path, fmt)
    if not rows:
        raise Quarantined("no_rows", "the file has a header but no data rows")
    mapping = _mapping(rows[0].keys())
    record_type = record_type or _infer(mapping)
    fields, required = SCHEMAS[record_type]
    # A project id in the file name stands in for a missing project column.
    missing = [" or ".join(group) for group in required if not any(f in mapping.values() for f in group)
               and not (group == ("project_id",) and _project_from_name(path))]
    if missing:
        raise Quarantined("schema_mismatch", f"missing column(s): {', '.join(missing)}; found {sorted(rows[0])[:12]}")
    parsed = ParsedDocument(document_id=document_id, doc_type=DOC_TYPES[record_type], modality="tabular", source_sha256=sha256,
                            source_name=path.name, project_id=_project_from_name(path))
    notes: set[str] = set()
    for number, raw in enumerate(rows, start=1):
        record, errors = _record(record_type, raw, mapping, fields, required, parsed.project_id, notes)
        if errors:
            parsed.rejected_rows.append({"row": number, "errors": errors})
        else:
            record.source_row = number
            parsed.records.append(record)
    if len(parsed.rejected_rows) > MAX_INVALID_SHARE * len(rows):
        raise Quarantined("too_many_invalid_rows", f"{len(parsed.rejected_rows)} of {len(rows)} rows are invalid; "
                                                    f"first: {parsed.rejected_rows[0]['errors']}")
    parsed.warnings += sorted(notes) + ([f"rejected_rows: {len(parsed.rejected_rows)}"] if parsed.rejected_rows else [])
    unmapped = sorted(k for k in rows[0] if k not in mapping)
    if unmapped:
        parsed.warnings.append(f"unmapped_columns_kept_in_extra: {unmapped[:10]}")
    return parsed


# --------------------------------------------------------------------------------------------------- reading
def _rows(path: Path, fmt: str) -> list[dict[str, Any]]:
    """The data rows as dicts: CSV with a sniffed delimiter (blank rows skipped), JSON or JSON Lines, or Excel."""
    if fmt == "csv":
        text = read_text(path)
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        return [r for r in csv.DictReader(io.StringIO(text), dialect=dialect) if any((v or "").strip() for v in r.values() if isinstance(v, str))]
    if fmt == "json":
        text = read_text(path).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        if isinstance(data, dict):
            # A JSON object is either a wrapper around the record list or a single record.
            data = next((v for k, v in data.items() if k in ("records", "data", "items", "rows") and isinstance(v, list)), [data])
        if not all(isinstance(r, dict) for r in data):
            raise Quarantined("schema_mismatch", "JSON must be a list of objects (or {\"records\": [...]}, or JSON Lines)")
        return data
    return _excel(path)


def _excel(path: Path) -> list[dict[str, Any]]:
    """The sheet whose header maps onto the most canonical fields; the header is the first row with 2+ text cells."""
    import openpyxl
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    best: list[dict[str, Any]] = []
    best_score = -1
    for sheet in book.worksheets:
        rows = [list(r) for r in sheet.iter_rows(values_only=True)]
        start = next((i for i, r in enumerate(rows[:20]) if sum(isinstance(c, str) and c.strip() != "" for c in r) >= 2), None)
        if start is None:
            continue
        header = [str(c).strip() if c is not None else f"column_{n}" for n, c in enumerate(rows[start])]
        records = [dict(zip(header, r)) for r in rows[start + 1:] if any(c not in (None, "") for c in r)]
        score = len(_mapping(header))
        if score > best_score:
            best, best_score = records, score
    book.close()
    return best


def _mapping(columns) -> dict[str, str]:
    """Original column -> canonical field (first alias wins; each canonical field is used once)."""
    mapping, used = {}, set()
    for column in columns:
        key = re.sub(r"[^a-z0-9]+", "_", str(column).lower()).strip("_")
        field = next((f for f, aliases in ALIASES.items() if key in aliases and f not in used), None)
        if field:
            mapping[column] = field
            used.add(field)
    return mapping


def _infer(mapping: dict[str, str]) -> str:
    """For files dropped in `other/`: the schema whose fields the columns cover best."""
    fields = set(mapping.values())
    return max(SCHEMAS, key=lambda t: (len(fields & set(SCHEMAS[t][0])), t == "contract"))


def _project_from_name(path: Path) -> str | None:
    found = common.PROJECT_IN_NAME.findall(path.name.upper())
    return found[0] if found else None


# ------------------------------------------------------------------------------------------------- records
def _record(record_type: str, raw: dict, mapping: dict, fields: tuple, required: tuple, default_project: str | None,
            notes: set[str]) -> tuple[Record | None, list[str]]:
    """Type and validate one row; returns the Record, or None and the reasons it was rejected."""
    values: dict[str, Any] = {}
    errors: list[str] = []
    for column, field in mapping.items():
        if field in fields:
            value, problem = _typed(field, raw.get(column), notes)
            values[field] = value
            if problem:
                errors.append(f"{field}: {problem}")
    values["project_id"] = values.get("project_id") or default_project
    for group in required:
        if not any(values.get(f) for f in group):
            errors.append(f"missing {' or '.join(group)}")
    if errors:
        return None, errors
    if values.get("description"):
        values["description"] = sanitize(str(values["description"]))[0]
    if values.get("supplier_name") and "individual" in str(values.get("category") or "").lower():
        values["supplier_name"] = "[INDIVIDUAL]"
    values["reference_norm"] = normalise_reference(values.get("reference"))
    values["extra"] = {str(k): _plain(v) for k, v in raw.items() if k not in mapping and v not in (None, "")}
    return Record(record_type, _record_id(record_type, values), values.get("project_id"), values), []


def _record_id(record_type: str, v: dict) -> str:
    """Natural key of a record: contract or notice number, else project + normalised reference (or a content hash)."""
    if record_type == "contract" and v.get("wb_contract_number"):
        return str(v["wb_contract_number"])
    if record_type == "notice" and v.get("notice_id"):
        return str(v["notice_id"])
    key = v.get("reference_norm") or hashlib.sha256(json.dumps(v, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return f"{v.get('project_id')}:{key}"


def _typed(field: str, value: Any, notes: set[str]) -> tuple[Any, str | None]:
    """Type one value for its field, as (value, problem); an empty value is missing, not invalid."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if field == "project_id":
        found = common.PROJECT_ID.findall(str(value).upper())
        return (found[0], None) if found else (None, f"not a project id ({str(value)[:20]})")
    if field.endswith("amount_usd"):
        return _amount(value)
    if field.endswith("_date"):
        return _date(value, notes)
    if field == "wb_contract_number":
        text = str(value).strip()
        return (text[:-2] if text.endswith(".0") else text), None              # Excel reads numbers as floats
    return " ".join(str(value).split()), None


def _amount(value: Any) -> tuple[float | None, str | None]:
    """A US$ amount from a number or from text such as "US$ 1,234.5" or "1.2m"."""
    if isinstance(value, (int, float)):
        return float(value), None
    text = re.sub(r"(US\$|USD|\$|,|\s)", "", str(value), flags=re.I)
    match = AMOUNT.match(text)
    if not match:
        return None, f"not an amount ({str(value)[:20]})"
    scale = {"k": 1e3, "m": 1e6, "b": 1e9}.get((match.group(2) or "").lower(), 1)
    return round(float(match.group(1)) * scale, 2), None


def _date(value: Any, notes: set[str]) -> tuple[str | None, str | None]:
    """An ISO date from a date value or text in a known format; d/m/Y is read day-first unless that is impossible."""
    if isinstance(value, dt.datetime):
        return value.date().isoformat(), None
    if isinstance(value, dt.date):
        return value.isoformat(), None
    text = str(value).strip()
    candidates = dict.fromkeys((text, text.split("T")[0], text.split(" ")[0]))      # also "2019-05-14T00:00:00"
    for candidate in candidates:
        for fmt in DATE_FORMATS:
            try:
                return dt.datetime.strptime(candidate, fmt).date().isoformat(), None
            except ValueError:
                continue
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        a, b, year = map(int, match.groups())
        day, month = (b, a) if b > 12 >= a else (a, b)                       # month-first only when day-first is impossible
        if a <= 12 and b <= 12 and a != b:
            notes.add("ambiguous_dates_read_day_first")
        try:
            return dt.date(year, month, day).isoformat(), None
        except ValueError:
            pass
    return None, f"not a date ({text[:20]})"


def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else value
