"""Stratified, reproducible project-cohort selection for the 2,000-project PoC (decision D-26).

Pure functions only (no I/O) so the selection can be unit-tested. The driver is
scripts/c01_select_poc_cohort.py.

Design (documented in cohort/COHORT_SELECTION_METHODOLOGY.md):
  * explicit strata = region x status x approval-FY band x contract-volume quartile
  * proportional allocation with the largest-remainder (Hamilton) method -> exact total
  * within each stratum, systematic sampling over units ordered by (country, sector, random key)
    -> implicit stratification by country and major sector
  * country-coverage repair: swaps *within the same stratum* only, so stratum counts and
    inclusion probabilities are unchanged
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping

import pandas as pd

FY_BANDS: list[tuple[str, int, int]] = [
    ("FY<=2014", 0, 2014),
    ("FY2015-2017", 2015, 2017),
    ("FY2018-2020", 2018, 2020),
    ("FY2021-2023", 2021, 2023),
    ("FY2024+", 2024, 9999),
]
UNKNOWN = "Unknown"
UNSPECIFIED_SECTOR = "Unspecified (not published)"


def first(value):
    """First element of a list-valued search-API field, else the value itself."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def approval_fy(fiscalyear, boardapprovaldate) -> tuple[int | None, str]:
    """Approval fiscal year and its source.

    The published v3 `fiscalyear` is used when it is a plausible year; otherwise the WB fiscal
    year (1 July - 30 June) is derived from `boardapprovaldate`.
    """
    fy = pd.to_numeric(first(fiscalyear), errors="coerce")
    if pd.notna(fy) and 1940 <= fy <= 2100:
        return int(fy), "v3.fiscalyear"
    d = pd.to_datetime(first(boardapprovaldate), errors="coerce", utc=True)
    if pd.notna(d):
        return int(d.year + (1 if d.month >= 7 else 0)), "derived.boardapprovaldate"
    return None, "missing"


def fy_band(fy: int | None) -> str:
    """Name of the fiscal-year band that contains fy, or UNKNOWN when fy is missing or outside every band."""
    if fy is None:
        return UNKNOWN
    for name, lo, hi in FY_BANDS:
        if lo <= fy <= hi:
            return name
    return UNKNOWN


def dominant_major_sector(major_sectors) -> str:
    """Major sector with the largest summed sector_percent (v3 `major_sectors`), 'FY17 - ' prefix removed.

    Ties keep the first sector in published order. Names are otherwise used as published
    (no merging of legacy and FY17 taxonomies).
    """
    best_name, best = None, -1.0
    for m in major_sectors or []:
        mm = m.get("major_sector", {}) if isinstance(m, dict) else {}
        total = 0.0
        for s in mm.get("sectors") or []:
            pct = pd.to_numeric(s.get("sector_percent"), errors="coerce")
            total += 0.0 if pd.isna(pct) else float(pct)
        if total > best:
            best, best_name = total, re.sub(r"^FY17 - ", "", mm.get("major_sector_name") or "").strip()
    return best_name or UNSPECIFIED_SECTOR


def volume_quartile(counts: pd.Series) -> pd.Series:
    """Equal-frequency quartile labels Q1 (lowest) .. Q4, ties broken by index order (deterministic)."""
    ranks = counts.rank(method="first")
    return pd.qcut(ranks, 4, labels=["Q1", "Q2", "Q3", "Q4"]).astype(str)


def largest_remainder_allocation(sizes: Mapping[str, int], total: int) -> dict[str, int]:
    """Proportional allocation n_h = total * N_h / N rounded by the largest-remainder method.

    Sums exactly to `total`; ties in the fractional part are broken by stratum key (deterministic).
    """
    population = sum(sizes.values())
    if not 0 <= total <= population:
        raise ValueError(f"total {total} outside 0..{population}")
    quotas = {k: total * n / population for k, n in sizes.items()}
    alloc = {k: math.floor(q) for k, q in quotas.items()}
    remaining = total - sum(alloc.values())
    for k in sorted(quotas, key=lambda k: (-(quotas[k] - alloc[k]), k))[:remaining]:
        alloc[k] += 1
    assert sum(alloc.values()) == total and all(alloc[k] <= sizes[k] for k in sizes)
    return alloc


def systematic_positions(n_units: int, n_select: int, start_fraction: float) -> list[int]:
    """Positions of a systematic sample of n_select from n_units ordered units.

    Interval k = n_units / n_select (>= 1, so positions are distinct); start = start_fraction * k
    with start_fraction in [0, 1).
    """
    if n_select == 0:
        return []
    if not 0 < n_select <= n_units or not 0 <= start_fraction < 1:
        raise ValueError("invalid systematic sample parameters")
    k = n_units / n_select
    return [min(n_units - 1, math.floor(start_fraction * k + i * k)) for i in range(n_select)]


def coverage_swaps(pool: pd.DataFrame, selected: set[str], fraction: float, min_pool: int = 2,
                   group: str = "country_code", stratum: str = "stratum_id", key: str = "random_key",
                   id_col: str = "project_id") -> list[dict]:
    """Within-stratum swaps so every `group` value with >= min_pool eligible units is represented.

    For each unrepresented group (sorted), its units are tried in random-key order. A unit is swapped
    in for the selected unit in the same stratum whose group is most over-represented relative to
    `fraction * pool count` (and keeps >= 1 selected unit after the swap). Mutates `selected`;
    returns the swap log.
    """
    pool_n = pool.groupby(group).size()
    log: list[dict] = []

    def cohort_n() -> pd.Series:
        return pool[pool[id_col].isin(selected)].groupby(group).size().reindex(pool_n.index, fill_value=0)

    for g in sorted(pool_n[pool_n >= min_pool].index):
        if cohort_n()[g] > 0:
            continue
        for _, cand in pool[pool[group] == g].sort_values(key).iterrows():
            cn = cohort_n()
            peers = pool[(pool[stratum] == cand[stratum]) & pool[id_col].isin(selected)].copy()
            peers["_excess"] = peers[group].map(lambda x: cn[x] - fraction * pool_n[x])
            peers = peers[peers[group].map(cn) >= 2].sort_values(["_excess", key], ascending=[False, False])
            if peers.empty:
                continue
            out = peers.iloc[0]
            selected.discard(out[id_col])
            selected.add(cand[id_col])
            log.append({"group": g, "swapped_in": cand[id_col], "swapped_out": out[id_col],
                        "swapped_out_group": out[group], "stratum_id": cand[stratum]})
            break
    return log


def share_table(pool: pd.DataFrame, cohort: pd.DataFrame, col: str) -> pd.DataFrame:
    """Pool vs cohort counts and shares (percent) for one dimension, with absolute deviation in points."""
    p = pool[col].value_counts()
    c = cohort[col].value_counts().reindex(p.index, fill_value=0)
    t = pd.DataFrame({"pool_n": p, "cohort_n": c})
    t["pool_pct"] = (100 * t.pool_n / t.pool_n.sum()).round(2)
    t["cohort_pct"] = (100 * t.cohort_n / t.cohort_n.sum()).round(2)
    t["abs_diff_pp"] = (t.pool_pct - t.cohort_pct).abs().round(2)
    return t


def split_project_ids(value) -> list[str]:
    """Project IDs from a scalar, list or comma/semicolon-separated string; non-P tokens dropped."""
    if value is None:
        return []
    items: Iterable = value if isinstance(value, (list, tuple)) else re.split(r"[,;\s]+", str(value))
    return [t.strip() for t in items if isinstance(t, str) and re.fullmatch(r"P\d{6}", t.strip())]
