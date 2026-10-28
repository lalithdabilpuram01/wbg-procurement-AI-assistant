"""What similar packages did: estimates, review types and cancellations of procurement-plan packages (pilot_v3 plans
of the 35 pilot projects, latest revision of each plan), by category and canonical method."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median, quantiles

from monitoring import data, normalise

FAMILY = {"Works": "WORK", "Goods": "GOOD", "Consultant Services": "CONS", "Non-consulting Services": "NONC"}
CLOSED_BAD = {"cancelled", "terminated"}
MIN_PACKAGES = 20                     # smallest group a benchmark is shown for
BAND = 2.0                            # "similar size": between half and twice the estimate
SOURCE = "Procurement-plan records of the 35 pilot projects (pilot_v3, latest plan revisions)"


@dataclass(frozen=True)
class Group:
    category: str
    method: str
    packages: tuple[data.Package, ...]

    @property
    def label(self) -> str:
        return f"{self.category} / {self.method}"

    def estimates(self) -> list[float]:
        return sorted(p.estimated_usd for p in self.packages if p.estimated_usd)

    def similar_size(self, amount: float) -> list[data.Package]:
        return [p for p in self.packages if p.estimated_usd and amount / BAND <= p.estimated_usd <= amount * BAND]


@dataclass(frozen=True)
class Spread:
    n: int
    p5: float
    p25: float
    p50: float
    p75: float
    p95: float

    def rank(self, value: float, values: list[float]) -> int:
        """Percent of the group at or below the value."""
        return round(100 * sum(v <= value for v in values) / len(values))


def group(category: str, method: str, directory: Path = data.SHARED) -> Group:
    family, canonical = FAMILY.get(category, category), normalise.method(method) or method
    return Group(category, canonical, tuple(p for p in data.packages(directory) if p.category == family and p.method == canonical))


def spread(values: list[float]) -> Spread | None:
    if len(values) < MIN_PACKAGES:
        return None
    q = quantiles(values, n=20)
    return Spread(len(values), q[0], q[4], median(values), q[14], q[18])


def prior_share(packages: list[data.Package]) -> float | None:
    reviewed = [p for p in packages if p.review_type]
    if len(reviewed) < MIN_PACKAGES:
        return None
    return sum(p.review_type.lower().startswith("prior") for p in reviewed) / len(reviewed)


def cancelled_share(packages: list[data.Package] | tuple[data.Package, ...]) -> float | None:
    return sum(p.status in CLOSED_BAD for p in packages) / len(packages) if len(packages) >= MIN_PACKAGES else None
