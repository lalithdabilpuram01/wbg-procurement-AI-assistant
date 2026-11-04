"""One procurement package -> the three frozen models' outputs, for an existing project (by ID) or a new one."""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

from . import features
from .features import PackageRequest, ProjectContext
from .gold import RunSql, project_context
from .models import DurationPrediction, FrozenModels, RiskPrediction


@dataclass(frozen=True)
class PackagePrediction:
    """All three model outputs for one package, with the looked-up context and the notes shown alongside."""
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
    """An existing project's package: the project context is looked up by its ID."""
    request.validate()
    return predict_for_context(request, project_context(request.project_id, request.tender_date, run_sql), models)


def predict_for_context(request: PackageRequest, context: ProjectContext, models: FrozenModels) -> PackagePrediction:
    """The three models for one package in a given project context (existing or new project)."""
    request.validate()
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
