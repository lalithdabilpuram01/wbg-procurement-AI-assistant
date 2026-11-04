"""Independent verification of gold_contract_analytics (read-only).

Collects the needed Silver columns (never supplier names or IDs: only ID types, country codes and row hashes) and
recomputes every Gold column per contract in plain Python with its own copy of the rules (no reuse of the Spark
transform or checks). Also checks the exact schema, the US$85,248,987,799.45 total, per-project reconciliation with
gold_project_portfolio, and the unchanged Bronze/Silver/Gold/grants/mask/cohort state against the run baseline.
Post-write mode adds constraints, NOT NULL, run_id property and history.

Usage (from procurement-intelligence/):
  python -m gold.contract_analytics.verify_contract_analytics_gold --dry-run --run-id gold_contract_analytics_<ts>
  python -m gold.contract_analytics.verify_contract_analytics_gold --run-id gold_contract_analytics_<ts>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from gold.common import baseline as B
from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "gold" / "runs"
TARGET = "workspace.wbg_gold.gold_contract_analytics"
PORTFOLIO = "workspace.wbg_gold.gold_project_portfolio"
S = "workspace.wbg_silver."
AUDIT_TOTAL = Decimal("85248987799.45")
SOURCES = sorted(["contract_awards", "contract_award_lines", "projects", "wdi_country", "income_classification_history", "notice_contract_links",
                  "procurement_notice_details"])
LIN = [("source_dataset", "string"), ("source_as_of_date", "date"), ("source_retrieved_at", "timestamp"), ("bronze_source_file", "string"),
       ("bronze_input_file_sha256", "string"), ("source_population_sha256", "string"), ("source_rows_sha256", "string"), ("contract_snapshot_semantics", "string"),
       ("silver_contracts_run_id", "string"), ("silver_source_versions", "string"), ("gold_run_id", "string"), ("gold_processed_at", "timestamp"),
       ("transform_version", "string")]
SCHEMA = [("wb_contract_number", "string"), ("project_id", "string"), ("project_name", "string"), ("region_name", "string"), ("geography_type", "string"),
          ("project_country_name", "string"), ("wdi_country_code", "string"), ("wdi_country_iso3", "string"), ("country_context_status", "string"),
          ("borrower_country_code_published", "string"), ("borrower_country_name_published", "string"), ("borrower_country_code_status", "string"),
          ("income_group_code_at_signing_fy", "string"), ("income_group_status_at_signing_fy", "string"), ("project_approval_fiscal_year", "int"),
          ("project_board_approval_date", "date"), ("procurement_category", "string"), ("procurement_method", "string"), ("review_type", "string"),
          ("global_practices", "array<string>"), ("contract_signing_date", "date"), ("contract_fiscal_year", "int"), ("contract_signed_calendar_year", "int"),
          ("days_from_board_approval_to_signing", "int"), ("signed_before_board_approval", "boolean"), ("contract_amount_usd", "decimal(32,2)"),
          ("contract_amount_basis", "string"), ("supplier_line_count", "bigint"), ("is_multi_supplier", "boolean"), ("supplier_lines_missing_amount", "bigint"),
          ("has_anonymized_individual_consultant", "boolean"), ("has_supplier_with_numeric_id", "boolean"), ("has_supplier_with_missing_id", "boolean"),
          ("supplier_country_codes_published", "array<string>"), ("supplier_lines_missing_country", "bigint"), ("supplier_country_relation", "string"),
          ("contracts_sharing_borrower_reference_in_project", "bigint"), ("award_notice_link_coverage", "boolean"), ("linked_award_notice_count", "bigint"),
          ("award_notice_link_status", "string"), ("earliest_linked_award_notice_date", "date"), *LIN]
CONSTRAINTS = sorted(["contract_number_format", "project_id_format", "amount_non_negative", "amount_basis_value", "line_counts_valid", "multi_supplier_consistent",
                      "fiscal_year_consistent", "calendar_year_consistent", "signing_not_after_snapshot", "country_semantics", "borrower_status_valid",
                      "supplier_relation_valid", "approval_lag_consistent", "link_semantics", "snapshot_semantics_value", "source_dataset_value"])


def epoch(v) -> int | None:
    """Timezone-free instant (Databricks Connect collect() returns naive local-time datetimes)."""
    return None if v is None else int(v.timestamp())


def rows(spark, table: str, *cols: str) -> list[dict]:
    return [r.asDict() for r in spark.table(S + table).select(*cols).collect()]


def expected(spark, run_id: str) -> dict:
    """Every expected Gold row, keyed by contract number, recomputed in plain Python from the Silver tables."""
    A = rows(spark, "contract_awards", "wb_contract_number", "project_id", "borrower_country_code", "borrower_country_name", "procurement_category",
             "procurement_method", "review_type", "global_practices", "contract_signing_date", "fiscal_year", "contract_signed_calendar_year", "contract_amount_usd",
             "contracts_sharing_reference_in_project",
             "source_dataset", "source_as_of_date", "source_retrieved_at", "bronze_source_file", "bronze_input_file_sha256", "source_population_sha256", "silver_run_id")
    lines: dict = defaultdict(list)
    for ln in rows(spark, "contract_award_lines", "wb_contract_number", "supplier_id_type", "supplier_country_code", "supplier_amount_usd", "source_row_sha256"):
        lines[ln["wb_contract_number"]].append(ln)
    links: dict = defaultdict(list)
    for k in rows(spark, "notice_contract_links", "wb_contract_number", "ambiguity_status", "notice_date"):
        links[k["wb_contract_number"]].append(k)
    P = {r["project_id"]: r for r in rows(spark, "projects", "project_id", "project_name", "region_name", "geography_type", "country_name", "wdi_country_code",
                                          "approval_fiscal_year", "board_approval_date")}
    iso = {r["country_code"]: r["country_wb_id"] for r in rows(spark, "wdi_country", "country_code", "country_wb_id")}
    inc = {(r["iso3_code"], r["fiscal_year"]): r for r in rows(spark, "income_classification_history", "iso3_code", "fiscal_year", "income_group_code", "classification_status")}
    covered = {r["project_id"] for r in rows(spark, "procurement_notice_details", "project_id")}
    versions = json.dumps({t: spark.sql(f"DESCRIBE HISTORY {S}{t} LIMIT 1").collect()[0].version for t in SOURCES}, separators=(",", ":"))
    out = {}
    for a in A:
        c, p = a["wb_contract_number"], P[a["project_id"]]
        single = p["geography_type"] == "single_country"
        w = p["wdi_country_code"] if single else None
        b = a["borrower_country_code"]
        bstatus = ("regional_project" if not single else "missing_in_source" if b is None else "equal_to_project_wdi_code" if b == p["wdi_country_code"]
                   else "differs_from_project_wdi_code")
        ic = inc.get((iso.get(w), a["fiscal_year"])) if single else None
        ls = lines[c]
        miss = [x for x in ls if x["supplier_country_code"] in (None, "null", "")]
        codes = sorted({x["supplier_country_code"] for x in ls} - {None, "null", ""})
        if bstatus != "equal_to_project_wdi_code" or miss:
            relation = "undetermined"
        else:
            relation = "all_suppliers_same_country" if all(x == b for x in codes) else "includes_other_country"
        days = (a["contract_signing_date"] - p["board_approval_date"]).days if p["board_approval_date"] else None
        ks, cov = links.get(c, []), a["project_id"] in covered
        out[c] = {
            "wb_contract_number": c, "project_id": a["project_id"], "project_name": p["project_name"], "region_name": p["region_name"],
            "geography_type": p["geography_type"], "project_country_name": p["country_name"], "wdi_country_code": w, "wdi_country_iso3": iso.get(w) if single else None,
            "country_context_status": ("single_country_matched" if w else "single_country_unmatched") if single else "regional_not_applicable",
            "borrower_country_code_published": b, "borrower_country_name_published": a["borrower_country_name"], "borrower_country_code_status": bstatus,
            "income_group_code_at_signing_fy": ic["income_group_code"] if ic else None,
            "income_group_status_at_signing_fy": "regional_not_applicable" if not single else (ic["classification_status"] if ic else "no_row_in_source"),
            "project_approval_fiscal_year": p["approval_fiscal_year"], "project_board_approval_date": p["board_approval_date"],
            "procurement_category": a["procurement_category"], "procurement_method": a["procurement_method"], "review_type": a["review_type"],
            "global_practices": a["global_practices"], "contract_signing_date": a["contract_signing_date"], "contract_fiscal_year": a["fiscal_year"],
            "contract_signed_calendar_year": a["contract_signed_calendar_year"], "days_from_board_approval_to_signing": days,
            "signed_before_board_approval": None if days is None else days < 0,
            "contract_amount_usd": sum((x["supplier_amount_usd"] for x in ls if x["supplier_amount_usd"] is not None), Decimal("0.00")),
            "contract_amount_basis": "sum_of_supplier_lines",
            "supplier_line_count": len(ls), "is_multi_supplier": len(ls) > 1, "supplier_lines_missing_amount": sum(x["supplier_amount_usd"] is None for x in ls),
            "has_anonymized_individual_consultant": any(x["supplier_id_type"] == "anonymized_individual_consultant" for x in ls),
            "has_supplier_with_numeric_id": any(x["supplier_id_type"] == "numeric" for x in ls),
            "has_supplier_with_missing_id": any(x["supplier_id_type"] == "missing" for x in ls),
            "supplier_country_codes_published": codes, "supplier_lines_missing_country": len(miss), "supplier_country_relation": relation,
            "contracts_sharing_borrower_reference_in_project": a["contracts_sharing_reference_in_project"],
            "award_notice_link_coverage": cov, "linked_award_notice_count": len(ks) if cov else None,
            "award_notice_link_status": "not_covered" if not cov else "no_link_found" if not ks else
                                        "ambiguous" if any(k["ambiguity_status"] == "ambiguous" for k in ks) else "unambiguous",
            "earliest_linked_award_notice_date": min((k["notice_date"] for k in ks if k["notice_date"] is not None), default=None),
            "source_dataset": a["source_dataset"], "source_as_of_date": a["source_as_of_date"], "source_retrieved_at": epoch(a["source_retrieved_at"]),
            "bronze_source_file": a["bronze_source_file"], "bronze_input_file_sha256": a["bronze_input_file_sha256"],
            "source_population_sha256": a["source_population_sha256"],
            "source_rows_sha256": hashlib.sha256(",".join(sorted(x["source_row_sha256"] for x in ls)).encode()).hexdigest(),
            "contract_snapshot_semantics": "ds00005_dataset_snapshot_as_of_source_as_of_date", "silver_contracts_run_id": a["silver_run_id"],
            "silver_source_versions": versions, "gold_run_id": run_id, "transform_version": "gold_contract_analytics_v1.0.0"}
    return out


def main() -> int:
    """Recompute the table (or the transform's frame, with --dry-run) independently and compare; 0 when all passed."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--approved-dry-run", help="dry-run folder whose pre-write baseline must equal this run's")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    base = json.loads((run_dir / "baseline_before_write.json").read_text())
    spark = get_spark(args.profile)
    rep = DQReport(TARGET + (" (dry run, independent)" if args.dry_run else " (post-write, independent)"))
    if args.dry_run:
        from gold.contract_analytics import transform as T
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
        got[x["wb_contract_number"]] = x
    rep.equals("rows_and_unique_contracts", (g.count(), len(got)), (178921, 178921))
    rep.equals("contracts_equal_silver", sorted(set(got) ^ set(exp))[:10], [])
    mism: dict = {}
    for k, e in exp.items():
        for c in cols:
            if got.get(k, {}).get(c) != e[c]:
                mism.setdefault(c, []).append(k)
    rep.equals(f"field_mismatches ({len(cols)} columns recomputed in Python)", {c: v[:5] for c, v in mism.items()}, {})
    total = sum((v["contract_amount_usd"] for v in got.values()), Decimal("0.00"))
    rep.equals("total_award_value_exact", total, AUDIT_TOTAL)
    with B.COHORT.open(newline="", encoding="utf-8") as fh:
        cohort = {r["project_id"] for r in csv.DictReader(fh)}
    by_p: dict = defaultdict(lambda: [0, Decimal("0.00")])
    for v in got.values():
        by_p[v["project_id"]][0] += 1
        by_p[v["project_id"]][1] += v["contract_amount_usd"]
    rep.equals("projects_in_cohort", (len(by_p), len(set(by_p) - cohort)), (2000, 0))
    port = {r[0]: (r[1], r[2]) for r in spark.table(PORTFOLIO).select("project_id", "contract_award_count", "contract_award_amount_usd").collect()}
    rep.equals("per_project_equal_gold_project_portfolio", sorted(p for p in set(port) | set(by_p) if tuple(by_p.get(p, [0, None])) != port.get(p))[:10], [])
    texts = [" ".join(str(v[c]) for c, t in SCHEMA if c in v and t in ("string", "array<string>") and not c.endswith("_sha256") and v[c] is not None) for v in got.values()]
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    phone = re.compile(r"\b(?:tel|t[ée]l[ée]phone|phone|fax|mobile|cell)\b[^0-9+]{0,12}\+?[0-9][0-9 ()./-]{6,}", re.I)
    rep.equals("privacy:emails_phones_in_output (counts)", (sum(bool(email.search(x)) for x in texts), sum(bool(phone.search(x)) for x in texts)), (0, 0))
    rep.equals("privacy:no_supplier_identity_or_free_text_columns", [n for n, _ in SCHEMA if re.search(
        r"supplier_(name|id|text)s?$|supplier_ids|line_key|description|reference$|notice_text|team|upi|email|phone|contact", n)], [])
    rep.equals("gold_processed_at_not_null", g.filter("gold_processed_at IS NULL").count(), 0)
    if not args.dry_run:
        props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {TARGET}").collect()}
        rep.equals("constraints_present", sorted(k.split(".")[-1] for k in props if k.startswith("delta.constraints.")), CONSTRAINTS)
        rep.equals("run_id_property", props.get("gold.run_id"), args.run_id)
        rep.equals("not_null_columns_enforced", sum(1 for f in g.schema.fields if not f.nullable), 32)
        hist = [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {TARGET}").orderBy("version").collect()]
        rep.equals("history_first_operation_is_create", hist[0][1] if hist else None, "CREATE TABLE AS SELECT")
        rep.add("delta_version_after_write", hist[-1][0] if hist else None, "reported", True, "warn")
        rep.equals("gold_schema_tables", sorted(r.tableName for r in spark.sql("SHOW TABLES IN workspace.wbg_gold").collect()),
                   ["gold_contract_analytics", "gold_project_portfolio"])
    else:
        rep.equals("target_still_absent", spark.catalog.tableExists(TARGET), False)
    if args.approved_dry_run:
        appr = json.loads((RUNS / args.approved_dry_run / "baseline_before_write.json").read_text())
        keys = ("bronze", "silver", "gold", "grants", "column_masks", "cohort_manifest_sha256")
        rep.equals("baseline_equals_approved_dry_run_baseline", {k: base[k] == appr[k] for k in keys}, {k: True for k in keys})
    B.check_unchanged(spark, base, rep, (17, 18, 1))
    print(rep.render())
    out = run_dir / ("dry_run_independent_verification.json" if args.dry_run else "post_write_verification.json")
    out.write_text(json.dumps({"run_id": args.run_id, "dry_run": args.dry_run, "verification": rep.to_dict(),
                               "verified_at": datetime.now(timezone.utc).isoformat()}, indent=1, default=str))
    print(f"\nreport: {out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
