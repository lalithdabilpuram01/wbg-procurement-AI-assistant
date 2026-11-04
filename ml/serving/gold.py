"""Project context for mode B, from the Gold tables through an injected read-only `run_sql(sql, parameters)`.

The SQL is fixed and parameterised (no generated SQL). Project sector is not in the Gold objects the read-only
principal can see, so it comes from `project_sector.csv`: the same Silver snapshot the models were trained on.
"""
from __future__ import annotations

import csv
import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .features import InvalidRequest, ProjectContext, fiscal_year

RunSql = Callable[[str, dict[str, Any]], list[dict[str, Any]]]
GOLD = "workspace.wbg_gold"
SECTORS = Path(__file__).resolve().parent / "project_sector.csv"
TENDER_TYPES = ("Invitation for Bids", "Request for Expression of Interest", "Invitation for Prequalification")

REGION_SQL = (f"SELECT region_name, COUNT(*) AS projects FROM {GOLD}.gold_project_portfolio "
              f"WHERE wdi_country_code = :country_code GROUP BY region_name ORDER BY projects DESC LIMIT 1")
PROJECT_SQL = (f"SELECT project_id, region_name, wdi_country_code, board_approval_date, approval_fiscal_year "
               f"FROM {GOLD}.gold_project_portfolio WHERE project_id = :project_id LIMIT 1")
INCOME_SQL = (f"SELECT year, income_group_fy_starting_in_year FROM {GOLD}.gold_country_context "
              f"WHERE country_code = :country_code AND income_group_fy_starting_in_year IS NOT NULL LIMIT 100")
PRIOR_TENDERS_SQL = (f"SELECT COUNT(*) AS prior_tender_notices FROM {GOLD}.gold_procurement_notices "
                     f"WHERE project_id = :project_id AND publication_date < :tender_date "
                     f"AND notice_type IN ('{TENDER_TYPES[0]}', '{TENDER_TYPES[1]}', '{TENDER_TYPES[2]}') LIMIT 1")


def project_sector(project_id: str, path: Path = SECTORS) -> str:
    with path.open(newline="") as handle:
        return next((r["sector"] for r in csv.DictReader(handle) if r["project_id"] == project_id), "Unknown")


def _date(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) and not isinstance(value, dt.datetime) else dt.date.fromisoformat(str(value)[:10])


def country_region(country_code: str, run_sql: RunSql) -> str:
    """Region with the most portfolio projects for a country; raises InvalidRequest when the country has none."""
    rows = run_sql(REGION_SQL, {"country_code": country_code})
    if not rows:
        raise InvalidRequest(f"no projects are recorded for country {country_code}")
    return rows[0]["region_name"]


def income_history(country_code: str, run_sql: RunSql) -> dict[int, str]:
    return {int(r["year"]): r["income_group_fy_starting_in_year"] for r in run_sql(INCOME_SQL, {"country_code": country_code})}


def new_project_context(country_code: str, sector: str, approval_date: dt.date, prior_tenders: int,
                        run_sql: RunSql) -> ProjectContext:
    """Context of a project that has no ID yet: region and income from the country, the rest from the plan."""
    return ProjectContext(project_id="new", region_name=country_region(country_code, run_sql), country_code=country_code,
                          sector=sector, board_approval_date=approval_date, approval_fiscal_year=fiscal_year(approval_date),
                          income_by_year=income_history(country_code, run_sql), prior_tender_notices=prior_tenders)


def project_context(project_id: str, tender_date: dt.date, run_sql: RunSql) -> ProjectContext:
    """Look up an existing project's context in Gold (portfolio row, income history, earlier tender notices).
    Raises InvalidRequest when the project or its board approval date is missing."""
    rows = run_sql(PROJECT_SQL, {"project_id": project_id})
    if not rows:
        raise InvalidRequest(f"project {project_id} is not in the project portfolio")
    project = rows[0]
    if not project.get("board_approval_date"):
        raise InvalidRequest(f"project {project_id} has no board approval date; the models need it")
    country = project.get("wdi_country_code") or None
    income = income_history(country, run_sql) if country else {}
    prior = run_sql(PRIOR_TENDERS_SQL, {"project_id": project_id, "tender_date": tender_date.isoformat()})
    return ProjectContext(project_id=project_id, region_name=project["region_name"], country_code=country,
                          sector=project_sector(project_id), board_approval_date=_date(project["board_approval_date"]),
                          approval_fiscal_year=int(project["approval_fiscal_year"]), income_by_year=income,
                          prior_tender_notices=int(prior[0]["prior_tender_notices"]) if prior else 0)
