"""Independent verification of gold_financing_analytics (read-only).

Collects the Silver IBRD and IDA rows and recomputes every Gold column per instrument in plain Python with its own copy of
the rules (no reuse of the Spark transform or checks): identifiers, lender-specific terms, currency status, exact Decimal
amounts, negative-amount and anomaly flags, dates and placeholders, validated country context, transaction counts and
lineage. Also checks the exact schema, per-project counts against gold_project_portfolio and the unchanged
Bronze/Silver/Gold/grants/mask/cohort state.

Usage (from procurement-intelligence/):
  python -m gold.financing_analytics.verify_financing_analytics_gold --dry-run --run-id gold_financing_analytics_<ts>
  python -m gold.financing_analytics.verify_financing_analytics_gold --run-id gold_financing_analytics_<ts> --approved-dry-run <dry_run_id>
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from gold.common import baseline as B
from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "gold" / "runs"
TARGET = "workspace.wbg_gold.gold_financing_analytics"
PORTFOLIO = "workspace.wbg_gold.gold_project_portfolio"
S = "workspace.wbg_silver."
SOURCES = sorted(["ibrd_loans", "ida_credits", "projects", "wdi_country", "project_loan_transactions", "project_details_v3"])
AMOUNTS = ["original_principal_amount_usd", "cancelled_amount_usd", "undisbursed_amount_usd", "disbursed_amount_usd", "repaid_to_lender_usd",
           "due_to_lender_usd", "exchange_adjustment_usd", "borrower_obligation_usd", "sold_third_party_usd", "repaid_third_party_usd",
           "due_third_party_usd", "held_amount_usd"]
DATES = ["agreement_signing_date", "effective_date_most_recent", "closed_date_most_recent", "last_disbursement_date", "first_repayment_date", "last_repayment_date"]
SCHEMA = ([("financing_number", "string"), ("lender", "string"), ("ibrd_loan_number", "string"), ("ida_credit_number", "string"),
           ("financing_number_prefix", "string"), ("source_dataset", "string"), ("project_id", "string"), ("project_name", "string"), ("region_name", "string"),
           ("geography_type", "string"), ("wdi_country_code", "string"), ("wdi_country_iso3", "string"), ("country_context_status", "string"),
           ("financing_country_code_published", "string"), ("financing_country_name_published", "string"), ("financing_country_code_status", "string"),
           ("financing_status", "string"), ("currency_of_commitment", "string"), ("currency_status", "string"), ("loan_type", "string"),
           ("interest_rate", "decimal(12,6)"), ("guarantor_country_code", "string"), ("service_charge_rate", "decimal(12,6)"), ("has_repayment_schedule", "boolean")]
          + [(a, "decimal(22,2)") for a in AMOUNTS]
          + [("has_negative_published_amount", "boolean"), ("board_approval_date", "date"), ("board_approval_fiscal_year", "int"),
             ("board_approval_calendar_year", "int"), ("approved_before_project_approval", "boolean"), ("agreement_signing_date", "date"),
             ("agreement_signing_date_is_placeholder", "boolean"), ("effective_date_most_recent", "date"), ("effective_date_is_placeholder", "boolean"),
             ("closed_date_most_recent", "date"), ("last_disbursement_date", "date"), ("last_disbursement_after_end_of_period", "boolean"),
             ("last_disbursement_over_183_days_after_closing", "boolean"), ("first_repayment_date", "date"), ("last_repayment_date", "date"),
             ("loan_transaction_coverage", "boolean"), ("published_loan_transaction_count", "bigint"), ("source_end_of_period", "date"),
             ("financing_snapshot_semantics", "string"), ("source_row_sha256", "string"), ("bronze_source_file", "string"), ("bronze_input_file_sha256", "string"),
             ("source_population_sha256", "string"), ("source_retrieved_at", "timestamp"), ("silver_financing_run_id", "string"),
             ("silver_source_versions", "string"), ("gold_run_id", "string"), ("gold_processed_at", "timestamp"), ("transform_version", "string")])


def epoch(v) -> int | None:
    """Timezone-free instant (Databricks Connect collect() returns naive local-time datetimes)."""
    return None if v is None else int(v.timestamp())


def rows(spark, table: str) -> list[dict]:
    return [r.asDict() for r in spark.table(S + table).collect()]


def expected(spark, run_id: str) -> dict:
    P = {r["project_id"]: r for r in rows(spark, "projects")}
    iso = {r["country_code"]: r["country_wb_id"] for r in rows(spark, "wdi_country")}
    tx = Counter(r["financing_number"] for r in spark.table(S + "project_loan_transactions").select("financing_number").collect())
    cov = {r[0] for r in spark.table(S + "project_details_v3").select("project_id").collect()}
    versions = json.dumps({t: spark.sql(f"DESCRIBE HISTORY {S}{t} LIMIT 1").collect()[0].version for t in SOURCES}, separators=(",", ":"))
    out = {}
    for lender, table, key in (("IBRD", "ibrd_loans", "loan_number"), ("IDA", "ida_credits", "credit_number")):
        for r in rows(spark, table):
            k, p = r[key], P[r["project_id"]]
            single = p["geography_type"] == "single_country"
            w = p["wdi_country_code"] if single else None
            cc = r["country_code"]
            amts = {a: r[a] for a in AMOUNTS}
            lde, cde = r["last_disbursement_date"], r["closed_date_most_recent"]
            out[k] = {
                "financing_number": k, "lender": lender, "ibrd_loan_number": k if lender == "IBRD" else None, "ida_credit_number": k if lender == "IDA" else None,
                "financing_number_prefix": r["financing_number_prefix"], "source_dataset": r["source_dataset"], "project_id": r["project_id"],
                "project_name": p["project_name"], "region_name": p["region_name"], "geography_type": p["geography_type"], "wdi_country_code": w,
                "wdi_country_iso3": iso.get(w) if single else None,
                "country_context_status": ("single_country_matched" if w else "single_country_unmatched") if single else "regional_not_applicable",
                "financing_country_code_published": cc, "financing_country_name_published": r["country_name"],
                "financing_country_code_status": "regional_project" if not single else "missing_in_source" if cc is None else
                                                 "equal_to_project_wdi_code" if cc == p["wdi_country_code"] else "differs_from_project_wdi_code",
                "financing_status": r["financing_status"], "currency_of_commitment": r["currency_of_commitment"],
                "currency_status": "not_published_by_source" if lender == "IBRD" else "missing_in_source" if r["currency_of_commitment"] is None else "published",
                "loan_type": r.get("loan_type") if lender == "IBRD" else None, "interest_rate": r.get("interest_rate") if lender == "IBRD" else None,
                "guarantor_country_code": r.get("guarantor_country_code") if lender == "IBRD" else None,
                "service_charge_rate": r.get("service_charge_rate") if lender == "IDA" else None,
                "has_repayment_schedule": r.get("has_repayment_schedule") if lender == "IDA" else None,
                **amts, "has_negative_published_amount": any(v is not None and v < Decimal(0) for v in amts.values()),
                "board_approval_date": r["board_approval_date"], "board_approval_fiscal_year": r["board_approval_fiscal_year"],
                "board_approval_calendar_year": r["board_approval_calendar_year"],
                "approved_before_project_approval": None if p["board_approval_date"] is None else r["board_approval_date"] < p["board_approval_date"],
                **{d: r[d] for d in DATES}, "agreement_signing_date_is_placeholder": r["agreement_signing_date_is_placeholder"],
                "effective_date_is_placeholder": r["effective_date_is_placeholder"],
                "last_disbursement_after_end_of_period": None if lde is None else lde > r["source_end_of_period"],
                "last_disbursement_over_183_days_after_closing": None if lde is None or cde is None else (lde - cde).days > 183,
                "loan_transaction_coverage": r["project_id"] in cov, "published_loan_transaction_count": tx.get(k, 0) if r["project_id"] in cov else None,
                "source_end_of_period": r["source_end_of_period"], "financing_snapshot_semantics": r["financing_snapshot_semantics"],
                "source_row_sha256": r["source_row_sha256"], "bronze_source_file": r["bronze_source_file"], "bronze_input_file_sha256": r["bronze_input_file_sha256"],
                "source_population_sha256": r["source_population_sha256"], "source_retrieved_at": epoch(r["source_retrieved_at"]),
                "silver_financing_run_id": r["silver_run_id"], "silver_source_versions": versions, "gold_run_id": run_id,
                "transform_version": "gold_financing_analytics_v1.0.0"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--approved-dry-run")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    base = json.loads((run_dir / "baseline_before_write.json").read_text())
    spark = get_spark(args.profile)
    rep = DQReport(TARGET + (" (dry run, independent)" if args.dry_run else " (post-write, independent)"))
    if args.dry_run:
        from gold.financing_analytics import transform as T
        dfs, versions = T.load_sources(spark)
        g = T.build(dfs, versions, args.run_id)
    else:
        g = spark.table(TARGET)
    rep.equals(f"schema_exact ({len(SCHEMA)} columns, names, order, types)", [(f.name, f.dataType.simpleString()) for f in g.schema.fields], SCHEMA)
    exp = expected(spark, args.run_id)
    cols = [n for n, _ in SCHEMA if n != "gold_processed_at"]
    assert set(cols) == set(next(iter(exp.values()))), "verifier must recompute every column except gold_processed_at"
    got = {}
    for r in g.select(*cols).collect():
        x = r.asDict()
        x["source_retrieved_at"] = epoch(x["source_retrieved_at"])
        got[x["financing_number"]] = x
    rep.equals("rows_and_unique_instruments", (g.count(), len(got)), (2685, 2685))
    rep.equals("instruments_equal_silver", sorted(set(got) ^ set(exp))[:10], [])
    mism: dict = {}
    for k, e in exp.items():
        for c in cols:
            if got.get(k, {}).get(c) != e[c]:
                mism.setdefault(c, []).append(k)
    rep.equals(f"field_mismatches ({len(cols)} columns recomputed in Python; exact Decimal amounts)", {c: v[:5] for c, v in mism.items()}, {})
    sums = {(ln, a): sum((v[a] for v in got.values() if v["lender"] == ln and v[a] is not None), Decimal("0")) for ln in ("IBRD", "IDA") for a in AMOUNTS}
    esums = {(ln, a): sum((v[a] for v in exp.values() if v["lender"] == ln and v[a] is not None), Decimal("0")) for ln in ("IBRD", "IDA") for a in AMOUNTS}
    rep.equals("amount_sums_per_lender_exact (no cross-lender totals produced)", sum(1 for k in sums if sums[k] != esums[k]), 0)
    with B.COHORT.open(newline="", encoding="utf-8") as fh:
        cohort = {r["project_id"] for r in csv.DictReader(fh)}
    rep.equals("projects_in_cohort", len({v["project_id"] for v in got.values()} - cohort), 0)
    per = Counter((v["project_id"], v["lender"]) for v in got.values())
    port = spark.table(PORTFOLIO).select("project_id", "ibrd_loan_count", "ida_credit_count").collect()
    rep.equals("per_project_counts_equal_gold_project_portfolio",
               sorted(r[0] for r in port if per.get((r[0], "IBRD"), 0) != r[1] or per.get((r[0], "IDA"), 0) != r[2])[:10], [])
    rep.equals("ibrd_currency_never_assumed", sum(1 for v in got.values() if v["lender"] == "IBRD" and v["currency_of_commitment"] is not None), 0)
    texts = [" ".join(str(v[c]) for c, t in SCHEMA if t == "string" and not c.endswith("_sha256") and v.get(c) is not None) for v in got.values()]
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    rep.equals("privacy:emails_in_output (counts)", sum(bool(email.search(x)) for x in texts), 0)
    rep.equals("privacy:no_free_text_name_columns", [n for n, _ in SCHEMA if re.search(r"borrower_name|guarantor_name|description|contact|email|phone", n)], [])
    rep.equals("gold_processed_at_not_null", g.filter("gold_processed_at IS NULL").count(), 0)
    if not args.dry_run:
        props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {TARGET}").collect()}
        rep.equals("constraints_present_count", len([k for k in props if k.startswith("delta.constraints.")]), 14)
        rep.equals("run_id_property", props.get("gold.run_id"), args.run_id)
        rep.equals("not_null_columns_enforced", sum(1 for f in g.schema.fields if not f.nullable), 36)
        hist = [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {TARGET}").orderBy("version").collect()]
        rep.equals("history_first_operation_is_create", hist[0][1] if hist else None, "CREATE TABLE AS SELECT")
        rep.add("delta_version_after_write", hist[-1][0] if hist else None, "reported", True, "warn")
    else:
        rep.equals("target_still_absent", spark.catalog.tableExists(TARGET), False)
    if args.approved_dry_run:
        appr = json.loads((RUNS / args.approved_dry_run / "baseline_before_write.json").read_text())
        keys = ("bronze", "silver", "gold", "grants", "column_masks", "cohort_manifest_sha256")
        rep.equals("baseline_equals_approved_dry_run_baseline", {k: base[k] == appr[k] for k in keys}, {k: True for k in keys})
    B.check_unchanged(spark, base, rep, (17, 18, 4))
    print(rep.render())
    out = run_dir / ("dry_run_independent_verification.json" if args.dry_run else "post_write_verification.json")
    out.write_text(json.dumps({"run_id": args.run_id, "dry_run": args.dry_run, "verification": rep.to_dict(),
                               "verified_at": datetime.now(timezone.utc).isoformat()}, indent=1, default=str))
    print(f"\nreport: {out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
