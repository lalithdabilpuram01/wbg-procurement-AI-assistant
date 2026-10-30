"""Run the IBRD loans / IDA credits Silver transformations (Databricks Connect, Serverless).

Default is a DRY RUN: records the pre-write baseline (all Bronze + existing Silver tables, column masks, cohort),
verifies both Volume CSVs against the extraction manifest (SHA-256 + every record equal to a Bronze row), builds both
DataFrames, runs all checks (incl. the Delta CHECK constraints and NOT NULL columns evaluated on the data), writes the
known-anomaly lists, prints schemas and summaries, and saves reports. Nothing is written to Databricks.

Writing requires --mode write --confirm-create, all "error" checks passing, and neither target existing (create-only).
Silver `projects` is read for validation only.

Usage (from procurement-intelligence/):
  python -m silver.financing.run_financing_silver
  python -m silver.financing.run_financing_silver --mode write --confirm-create
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from silver.common import pipeline
from silver.common.dq import DQReport
from silver.common.pipeline import CATALOG, runs_dir
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.common.sourcefiles import verify_csv_source_file
from silver.common.write import create_table
from silver.financing import transform as T
from silver.financing.checks import check, known_anomalies

S = f"{CATALOG}.wbg_silver"
TARGETS = {"ibrd": f"{S}.ibrd_loans", "ida": f"{S}.ida_credits"}
EXPECTED = {"ibrd": 612, "ida": 2073}
RUNS = runs_dir(Path(__file__).resolve().parents[1] / "runs")
EXISTING_SILVER = ["wdi_indicator_observations", "wdi_country", "income_classification_history", "wgi_estimates", "cpia_ratings", "projects",
                   "contract_award_lines", "contract_awards", "contract_awards_legacy", "procurement_notices", "procurement_notice_details",
                   "notice_contract_link_candidates", "notice_contract_links", "ieg_project_ratings"]
COHORT = T.REPO / "cohort" / "poc_cohort_2000_manifest.csv"
LINEAGE_NN = ["source_dataset", "source_row_sha256", "bronze_source_file", "bronze_ingested_at", "bronze_input_file_sha256"]
AMOUNT_DOC = "Published US$ amount, exact (decimal(22,2), sign preserved); no conversion. For non-USD commitments US$ fields use different exchange-rate bases: do not combine arithmetically"


def _in(values: list[str]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def spec(source: str) -> dict[str, Any]:
    s = T.SOURCES[source]
    key = s["key"]
    constraints = {
        "key_format": f"{key} RLIKE '{s['key_regex']}'",
        "prefix_matches_key": f"financing_number_prefix = regexp_extract({key}, '^([A-Z]+)', 1)",
        "project_id_format": "project_id RLIKE '^P[0-9]{6}$'",
        "lender_value": f"lender = '{s['lender']}'",
        "non_negative_amounts": " AND ".join(f"({c} IS NULL OR {c} >= 0)" for c in T.NON_NEGATIVE),
        "placeholder_consistent": "(NOT agreement_signing_date_is_placeholder OR agreement_signing_date IS NULL) AND "
                                  "(NOT effective_date_is_placeholder OR effective_date_most_recent IS NULL)",
        "status_valid": f"financing_status IN {_in(s['statuses'])}",
        "approval_years_consistent": "board_approval_fiscal_year = year(board_approval_date) + CASE WHEN month(board_approval_date) >= 7 THEN 1 ELSE 0 END "
                                     "AND board_approval_calendar_year = year(board_approval_date)",
    }
    not_null = [key, "financing_number_prefix", "lender", "project_id", "financing_status", "board_approval_date", "board_approval_fiscal_year",
                "board_approval_calendar_year", "agreement_signing_date_is_placeholder", "effective_date_is_placeholder", "source_end_of_period",
                "financing_snapshot_semantics", *LINEAGE_NN]
    columns = {out: f"{AMOUNT_DOC}. Source column: {src}" for src, out in s["amounts"].items()}
    columns |= {key: f"{s['key_src']} as published; one row per number (financing commitment, not a contract award)",
                "financing_number_prefix": "Letters before the digits of the number, as published (not classified)",
                "country_code": "Borrower country code as published (not aliased; regional projects carry participating borrower countries)",
                "currency_of_commitment": "As published" + ("; NULL: IBRD does not publish it (not assumed USD)" if source == "ibrd" else ""),
                "agreement_signing_date": "NULL when the source shows the 01/01/1900 placeholder (see agreement_signing_date_is_placeholder)",
                "effective_date_most_recent": "Most recent (possibly revised) effective date; NULL when 01/01/1900 placeholder",
                "closed_date_most_recent": "Most recent (possibly extended) closing date as of source_end_of_period; not the date known at approval",
                "financing_status": "Status as of source_end_of_period (latest snapshot), not at approval",
                "financing_snapshot_semantics": T.SNAPSHOT_SEMANTICS,
                "last_disbursement_date": "As published. Values after the closed date or after source_end_of_period are kept and flagged in "
                                          "known_anomalies; their meaning is not inferred"}
    if source == "ibrd":
        constraints["loan_type_valid"] = f"loan_type IS NULL OR loan_type IN {_in(T.IBRD_LOAN_TYPES)}"
        columns["interest_rate"] = ("Interest Rate exactly as published. The meaning of 0.0 on FSL loans is unverified (possibly not published); "
                                    "it must NOT be interpreted as interest-free")
    else:
        constraints["currency_valid"] = f"currency_of_commitment IS NULL OR currency_of_commitment IN {_in(T.IDA_CURRENCIES)}"
        constraints["repayment_schedule_consistent"] = "has_repayment_schedule = (first_repayment_date IS NOT NULL AND last_repayment_date IS NOT NULL)"
        not_null.append("has_repayment_schedule")
        columns["has_repayment_schedule"] = "True when both First and Last Repayment Date are published; prefixes are not classified as grants"
    comment = (f"{s['lender']} financing commitments ({s['dataset']}, monthly statement as of 2026-09-30): one row per {s['key_src'].lower()} for cohort projects. "
               "Balances, status and most-recent dates are as of the snapshot, not as of approval (leakage). Amounts as published in US$; "
               "projects can have several instruments: aggregate per project only as-of and never add commitments to contract awards")
    return {"constraints": constraints, "not_null": not_null, "columns": columns, "comment": comment}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry-run", "write"], default="dry-run")
    ap.add_argument("--confirm-create", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    if args.mode == "write" and not args.confirm_create:
        ap.error("--mode write requires --confirm-create")

    run_id = "financing_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(args.profile)
    baseline: dict = {"bronze": {}, "silver": {}, "targets_exist": {k: spark.catalog.tableExists(v) for k, v in TARGETS.items()},
                      "cohort_manifest_sha256": hashlib.sha256(COHORT.read_bytes()).hexdigest()}
    bronze_tables = [f"{CATALOG}.{sc}.{r.tableName}" for sc in ("wbg_bronze", "wbg_ref_bronze") for r in spark.sql(f"SHOW TABLES IN {CATALOG}.{sc}").collect()]
    for group, tables in (("bronze", bronze_tables), ("silver", [f"{S}.{t}" for t in EXISTING_SILVER])):
        for t in tables:
            h = spark.sql(f"DESCRIBE HISTORY {t} LIMIT 1").collect()[0]
            baseline[group][t] = {"version": h.version, "rows": spark.table(t).count()}
    baseline["column_masks"] = sorted([r[0], r[1], r[2], r[3]] for r in spark.sql(
        f"SELECT table_schema, table_name, column_name, mask_name FROM {CATALOG}.information_schema.column_masks").collect())
    (run_dir / "baseline_before_write.json").write_text(json.dumps(baseline, indent=1))

    projects = spark.table(f"{S}.projects")
    with COHORT.open(newline="", encoding="utf-8") as f:
        cohort_ids = {r["project_id"] for r in csv.DictReader(f)}
    rep = DQReport(", ".join(TARGETS.values()))
    rep.equals("targets_absent_before_write", baseline["targets_exist"], {k: False for k in TARGETS})
    rep.equals("baseline_table_counts (Bronze, existing Silver)", (len(baseline["bronze"]), len(baseline["silver"])), (17, 14))
    rep.equals("cohort_manifest_project_count", len(cohort_ids), 2000)
    out: dict = {}
    anomalies: dict = {}
    specs = {s: spec(s) for s in TARGETS}
    for source in TARGETS:
        bronze = spark.table(T.SOURCES[source]["bronze"])
        prov = T.load_provenance(source)
        rep.equals(f"{source}:bronze_rows", bronze.count(), EXPECTED[source])
        verify_csv_source_file(spark, bronze, T.source_columns(bronze), prov["bronze_input_file_sha256"], T.SOURCES[source]["dataset"].lower(), rep)
        df = T.build(bronze, source, prov, run_id)
        check(df, bronze, source, projects, cohort_ids, prov, rep, EXPECTED[source])
        clean = T.drop_validation(df)
        rep.equals(f"{source}:delta_check_constraints_hold_before_write", {k: clean.filter(f"NOT coalesce(({e}), false)").count() for k, e in specs[source]["constraints"].items()},
                   {k: 0 for k in specs[source]["constraints"]})
        rep.equals(f"{source}:not_null_columns_have_no_nulls", {c: clean.filter(f"`{c}` IS NULL").count() for c in specs[source]["not_null"]},
                   {c: 0 for c in specs[source]["not_null"]})
        out[source] = clean
        anomalies[source] = known_anomalies(clean, source, projects)
    (run_dir / "known_anomalies.json").write_text(json.dumps(anomalies, indent=1))
    summary = {s: {"rows": out[s].count(), "projects": out[s].select("project_id").distinct().count(),
                   "status": {r[0]: r[1] for r in out[s].groupBy("financing_status").count().collect()},
                   "anomaly_counts": {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in anomalies[s].items() if k != "country_differs_from_project"}
                   | {"country_differs_alias_or_other": len(anomalies[s]["country_differs_from_project"]["curated_alias_or_other_single_country"]),
                      "regional_projects_with_borrower_countries": len(anomalies[s]["country_differs_from_project"]["regional_project_borrower_countries (D-29 evidence; not applied)"])}}
               for s in TARGETS}
    both = out["ibrd"].select("project_id").distinct().intersect(out["ida"].select("project_id").distinct()).count()
    summary["projects_with_ibrd_and_ida"] = both
    print(f"run_id={run_id} mode={args.mode} transform={T.TRANSFORM_VERSION}")
    for s, df in out.items():
        print(f"\n== proposed schema: {TARGETS[s]} ({len(df.columns)} columns)")
        for fld in df.schema.fields:
            print(f"   {fld.name:40s} {fld.dataType.simpleString()}")
    print("\n== summary\n" + json.dumps(summary, indent=1, default=str))
    print(rep.render())
    (run_dir / "dq_report.json").write_text(json.dumps({"run_id": run_id, "mode": args.mode, "transform_version": T.TRANSFORM_VERSION, "targets": TARGETS,
                                                        "summary": summary, "constraints": {s: specs[s]["constraints"] for s in TARGETS}, "dq": rep.to_dict()},
                                                       indent=1, default=str))
    print(f"\nreports: {run_dir}")
    if not rep.passed:
        print("DQ FAILED: nothing written")
        return 1
    if args.mode == "dry-run":
        print("DRY RUN: nothing written to Databricks")
        return 0
    if any(baseline["targets_exist"].values()) and not pipeline.ENABLED:
        raise SystemExit(f"target(s) already exist: {baseline['targets_exist']}; refusing to overwrite (create-only)")
    for s in ("ibrd", "ida"):
        sp = specs[s]
        create_table(spark, out[s], TARGETS[s],
                     {"silver.run_id": run_id, "silver.transform_version": T.TRANSFORM_VERSION, "silver.source_table": T.SOURCES[s]["bronze"],
                      "silver.decisions": "D-26,D-31; latest monthly snapshot; no FX conversion; prefixes not classified"},
                     sp["comment"], sp["columns"], sp["not_null"], sp["constraints"])
        print(f"written {TARGETS[s]}: {spark.table(TARGETS[s]).count()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
