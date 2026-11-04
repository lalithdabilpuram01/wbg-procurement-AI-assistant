"""Run the gold_financing_analytics transformation (Databricks Connect, Serverless).

Default is a DRY RUN: records the baseline (all Bronze, Silver and Gold tables: versions, row counts, grants; column masks;
cohort), builds the instrument-grain DataFrame, runs all checks (incl. field fidelity to Silver, exact decimal sums per
lender, reconciliation with gold_project_portfolio, the Delta CHECK constraints and NOT NULL columns evaluated on the data
and a personal-data scan), prints the schema and saves reports. Nothing is written to Databricks.

Writing requires --mode write --confirm-create, all "error" checks passing and the target not existing (create-only).

Usage (from procurement-intelligence/):
  python -m gold.financing_analytics.run_financing_analytics_gold
  python -m gold.financing_analytics.run_financing_analytics_gold --mode write --confirm-create
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gold.common import baseline as B
from gold.financing_analytics import transform as T
from gold.financing_analytics.checks import check
from silver.common.dq import DQReport
from silver.common.pipeline import CATALOG, runs_dir
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.common.write import create_table

REPO = Path(__file__).resolve().parents[2]
RUNS = runs_dir(REPO / "gold" / "runs")
PORTFOLIO = f"{CATALOG}.wbg_gold.gold_project_portfolio"
NOT_NULL = ["financing_number", "lender", "financing_number_prefix", "source_dataset", "project_id", "geography_type", "country_context_status",
            "financing_country_code_status", "financing_status", "currency_status", *T.AMOUNTS, "has_negative_published_amount", "board_approval_date",
            "board_approval_fiscal_year", "board_approval_calendar_year", "agreement_signing_date_is_placeholder", "effective_date_is_placeholder",
            "loan_transaction_coverage", "source_end_of_period", "financing_snapshot_semantics", "source_row_sha256", "source_retrieved_at",
            "silver_source_versions", "gold_run_id", "transform_version"]
CONSTRAINTS = {
    "financing_number_format": "financing_number RLIKE '^(IBRD|IDA)[A-Z]?[0-9]{4,5}$'",
    "lender_identifiers_consistent": "(lender = 'IBRD' AND ibrd_loan_number = financing_number AND ida_credit_number IS NULL AND source_dataset = 'DS00047') OR "
                                    "(lender = 'IDA' AND ida_credit_number = financing_number AND ibrd_loan_number IS NULL AND source_dataset = 'DS00001')",
    "prefix_matches_number": "financing_number LIKE concat(financing_number_prefix, '%') AND financing_number_prefix LIKE concat(lender, '%')",
    "project_id_format": "project_id RLIKE '^P[0-9]{6}$'",
    "lender_specific_terms": "(lender = 'IBRD' AND service_charge_rate IS NULL AND has_repayment_schedule IS NULL) OR "
                             "(lender = 'IDA' AND loan_type IS NULL AND interest_rate IS NULL AND guarantor_country_code IS NULL AND has_repayment_schedule IS NOT NULL)",
    "currency_semantics": "(lender = 'IBRD' AND currency_of_commitment IS NULL AND currency_status = 'not_published_by_source') OR "
                          "(lender = 'IDA' AND currency_status = CASE WHEN currency_of_commitment IS NULL THEN 'missing_in_source' ELSE 'published' END)",
    "non_negative_core_amounts": "original_principal_amount_usd >= 0 AND cancelled_amount_usd >= 0 AND disbursed_amount_usd >= 0 AND repaid_to_lender_usd >= 0 "
                                 "AND sold_third_party_usd >= 0 AND repaid_third_party_usd >= 0 AND due_third_party_usd >= 0",
    "negative_flag_consistent": "has_negative_published_amount = (" + " OR ".join(f"{a} < 0" for a in T.AMOUNTS) + ")",
    "placeholder_consistent": "(NOT agreement_signing_date_is_placeholder OR agreement_signing_date IS NULL) AND (NOT effective_date_is_placeholder OR "
                              "effective_date_most_recent IS NULL)",
    "approval_years_consistent": "board_approval_calendar_year = year(board_approval_date) AND board_approval_fiscal_year = CASE WHEN month(board_approval_date) >= 7 "
                                 "THEN year(board_approval_date) + 1 ELSE year(board_approval_date) END",
    "country_semantics": "(geography_type = 'single_country' AND country_context_status IN ('single_country_matched', 'single_country_unmatched')) OR "
                         "(geography_type <> 'single_country' AND country_context_status = 'regional_not_applicable' AND wdi_country_code IS NULL "
                         "AND wdi_country_iso3 IS NULL AND financing_country_code_status = 'regional_project')",
    "transaction_count_semantics": "(NOT loan_transaction_coverage AND published_loan_transaction_count IS NULL) OR "
                                   "(loan_transaction_coverage AND published_loan_transaction_count >= 0)",
    "snapshot_values": "source_end_of_period = DATE'2026-09-30' AND financing_snapshot_semantics = 'latest_monthly_snapshot_as_of_end_of_period'",
    "status_valid": "financing_status IN ('Approved', 'Signed', 'Effective', 'Disbursing', 'Disbursing&Repaying', 'Repaying', 'Fully Disbursed', "
                    "'Fully Repaid', 'Fully Cancelled', 'Terminated')",
}
SNAP = " Snapshot value as of 2026-09-30: not known at approval; never a historical ML feature."
AMT = (" Published US$ value exactly as in the source: US$ equivalents NOT calculated on one consistent exchange-rate basis for non-USD commitments; "
       "no conversion.")
COMMENTS: dict[str, Any] = {
    "table": "Financing-instrument fact: one row per IBRD loan (DS00047, 612) or IDA credit/grant (DS00001, 2,073) of the 2,000 cohort projects, World Bank "
             "monthly statements as of 2026-09-30. Published amounts are kept exactly; IBRD currency is not published (never assumed USD). No currency "
             "conversions, cross-lender totals, ratios or reconciliations; financing commitments are not contract awards. Loan transactions appear only as a "
             "count (transaction-level analysis belongs in a separate table). Status, balances, current terms and transaction counts are descriptive snapshot "
             "measures, not historical ML features. Borrower and guarantor names excluded",
    "columns": {
        "financing_number": "Instrument number (primary key; unique across IBRD and IDA)",
        "lender": "IBRD or IDA",
        "ibrd_loan_number": "IBRD source identifier (DS00047 Loan Number); NULL for IDA",
        "ida_credit_number": "IDA source identifier (DS00001 Credit Number); NULL for IBRD",
        "financing_number_prefix": "Published prefix (IBRD, IBRDK, IDA, IDAD, IDAE, IDAH). Not classified as grant/credit",
        "wdi_country_code": "Project's validated WDI country code; NULL for regional projects",
        "financing_country_code_published": "Country code as published in DS00047/DS00001 (legacy codes YF/ZR/TP/RY and regional codes kept); never used for joins",
        "financing_status": "Instrument status as published." + SNAP,
        "currency_of_commitment": "IDA currency of commitment as published (XDR, EUR, USD, JPY, GBP); NULL for IBRD (not published by the source)",
        "currency_status": "published / not_published_by_source (all IBRD) / missing_in_source",
        "interest_rate": "IBRD interest rate as published; 0.0 on FSL loans has unverified meaning (do not read as interest-free); current terms." + SNAP,
        "service_charge_rate": "IDA service charge rate as published; current terms." + SNAP,
        "has_repayment_schedule": "IDA only: both repayment dates published (observable fact; not a grant classification)",
        "original_principal_amount_usd": "Original principal (US$) as published." + AMT,
        "cancelled_amount_usd": "Cancelled amount (US$)." + AMT + SNAP,
        "undisbursed_amount_usd": "Undisbursed amount (US$); small negatives published." + AMT + SNAP,
        "disbursed_amount_usd": "Disbursed amount (US$)." + AMT + SNAP,
        "exchange_adjustment_usd": "Exchange adjustment (US$); negatives published." + AMT + SNAP,
        "held_amount_usd": "Loans/credits held (US$); negatives published." + AMT + SNAP,
        "has_negative_published_amount": "Any published amount below zero (kept as published)",
        "approved_before_project_approval": "Instrument board approval precedes the project's board approval (published dates; kept)",
        "last_disbursement_after_end_of_period": "Last disbursement date later than the 2026-09-30 statement (kept; no inferred meaning)",
        "last_disbursement_over_183_days_after_closing": "Last disbursement more than 183 days after the most recent closing date (kept; no inferred meaning)",
        "agreement_signing_date": "Agreement signing date; NULL where the source published the 01/01/1900 placeholder",
        "effective_date_most_recent": "Most recent effective date; NULL for the 01/01/1900 placeholder." + SNAP,
        "closed_date_most_recent": "Most recent closing date (may have been extended)." + SNAP,
        "loan_transaction_coverage": "Project has v3 loan_transactions detail (184 Phase 1 projects only)",
        "published_loan_transaction_count": "Published loan transactions for this instrument (count only; amounts not joined, currency unverified); NULL outside "
                                            "coverage. Descriptive snapshot measure, not a historical ML feature",
        "silver_source_versions": "JSON {silver table: Delta version} read by this run",
    },
}


def main() -> int:
    """Dry run by default: build and check gold_financing_analytics, save the reports; write mode creates the table."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry-run", "write"], default="dry-run")
    ap.add_argument("--confirm-create", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    if args.mode == "write" and not args.confirm_create:
        ap.error("--mode write requires --confirm-create")
    run_id = "gold_financing_analytics_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(args.profile)
    target_exists = spark.catalog.tableExists(T.TARGET)
    # Version, row count and grants of every other table before the write, so the verify script can prove no change.
    base = B.capture(spark, exclude={T.TARGET})
    base["target_exists"] = target_exists
    (run_dir / "baseline_before_write.json").write_text(json.dumps(base, indent=1))

    rep = DQReport(T.TARGET)
    rep.equals("target_absent_before_write", target_exists, False)
    rep.equals("baseline_table_counts (Bronze, Silver, Gold)", (len(base["bronze"]), len(base["silver"]), len(base["gold"])), (17, 18, 4))
    with B.COHORT.open(newline="", encoding="utf-8") as fh:
        cohort_ids = {r["project_id"] for r in csv.DictReader(fh)}
    dfs, versions = T.load_sources(spark)
    g = T.build(dfs, versions, run_id)
    summary = check(g, dfs, spark.table(PORTFOLIO), cohort_ids, rep)
    rep.equals("delta_check_constraints_hold_before_write", {c: g.filter(f"NOT coalesce(({e}), false)").count() for c, e in CONSTRAINTS.items()}, {c: 0 for c in CONSTRAINTS})
    rep.equals("not_null_columns_have_no_nulls", {c: g.filter(f"`{c}` IS NULL").count() for c in NOT_NULL}, {c: 0 for c in NOT_NULL})
    summary["silver_source_versions"] = json.loads(versions)
    summary["null_counts_nullable_columns"] = {c: g.filter(f"`{c}` IS NULL").count() for c in g.columns if c not in NOT_NULL}
    summary["distributions"] = {c: {f"{r[0]}|{r[1]}": r[2] for r in g.groupBy("lender", c).count().collect()} for c in
                                ("financing_status", "currency_status", "country_context_status", "financing_country_code_status", "financing_number_prefix",
                                 "loan_transaction_coverage", "has_negative_published_amount")}
    print(f"run_id={run_id} mode={args.mode} transform={T.TRANSFORM_VERSION}")
    print(f"\n== proposed schema: {T.TARGET} ({len(g.columns)} columns)")
    for fld in g.schema.fields:
        print(f"   {fld.name:46s} {fld.dataType.simpleString()}")
    print("\n== summary\n" + json.dumps(summary, indent=1, default=str))
    print(rep.render())
    (run_dir / "dq_report.json").write_text(json.dumps({"run_id": run_id, "mode": args.mode, "transform_version": T.TRANSFORM_VERSION, "target": T.TARGET,
                                                        "schema": [(f.name, f.dataType.simpleString()) for f in g.schema.fields], "summary": summary,
                                                        "constraints": CONSTRAINTS, "not_null": NOT_NULL, "dq": rep.to_dict()}, indent=1, default=str))
    print(f"\nreports: {run_dir}")
    if not rep.passed:
        print("DQ FAILED: nothing written")
        return 1
    if args.mode == "dry-run":
        print("DRY RUN: nothing written to Databricks")
        return 0
    create_table(spark, g, T.TARGET, {"gold.run_id": run_id, "gold.transform_version": T.TRANSFORM_VERSION, "gold.silver_source_versions": versions,
                                       "gold.model": "financing-instrument fact, monthly statement 2026-09-30"}, COMMENTS["table"], COMMENTS["columns"], NOT_NULL, CONSTRAINTS)
    print(f"written {T.TARGET}: {spark.table(T.TARGET).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
