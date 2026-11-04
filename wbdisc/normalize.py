"""Normalization helpers used for profiling and join analysis.

These functions never overwrite raw values: callers derive *new* columns from
them. Each returns None when a value cannot be interpreted, so that malformed
inputs remain visible as unparsed rather than silently coerced.
"""
from __future__ import annotations

import html
import re
import unicodedata
from datetime import date, datetime

PROJECT_ID_RE = re.compile(r"^P\d{6}$")
_PROJECT_ID_SEARCH = re.compile(r"\bP\d{6}\b")

_DATE_FORMATS = (
    "%d-%b-%Y",              # 19-May-2022 (Finances One, procnotices.noticedate)
    "%Y-%m-%dT%H:%M:%SZ",    # 2024-12-20T00:00:00Z (search APIs)
    "%Y-%m-%d %H:%M:%S.%f",  # 2024-12-22 00:00:00.0 (projects.p2a_updated_date)
    "%m/%d/%Y %I:%M:%S %p",  # 9/30/2021 12:00:00 AM (projects.closingdate)
    "%Y-%m-%d",
    "%Y/%m/%d",              # 2021/05/31 (award notice text)
    "%m/%d/%Y",              # 10/20/2026 (Finances One bulk CSVs are US month-first)
)
# NOTE: DD/MM/YYYY is deliberately NOT accepted: no observed source uses it, and accepting
# it would silently mis-parse ambiguous dates such as 03/11/2022.


def parse_date(value) -> date | None:
    """Parse the date formats observed across WB sources. Unknown formats -> None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "null", "n/a"}:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def wb_fiscal_year(d: date | None) -> int | None:
    """World Bank FY runs 1 July (Y-1) to 30 June (Y)."""
    if d is None:
        return None
    return d.year + 1 if d.month >= 7 else d.year


def normalize_project_id(value) -> str | None:
    """Return a canonical P-number (P + 6 digits) or None if the value is not one."""
    if value is None:
        return None
    s = str(value).strip().upper()
    if PROJECT_ID_RE.match(s):
        return s
    return None


def extract_project_ids(text) -> list[str]:
    """All P-numbers appearing in free text, in order of appearance, de-duplicated."""
    if not text:
        return []
    seen: dict[str, None] = {}
    for m in _PROJECT_ID_SEARCH.findall(str(text).upper()):
        seen.setdefault(m, None)
    return list(seen)


def parse_amount(value) -> float | None:
    """Parse '12,000,000', '161538.89', 12.0. Empty/invalid -> None (never 0)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_ref(value) -> str | None:
    """Normalize a borrower bid/contract reference number for exact-after-normalization matching.

    Upper-cases, removes all whitespace and unifies dash/underscore/slash/dot separators.
    'mr-wardip-mr-538131-cs-indv ' -> 'MR-WARDIP-MR-538131-CS-INDV'
    """
    if value is None:
        return None
    s = unicodedata.normalize("NFKC", str(value)).upper().strip()
    if not s or s in {"NAN", "N/A", "NA", "NONE", "-"}:
        return None
    s = re.sub(r"[‐-―_/\\.]", "-", s)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or None


_LEGAL_SUFFIXES = (
    "LIMITED", "LTD", "LLC", "LLP", "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "PLC",
    "SA", "SAS", "SARL", "SRL", "SPA", "GMBH", "AG", "BV", "NV", "PVT", "PTE", "PTY", "JSC", "OJSC", "CJSC",
    "OOO", "TOO", "LLP", "SL", "SAU", "EURL", "SNC", "KFT", "DOO", "AS", "AB", "OY", "BHD", "SDN", "TBK", "PT",
)
_LEGAL_SUFFIX_RE = re.compile(r"\b(" + "|".join(_LEGAL_SUFFIXES) + r")\b")
_PREFIX_RE = re.compile(r"^(M\s*/\s*S|MESSRS)\b\.?")


def normalize_supplier_name(value) -> str | None:
    """Aggressive name key for *candidate* supplier grouping (not identity!).

    Strips accents, punctuation, 'M/s.' prefixes and common legal-form suffixes.
    Two suppliers sharing a key are only a candidate match.
    """
    if value is None:
        return None
    s = html.unescape(str(value))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).upper().strip()
    s = _PREFIX_RE.sub(" ", s)
    s = s.replace("&", " AND ")
    s = s.replace(".", "")  # 'S.R.L.' -> 'SRL' so dotted legal forms match the suffix list
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = _LEGAL_SUFFIX_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def clean_label(value) -> str | None:
    """Collapse internal whitespace/newlines in categorical labels ('Project Information and\\n   Integrated')."""
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s or None


def strip_html(value) -> str:
    """Turn HTML into plain text: line breaks at block ends, ' | ' between table cells, entities decoded."""
    if not value:
        return ""
    s = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</li>|</h\d>", "\n", str(value))
    s = re.sub(r"(?i)</t[dh]>", " | ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()
