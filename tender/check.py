"""The checks of a tender design: each compares the package (and the terms read from its draft) with a benchmark and
says ok / warn / fail / info, with the benchmark and its source.

    bid_window        the bid window against published response windows of the same notice type, category and method
    method_fit        the estimate against estimates of packages procured with this method
    review_type       prior or post review against packages of similar size
    packaging         cancellations of packages of similar size against the method's overall rate; lots for large packages
    turnover          required annual turnover as a multiple of the contract's annual value (heuristic threshold)
    similar_contracts required value of each similar contract as a multiple of this package (heuristic threshold)
Heuristic thresholds are named as such in the message: they are rules of thumb, not World Bank rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from monitoring import data

from . import history
from .extract import Term

Status = Literal["ok", "warn", "fail", "info"]
TURNOVER_MULTIPLE = 2.5               # heuristic: turnover above 2.5x the annual contract value narrows the field
EXPERIENCE_WARN, EXPERIENCE_FAIL = 1.0, 2.0   # heuristic: similar contracts larger than this package (x times)
CANCELLATION_GAP = 0.10               # similar-size cancellations this much above the method's rate


@dataclass(frozen=True)
class Package:
    """The package under design: category, method, review type, estimate, bid window and number of lots."""

    project_id: str
    category: str                     # Works | Goods | Consultant Services | Non-consulting Services
    method: str
    review_type: str
    estimated_usd: float | None
    bid_window_days: int
    lots: int = 1


@dataclass(frozen=True)
class WindowBenchmark:
    """Percentiles (days) of published bid windows for one notice type and peer group, from `since_fiscal_year` on."""

    notice_type: str
    scope: str                        # "category and method" or "category"
    notices: int
    p25: float
    median: float
    p75: float
    since_fiscal_year: int


@dataclass
class Check:
    """One check result: status, plain message, the benchmark it used and the sources it rests on."""

    code: str
    status: Status
    title: str
    message: str
    benchmark: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)


def run(package: Package, terms: list[Term], window: WindowBenchmark | None, directory: Path = data.SHARED) -> list[Check]:
    """Run every check that applies. Size checks need an estimate; without one a note says they were skipped.
    Checks without enough history return None and are left out."""
    found = {t.name: t for t in terms}
    peers = history.group(package.category, package.method, directory)
    checks = [_bid_window(package, found.get("bid_window_days"), window)]
    if package.estimated_usd:
        checks += [_method_fit(package, peers), _review_type(package, peers), _packaging(package, peers),
                   _turnover(package, found.get("turnover_usd"), found.get("completion_months")),
                   _similar_contracts(package, found.get("similar_contract_value_usd"), found.get("similar_contracts"))]
    else:
        checks.append(Check("estimate_missing", "info", "Size checks", "No estimated amount was given, so the method, review "
                            "type, packaging and qualification checks were not run."))
    return [c for c in checks if c is not None]


# --------------------------------------------------------------------------------------------------- checks
def _bid_window(package: Package, stated: Term | None, window: WindowBenchmark | None) -> Check:
    """Compare the bid window with published windows: below the 25th percentile warns, below half the median fails."""
    days = package.bid_window_days
    note = (f" The draft itself says {stated.value:.0f} days." if stated and round(stated.value) != days else "")
    if window is None:
        return Check("bid_window", "info", "Bid window", f"{days} days. No published windows to compare for this notice type and "
                     f"category.{note}")
    group = f"{window.notice_type} notices for {package.category}" + (f" / {package.method}" if window.scope != "category" else "")
    benchmark = {"notices": window.notices, "p25_days": window.p25, "median_days": window.median, "p75_days": window.p75,
                 "since_fiscal_year": window.since_fiscal_year, "scope": window.scope}
    source = [f"gold_procurement_notices: published response windows, FY{window.since_fiscal_year} onwards"]
    if days < window.p25:
        status: Status = "fail" if days < window.median / 2 else "warn"
        text = f"{days} days is shorter than 75% of published {group} (median {window.median:.0f}, 25th percentile {window.p25:.0f} days; {window.notices} notices)."
    else:
        status, text = "ok", f"{days} days is in line with published {group} (median {window.median:.0f}, 25th percentile {window.p25:.0f} days; {window.notices} notices)."
    return Check("bid_window", status, "Bid window", text + note, benchmark, source)


def _method_fit(package: Package, peers: history.Group) -> Check:
    """Is the estimate usual for this method? Warn above the 95th percentile of its peers, note below the 5th."""
    values = peers.estimates()
    s = history.spread(values)
    amount = package.estimated_usd
    if s is None:
        return Check("method_fit", "info", "Method", f"Too few {peers.label} packages in the plan records to compare ({len(values)}).")
    rank = s.rank(amount, values)
    benchmark = {"packages": s.n, "p5_usd": round(s.p5), "median_usd": round(s.p50), "p95_usd": round(s.p95), "percentile": rank}
    where = f"{peers.label} packages (median US$ {_money(s.p50)}, 95th percentile US$ {_money(s.p95)}; {s.n} packages)"
    if amount > s.p95:
        return Check("method_fit", "warn", "Method", f"US$ {_money(amount)} is larger than 95% of {where}: check that the method "
                     f"suits a package this size.", benchmark, [history.SOURCE])
    if amount < s.p5:
        return Check("method_fit", "info", "Method", f"US$ {_money(amount)} is smaller than 95% of {where}: a simpler method may "
                     f"be enough.", benchmark, [history.SOURCE])
    return Check("method_fit", "ok", "Method", f"US$ {_money(amount)} sits within {where}, at the {rank}th percentile.", benchmark,
                 [history.SOURCE])


def _review_type(package: Package, peers: history.Group) -> Check | None:
    """Compare the review type with how often packages of similar size were prior review."""
    similar = peers.similar_size(package.estimated_usd)
    share = history.prior_share(similar)
    if share is None:
        return None
    benchmark = {"similar_size_packages": len(similar), "prior_review_share": round(share, 3)}
    prior = package.review_type.lower().startswith("prior")
    text = f"{share:.0%} of {len(similar)} {peers.label} packages of similar size (US$ {_money(package.estimated_usd / history.BAND)}" \
           f" to {_money(package.estimated_usd * history.BAND)}) were prior review."
    # Post review where most similar packages had prior review warns; prior review where almost none did is only noted.
    status: Status = "warn" if (not prior and share >= 0.5) else "info" if (prior and share < 0.1) else "ok"
    return Check("review_type", status, "Review type", f"{package.review_type} review. {text}", benchmark, [history.SOURCE])


def _packaging(package: Package, peers: history.Group) -> Check | None:
    """Warn when similar-size packages were cancelled clearly more often than the method overall; suggest lots
    for a large single-lot package."""
    overall = history.cancelled_share(peers.packages)
    if overall is None:
        return None
    similar = peers.similar_size(package.estimated_usd)
    band = history.cancelled_share(similar)
    s = history.spread(peers.estimates())
    benchmark = {"cancelled_share": round(overall, 3), "packages": len(peers.packages)}
    text = f"{overall:.0%} of {len(peers.packages)} {peers.label} packages were cancelled"
    status: Status = "ok"
    if band is not None:
        benchmark |= {"similar_size_cancelled_share": round(band, 3), "similar_size_packages": len(similar)}
        text += f"; {band:.0%} of the {len(similar)} of similar size"
        if band >= overall + CANCELLATION_GAP:
            status = "warn"
    text += "."
    if package.lots <= 1 and s is not None and package.estimated_usd > s.p75:
        status = "warn" if status == "warn" else "info"
        text += f" The package is larger than 75% of them and has one lot: consider splitting it into lots."
    return Check("packaging", status, "Packaging", text, benchmark, [history.SOURCE])


def _turnover(package: Package, turnover: Term | None, months: Term | None) -> Check | None:
    """Required annual turnover as a multiple of the contract's annual value; warn above TURNOVER_MULTIPLE."""
    if turnover is None:
        return None
    # A contract shorter than a year counts as one year, so its annual value is never inflated.
    years = max(1.0, months.value / 12) if months else 1.0
    annual = package.estimated_usd / years
    multiple = turnover.value / annual
    benchmark = {"multiple_of_annual_value": round(multiple, 2), "heuristic_threshold": TURNOVER_MULTIPLE}
    text = (f"Required average annual turnover US$ {_money(turnover.value)} is {multiple:.1f}x the contract's annual value "
            f"(US$ {_money(annual)}{f' over {months.value:g} months' if months else ''}).")
    if multiple > TURNOVER_MULTIPLE:
        return Check("turnover", "warn", "Turnover", text + f" Above {TURNOVER_MULTIPLE}x (a rule of thumb) it may exclude capable "
                     f"bidders.", benchmark, ["Ratio to the package estimate (heuristic threshold)"])
    return Check("turnover", "ok", "Turnover", text, benchmark, ["Ratio to the package estimate (heuristic threshold)"])


def _similar_contracts(package: Package, value: Term | None, count: Term | None) -> Check | None:
    """Required value of each similar contract as a multiple of this package's estimate."""
    if value is None:
        return None
    multiple = value.value / package.estimated_usd
    benchmark = {"multiple_of_estimate": round(multiple, 2), "contracts": count.value if count else None,
                 "heuristic_thresholds": [EXPERIENCE_WARN, EXPERIENCE_FAIL]}
    text = (f"{f'{count.value:g} ' if count else ''}similar contract(s) of US$ {_money(value.value)} each: {multiple:.1f}x this "
            f"package's estimate.")
    status: Status = "fail" if multiple > EXPERIENCE_FAIL else "warn" if multiple > EXPERIENCE_WARN else "ok"
    if status != "ok":
        text += " Asking for experience larger than the contract itself narrows competition (a rule of thumb, not a World Bank rule)."
    return Check("similar_contracts", status, "Similar experience", text, benchmark,
                 ["Ratio to the package estimate (heuristic threshold)"])


def _money(value: float) -> str:
    return f"{value / 1e6:.1f}M" if value >= 1e6 else f"{value:,.0f}"
