"""A project's milestones and early warnings.

Statuses in a procurement plan are "as of" its revision date, so overdue items are judged at that date (never at
today's date, which would make every old plan look late). Thresholds are benchmarks from the whole corpus: how late
similar packages (same category and method) were actually signed.

Warnings (each cites the plan row, contract record or ISR it rests on):
    overdue_signing      not signed, planned signing date already passed at the plan date (within STALE_DAYS)
    stale_plan_entry     still "pending" more than STALE_DAYS after its planned signing: most likely abandoned but never
                         updated or cancelled in the plan (incomplete reporting, not a live delay)
    slipped_signing      signed later than 75% (medium) or 90% (high) of similar packages
    stalled_stage        a stage planned before the plan date has no actual date, after an earlier stage was done
    cost_overrun         contract or actual amount more than 20% above the estimate
    plan_out_of_date     the plan's signing date disagrees with the contract record by more than 30 days
    many_cancellations   the project's share of cancelled packages is in the top quarter of projects
    rating_downgraded    an ISR rating fell since the previous ISR
    rating_unsatisfactory  the latest ISR rates an indicator Moderately Unsatisfactory or worse
    high_risk            the latest ISR rates overall risk Substantial or High
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from statistics import median, quantiles
from typing import Any

from . import data
from .data import RATINGS, RISKS, Package

OPEN = {"under/pending implementation", "under review", "blank", "unclassified"}
CLOSED_BAD = {"cancelled", "terminated"}
COST_OVERRUN = 1.2
DATE_DISAGREEMENT_DAYS = 30
MIN_GROUP = 10                          # smallest group a benchmark is computed from (else a wider group is used)
STALE_DAYS = 730                        # pending this long after the planned signing: a reporting gap, not a delay


@dataclass(frozen=True)
class Benchmark:
    """Signing-slippage percentiles (in days) of one group of similar signed packages."""

    group: str
    n: int
    p50: float
    p75: float
    p90: float


@dataclass
class Warning:
    """One early warning, with the value it rests on, the benchmark used and the records it cites."""

    code: str
    severity: str                       # high | medium | low
    message: str
    activity_id: str | None = None
    reference: str | None = None
    value: float | None = None
    benchmark: dict[str, Any] | None = None
    citations: list[str] = field(default_factory=list)


@dataclass
class ProjectWatch:
    """The monitoring result for one project: summary counts, warnings, packages and ISR ratings."""

    project_id: str
    title: str | None
    country: str | None
    as_of: str | None
    summary: dict[str, Any]
    warnings: list[Warning]
    packages: list[dict[str, Any]]
    ratings: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------------------------- benchmarks
@lru_cache(maxsize=2)
def slippage_benchmarks(directory: Path = data.SHARED) -> dict[tuple[str, str], Benchmark]:
    """Signing slippage (days late) of signed packages, by category x method, by category, and overall."""
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for p in data.packages(directory):
        if p.slippage_days is not None:
            for k in ((p.category or "?", p.method or "?"), (p.category or "?", "*"), ("*", "*")):
                groups[k].append(p.slippage_days)
    out = {}
    for k, values in groups.items():
        if len(values) >= MIN_GROUP:
            # Cut points at 5% steps: index 14 is the 75th percentile and index 17 the 90th.
            q = quantiles(values, n=20)
            out[k] = Benchmark(f"{k[0]} / {k[1]}", len(values), median(values), q[14], q[17])
    return out


def benchmark_for(package: Package, directory: Path = data.SHARED) -> Benchmark:
    """The most specific benchmark with enough data: category and method, then category, then all packages."""
    table = slippage_benchmarks(directory)
    for k in ((package.category or "?", package.method or "?"), (package.category or "?", "*"), ("*", "*")):
        if k in table:
            return table[k]
    raise LookupError("no slippage benchmark")


@lru_cache(maxsize=2)
def cancellation_p75(directory: Path = data.SHARED) -> float:
    """75th percentile of the cancelled share among projects with 5+ planned packages (1.0 when there are too few)."""
    shares = []
    by_project: dict[str, list[Package]] = defaultdict(list)
    for p in data.packages(directory):
        by_project[p.project_id].append(p)
    for items in by_project.values():
        if len(items) >= 5:
            shares.append(sum(p.status in CLOSED_BAD for p in items) / len(items))
    return quantiles(shares, n=4)[2] if len(shares) >= 4 else 1.0


# --------------------------------------------------------------------------------------------------- watch
def watch(project_id: str, directory: Path = data.SHARED) -> ProjectWatch:
    """Milestones, early warnings (most severe first) and summary counts for one project."""
    packages = [p for p in data.packages(directory) if p.project_id == project_id]
    ratings = list(data.ratings(directory).get(project_id, ()))
    warnings = ([w for p in packages for w in _package_warnings(p, directory)] + _project_warnings(packages, directory)
                + _rating_warnings(ratings))
    order = {"high": 0, "medium": 1, "low": 2}
    warnings.sort(key=lambda w: (order[w.severity], -(w.value or 0)))
    as_of = max((p.plan_date for p in packages if p.plan_date), default=None)
    signed = [p for p in packages if p.signed]
    slips = [p.slippage_days for p in packages if p.slippage_days is not None]
    summary = {"packages": len(packages), "signed": len(signed), "open": sum(p.status in OPEN and not p.signed for p in packages),
               "cancelled": sum(p.status in CLOSED_BAD for p in packages),
               "overdue": sum(w.code == "overdue_signing" for w in warnings),
               "stale_plan_entries": sum(w.code == "stale_plan_entry" for w in warnings),
               "median_slippage_days": median(slips) if slips else None,
               "planned_value_usd": round(sum(p.estimated_usd or 0 for p in packages), 2),
               "warnings": {s: sum(w.severity == s for w in warnings) for s in ("high", "medium", "low")},
               "plans": sorted({p.document_id for p in packages}), "isr_reports": sorted({r.document_id for r in ratings})}
    project = data.projects(directory).get(project_id)
    return ProjectWatch(project_id, project.title if project else None, project.country if project else None,
                        as_of.isoformat() if as_of else None, summary, warnings,
                        [_package_view(p) for p in sorted(packages, key=lambda p: (p.planned_signing or dt.date.max))],
                        [asdict(r) | {"date": r.date.isoformat() if r.date else None} for r in ratings])


def watched_projects(directory: Path = data.SHARED) -> list[dict[str, Any]]:
    """The projects that have a procurement plan or an ISR, for picking one to watch."""
    counts: dict[str, int] = defaultdict(int)
    for p in data.packages(directory):
        counts[p.project_id] += 1
    known = data.projects(directory)
    ids = set(counts) | set(data.ratings(directory))
    return sorted(({"project_id": i, "title": known[i].title if i in known else None, "country": known[i].country if i in known else None,
                    "packages": counts.get(i, 0)} for i in ids), key=lambda r: (r["country"] or "", r["project_id"]))


def _package_warnings(p: Package, directory: Path) -> list[Warning]:
    """Package warnings: overdue or stale signing, slipped signing, stalled stage, cost overrun, plan out of date."""
    out, cite = [], [_plan_citation(p)]
    bench = benchmark_for(p, directory)
    if p.status in OPEN and not p.signed and p.planned_signing and p.plan_date and p.planned_signing < p.plan_date:
        late = (p.plan_date - p.planned_signing).days
        if late > STALE_DAYS:
            out.append(Warning("stale_plan_entry", "low", f"Still \"{p.status}\" {late} days after its planned signing date "
                               f"({p.planned_signing}): probably abandoned but never updated or cancelled in the plan.",
                               p.activity_id, p.reference, late, None, cite))
        else:
            out.append(Warning("overdue_signing", "high" if late > bench.p75 else "medium",
                               f"Not signed {late} days after its planned signing date ({p.planned_signing}); 75% of similar "
                               f"packages ({bench.group}) were signed within {bench.p75:.0f} days of plan.",
                               p.activity_id, p.reference, late, asdict(bench), cite))
    if p.slippage_days is not None and p.slippage_days > bench.p75:
        out.append(Warning("slipped_signing", "high" if p.slippage_days > bench.p90 else "medium",
                           f"Signed {p.slippage_days} days after plan; similar packages: median {bench.p50:.0f}, "
                           f"75th percentile {bench.p75:.0f} days.", p.activity_id, p.reference, p.slippage_days, asdict(bench),
                           cite + _contract_citation(p)))
    stalled = _stalled_stage(p)
    if stalled:
        out.append(Warning("stalled_stage", "medium", f"Stuck at \"{stalled.name}\": planned {stalled.planned}, no actual date "
                           f"by the plan date {p.plan_date}.", p.activity_id, p.reference,
                           float((p.plan_date - stalled.planned).days), None, cite))
    amount = p.contract_amount_usd or p.actual_usd
    if p.estimated_usd and amount and amount > p.estimated_usd * COST_OVERRUN:
        out.append(Warning("cost_overrun", "medium", f"Contract amount US$ {amount:,.0f} is {amount / p.estimated_usd - 1:.0%} above "
                           f"the estimate (US$ {p.estimated_usd:,.0f}).", p.activity_id, p.reference,
                           round(amount / p.estimated_usd - 1, 3), None, cite + _contract_citation(p)))
    if p.actual_signing and p.recorded_signing and abs((p.actual_signing - p.recorded_signing).days) > DATE_DISAGREEMENT_DAYS:
        out.append(Warning("plan_out_of_date", "low", f"The plan says signed {p.actual_signing}; the contract record says "
                           f"{p.recorded_signing}.", p.activity_id, p.reference,
                           float(abs((p.actual_signing - p.recorded_signing).days)), None, cite + _contract_citation(p)))
    return out


def _stalled_stage(p: Package) -> data.Stage | None:
    """The first stage past due on a live package (a stale plan entry is reported once, as stale, not as stalled)."""
    if p.signed or p.status not in OPEN or not p.plan_date:
        return None
    started = any(s.actual for s in p.stages)
    return next((s for s in p.stages if started and not s.actual and s.planned and s.planned < p.plan_date
                 and (p.plan_date - s.planned).days <= STALE_DAYS), None)


def _project_warnings(packages: list[Package], directory: Path) -> list[Warning]:
    """many_cancellations when the project's cancelled share is in the top quarter (projects with 5+ packages only)."""
    if len(packages) < 5:
        return []
    share = sum(p.status in CLOSED_BAD for p in packages) / len(packages)
    p75 = cancellation_p75(directory)
    if share > p75:
        return [Warning("many_cancellations", "medium", f"{share:.0%} of this project's planned packages were cancelled or "
                        f"terminated; the top quarter of projects starts at {p75:.0%}. Re-tendering is likely.", value=round(share, 3),
                        benchmark={"group": "projects with 5+ planned packages", "p75": round(p75, 3)},
                        citations=sorted({_plan_citation(p) for p in packages if p.status in CLOSED_BAD})[:5])]
    return []


