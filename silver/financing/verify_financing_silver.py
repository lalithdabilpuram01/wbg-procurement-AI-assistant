"""Independent post-write verification of wbg_silver.ibrd_loans and wbg_silver.ida_credits (read-only).

Re-reads both Volume CSVs (bytes via binaryFile), parses them with pandas, and recomputes every Silver column in plain
Python with its own copy of the rules (no reuse of the Spark transform): keys, prefixes, exact Decimal amounts with
signs, rates, dates with the 01/01/1900 placeholder -> NULL + flag, repayment-schedule flag, years, snapshot date and all
lineage fields. Also checks the exact schemas, Delta constraints / NOT NULL / run_id, the known anomalies against the
approved dry run, and that all Bronze and existing Silver tables, row counts, column masks and the cohort are unchanged.

Usage (from procurement-intelligence/):
  python -m silver.financing.verify_financing_silver --run-id financing_<timestamp> --approved-dry-run financing_<timestamp>
"""
from __future__ import annotations

from app_config.paths import BRONZE_INPUTS_MANIFEST, COHORT_MANIFEST, SOURCE_PULLS_CACHE_MANIFEST
import argparse
import hashlib
import io
import json
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "silver" / "runs"
COHORT = COHORT_MANIFEST
MANIFEST = BRONZE_INPUTS_MANIFEST
CACHE_MANIFEST = SOURCE_PULLS_CACHE_MANIFEST
SEP = "\x1f"
PLACEHOLDER = "01/01/1900"
CFG = {
    "ibrd": {"table": "workspace.wbg_silver.ibrd_loans", "bronze": "workspace.wbg_bronze.bronze_ibrd_loans_ds00047", "short": "bronze_ibrd_loans_ds00047",
             "dataset": "DS00047", "lender": "IBRD", "key_src": "Loan Number", "key": "loan_number", "status": "Loan Status", "org": "IBRD", "held": "Loans Held (US$)",
             "oblig": "Borrower's Obligation (US$)", "rows": 612,
             "constraints": ["approval_years_consistent", "key_format", "lender_value", "loan_type_valid", "non_negative_amounts", "placeholder_consistent",
                             "prefix_matches_key", "project_id_format", "status_valid"]},
    "ida": {"table": "workspace.wbg_silver.ida_credits", "bronze": "workspace.wbg_bronze.bronze_ida_credits_ds00001", "short": "bronze_ida_credits_ds00001",
            "dataset": "DS00001", "lender": "IDA", "key_src": "Credit Number", "key": "credit_number", "status": "Credit Status", "org": "IDA", "held": "Credits Held (US$)",
            "oblig": "Borrowers Obligation (US$)", "rows": 2073,
            "constraints": ["approval_years_consistent", "currency_valid", "key_format", "lender_value", "non_negative_amounts", "placeholder_consistent",
                            "prefix_matches_key", "project_id_format", "repayment_schedule_consistent", "status_valid"]},
}
AMOUNTS = ["original_principal_amount_usd", "cancelled_amount_usd", "undisbursed_amount_usd", "disbursed_amount_usd", "repaid_to_lender_usd",
           "due_to_lender_usd", "exchange_adjustment_usd", "borrower_obligation_usd", "sold_third_party_usd", "repaid_third_party_usd",
           "due_third_party_usd", "held_amount_usd"]
DATES = ["first_repayment_date", "last_repayment_date", "agreement_signing_date", "board_approval_date", "effective_date_most_recent",
         "closed_date_most_recent", "last_disbursement_date"]
LINEAGE = ["source_dataset", "source_row_sha256", "bronze_table", "bronze_source_file", "bronze_cohort_id", "bronze_ingested_at",
           "bronze_input_file_sha256", "source_population_sha256", "source_retrieved_at", "silver_run_id", "silver_processed_at", "transform_version"]


def schema(source: str) -> list[tuple[str, str]]:
    """The exact expected schema (name, type) of one financing table, in column order."""
    key = str(CFG[source]["key"])
    head = [(key, "string"), ("financing_number_prefix", "string"), ("lender", "string"), ("project_id", "string"), ("project_name_source", "string"),
            ("region_name_source", "string"), ("country_code", "string"), ("country_name", "string"), ("borrower_name", "string")]
    terms = ([("guarantor_country_code", "string"), ("guarantor_name", "string"), ("loan_type", "string"), ("interest_rate", "decimal(12,6)")]
             if source == "ibrd" else [("service_charge_rate", "decimal(12,6)"), ("has_repayment_schedule", "boolean")])
    dates = [("first_repayment_date", "date"), ("last_repayment_date", "date"), ("agreement_signing_date", "date"), ("agreement_signing_date_is_placeholder", "boolean"),
             ("board_approval_date", "date"), ("effective_date_most_recent", "date"), ("effective_date_is_placeholder", "boolean"),
             ("closed_date_most_recent", "date"), ("last_disbursement_date", "date")]
    tail = [("board_approval_fiscal_year", "int"), ("board_approval_calendar_year", "int"), ("source_end_of_period", "date"), ("financing_snapshot_semantics", "string")]
    lin = [(c, "timestamp" if c in ("bronze_ingested_at", "source_retrieved_at", "silver_processed_at") else "string") for c in LINEAGE]
    return head + terms + [("financing_status", "string"), ("currency_of_commitment", "string")] + [(a, "decimal(22,2)") for a in AMOUNTS] + dates + tail + lin


