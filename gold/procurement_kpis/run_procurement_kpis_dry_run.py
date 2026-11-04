"""READ-ONLY dry run of the proposed procurement KPI views (D-23 proposal).

Executes each KPI query (SELECT only) against the five verified Gold tables, reconciles the results with the verified
totals, checks grain uniqueness, privacy and the no-amount rule for financing, records the baseline (Bronze, Silver, Gold:
versions, rows, grants; masks; cohort) and writes the proposed CREATE VIEW DDL to the run folder WITHOUT executing it.
Nothing is created in Databricks; there is no write mode.

Usage (from procurement-intelligence/):
  python -m gold.procurement_kpis.run_procurement_kpis_dry_run
"""
from __future__ import annotations

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

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "gold" / "runs"
TOTAL_USD = Decimal("85248987799.45")
PROHIBITED = re.compile(r"supplier_(name|id|text)|description|notice_text|bid_desc|reference$|(^|_)url(_|$)|contact|email|phone|borrower_name|guarantor_name|"
                        r"team_?lead|(^|_)upi(_|$)", re.I)


def main() -> int:
    """Run every KPI query read-only, check and reconcile the results, and save the proposed DDL; 0 when all passed."""
    run_id = "gold_procurement_kpis_dryrun_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(DEFAULT_PROFILE)
    base = B.capture(spark, exclude=set())
    (run_dir / "baseline_before_write.json").write_text(json.dumps(base, indent=1))
    rep = DQReport("proposed procurement KPI views (read-only)")
    rep.equals("baseline_table_counts (Bronze, Silver, Gold)", (len(base["bronze"]), len(base["silver"]), len(base["gold"])), (17, 18, 5))
    existing = [r.tableName for r in spark.sql("SHOW TABLES IN workspace.wbg_gold").collect()]
    rep.equals("no_kpi_objects_exist (nothing created)", sorted(t for t in existing if "kpi" in t), [])

    out, schemas = {}, {}
    for name in Q.QUERIES:
        df = spark.sql(Q.bound(name))
        rows = [r.asDict() for r in df.collect()]
        out[name], schemas[name] = rows, [(f.name, f.dataType.simpleString()) for f in df.schema.fields]
        grain = Q.GRAINS[name]
        rep.equals(f"{name}:grain_unique ({', '.join(grain) or 'single row'})", len({tuple(r[c] for c in grain) for r in rows}), len(rows))
        rep.equals(f"{name}:no_prohibited_columns", [c for c, _ in schemas[name] if PROHIBITED.search(c)], [])
    rep.equals("financing_view:no_monetary_columns (no DECIMAL columns)", [c for c, t in schemas["vw_kpi_financing_instruments"] if t.startswith("decimal")], [])
    rep.equals("country_view:no_financing_amount_columns", [c for c, _ in schemas["vw_kpi_country_comparison"] if "principal" in c or "disbursed" in c], [])

    o = out["vw_kpi_overview"][0]
    rep.equals("overview:verified_totals", {k: o[k] for k in ("total_projects", "single_country_projects", "regional_projects", "contract_awards",
                                                             "distinct_contract_awards", "total_contract_award_value_usd", "procurement_notices",
                                                             "ibrd_loan_instruments", "ida_credit_instruments", "projects_with_ibrd_ida_instruments",
                                                             "economies_in_country_context")},
               {"total_projects": 2000, "single_country_projects": 1861, "regional_projects": 139, "contract_awards": 178921, "distinct_contract_awards": 178921,
                "total_contract_award_value_usd": TOTAL_USD, "procurement_notices": 243533, "ibrd_loan_instruments": 612, "ida_credit_instruments": 2073,
                "projects_with_ibrd_ida_instruments": 1597, "economies_in_country_context": 217})
    rep.equals("overview:notice_classes", [o[k] for k in ("tender_invitation_notices", "prequalification_notices", "expression_of_interest_notices",
                                                          "contract_award_announcement_notices", "general_procurement_notices")], [23780, 256, 31187, 187071, 1239])
    ca = out["vw_kpi_contract_awards"]
    rep.equals("contract_awards:reconciles_count_and_exact_value", (sum(r["contract_awards"] for r in ca), sum((r["contract_award_value_usd"] for r in ca), Decimal(0))),
               (178921, TOTAL_USD))
    rep.equals("contract_awards:review_and_supplier_relation_partitions", sum(1 for r in ca if r["prior_review_contracts"] + r["post_review_contracts"] +
                                                                              r["review_type_unknown_contracts"] != r["contract_awards"]
                                                                              or r["same_country_supplier_contracts"] + r["other_country_supplier_contracts"] +
                                                                              r["supplier_country_undetermined_contracts"] != r["contract_awards"]), 0)
    rep.equals("contract_awards:regional_rows_have_no_country", sum(1 for r in ca if r["geography_type"] != "single_country" and (r["wdi_country_code"] or r["country_name"])), 0)
    vd = {r["grouping_level"]: r for r in out["vw_kpi_contract_value_distribution"] if r["grouping_level"] == "all_contracts"}
    rep.equals("value_distribution:all_contracts_row", (vd["all_contracts"]["contract_awards"], vd["all_contracts"]["contract_award_value_usd"]), (178921, TOTAL_USD))
    for lvl in ("fiscal_year_x_category", "fiscal_year", "category"):
        rows = [r for r in out["vw_kpi_contract_value_distribution"] if r["grouping_level"] == lvl]
        rep.equals(f"value_distribution:{lvl}_partitions_all_contracts", (sum(r["contract_awards"] for r in rows), sum((r["contract_award_value_usd"] for r in rows), Decimal(0))),
                   (178921, TOTAL_USD))
    na = out["vw_kpi_notice_activity"]
    by_type: dict = {}
    for r in na:
        by_type[r["notice_type"]] = by_type.get(r["notice_type"], 0) + r["procurement_notices"]
    rep.equals("notice_activity:reconciles_by_type", by_type, {"Contract Award": 187070, "Invitation for Bids": 23780, "Request for Expression of Interest": 31187,
                                                              "General Procurement Notice": 1239, "Invitation for Prequalification": 256, "Small Contracts Award": 1})
    rw = {r["notice_type"]: r for r in out["vw_kpi_response_windows"] if r["grouping_level"] == "type"}
    rep.equals("response_windows:eligible_solicitations_only", sorted(rw), ["Invitation for Bids", "Invitation for Prequalification", "Request for Expression of Interest"])
    rep.equals("response_windows:status_counts_equal_gold_notices", {t: (r["eligible_solicitation_notices"], r["positive_window_notices"], r["over_180_day_review_notices"],
                                                                        r["no_window_notices"], r["invalid_window_notices"]) for t, r in rw.items()},
               {"Invitation for Bids": (23780, 19369, 42, 4190, 179), "Invitation for Prequalification": (256, 161, 0, 89, 6),
                "Request for Expression of Interest": (31187, 25504, 54, 5229, 400)})
    fi = out["vw_kpi_financing_instruments"]
    rep.equals("financing:instrument_counts_by_lender", {lender: sum(r["financing_instruments"] for r in fi if r["lender"] == lender) for lender in ("IBRD", "IDA")},
               {"IBRD": 612, "IDA": 2073})
    cc = out["vw_kpi_country_comparison"]
    rep.equals("country_comparison:rows_134_countries_plus_regional", (len(cc), sum(1 for r in cc if r["geography_type"] != "single_country")), (135, 1))
    rep.equals("country_comparison:reconciles", (sum(r["projects"] for r in cc), sum(r["contract_awards"] for r in cc),
                                                 sum((r["contract_award_value_usd"] or Decimal(0) for r in cc), Decimal(0)), sum(r["procurement_notices"] for r in cc),
                                                 sum(r["ibrd_loan_instruments"] for r in cc), sum(r["ida_credit_instruments"] for r in cc)),
               (2000, 178921, TOTAL_USD, 243533, 612, 2073))
    rep.equals("country_comparison:every_single_country_code_found_in_country_context", sum(1 for r in cc if r["geography_type"] == "single_country" and r["country_name"] is None), 0)
    B.check_unchanged(spark, base, rep, (17, 18, 5))
    rep.equals("no_kpi_objects_created", sorted(r.tableName for r in spark.sql("SHOW TABLES IN workspace.wbg_gold").collect() if "kpi" in r.tableName), [])

    summary = {"row_counts": {k: len(v) for k, v in out.items()}, "schemas": schemas, "overview": o,
               "response_windows_by_type": rw, "value_distribution_all": vd["all_contracts"]}
    print(f"run_id={run_id}")
    for k, v in summary["row_counts"].items():
        print(f"  {k:38s} rows={v:6d} columns={len(schemas[k])}")
    print(rep.render())
    (run_dir / "proposed_kpi_views.sql").write_text("-- PROPOSED ONLY (D-23 pending). NOT EXECUTED.\n\n" + ";\n\n".join(Q.view_ddl(n) for n in Q.QUERIES) + ";\n")
    (run_dir / "dq_report.json").write_text(json.dumps({"run_id": run_id, "summary": summary, "dq": rep.to_dict()}, indent=1, default=str))
    (run_dir / "kpi_results.json").write_text(json.dumps(out, indent=0, default=str))
    print(f"\nreports: {run_dir}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
