"""predict_package: one procurement package (mode B) -> the three frozen models' outputs with their context."""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

from . import features
from .features import PackageRequest
from .gold import RunSql, project_context
from .models import DurationPrediction, FrozenModels, RiskPrediction


@dataclass(frozen=True)
class PackagePrediction:
    request: PackageRequest
    context: dict                         # what was looked up (region, country, sector, approval, prior tenders)
    duration: DurationPrediction
    estimated_signing_date: dt.date       # tender date + Model 1 estimate (input to Model 2)
    amendment: RiskPrediction
    rebid: RiskPrediction
    notes: tuple[str, ...]                # unknown categories, income fallbacks: always shown with the result

    def to_dict(self) -> dict:
        return asdict(self)


def predict_package(request: PackageRequest, run_sql: RunSql, models: FrozenModels) -> PackagePrediction:
    request.validate()
    context = project_context(request.project_id, request.tender_date, run_sql)
    notes: list[str] = []

    row, row_notes = features.model1_row(request, context)
    duration, unknown = models.duration(row)
    notes += row_notes + unknown
    signing = request.tender_date + dt.timedelta(days=duration.days)

    row, row_notes = features.model2_row(request, context, signing)
    amendment, unknown = models.amendment(row)
    notes += row_notes + unknown

    row, row_notes = features.model3_row(request, context)
    rebid, unknown = models.rebid(row)
    notes += row_notes + unknown

    looked_up = {"region": context.region_name, "country": context.country_code or "regional project",
                 "sector": context.sector, "board_approval_date": context.board_approval_date.isoformat(),
                 "approval_fiscal_year": context.approval_fiscal_year,
                 "prior_tender_notices": context.prior_tender_notices}
    return PackagePrediction(request, looked_up, duration, signing, amendment, rebid, tuple(dict.fromkeys(notes)))