def d(s: str):
    return datetime.strptime(s, "%m/%d/%Y").date() if s and s != PLACEHOLDER else None


def epoch(v) -> int | None:
    """Timezone-free instant (Databricks Connect collect() returns naive local-time datetimes)."""
    if v is None:
        return None
    return int(datetime.fromisoformat(v).timestamp()) if isinstance(v, str) else int(v.timestamp())


def verify(spark, source: str, run_id: str, rep: DQReport) -> None:
    """Recompute one financing table from its Volume CSV and compare schema, values, lineage and Delta metadata."""
    c, p = CFG[source], f"{source}:"
    got_schema = [(f.name, f.dataType.simpleString()) for f in spark.table(c["table"]).schema.fields]
    rep.equals(p + f"schema_exact ({len(schema(source))} columns, names, order, types)", got_schema, schema(source))
    bronze = spark.table(c["bronze"])
    path = bronze.select("source_file").distinct().collect()[0][0]
    raw = bytes(spark.read.format("binaryFile").load(path).select("content").collect()[0][0])
    entry = next(f for f in json.loads(MANIFEST.read_text())["files"] if f["bronze_table"] == c["short"])
    pull = next(json.loads(ln) for ln in CACHE_MANIFEST.read_text().splitlines() if Path(entry["source_file"]).name in ln)
    rep.equals(p + "volume_file_sha256_equals_manifest", hashlib.sha256(raw).hexdigest(), entry["output_sha256"])
    ingested = {epoch(r[0]) for r in bronze.select("ingested_at").distinct().collect()}
    src = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    amt_src = {"original_principal_amount_usd": "Original Principal Amount (US$)", "cancelled_amount_usd": "Cancelled Amount (US$)",
               "undisbursed_amount_usd": "Undisbursed Amount (US$)", "disbursed_amount_usd": "Disbursed Amount (US$)",
               "repaid_to_lender_usd": f"Repaid to {c['org']} (US$)", "due_to_lender_usd": f"Due to {c['org']} (US$)",
               "exchange_adjustment_usd": "Exchange Adjustment (US$)", "borrower_obligation_usd": c["oblig"], "sold_third_party_usd": "Sold 3rd Party (US$)",
               "repaid_third_party_usd": "Repaid 3rd Party (US$)", "due_third_party_usd": "Due 3rd Party (US$)", "held_amount_usd": c["held"]}
    date_src = dict(zip(DATES, ["First Repayment Date", "Last Repayment Date", "Agreement Signing Date", "Board Approval Date",
                                "Effective Date (Most Recent)", "Closed Date (Most Recent)", "Last Disbursement Date"]))
    exp = {}
    for row in src.itertuples(index=False, name=None):
        r = dict(zip(src.columns, row))
        k = r[c["key_src"]]
        rec = {c["key"]: k, "financing_number_prefix": re.sub(r"[0-9].*$", "", k), "lender": c["lender"], "project_id": r["Project ID"],
               "project_name_source": r["Project Name"] or None, "region_name_source": r["Region"] or None, "country_code": r["Country / Economy Code"] or None,
               "country_name": r["Country / Economy"] or None, "borrower_name": r["Borrower"] or None, "financing_status": r[c["status"]],
               "currency_of_commitment": r["Currency of Commitment"] or None,
               "agreement_signing_date_is_placeholder": r["Agreement Signing Date"] == PLACEHOLDER,
               "effective_date_is_placeholder": r["Effective Date (Most Recent)"] == PLACEHOLDER,
               "board_approval_fiscal_year": int(r["Board approval - Fiscal year"]), "board_approval_calendar_year": int(r["Board approval - Calendar year"]),
               "source_end_of_period": d(r["End of Period"]), "financing_snapshot_semantics": "latest_monthly_snapshot_as_of_end_of_period",
               "source_dataset": c["dataset"], "source_row_sha256": hashlib.sha256(SEP.join(row).encode("utf-8")).hexdigest(), "bronze_table": c["bronze"],
               "bronze_source_file": path, "bronze_cohort_id": "wbg_poc_2000", "bronze_ingested_at": next(iter(ingested)),
               "bronze_input_file_sha256": entry["output_sha256"], "source_population_sha256": entry["source_sha256"],
               "source_retrieved_at": epoch(pull["extracted_at_utc"]), "silver_run_id": run_id, "transform_version": "financing_silver_v1.0.0"}
        rec |= {a: (Decimal(r[s]) if r[s] else None) for a, s in amt_src.items()}
        rec |= {o: d(r[s]) for o, s in date_src.items()}
        if source == "ibrd":
            rec |= {"guarantor_country_code": r["Guarantor Country / Economy Code"] or None, "guarantor_name": r["Guarantor"] or None,
                    "loan_type": r["Loan Type"] or None, "interest_rate": Decimal(r["Interest Rate"]) if r["Interest Rate"] else None}
        else:
            rec |= {"service_charge_rate": Decimal(r["Service Charge Rate"]) if r["Service Charge Rate"] else None,
                    "has_repayment_schedule": bool(r["First Repayment Date"]) and bool(r["Last Repayment Date"])}
        exp[k] = rec
    cols = [n for n, _ in schema(source) if n != "silver_processed_at"]
    assert set(cols) == set(next(iter(exp.values()))), f"{source}: verifier must recompute every column except silver_processed_at"
    got = {x[c["key"]]: {**x, "bronze_ingested_at": epoch(x["bronze_ingested_at"]), "source_retrieved_at": epoch(x["source_retrieved_at"])}
           for x in (r.asDict() for r in spark.table(c["table"]).select(*cols).collect())}
    rep.equals(p + "row_count", len(got), c["rows"])
    rep.equals(p + "distinct_keys", len(set(got)), c["rows"])
    rep.equals(p + "keys_equal_source", sorted(set(got) ^ set(exp))[:10], [])
    mism: dict = {}
    for k, e in exp.items():
        for col in cols:
            if got.get(k, {}).get(col) != e[col]:
                mism.setdefault(col, []).append(k)
    rep.equals(p + f"field_mismatches ({len(cols)} columns recomputed in Python incl. exact amounts and lineage)", {k: v[:5] for k, v in mism.items()}, {})
    cohort = set(pd.read_csv(COHORT, dtype=str, keep_default_na=False)["project_id"])
    rep.equals(p + "cohort_membership_100pct", sorted({e["project_id"] for e in exp.values()} - cohort)[:10], [])
    projects = {r[0] for r in spark.table("workspace.wbg_silver.projects").select("project_id").collect()}
    rep.equals(p + "project_ids_in_silver_projects", sorted({e["project_id"] for e in exp.values()} - projects)[:10], [])
    rep.equals(p + "silver_processed_at_not_null", spark.table(c["table"]).filter("silver_processed_at IS NULL").count(), 0)
    props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {c['table']}").collect()}
    rep.equals(p + "constraints_present", sorted(x.split(".")[-1] for x in props if x.startswith("delta.constraints.")), c["constraints"])
    rep.equals(p + "run_id_property", props.get("silver.run_id"), run_id)
    rep.equals(p + "key_not_null_enforced", [f.nullable for f in spark.table(c["table"]).schema.fields if f.name == c["key"]], [False])
    hist = [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {c['table']}").orderBy("version").collect()]
    rep.equals(p + "history_first_operation_is_create", hist[0][1] if hist else None, "CREATE TABLE AS SELECT")
    rep.add(p + "delta_version_after_write", hist[-1][0] if hist else None, "reported", True, "warn")


def main() -> int:
    """Verify both financing tables and the baseline; save the report and return 0 when every check passed."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--approved-dry-run", required=True)
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    baseline = json.loads((run_dir / "baseline_before_write.json").read_text())
    spark = get_spark(args.profile)
    rep = DQReport("ibrd_loans + ida_credits (post-write, independent)")
    for source in CFG:
        verify(spark, source, args.run_id, rep)
    approved = json.loads((RUNS / args.approved_dry_run / "known_anomalies.json").read_text())
    rep.equals("known_anomalies_equal_approved_dry_run", json.loads((run_dir / "known_anomalies.json").read_text()), approved)
    rep.equals("cohort_manifest_unchanged", hashlib.sha256(COHORT.read_bytes()).hexdigest(), baseline["cohort_manifest_sha256"])
    masks = sorted([r[0], r[1], r[2], r[3]] for r in spark.sql(
        "SELECT table_schema, table_name, column_name, mask_name FROM workspace.information_schema.column_masks").collect())
    rep.equals("column_masks_unchanged", masks, baseline["column_masks"])
    rep.equals("baseline_covers_17_bronze_14_silver", (len(baseline["bronze"]), len(baseline["silver"])), (17, 14))
    for tbl, before in {**baseline["bronze"], **baseline["silver"]}.items():
        h = spark.sql(f"DESCRIBE HISTORY {tbl} LIMIT 1").collect()[0]
        rep.equals(f"unchanged:{tbl}", {"version": h.version, "rows": spark.table(tbl).count()}, before)
    print(rep.render())
    hist = {CFG[s]["table"]: [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {CFG[s]['table']}").orderBy("version").collect()] for s in CFG}
    out = run_dir / "post_write_verification.json"
    out.write_text(json.dumps({"run_id": args.run_id, "verification": rep.to_dict(), "table_history": hist}, indent=1, default=str))
    print(f"\nreport: {out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
