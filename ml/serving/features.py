"""Feature rows for one package, built exactly as in training (stage2b/regression.py, stage3a/features.py).

A package assessment takes the project from the user (`user_context`), so it may have no World Bank ID yet; only the
country's region and income history are read from Gold (gold.py), as for a Planning Brief.
World Bank fiscal year F runs from 1 July F-1 to 30 June F. Income group: training used the classification of the
fiscal year starting in calendar year F-1 (gold_country_context row `year` = F-1).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

INCOME_ORDINAL = {"L": 1.0, "LM": 2.0, "UM": 3.0, "H": 4.0}
LONG_WINDOW_DAYS = 180


class InvalidRequest(ValueError):
    """The package request cannot be scored (message is safe to show)."""


@dataclass(frozen=True)
class PackageRequest:
    project_id: str | None                    # None for a new project (context comes from its profile)
    procurement_method: str
    procurement_category: str
    review_type: str
    bid_window_days: float
    tender_date: dt.date

    def validate(self) -> None:
        if self.project_id is not None and not (self.project_id.startswith("P") and self.project_id[1:].isdigit()):
            raise InvalidRequest("project_id must look like P123456")
        if self.bid_window_days < 0 or self.bid_window_days > 365:
            raise InvalidRequest("bid_window_days must be between 0 and 365")
        for name in ("procurement_method", "procurement_category", "review_type"):
            if not getattr(self, name).strip():
                raise InvalidRequest(f"{name} is required")


@dataclass(frozen=True)
class ProjectContext:
    project_id: str
    region_name: str
    country_code: str | None                  # WDI code; None = regional project
    sector: str
    board_approval_date: dt.date
    approval_fiscal_year: int
    income_by_year: dict[int, str] = field(default_factory=dict)   # gold_country_context year -> income group code
    prior_tender_notices: int = 0             # tender notices published before the tender date
    income_group: str | None = None           # given by the user (L, LM, UM, H): used for every fiscal year


def user_context(region_name: str, country_code: str | None, sector: str, board_approval_date: dt.date,
                 income_group: str | None, prior_tender_notices: int, project_id: str | None = None,
                 income_by_year: dict[int, str] | None = None) -> ProjectContext:
    """A project described by the user (its country's income history, when known, from Gold)."""
    if income_group is not None and income_group not in INCOME_ORDINAL:
        raise InvalidRequest("income_group must be one of L, LM, UM, H")
    return ProjectContext(project_id=project_id or "new project", region_name=region_name, country_code=country_code,
                          sector=sector, board_approval_date=board_approval_date,
                          approval_fiscal_year=fiscal_year(board_approval_date), prior_tender_notices=prior_tender_notices,
                          income_group=income_group, income_by_year=income_by_year or {})


def fiscal_year(day: dt.date) -> int:
    return day.year + 1 if day.month >= 7 else day.year


def income_at(context: ProjectContext, fy: int) -> tuple[float | None, str | None]:
    """Income ordinal for fiscal year `fy`; falls back to the latest earlier classification (noted)."""
    if context.country_code is None:
        return None, None
    if context.income_group is not None:
        return INCOME_ORDINAL[context.income_group], None
    if not context.income_by_year:
        return None, "no income group given; the model's median was used"
    year = fy - 1
    if year in context.income_by_year:
        return INCOME_ORDINAL.get(context.income_by_year[year]), None
    earlier = [y for y in context.income_by_year if y < year]
    if not earlier:
        return None, f"no income classification for FY{fy}; the model's median was used"
    latest = max(earlier)
    return INCOME_ORDINAL.get(context.income_by_year[latest]), f"income group for FY{fy} not published; FY{latest + 1} used"


def deadline_status(window_days: float) -> str:
    if window_days == 0:
        return "equals_publication_date_no_window"
    return "positive_window_over_180_days_review" if window_days > LONG_WINDOW_DAYS else "positive_window"


def _common(request: PackageRequest, context: ProjectContext) -> dict:
    return {"procurement_method": " ".join(request.procurement_method.split()),
            "procurement_category": request.procurement_category, "region_name": context.region_name,
            "country_grp": context.country_code or "REGIONAL", "sector": context.sector}


def model1_row(request: PackageRequest, context: ProjectContext) -> tuple[dict, list[str]]:
    """Tender duration (first tender -> contract signing)."""
    income, note = income_at(context, fiscal_year(request.tender_date))
    row = {**_common(request, context), "review_type": request.review_type or "Unknown",
           "api_window_days": float(request.bid_window_days), "project_prior_tender_notices": float(context.prior_tender_notices),
           "project_age_days_at_tender": float((request.tender_date - context.board_approval_date).days),
           "approval_fiscal_year": float(context.approval_fiscal_year), "income_ordinal": income}
    return row, [note] if note else []


def model2_row(request: PackageRequest, context: ProjectContext, signing_date: dt.date) -> tuple[dict, list[str]]:
    """Amendment risk at contract signing; the signing date is estimated from Model 1."""
    income, note = income_at(context, fiscal_year(signing_date))
    days = float((signing_date - context.board_approval_date).days)
    row = {**_common(request, context), "review_type": request.review_type or "Unknown", "days_from_approval": days,
           "signed_before_approval": float(days < 0), "approval_fiscal_year": float(context.approval_fiscal_year),
           "income_ordinal": income}
    return row, [note] if note else []


def model3_row(request: PackageRequest, context: ProjectContext) -> tuple[dict, list[str]]:
    """Re-bid risk at first tender publication."""
    income, note = income_at(context, fiscal_year(request.tender_date))
    window = float(request.bid_window_days)
    row = {**_common(request, context), "deadline_status": deadline_status(window), "window_days": window,
           "has_positive_window": 1.0, "project_age_days": float((request.tender_date - context.board_approval_date).days),
           "income_ordinal": income}
    return row, [note] if note else []