def _rating_warnings(ratings: list[data.Rating]) -> list[Warning]:
    """Warnings from the latest ISR value of each indicator: a downgrade, an unsatisfactory rating, or high risk."""
    latest: dict[str, data.Rating] = {}
    for r in ratings:
        latest[r.indicator] = r                                              # ratings are oldest first
    out = []
    for r in latest.values():
        cite = [f"ISR {r.document_id} ({r.date})"]
        scale = RISKS if r.indicator == "Overall risk" else RATINGS
        # The text pattern accepts both scales, so a value from the wrong scale for this indicator is skipped.
        if r.current not in scale:
            continue
        if r.indicator == "Overall risk":
            if r.current in ("Substantial", "High"):
                out.append(Warning("high_risk", "high" if r.current == "High" else "medium",
                                   f"The latest ISR rates overall risk {r.current}.", citations=cite))
            continue
        if r.previous in scale and scale.index(r.current) < scale.index(r.previous):
            out.append(Warning("rating_downgraded", "high", f"{r.indicator} fell from {r.previous} to {r.current}.", citations=cite))
        if scale.index(r.current) <= RATINGS.index("Moderately Unsatisfactory"):
            out.append(Warning("rating_unsatisfactory", "high", f"{r.indicator} is rated {r.current} in the latest ISR.", citations=cite))
    return out


def _plan_citation(p: Package) -> str:
    return f"Procurement plan {p.document_id}{f', p. {p.page}' if p.page else ''} ({p.reference}, plan of {p.plan_date})"


def _contract_citation(p: Package) -> list[str]:
    return [f"Contract {p.contract_number} (World Bank contract awards)"] if p.contract_number else []


def _package_view(p: Package) -> dict[str, Any]:
    """The package as a JSON-ready dict (ISO dates, description cut to 200 characters)."""
    return {"activity_id": p.activity_id, "reference": p.reference, "description": (p.description or "")[:200], "category": p.category,
            "method": p.method, "review_type": p.review_type, "status": p.status, "estimated_usd": p.estimated_usd,
            "contract_amount_usd": p.contract_amount_usd, "planned_signing": _iso(p.planned_signing), "signed": _iso(p.signed),
            "slippage_days": p.slippage_days, "contract_number": p.contract_number, "plan": p.document_id, "page": p.page,
            "stages": [{"name": s.name, "planned": _iso(s.planned), "actual": _iso(s.actual)} for s in p.stages]}


def _iso(value: dt.date | None) -> str | None:
    return value.isoformat() if value else None
