"""Create the seven approved vw_kpi_* views (CREATE VIEW only; create-only, never OR REPLACE).

Pre-flight (always; read-only):
  * each view body equals the reviewed SQL in the approved dry run's proposed_kpi_views.sql (comments are the only change);
  * every referenced Gold column exists; each query's output schema equals the reviewed schema; grain unique;
  * subtotal levels (grouping_level) each partition the totals, so only a cross-level sum could double count;
  * fiscal-year columns agree with their source dates (World Bank FY = July-June) in the Gold tables;
  * no sensitive column names and no e-mail / phone strings in any view output;
  * none of the seven views exists yet; baseline of all Bronze, Silver and Gold tables recorded.
Creation runs only with --confirm-create and a fully passing pre-flight. If a CREATE fails, the run stops and reports
which views exist; nothing is dropped or replaced.

Usage (from procurement-intelligence/):
  python -m gold.procurement_kpis.create_kpi_views --approved-dry-run gold_procurement_kpis_dryrun_<ts>                     # pre-flight only
  python -m gold.procurement_kpis.create_kpi_views --approved-dry-run gold_procurement_kpis_dryrun_<ts> --confirm-create
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from gold.common import baseline as B
from gold.procurement_kpis import kpi_queries as Q
from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.notices.transform import EMAIL_RE, PHONE_RE

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "gold" / "runs"
TOTAL_USD = Decimal("85248987799.45")
PROHIBITED = re.compile(r"supplier_(name|id|text)|description|notice_text|bid_desc|reference$|(^|_)url(_|$)|contact|email|phone|borrower_name|guarantor_name|"
                        r"team_?lead|(^|_)upi(_|$)", re.I)
FY_CHECKS = {
    "gold_contract_analytics": ("contract_fiscal_year", "contract_signing_date"),
    "gold_procurement_notices": ("publication_fiscal_year", "publication_date"),
    "gold_financing_analytics": ("board_approval_fiscal_year", "board_approval_date"),
}


def reviewed_bodies(path: Path) -> dict[str, str]:
    """The reviewed body of each view, parsed from the approved dry run's proposed_kpi_views.sql."""
    text = path.read_text()
    out = {}
    for block in text.split(";\n\n"):
        m = re.search(r"CREATE VIEW workspace\.wbg_gold\.(vw_kpi_\w+)\nCOMMENT '[^']*'\nAS(.*)", block, re.S)
        if m:
            out[m.group(1)] = m.group(2).rstrip(";\n")
    return out


def view_exists(spark, name: str) -> bool:
    return bool(spark.sql(f"SHOW VIEWS IN {Q.VIEW_SCHEMA} LIKE '{name}'").collect())


