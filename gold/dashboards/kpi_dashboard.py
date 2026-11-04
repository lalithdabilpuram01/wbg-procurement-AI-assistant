"""The procurement KPI dashboard (Databricks AI/BI) over the seven vw_kpi_* views of a catalog's wbg_gold schema.

    uv run python -m gold.dashboards.kpi_dashboard                    workspace.wbg_gold (the verified snapshot)
                                                                      -> gold/dashboards/wbg_procurement_kpis.lvdash.json
    uv run python -m gold.dashboards.kpi_dashboard --catalog wbg_etl  wbg_etl.wbg_gold (rebuilt by the ETL job on each upload)
                                                                      -> gold/dashboards/wbg_procurement_kpis_wbg_etl.lvdash.json

Read-only: every dataset is a SELECT on one approved Gold view. Views whose rows are subtotals at several levels
(grouping_level) are always filtered to exactly one level, as the views require; nothing is summed across levels.
"""
from __future__ import annotations

import json
from pathlib import Path

GOLD = "workspace.wbg_gold"
OUT = Path(__file__).with_name("wbg_procurement_kpis.lvdash.json")


def datasets(gold: str) -> dict[str, tuple[str, str]]:
    """Dashboard datasets: {name: (display name, SELECT on one Gold view)}; subtotal views are pinned to one level."""
    return {
        "overview": ("Overview", f"SELECT * FROM {gold}.vw_kpi_overview"),
        "awards": ("Contract awards", f"SELECT * FROM {gold}.vw_kpi_contract_awards"),
        "notices": ("Notice activity", f"SELECT * FROM {gold}.vw_kpi_notice_activity"),
        "windows": ("Bid windows by notice type",
                    f"SELECT * FROM {gold}.vw_kpi_response_windows WHERE grouping_level = 'type'"),
        "countries": ("Top countries by contract value",
                      f"SELECT * FROM {gold}.vw_kpi_country_comparison WHERE geography_type = 'single_country' "
                      f"ORDER BY contract_award_value_usd DESC LIMIT 15"),
        "income": ("Projects by income group",
                   f"SELECT coalesce(latest_income_group, 'Not classified') AS income_group, projects "
                   f"FROM {gold}.vw_kpi_country_comparison WHERE geography_type = 'single_country'"),
        "sizes": ("Contract size by category",
                  f"SELECT * FROM {gold}.vw_kpi_contract_value_distribution WHERE grouping_level = 'category'"),
        "financing": ("Financing instruments", f"SELECT * FROM {gold}.vw_kpi_financing_instruments"),
    }


# ------------------------------------------------------------------------------------------------- widgets
def field(name: str, expression: str | None = None) -> dict:
    return {"name": name, "expression": expression or f"`{name}`"}


def counter(name: str, title: str, column: str, x: int, y: int) -> dict:
    """A single-number tile showing one column of the overview dataset."""
    return {"widget": {"name": name,
                       "queries": [{"name": "main_query", "query": {"datasetName": "overview", "fields": [field(column)],
                                                                     "disaggregated": True}}],
                       "spec": {"version": 2, "widgetType": "counter",
                                "encodings": {"value": {"fieldName": column, "displayName": title}},
                                "frame": {"showTitle": True, "title": title}}},
            "position": {"x": x, "y": y, "width": 1, "height": 2}}


def chart(name: str, kind: str, title: str, dataset: str, x_col: str, x_title: str, y_col: str, y_title: str,
          position: tuple[int, int, int, int], color: str | None = None, aggregate: str = "SUM",
          categorical_x: bool = True, sort_by_value: bool = False) -> dict:
    """A bar or line chart: x by the aggregate of y, optionally split by colour."""
    y_name = f"{aggregate.lower()}({y_col})"
    fields = [field(x_col), field(y_name, f"{aggregate}(`{y_col}`)")] + ([field(color)] if color else [])
    x_scale = {"type": "categorical" if categorical_x else "quantitative"}
    if sort_by_value:
        x_scale["sort"] = {"by": "y-reversed"}
    encodings = {"x": {"fieldName": x_col, "scale": x_scale, "displayName": x_title},
                 "y": {"fieldName": y_name, "scale": {"type": "quantitative"}, "displayName": y_title}}
    if color:
        encodings["color"] = {"fieldName": color, "scale": {"type": "categorical"}, "displayName": color.replace("_", " ")}
    x, y, w, h = position
    return {"widget": {"name": name,
                       "queries": [{"name": "main_query", "query": {"datasetName": dataset, "fields": fields, "disaggregated": False}}],
                       "spec": {"version": 3, "widgetType": kind, "encodings": encodings,
                                "frame": {"showTitle": True, "title": title}}},
            "position": {"x": x, "y": y, "width": w, "height": h}}


