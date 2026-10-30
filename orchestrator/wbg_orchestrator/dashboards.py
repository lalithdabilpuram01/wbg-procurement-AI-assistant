"""The procurement KPI dashboards (app_config/dashboards.json): the app draws them from the same Gold `vw_kpi_*` views
as the Databricks AI/BI dashboards, and links to those for the full view.

The queries are fixed (no user or model SQL), read-only, and run on the SQL warehouse through the Statement Execution
API with the Databricks CLI profile. The five run in parallel and are cached for a few minutes per catalog: a cold
warehouse needs about 25 s, a warm one about 2 s. A catalog the ETL job has not built yet answers `not_built`.
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .logs import get_logger

log = get_logger("dashboards")

CONFIG = Path(__file__).resolve().parents[2] / "app_config" / "dashboards.json"
CACHE_SECONDS = 300
NOT_BUILT = "TABLE_OR_VIEW_NOT_FOUND"

RunSql = Callable[[str], list[dict[str, Any]]]


def settings(config: Path = CONFIG) -> dict:
    return json.loads(config.read_text()) if config.is_file() else {"dashboards": []}


def dashboards(config: Path = CONFIG) -> list[dict[str, str]]:
    """Each dashboard with its embed URL and its page in Databricks."""
    s = settings(config)
    if not s["dashboards"]:
        return []
    host, workspace = s["workspace_host"].rstrip("/"), s["workspace_id"]
    return [{"key": d["key"], "title": d["title"], "source": d["source"],
             "embed_url": f"{host}/embed/dashboardsv3/{d['dashboard_id']}?o={workspace}",
             "open_url": f"{host}/dashboardsv3/{d['dashboard_id']}/published?o={workspace}"}
            for d in s["dashboards"]]


def queries(gold: str) -> dict[str, str]:
    """The fixed KPI queries over one catalog's Gold views (the AI/BI dashboard's datasets, aggregated for drawing)."""
    awards = f"{gold}.vw_kpi_contract_awards"
    return {
        "overview": f"SELECT total_projects, contract_awards, total_contract_award_value_usd, procurement_notices, "
                    f"ibrd_loan_instruments, ida_credit_instruments, last_contract_signing_date, contracts_source_as_of_date, "
                    f"contracts_gold_run_id FROM {gold}.vw_kpi_overview",
        "value_by_year": f"SELECT contract_fiscal_year AS label, SUM(contract_award_value_usd) AS value, SUM(contract_awards) AS count "
                         f"FROM {awards} GROUP BY contract_fiscal_year ORDER BY contract_fiscal_year",
        "awards_by_method": f"SELECT procurement_method AS label, SUM(contract_awards) AS value FROM {awards} "
                            f"GROUP BY procurement_method ORDER BY value DESC LIMIT 8",
        "top_countries": f"SELECT country_name AS label, contract_award_value_usd AS value FROM {gold}.vw_kpi_country_comparison "
                         f"WHERE geography_type = 'single_country' ORDER BY contract_award_value_usd DESC LIMIT 10",
        "bid_windows": f"SELECT notice_type AS label, MAX(p50_window_days) AS value FROM {gold}.vw_kpi_response_windows "
                       f"WHERE grouping_level = 'type' GROUP BY notice_type ORDER BY value DESC",
    }


def warehouse_sql(profile: str, warehouse_id: str) -> RunSql:
    """One read-only statement on the SQL warehouse -> rows as dicts (numbers as numbers)."""
    from etl.cli import workspace

    client = workspace(profile)

    def run(statement: str) -> list[dict[str, Any]]:
        result = client.statement_execution.execute_statement(warehouse_id=warehouse_id, statement=statement, wait_timeout="50s")
        if result.status.error:
            raise LookupError(result.status.error.message or "query failed")
        names = [c.name for c in result.manifest.schema.columns]
        return [dict(zip(names, map(_number, row))) for row in (result.result.data_array or [])]
    return run


def _number(value: str | None) -> Any:
    if value is None:
        return None
    try:
        number = Decimal(value)
    except ArithmeticError:
        return value
    return int(number) if number == number.to_integral_value() and "." not in value else float(number)


class KpiBoard:
    """KPIs per dashboard key, cached; `warm()` fills the cache in the background at start-up."""

    def __init__(self, run_sql: RunSql, config: Path = CONFIG, clock: Callable[[], float] = time.monotonic):
        self._run, self._clock = run_sql, clock
        self._catalogs = {d["key"]: d["catalog"] for d in settings(config)["dashboards"]}
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def kpis(self, key: str) -> dict[str, Any]:
        if key not in self._catalogs:
            raise KeyError(key)
        with self._lock:
            hit = self._cache.get(key)
        if hit and self._clock() - hit[0] < CACHE_SECONDS:
            return hit[1]
        board = self._fetch(self._catalogs[key])
        with self._lock:
            self._cache[key] = (self._clock(), board)
        return board

    def _fetch(self, catalog: str) -> dict[str, Any]:
        named = queries(f"{catalog}.wbg_gold")
        try:
            with ThreadPoolExecutor(max_workers=len(named)) as pool:
                rows = dict(zip(named, pool.map(self._run, named.values())))
        except LookupError as error:
            if NOT_BUILT in str(error):
                return {"status": "not_built", "catalog": catalog}
            return {"status": "unavailable", "catalog": catalog, "detail": str(error)[:200]}
        overview = rows.pop("overview")[0] if rows["overview"] else {}
        return {"status": "ok", "catalog": catalog, "overview": overview, **rows}

    def warm(self) -> None:
        def fill() -> None:
            for key in self._catalogs:
                try:
                    self.kpis(key)
                except Exception:                                       # warming is best effort; the page retries
                    log.warning("KPI warm-up failed", extra={"dashboard": key}, exc_info=True)
        threading.Thread(target=fill, name="kpi-warm", daemon=True).start()
