"""Shared helpers for Module 2 Phase 1 (data readiness and EDA). Read-only by construction.

- Every Databricks access goes through `ro_sql` / `ro_table`. `ro_sql` refuses any statement that is not a
  SELECT / WITH / SHOW / DESCRIBE, so no DDL or DML can be issued from these scripts.
- `table_versions` records the latest Delta version of each table read; scripts record it before and after
  their queries so the run report can show the data was not mutated.
- Outputs go to `ml/eda/<run_id>/` (new folder per run); nothing existing is overwritten.
- No notice text, references, supplier identifiers or contact fields are written to any output.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

ML_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ML_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "discovery"))

SNAPSHOT_DATE = dt.date(2026, 10, 20)  # DS00005 source_as_of_date (Gold #2); DS00979 retrieved 2026-10-21
CATALOG = "workspace"
TENDER_TYPES = ("Invitation for Bids", "Request for Expression of Interest", "Invitation for Prequalification")
_READ_ONLY = re.compile(r"^\s*(select|with|show|describe)\b", re.I)


def new_run_dir(stage: str) -> Path:
    run_id = f"{stage}_{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}"
    d = ML_ROOT / "eda" / run_id
    d.mkdir(parents=True, exist_ok=False)
    return d


def spark():
    from silver.common.session import get_spark

    return get_spark()


def ro_sql(s, query: str):
    if not _READ_ONLY.match(query):
        raise PermissionError("Phase 1 is read-only: only SELECT / WITH / SHOW / DESCRIBE are allowed")
    return s.sql(query)


def ro_table(s, name: str):
    return s.table(f"{CATALOG}.{name}")


def table_versions(s, names: list[str]) -> dict[str, int]:
    out = {}
    for n in names:
        h = ro_sql(s, f"DESCRIBE HISTORY {CATALOG}.{n} LIMIT 1").select("version").collect()
        out[n] = int(h[0]["version"])
    return out


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, default=str, sort_keys=False))


def pct(a: float, b: float) -> float | None:
    return round(100.0 * a / b, 2) if b else None


def wb_fiscal_year(d) -> int | None:
    if d is None or (isinstance(d, float)):
        return None
    return d.year + 1 if d.month >= 7 else d.year


# ---- chart style (light surface; single-series blue; text in ink tokens) ----
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]


def style_mpl():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.titlecolor": INK, "text.color": INK,
        "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False, "font.size": 9.5, "axes.titlesize": 11,
        "axes.titleweight": "bold", "figure.dpi": 110,
    })
    return plt


def caption(fig, population: str, kind: str = "REAL OBSERVED DATA") -> None:
    """Footer naming the population and whether values are real observations or simulations."""
    import textwrap

    width = int(fig.get_figwidth() * 17)
    fig.text(0.01, 0.005, textwrap.fill(f"{kind} · {population}", width), fontsize=7.5, color=INK2, ha="left", va="bottom")