def text(name: str, lines: list[str], y: int, height: int = 2) -> dict:
    return {"widget": {"name": name, "multilineTextboxSpec": {"lines": lines}}, "position": {"x": 0, "y": y, "width": 6, "height": height}}


# --------------------------------------------------------------------------------------------------- pages
def overview_page(gold: str) -> dict:
    """The Overview page: title text, six headline counters and four charts."""
    counters = [("projects", "Projects", "total_projects"), ("awards_n", "Contract awards", "contract_awards"),
                ("value", "Contract value (USD)", "total_contract_award_value_usd"),
                ("notices_n", "Procurement notices", "procurement_notices"), ("ibrd", "IBRD loans", "ibrd_loan_instruments"),
                ("ida", "IDA credits", "ida_credit_instruments")]
    return {"name": "overview", "displayName": "Overview", "pageType": "PAGE_TYPE_CANVAS", "layout": [
        text("title", ["## WBG procurement KPIs",
                       f"2,000-project PoC cohort. Source: the Gold layer (`{gold}`), read-only. "
                       "Contract values are exact sums of contract awards; bid windows are published windows, not tender durations."], 0),
        *[counter(n, t, c, i, 2) for i, (n, t, c) in enumerate(counters)],
        chart("value_by_year", "bar", "Contract value by fiscal year and category", "awards", "contract_fiscal_year", "Fiscal year",
              "contract_award_value_usd", "Contract value (USD)", (0, 4, 3, 6), color="procurement_category"),
        chart("awards_by_method", "bar", "Contract awards by procurement method", "awards", "procurement_method", "Method",
              "contract_awards", "Contract awards", (3, 4, 3, 6), sort_by_value=True),
        chart("notices_by_year", "line", "Procurement notices by fiscal year", "notices", "publication_fiscal_year", "Fiscal year",
              "procurement_notices", "Notices", (0, 10, 3, 6), color="notice_class"),
        chart("windows_by_type", "bar", "Median published bid window (days) by notice type", "windows", "notice_type", "Notice type",
              "p50_window_days", "Median window (days)", (3, 10, 3, 6), aggregate="MAX", sort_by_value=True),
    ]}


def countries_page() -> dict:
    """The Countries page: top countries by contract value, projects by income group, median contract size."""
    return {"name": "countries", "displayName": "Countries", "pageType": "PAGE_TYPE_CANVAS", "layout": [
        text("countries_title", ["## Countries", "Single-country projects only; regional projects are kept as a separate geography."], 0),
        chart("top_countries", "bar", "Top 15 countries by contract value (USD)", "countries", "country_name", "Country",
              "contract_award_value_usd", "Contract value (USD)", (0, 2, 6, 7), sort_by_value=True),
        chart("income_groups", "bar", "Projects by latest income group", "income", "income_group", "Income group",
              "projects", "Projects", (0, 9, 3, 6), sort_by_value=True),
        chart("median_size", "bar", "Median contract size by category (USD)", "sizes", "procurement_category", "Category",
              "p50_contract_value_usd", "Median contract value (USD)", (3, 9, 3, 6), aggregate="MAX", sort_by_value=True),
    ]}


def financing_page() -> dict:
    """The Financing page: IBRD and IDA instruments by Board approval fiscal year."""
    return {"name": "financing", "displayName": "Financing", "pageType": "PAGE_TYPE_CANVAS", "layout": [
        text("financing_title", ["## Financing instruments", "IBRD loans and IDA credits of the cohort's projects (counts only)."], 0),
        chart("instruments_by_year", "bar", "Instruments by Board approval fiscal year", "financing", "board_approval_fiscal_year",
              "Fiscal year", "financing_instruments", "Instruments", (0, 2, 6, 7), color="lender"),
    ]}


def build(gold: str = GOLD) -> dict:
    return {"datasets": [{"name": name, "displayName": title, "queryLines": [sql]} for name, (title, sql) in datasets(gold).items()],
            "pages": [overview_page(gold), countries_page(), financing_page()]}


def output_path(catalog: str) -> Path:
    return OUT if catalog == "workspace" else OUT.with_name(f"wbg_procurement_kpis_{catalog}.lvdash.json")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Write the KPI dashboard definition for one catalog's Gold views.")
    parser.add_argument("--catalog", default="workspace")
    catalog = parser.parse_args().catalog
    out = output_path(catalog)
    out.write_text(json.dumps(build(f"{catalog}.wbg_gold"), indent=1))
    print(f"written {out}")
