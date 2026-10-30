"""Read-only table-quality audit of the 10-PDF pilot (pilot_v2). Changes nothing outside results/ of this folder.

Inputs (read-only): rag/corpus_v3/processed/pilot_v2/shared/*.jsonl (sanitised records; restricted/ is never read),
rag/corpus_v3/pdf/**.pdf (original PDFs, opened with pdfplumber for source-page comparison), and
manual_verification.json (verdicts from visual page-by-page comparison of a stratified sample).

    /opt/anaconda3/bin/python3 -B audits/pdf_table_quality/audit_tables.py      (from procurement-intelligence/)

Every automated check is a deterministic comparison; the manual file is the only source of the `verified` label.
Source cells are re-extracted from the original PDFs with the pipeline's own pdfplumber settings (all 220 tables
reproduce exactly) and held in memory only: they are never written, because staff tables contain personal names.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "rag" / "corpus_v3" / "processed" / "pilot_v2" / "shared"
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
MANUAL = HERE / "manual_verification.json"

NUMBER = re.compile(r"(?<![\w.])\(?-?(?:US\$|\$|€|£)?\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?%?|(?<![\w.,])\(?-?(?:US\$|\$)?\d+(?:\.\d+)?\)?%?(?![\w])")
CURRENCY = re.compile(r"US\$|USD|\$|SDR|EUR|€|IDA|IBRD|million|billion|\bm\b", re.I)
PERCENT = re.compile(r"%|percent", re.I)
DATE = re.compile(r"\b\d{1,2}-[A-Z][a-z]{2}-\d{4}\b|\b\d{4}-\d{2}-\d{2}\b|\b[A-Z][a-z]{2}/\d{4}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
                  r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b")
TOTAL = re.compile(r"^\s*(sub-?\s?total|total|grand total)\b", re.I)
FOOTNOTE = re.compile(r"(?:^|\s)(?:\*+|[a-z]\/|\d\/|\[\d+\])(?:\s|$)|^\s*(?:Note|Notes|Source)\s*:", re.I)
DASH = re.compile(r"^\s*(?:-|–|—|n\.?a\.?|N/A)\s*$", re.I)
NEGATIVE = re.compile(r"^\(?-\d|^\(\d[\d,.]*\)$")
REVERSED_WORD = re.compile(r"^[a-z][a-z]{2,}[A-Z]$")      # 'eganiarD' = 'Drainage' read from vertical text


@dataclass
class Finding:
    table_id: str
    document_id: str
    page: int
    check: str
    severity: str           # high | medium | low
    detail: str


@dataclass
class TableAudit:
    table: dict[str, Any]
    doc_type: str
    category: str
    rows: list[dict[str, Any]]
    metrics: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    classification: str = "needs_review"
    basis: str = "automated"


def jsonl(name: str) -> list[dict[str, Any]]:
    with (SHARED / name).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def is_real_label(label: str) -> bool:
    """A usable column label: not a placeholder, not a paragraph, not a data line (no dates or 2+ numbers)."""
    return bool(label) and not label.startswith("col_") and len(label) <= 60 and not DATE.search(label) \
        and len(numeric_tokens(label)) < 2


def numeric_tokens(cell: str) -> list[str]:
    return [m.group(0).strip() for m in NUMBER.finditer(cell or "") if re.search(r"\d", m.group(0))]


def quantity_tokens(cell: str) -> list[str]:
    """Numeric tokens that are values, not parts of identifiers such as 'MIHAPCLG-108999-CW-RFB' or 'IDA-56250'."""
    out = []
    for m in NUMBER.finditer(cell or ""):
        before = (cell or "")[max(0, m.start() - 2):m.start()]
        after = (cell or "")[m.end():m.end() + 1]
        if re.search(r"\d", m.group(0)) and not re.search(r"[A-Za-z0-9]-$", before) and after != "-":
            out.append(m.group(0).strip())
    return out


WELL_FORMED = re.compile(r"\(?-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?%?")
COMPLETE = re.compile(r"\(?-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?\)?%?")


def is_wrapped_number(pieces: list[str], joined: str) -> bool:
    """One value wrapped over lines ('140,348,30' + '8.59'), not several values stacked in one cell ('57' + '37').
    True when the joined text is a well-formed number and the first line is not a complete value on its own."""
    if len(pieces) < 2 or "".join(pieces) != joined or not WELL_FORMED.fullmatch(joined):
        return len(pieces) < 2 and bool(WELL_FORMED.fullmatch(joined))
    return not COMPLETE.fullmatch(pieces[0])


def compare_cell(clean: str, raw: str | None) -> list[tuple[str, str]]:
    """(token, verdict) for each quantity token of a cleaned cell against its own source cell.
    verdict: exact | wrap_joined (one number wrapped over lines) | line_merge (separate numbers glued) | not_in_source."""
    results = []
    raw = raw or ""
    raw_lines = [line.strip() for line in raw.split("\n") if line.strip()]
    raw_tokens = {compact(t) for line in raw_lines for t in numeric_tokens(line)}
    for token in quantity_tokens(clean):
        key = compact(token)
        if key in raw_tokens:
            results.append((token, "exact"))
        elif key and key in compact(raw):
            pieces = [compact(line) for line in raw_lines if compact(line) and compact(line) in key]
            results.append((token, "wrap_joined" if is_wrapped_number(pieces, key) else "line_merge"))
        else:
            results.append((token, "not_in_source"))
    return results


# ---------------------------------------------------------------------------------------------- categorisation
def table_category(table: dict[str, Any], doc_type: str, rows: list[dict[str, Any]]) -> str:
    text = " ".join([table.get("caption") or "", table.get("section_path") or "", " ".join(table.get("columns") or [])]
                    + [" ".join(r["cells_clean"]) for r in rows[:4]])
    if table["kind"] == "staff":
        return "staff"
    if table["kind"] == "step_plan":
        return "procurement_plan_step"
    if doc_type == "PP":
        return "procurement_plan_other"
    if doc_type == "ISR":
        return "isr_results_indicators" if "Indicator Name" in text else "isr_other"
    if doc_type == "PAD":
        return "pad_financial" if re.search(r"US\$|million|cost|financ|amount|disburse", text, re.I) else "pad_other"
    if doc_type == "ICR":
        return "icr_annex" if re.search(r"annex", table.get("section_path") or "", re.I) else "icr_body"
    return f"{doc_type.lower()}_table"


# --------------------------------------------------------------------------------------------- automated checks
def structural_checks(audit: TableAudit) -> None:
    t, rows = audit.table, audit.rows
    data = [r for r in rows if not r["is_header"]]
    columns = t.get("columns") or []
    labelled = [c for c in columns if is_real_label(str(c))]
    misaligned = [r for r in rows if len(r["cells_clean"]) != t["n_columns"]]
    title_like = [r for r in data if r["cells_clean"] and r["cells_clean"][0] and not any(c for c in r["cells_clean"][1:])]
    empty_columns = [i for i in range(t["n_columns"]) if not any(i < len(r["cells_clean"]) and r["cells_clean"][i] for r in data)]
    signature = Counter(tuple(r["cells_clean"]) for r in data if any(r["cells_clean"]))
    duplicates = sum(n - 1 for n in signature.values() if n > 1)
    audit.metrics.update({
        "n_columns": t["n_columns"], "rows": len(rows), "data_rows": len(data), "header_rows": t["header_rows"],
        "header_inherited": t["header_inherited"], "continuation_of": t["continuation_of"],
        "labelled_column_share": round(len(labelled) / len(columns), 3) if columns else 0.0,
        "misaligned_rows": len(misaligned), "single_cell_rows": len(title_like), "empty_columns": len(empty_columns),
        "duplicate_data_rows": duplicates, "row_pages_match_table_page": all(r["page"] == t["page"] for r in rows)})
    if audit.metrics["labelled_column_share"] < 0.5:
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "column_labels", "high",
                                      f"only {len(labelled)}/{len(columns)} columns labelled"))
    elif audit.metrics["labelled_column_share"] < 1.0:
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "column_labels", "medium",
                                      f"{len(columns) - len(labelled)} unlabelled column(s)"))
    reversed_words = [w for c in columns for w in str(c).split() if REVERSED_WORD.match(w)]
    audit.metrics["reversed_header_words"] = len(reversed_words)
    if len(reversed_words) >= 2:
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "reversed_header_text", "high",
                                      f"{len(reversed_words)} header word(s) read backwards from rotated text (e.g. {', '.join(reversed_words[:3])})"))
    if misaligned:
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "alignment", "high",
                                      f"{len(misaligned)} row(s) with a cell count different from {t['n_columns']}"))
    if data and len(title_like) / len(data) > 0.3:
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "merged_cells", "medium",
                                      f"{len(title_like)}/{len(data)} data rows have text only in the first cell (spanning/merged)"))
    if empty_columns and t["n_columns"] > 2:
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "empty_columns", "low",
                                      f"{len(empty_columns)} column(s) empty in every data row (split/merged header columns)"))
    if duplicates and audit.category != "isr_results_indicators":
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "duplicate_rows", "medium",
                                      f"{duplicates} repeated data row(s)"))
    if not audit.metrics["row_pages_match_table_page"]:
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "page_reference", "high", "row page differs from table page"))


def content_checks(audit: TableAudit, raw_rows: list[list[str | None]]) -> None:
    t, rows = audit.table, [r for r in audit.rows if not r["is_header"]]
    cells = [c for r in rows for c in r["cells_clean"] if c]
    header_text = " ".join(" ".join(r) for r in t.get("header_clean") or []) + " " + (t.get("caption") or "")
    verdicts: Counter = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        raw = raw_rows[row["row_index"] - 1] if row["row_index"] - 1 < len(raw_rows) else []
        for col, clean in enumerate(row["cells_clean"]):
            for token, verdict in compare_cell(clean, raw[col] if col < len(raw) else None):
                verdicts[verdict] += 1
                if verdict in ("line_merge", "not_in_source") and len(examples[verdict]) < 4:
                    examples[verdict].append(f"r{row['row_index']}c{col + 1}:{token}")
    totals = [r for r in rows if r["cells_clean"] and TOTAL.match(r["cells_clean"][0] or "")]
    tokens = sum(verdicts.values())
    audit.metrics.update({
        "cells": len(cells), "numeric_cells": sum(1 for c in cells if quantity_tokens(c)), "quantity_tokens": tokens,
        "tokens_exact": verdicts["exact"], "tokens_wrap_joined": verdicts["wrap_joined"],
        "tokens_line_merge": verdicts["line_merge"], "tokens_not_in_source": verdicts["not_in_source"],
        "currency_or_unit_in_header": bool(CURRENCY.search(header_text)) or bool(PERCENT.search(header_text)),
        "currency_cells": sum(1 for c in cells if CURRENCY.search(c)), "percent_cells": sum(1 for c in cells if PERCENT.search(c)),
        "negative_cells": sum(1 for c in cells if NEGATIVE.match(c.strip())), "dash_or_na_cells": sum(1 for c in cells if DASH.match(c)),
        "date_cells": sum(1 for c in cells if DATE.search(c)), "total_rows": len(totals),
        "footnote_cells": sum(1 for c in cells if FOOTNOTE.search(c)),
        "masked_cells": sum(c.count("[PERSON]") + c.count("[EMAIL]") + c.count("[PHONE]") for c in cells)})
    packed = [c for c in cells if len(quantity_tokens(c)) >= 3 and re.search(r"\s", c)
              and len(re.findall(r"[A-Za-z]", c)) <= 15]                      # a data line, not prose with figures
    audit.metrics["packed_cells"] = len(packed)
    if packed and audit.category not in ("procurement_plan_step",) and len(packed) >= 2:
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "packed_cells", "high",
                                      f"{len(packed)} cell(s) hold several values (a whole printed line in one cell)"))
    if verdicts["line_merge"]:
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "numeric_line_merge", "high",
                                      f"{verdicts['line_merge']} value(s) glued from separate numbers stacked in one source cell "
                                      f"(e.g. {', '.join(examples['line_merge'])})"))
    if verdicts["not_in_source"]:
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "numeric_not_in_source", "high",
                                      f"{verdicts['not_in_source']} value(s) not present in the same source cell "
                                      f"(e.g. {', '.join(examples['not_in_source'])})"))
    if audit.metrics["numeric_cells"] >= 3 and not audit.metrics["currency_or_unit_in_header"] and audit.category not in (
            "procurement_plan_step", "isr_results_indicators", "staff"):
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "units", "medium",
                                      "numeric cells without a currency/unit in header or caption"))
    if totals:
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "totals", "low",
                                      f"{len(totals)} total/subtotal row(s) stored as ordinary rows (not flagged)"))
    if audit.metrics["dash_or_na_cells"]:
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "blank_vs_zero", "low",
                                      f"{audit.metrics['dash_or_na_cells']} dash/n.a. cell(s): must stay non-numeric (not zero)"))
    if audit.metrics["footnote_cells"]:
        audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "footnotes", "low",
                                      f"{audit.metrics['footnote_cells']} cell(s) with footnote markers/notes kept inline"))


def continuation_checks(audits: list[TableAudit]) -> None:
    by_id = {a.table["table_id"]: a for a in audits}
    for audit in audits:
        parent_id = audit.table["continuation_of"]
        if not parent_id:
            continue
        parent = by_id[parent_id]
        if parent.table["n_columns"] != audit.table["n_columns"]:
            audit.findings.append(Finding(audit.table["table_id"], audit.table["document_id"], audit.table["page"], "continuation", "high",
                                          "column count differs from parent"))
        last = next((r["cells_clean"] for r in reversed(parent.rows) if not r["is_header"]), None)
        first = next((r["cells_clean"] for r in audit.rows if not r["is_header"]), None)
        if first and last and first[0] and not any(first[1:]) and audit.category in ("isr_results_indicators", "pad_financial", "icr_annex") \
                and not re.match(r"\s*(Component|Annex|Table|Section)\b", first[0]):
            unit_closed = bool(re.search(r"\([^()]+\)\s*\.?$", first[0]))
            audit.findings.append(Finding(audit.table["table_id"], audit.table["document_id"], audit.table["page"], "continuation_split_row",
                                          "high",
                                          "first row has a label but no values; " + ("its values may be on the previous page"
                                          if unit_closed else "a row is split across the page break (label fragment)")))
        if first and last and first == last:
            audit.findings.append(Finding(audit.table["table_id"], audit.table["document_id"], audit.table["page"], "continuation_duplicate",
                                          "medium", "first row repeats the last row of the parent"))


# ----------------------------------------------------------------------------------------- missed-table detection
def numeric_lines_outside_tables(page: pdfplumber.page.Page, boxes: list[list[float]]) -> int:
    """Text lines with >= 3 numeric tokens whose vertical centre is outside every extracted table box."""
    lines: dict[int, list[str]] = defaultdict(list)
    for word in page.extract_words(keep_blank_chars=False, use_text_flow=False):
        lines[round(word["top"] / 3)].append(word["text"])
    count = 0
    for key, words in lines.items():
        y = key * 3
        if sum(1 for w in words if NUMBER.fullmatch(w.strip(",;")) and re.search(r"\d", w)) >= 3:
            if not any(b[1] - 2 <= y <= b[3] + 2 for b in boxes):
                count += 1
    return count


# ------------------------------------------------------------------------------------------------ plan activities
def plan_activity_checks(activities: list[dict], raw_cells: dict[str, list[list[str | None]]]) -> dict[str, Any]:
    """Typed plan amounts and stage dates against the activity's own source row (same table, same row)."""
    def variants(value: float) -> set[str]:
        return {compact(v) for v in (f"{value:,.2f}", f"{value:,.0f}", f"{value:.2f}", f"{value:,.1f}")}

    amounts = found = dates = dates_found = 0
    missing, missing_dates = [], []
    for a in activities:
        raw = raw_cells.get(a["table_id"], [])
        row = compact(" ".join(c or "" for c in raw[a["row_index"] - 1])) if 0 < a["row_index"] <= len(raw) else ""
        for name in ("estimated_amount_usd", "actual_amount_usd"):
            value = a.get(name)
            if value is None:
                continue
            amounts += 1
            if any(v in row for v in variants(float(value))):
                found += 1
            else:
                missing.append((a["activity_id"], name, value))
        for stage, values in (a.get("stage_dates") or {}).items():
            for kind, date in values.items():
                if date:
                    dates += 1
                    if compact(date) in row:
                        dates_found += 1
                    else:
                        missing_dates.append((a["activity_id"], stage, kind, date))
    versions = Counter((a["document_id"], a["plan_version_id"]) for a in activities)
    return {"activities": len(activities), "amounts_checked": amounts, "amounts_found_in_source_row": found,
            "amounts_not_found": missing[:10], "amounts_not_found_count": len(missing), "stage_dates_checked": dates,
            "stage_dates_found_in_source_row": dates_found, "stage_dates_not_found": missing_dates[:10],
            "plan_versions_per_document": {d: sorted(v for (doc, v) in versions if doc == d) for d in {d for d, _ in versions}},
            "value_flags": Counter(f for a in activities for f in (a.get("value_flags") or [])),
            "activities_with_both_amounts_missing": sum(1 for a in activities if a.get("estimated_amount_usd") is None and a.get("actual_amount_usd") is None)}


