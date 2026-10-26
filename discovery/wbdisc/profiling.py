"""Column profiling for raw (string-typed or JSON) records.

Missingness is reported in three separate buckets so that nothing is silently
treated as zero:
  * null      – key absent, None, or JSON null
  * empty     – empty / whitespace-only string, or empty list/dict
  * sentinel  – placeholder tokens observed in WB sources ("NIL", "N/A", "NA", "1900-01-01...")
"""
from __future__ import annotations

import collections
import json
import re
from typing import Any, Iterable

from .normalize import parse_date

SENTINELS = {"NIL", "N/A", "NA", "NONE", "NULL", "-", "NAN", "NOT RATED", "!$!0"}
_INT = re.compile(r"^-?\d+$")
_DEC = re.compile(r"^-?\d+\.\d+$")
_THOUSANDS = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_DATE_PATTERNS = [
    ("DD-Mon-YYYY", re.compile(r"^\d{2}-[A-Z][a-z]{2}-\d{4}$")),
    ("MM/DD/YYYY", re.compile(r"^\d{2}/\d{2}/\d{4}$")),
    ("ISO8601Z", re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")),
    ("YYYY-MM-DD hh:mm:ss.f", re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+$")),
    ("M/D/YYYY h:mm:ss AM", re.compile(r"^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M$")),
    ("YYYY-MM-DD", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
]


def value_kind(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "object"
    s = str(v).strip()
    if not s:
        return "empty"
    if s.upper() in SENTINELS:
        return "sentinel"
    if s.startswith("1900-01-01"):
        return "sentinel"
    if _INT.match(s):
        return "str:int"
    if _DEC.match(s):
        return "str:decimal"
    if _THOUSANDS.match(s):
        return "str:thousands-separated-number"
    for name, pat in _DATE_PATTERNS:
        if pat.match(s):
            return f"str:date[{name}]"
    if "\n" in s:
        return "str:multiline"
    return "str"


def _hashable(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v, sort_keys=True, ensure_ascii=False)


def profile_records(records: Iterable[dict[str, Any]], *, columns: list[str] | None = None, top_n: int = 8,
                    max_distinct_tracked: int = 200_000) -> dict[str, Any]:
    recs = list(records)
    n = len(recs)
    cols = columns or sorted({k for r in recs for k in r if not k.startswith("_")})
    out: dict[str, Any] = {"row_count": n, "columns": {}}
    for c in cols:
        kinds: collections.Counter[str] = collections.Counter()
        values: collections.Counter[str] = collections.Counter()
        dates = []
        lengths = []
        for r in recs:
            v = r.get(c) if c in r else None
            k = value_kind(v)
            kinds[k] += 1
            if k in {"null", "empty", "sentinel"}:
                if k == "sentinel":
                    values[_hashable(v)] += 1
                continue
            hv = _hashable(v)
            if len(values) < max_distinct_tracked or hv in values:
                values[hv] += 1
            lengths.append(len(hv))
            if k.startswith("str:date"):
                d = parse_date(v)
                if d:
                    dates.append(d)
        present = n - kinds["null"] - kinds["empty"] - kinds["sentinel"]
        col = {
            "observed_kinds": dict(kinds.most_common()),
            "null_pct": round(100 * kinds["null"] / n, 2) if n else None,
            "empty_pct": round(100 * kinds["empty"] / n, 2) if n else None,
            "sentinel_pct": round(100 * kinds["sentinel"] / n, 2) if n else None,
            "populated_pct": round(100 * present / n, 2) if n else None,
            "distinct": len(values),
            "distinct_capped": len(values) >= max_distinct_tracked,
            "top_values": [(k[:120], cnt) for k, cnt in values.most_common(top_n)],
            "max_len": max(lengths) if lengths else None,
        }
        date_kinds = sum(v for k, v in kinds.items() if k.startswith("str:date"))
        if dates and present and date_kinds / present >= 0.5:  # only for genuinely date-typed columns
            col["date_min"], col["date_max"] = str(min(dates)), str(max(dates))
        if present and len(values) == present and not col["distinct_capped"]:
            col["unique_when_present"] = True
        out["columns"][c] = col
    return out


def markdown_table(profile: dict[str, Any], *, max_cols: int | None = None) -> str:
    lines = ["| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |",
             "|---|---|---:|---:|---:|---:|---:|---|"]
    for i, (c, p) in enumerate(profile["columns"].items()):
        if max_cols and i >= max_cols:
            break
        kinds = ", ".join(k for k in p["observed_kinds"] if k not in {"null", "empty", "sentinel"}) or "—"
        if "date_min" in p:
            rng = f"{p['date_min']} → {p['date_max']}"
        else:
            rng = "; ".join(f"{str(v)[:40]} ({n})" for v, n in p["top_values"][:3])
        rng = rng.replace("|", "\\|").replace("\n", " ")
        uniq = " (unique)" if p.get("unique_when_present") else ""
        distinct = f"≥{p['distinct']} (capped)" if p.get("distinct_capped") else f"{p['distinct']}"
        lines.append(f"| `{c}` | {kinds} | {p['populated_pct']} | {p['null_pct']} | {p['empty_pct']} | "
                     f"{p['sentinel_pct']} | {distinct}{uniq} | {rng} |")
    return "\n".join(lines)