def main() -> int:
    """Run the pre-flight checks and, with --confirm-create, create the seven views; 0 on success or pre-flight only."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--approved-dry-run", required=True)
    ap.add_argument("--confirm-create", action="store_true")
    args = ap.parse_args()
    approved = RUNS / args.approved_dry_run
    run_id = "gold_procurement_kpis_create_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(DEFAULT_PROFILE)
    base = B.capture(spark, exclude=set())
    (run_dir / "baseline_before_write.json").write_text(json.dumps(base, indent=1))
    rep = DQReport("vw_kpi_* create-only pre-flight")
    rep.equals("baseline_table_counts (Bronze, Silver, Gold)", (len(base["bronze"]), len(base["silver"]), len(base["gold"])), (17, 18, 5))
    rep.equals("views_absent_before_create", {n: view_exists(spark, n) for n in Q.QUERIES}, {n: False for n in Q.QUERIES})

    reviewed = reviewed_bodies(approved / "proposed_kpi_views.sql")
    rep.equals("ddl_bodies_equal_reviewed_sql (comments are the only change)", {n: Q.bound(n) == reviewed.get(n) for n in Q.QUERIES}, {n: True for n in Q.QUERIES})
    rep.equals("ddl_create_only (no OR REPLACE, no other statements)", [n for n in Q.QUERIES if not Q.view_ddl(n).startswith(f"CREATE VIEW {Q.VIEW_SCHEMA}.{n}\n")
                                                                         or "OR REPLACE" in Q.view_ddl(n).upper() or ";" in Q.view_ddl(n)], [])
    appr = json.loads((approved / "dq_report.json").read_text())["summary"]
    gold_cols = {t: {f.name: f.dataType.simpleString() for f in spark.table(f"{Q.VIEW_SCHEMA}.{t}").schema.fields} for t in Q.SOURCES.values()}
    results = {}
    for n in Q.QUERIES:
        df = spark.sql(Q.bound(n))
        schema = [[f.name, f.dataType.simpleString()] for f in df.schema.fields]
        rows = [r.asDict() for r in df.collect()]
        results[n] = rows
        rep.equals(f"{n}:schema_equals_reviewed", schema, [list(x) for x in appr["schemas"][n]])
        rep.equals(f"{n}:rows_equal_reviewed", len(rows), appr["row_counts"][n])
        rep.equals(f"{n}:grain_unique", len({tuple(r[c] for c in Q.GRAINS[n]) for r in rows}), len(rows))
        rep.equals(f"{n}:no_sensitive_columns", [c for c, _ in schema if PROHIBITED.search(c)], [])
        strings = [r[c] for r in rows for c, t in schema if t == "string" and r[c] is not None]
        rep.equals(f"{n}:no_email_or_phone_values", (sum(bool(re.search(EMAIL_RE, s)) for s in strings), sum(bool(re.search("(?i)" + PHONE_RE, s)) for s in strings)), (0, 0))
    referenced = {t: sorted(c for c in gold_cols[t] if any(re.search(rf"\b{c}\b", Q.QUERIES[n]) for n in Q.QUERIES)) for t in gold_cols}
    rep.add("ddl:referenced_gold_columns_exist (reported)", {t: len(v) for t, v in referenced.items()}, "reported", True, "warn")
    for t, (fy, d) in FY_CHECKS.items():
        bad = spark.table(f"{Q.VIEW_SCHEMA}.{t}").filter(f"{fy} <> CASE WHEN month({d}) >= 7 THEN year({d}) + 1 ELSE year({d}) END").count()
        rep.equals(f"fiscal_year_mapping:{t}.{fy}_matches_{d}", bad, 0)
    for n, level_col in (("vw_kpi_contract_value_distribution", "contract_awards"), ("vw_kpi_response_windows", "eligible_solicitation_notices")):
        levels: dict = {}
        for r in results[n]:
            levels[r["grouping_level"]] = levels.get(r["grouping_level"], 0) + r[level_col]
        rep.equals(f"{n}:each_grouping_level_partitions_the_total (no cross-level sums)", len(set(levels.values())), 1)
        rep.equals(f"{n}:grouping_level_never_null", sum(r["grouping_level"] is None for r in results[n]), 0)
    o = results["vw_kpi_overview"][0]
    rep.equals("headline:totals", (o["total_projects"], o["contract_awards"], o["total_contract_award_value_usd"], o["procurement_notices"],
                                   o["ibrd_loan_instruments"], o["ida_credit_instruments"]), (2000, 178921, TOTAL_USD, 243533, 612, 2073))
    rw = [r for r in results["vw_kpi_response_windows"] if r["grouping_level"] == "type"]
    rep.equals("response_windows:over_180_outliers_reported_separately", sum(r["over_180_day_review_notices"] for r in rw), 96)
    rep.equals("response_windows:statistics_exclude_over_180", max(r["max_window_days"] for r in rw if r["max_window_days"] is not None) <= 180, True)
    cc = results["vw_kpi_country_comparison"]
    rep.equals("country_comparison:income_label_fy2026", sorted({r["latest_income_fiscal_year"] for r in cc if r["geography_type"] == "single_country"}), [2026])
    print(rep.render())
    created: list[str] = []
    status = "preflight_only"
    if rep.passed and args.confirm_create:
        # One view at a time; stop at the first problem. Views already created are reported, never dropped.
        for n in Q.QUERIES:
            if view_exists(spark, n):
                status = f"stopped: {n} already exists"
                break
            try:
                spark.sql(Q.view_ddl(n))
                created.append(n)
            except Exception as e:  # noqa: BLE001
                status = f"stopped: CREATE {n} failed ({type(e).__name__})"
                break
        else:
            status = "created_all_7"
    elif not rep.passed:
        status = "preflight_failed: nothing created"
    (run_dir / "preflight_and_create.json").write_text(json.dumps({"run_id": run_id, "approved_dry_run": args.approved_dry_run, "status": status,
                                                                   "created": created, "ddl": {n: Q.view_ddl(n) for n in Q.QUERIES},
                                                                   "preflight": rep.to_dict()}, indent=1, default=str))
    print(f"\nstatus={status} created={created}\nreports: {run_dir}")
    return 0 if status in ("preflight_only", "created_all_7") else 1


if __name__ == "__main__":
    sys.exit(main())