# ------------------------------------------------------------------------------------------------------- driver
def classify(audit: TableAudit, manual: dict[str, Any]) -> None:
    severities = {f.severity for f in audit.findings}
    verdict = manual.get(audit.table["table_id"])
    if audit.category == "staff":
        audit.classification = "unusable_for_structured_queries"
        audit.basis = "policy: staff/personnel tables hold personal data (D-14); never for SQL"
    elif verdict:
        audit.classification, audit.basis = verdict["classification"], "manual page comparison + automated"
        audit.metrics["manual_notes"] = verdict["notes"]
    elif "high" in severities or audit.metrics.get("labelled_column_share", 0) < 0.5:
        audit.classification = "unusable_for_structured_queries"
        audit.basis = "automated: high-severity structural/numeric finding"
    else:
        audit.classification, audit.basis = "needs_review", "automated checks only (not manually verified)"


def run() -> dict[str, Any]:
    documents = {d["document_id"]: d for d in jsonl("documents.jsonl")}
    versions = {d["document_id"]: d for d in jsonl("document_versions.jsonl")}
    tables = jsonl("tables.jsonl")
    rows_by_table: dict[str, list[dict]] = defaultdict(list)
    for row in jsonl("table_rows.jsonl"):
        rows_by_table[row["table_id"]].append(row)
    for rows in rows_by_table.values():
        rows.sort(key=lambda r: r["row_index"])
    manual = json.loads(MANUAL.read_text())["tables"] if MANUAL.is_file() else {}

    page_texts: dict[tuple[str, int], str] = {}
    page_meta: dict[tuple[str, int], dict] = {}
    raw_cells: dict[str, list[list[str | None]]] = {}            # in memory only; never written
    for document_id, version in versions.items():
        with pdfplumber.open(REPO / version["local_path"]) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                page_texts[(document_id, number)] = page.extract_text() or ""
                for index, found in enumerate(page.find_tables(), start=1):   # numbered like the pipeline (incl. skipped)
                    data = found.extract()
                    if len(data) >= 1 and max((len(r) for r in data), default=0) >= 2:
                        raw_cells[f"{document_id}:t{number:03d}{index:02d}"] = data
                boxes = [t["bbox"] for t in tables if t["document_id"] == document_id and t["page"] == number]
                rotated = (page.rotation or 0) % 180 == 90
                page_meta[(document_id, number)] = {"width": page.height if rotated else page.width,
                                                     "height": page.width if rotated else page.height, "rotation": page.rotation,
                                                     "numeric_lines_outside_tables": numeric_lines_outside_tables(page, boxes),
                                                     "tables_extracted": len(boxes)}

    audits = []
    for t in tables:
        doc_type = documents[t["document_id"]]["doc_type"]
        rows = rows_by_table[t["table_id"]]
        audit = TableAudit(t, doc_type, table_category(t, doc_type, rows), rows)
        structural_checks(audit)
        content_checks(audit, raw_cells[t["table_id"]])
        meta = page_meta[(t["document_id"], t["page"])]
        x0, top, x1, bottom = t["bbox"]
        if not (0 <= x0 < x1 <= max(meta["width"], meta["height"]) + 2 and 0 <= top < bottom <= max(meta["width"], meta["height"]) + 2):   # 2 pt rounding tolerance
            audit.findings.append(Finding(t["table_id"], t["document_id"], t["page"], "bbox", "high", "bbox outside the page"))
        audits.append(audit)
    continuation_checks(audits)
    for audit in audits:
        classify(audit, manual)

    missed = [{"document_id": d, "doc_type": documents[d]["doc_type"], "page": p, **m} for (d, p), m in sorted(page_meta.items())
              if m["numeric_lines_outside_tables"] >= 3]
    plan = plan_activity_checks(jsonl("plan_activities.jsonl"), raw_cells)
    return {"audits": audits, "missed_table_candidates": missed, "plan_activities": plan, "manual": manual,
            "documents": documents, "versions": versions}


def write(result: dict[str, Any]) -> dict[str, Any]:
    RESULTS.mkdir(exist_ok=True)
    audits: list[TableAudit] = result["audits"]
    with (RESULTS / "table_audit.csv").open("w", newline="") as handle:
        fields = ["table_id", "document_id", "doc_type", "category", "kind", "page", "classification", "basis", "high", "medium", "low"]
        metric_fields = sorted({k for a in audits for k in a.metrics})
        writer = csv.DictWriter(handle, fieldnames=fields + metric_fields)
        writer.writeheader()
        for a in audits:
            severities = Counter(f.severity for f in a.findings)
            writer.writerow({"table_id": a.table["table_id"], "document_id": a.table["document_id"], "doc_type": a.doc_type,
                             "category": a.category, "kind": a.table["kind"], "page": a.table["page"],
                             "classification": a.classification, "basis": a.basis, **{s: severities.get(s, 0) for s in ("high", "medium", "low")},
                             **a.metrics})
    with (RESULTS / "findings.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["table_id", "document_id", "page", "check", "severity", "detail"])
        writer.writeheader()
        writer.writerows(f.__dict__ for a in audits for f in a.findings)
    total = lambda key: sum(a.metrics.get(key, 0) for a in audits)
    summary = {
        "tables_detected": len(audits), "tables_audited_automated": len(audits),
        "tables_manually_verified": len(result["manual"]), "rows_examined": sum(a.metrics["rows"] for a in audits),
        "by_doc_type": {k: Counter(a.doc_type for a in audits)[k] for k in sorted({a.doc_type for a in audits})},
        "by_category": dict(sorted(Counter(a.category for a in audits).items())),
        "classification": dict(Counter(a.classification for a in audits)),
        "classification_by_category": {c: dict(Counter(a.classification for a in audits if a.category == c))
                                       for c in sorted({a.category for a in audits})},
        "tables_with_all_columns_labelled": sum(1 for a in audits if a.metrics["labelled_column_share"] == 1.0),
        "tables_with_majority_unlabelled": sum(1 for a in audits if a.metrics["labelled_column_share"] < 0.5),
        "continuation_tables": sum(1 for a in audits if a.table["continuation_of"]),
        "continuation_findings": dict(Counter(f.check for a in audits for f in a.findings if f.check.startswith("continuation"))),
        "quantity_tokens": total("quantity_tokens"), "tokens_exact": total("tokens_exact"),
        "tokens_wrap_joined": total("tokens_wrap_joined"), "tokens_line_merge": total("tokens_line_merge"),
        "tokens_not_in_source": total("tokens_not_in_source"),
        "findings_by_check": dict(Counter((f.check, f.severity) for a in audits for f in a.findings).most_common()),
        "missed_table_candidate_pages": len(result["missed_table_candidates"]),
        "plan_activities": result["plan_activities"],
    }
    summary["findings_by_check"] = {f"{c}/{s}": n for (c, s), n in summary["findings_by_check"].items()}
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    (RESULTS / "missed_table_candidates.json").write_text(json.dumps(result["missed_table_candidates"], indent=1))
    return summary


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    print(json.dumps(write(run()), indent=1, default=str))
