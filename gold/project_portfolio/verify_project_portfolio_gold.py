"""Independent verification of gold_project_portfolio (read-only).

Collects the needed Silver columns and recomputes every Gold column per project in plain Python with its own copy of the
rules (no reuse of the Spark transform or checks). Contract measures are recomputed from contract_award_LINES (distinct
contract numbers, exact Decimal sums), an independent path from the contract_awards table the transform uses. Also checks
the exact schema, the 2,000-project cohort, the US$85,248,987,799.45 audit total and the unchanged Bronze/Silver/mask/cohort
state against the run baseline. Post-write mode adds constraints, NOT NULL, run_id property and history.

Usage (from procurement-intelligence/):
  python -m gold.project_portfolio.verify_project_portfolio_gold --dry-run --run-id gold_project_portfolio_<ts>
  python -m gold.project_portfolio.verify_project_portfolio_gold --run-id gold_project_portfolio_<ts>
"""
from __future__ import annotations

from app_config.paths import COHORT_MANIFEST
import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "gold" / "runs"
COHORT = COHORT_MANIFEST
TARGET = "workspace.wbg_gold.gold_project_portfolio"
S = "workspace.wbg_silver."
AUDIT_TOTAL = Decimal("85248987799.45")
TYPES = {"Contract Award": "contract_award_notice_count", "Invitation for Bids": "invitation_for_bids_notice_count",
         "Request for Expression of Interest": "expression_of_interest_notice_count", "General Procurement Notice": "general_procurement_notice_count",
         "Invitation for Prequalification": "prequalification_notice_count", "Small Contracts Award": "small_contracts_award_notice_count"}
SOURCES = ["contract_awards", "ibrd_loans", "ida_credits", "ieg_project_ratings", "income_classification_history", "procurement_notice_details",
           "procurement_notices", "project_details_v3", "projects", "wdi_country"]
SCHEMA = ([("project_id", "string"), ("project_name", "string"), ("region_name", "string"), ("geography_type", "string"), ("source_country_code", "string"),
           ("source_country_name", "string"), ("wdi_country_code", "string"), ("wdi_country_iso3", "string"), ("wdi_match_method", "string"),
           ("country_context_status", "string"), ("income_group_code_at_approval", "string"), ("income_group_status_at_approval", "string"),
           ("approval_fiscal_year", "int"), ("board_approval_date", "date"), ("project_status_snapshot", "string"), ("closing_date_snapshot", "date"),
           ("current_total_commitment_usd_snapshot", "decimal(22,2)"), ("contract_award_count", "bigint"), ("contract_award_amount_usd", "decimal(38,2)"),
           ("multi_supplier_contract_count", "bigint"), ("contracts_with_missing_line_amount_count", "bigint"), ("first_contract_signing_date", "date"),
           ("last_contract_signing_date", "date"), ("contract_awards_coverage", "string"), ("notice_count", "bigint")]
          + [(c, "bigint") for c in TYPES.values()]
          + [("first_notice_publication_date", "date"), ("last_notice_publication_date", "date"), ("notices_in_identical_metadata_groups", "bigint"),
             ("has_api_notice_details", "boolean"), ("api_notice_detail_count", "bigint"), ("ibrd_loan_count", "int"), ("ida_credit_count", "int"),
             ("has_ibrd_ida_financing", "boolean"), ("has_ieg_rating", "boolean"), ("ieg_evaluation_fiscal_year", "int"), ("ieg_outcome_rating", "string"),
             ("ieg_outcome_score", "int"), ("has_project_details_v3", "boolean"), ("product_line_code", "string"), ("environmental_social_risk_rating", "string"),
             ("results_indicator_count", "int"), ("geo_location_count", "int"), ("loan_transaction_count", "int"), ("projects_source_retrieved_at", "timestamp"),
             ("contract_awards_source_as_of_date", "date"), ("notices_source_retrieved_at", "timestamp"), ("financing_source_end_of_period", "date"),
             ("ieg_source_as_of_date", "date"), ("project_details_source_retrieved_at", "timestamp"), ("portfolio_snapshot_semantics", "string"),
             ("projects_raw_record_sha256", "string"), ("silver_source_versions", "string"), ("gold_run_id", "string"), ("gold_processed_at", "timestamp"),
             ("transform_version", "string")])
TIMESTAMPS = {"projects_source_retrieved_at", "notices_source_retrieved_at", "project_details_source_retrieved_at"}
CONSTRAINTS = sorted(["project_id_format", "counts_non_negative", "notice_type_counts_sum", "contract_amount_null_iff_no_contracts", "contract_amount_non_negative",
                      "country_semantics", "api_detail_consistent", "financing_flag_consistent", "ieg_null_when_unrated", "details_null_when_uncovered",
                      "risk_rating_valid", "snapshot_semantics_value", "contract_coverage_value"])


def epoch(v) -> int | None:
    """Timezone-free instant (Databricks Connect collect() returns naive local-time datetimes)."""
    return None if v is None else int(v.timestamp())


def rows(spark, table: str, *cols: str) -> list[dict]:
    return [r.asDict() for r in spark.table(S + table).select(*cols).collect()]


def single(values) -> object:
    """The one distinct value in `values`; fails if there are several."""
    vals = set(values)
    assert len(vals) == 1, f"expected one source-wide value, got {len(vals)}"
    return next(iter(vals))


def expected(spark, run_id: str) -> tuple[dict, dict]:
    """Expected Gold rows keyed by project_id, recomputed in plain Python, plus reconciliation totals and counts."""
    P = rows(spark, "projects", "project_id", "project_name", "region_name", "geography_type", "country_code", "country_name", "wdi_country_code", "wdi_match_method",
             "approval_fiscal_year", "board_approval_date", "project_status", "closing_date", "current_total_commitment_usd", "source_retrieved_at", "raw_record_sha256")
    lines = rows(spark, "contract_award_lines", "project_id", "wb_contract_number", "supplier_amount_usd", "contract_signing_date")
    notices = rows(spark, "procurement_notices", "project_id", "notice_type", "publication_date", "identical_metadata_group_size", "source_retrieved_at")
    detail_ids = Counter(r["project_id"] for r in rows(spark, "procurement_notice_details", "project_id"))
    ibrd = Counter(r["project_id"] for r in rows(spark, "ibrd_loans", "project_id"))
    ida = Counter(r["project_id"] for r in rows(spark, "ida_credits", "project_id"))
    ieg = {r["project_id"]: r for r in rows(spark, "ieg_project_ratings", "project_id", "evaluation_fiscal_year", "outcome_rating", "outcome_score", "source_as_of_date")}
    pdv = {r["project_id"]: r for r in [x.asDict() for x in spark.table(S + "project_details_v3").selectExpr(
        "project_id", "product_line_code", "environmental_social_risk_rating", "results_indicators IS NULL AS ind_null", "indicator_count",
        "geo_locations IS NULL AS geo_null", "geo_location_count", "loan_transaction_count", "source_retrieved_at").collect()]}
    iso = {r["country_code"]: r["country_wb_id"] for r in rows(spark, "wdi_country", "country_code", "country_wb_id")}
    inc = {(r["iso3_code"], r["fiscal_year"]): r for r in rows(spark, "income_classification_history", "iso3_code", "fiscal_year", "income_group_code", "classification_status")}
    fin_eop = single(r["source_end_of_period"] for t in ("ibrd_loans", "ida_credits") for r in rows(spark, t, "source_end_of_period"))
    versions = json.dumps({t: spark.sql(f"DESCRIBE HISTORY {S}{t} LIMIT 1").collect()[0].version for t in sorted(SOURCES)}, separators=(",", ":"))

    contracts: dict = defaultdict(lambda: {"amounts": [], "dates": set()})
    for ln in lines:
        c = contracts[(ln["project_id"], ln["wb_contract_number"])]
        c["amounts"].append(ln["supplier_amount_usd"])
        c["dates"].add(ln["contract_signing_date"])
    per_p: dict = defaultdict(list)
    for (pid, _), c in contracts.items():
        assert len(c["dates"]) == 1, "a contract's lines must share one signing date"
        per_p[pid].append(c)
    npp: dict = defaultdict(list)
    for nrow in notices:
        npp[nrow["project_id"]].append(nrow)
    consts = {"contract_awards_source_as_of_date": single(r["source_as_of_date"] for r in rows(spark, "contract_awards", "source_as_of_date")),
              "notices_source_retrieved_at": epoch(single(r["source_retrieved_at"] for r in notices)), "financing_source_end_of_period": fin_eop,
              "ieg_source_as_of_date": single(r["source_as_of_date"] for r in ieg.values()),
              "project_details_source_retrieved_at": epoch(single(r["source_retrieved_at"] for r in pdv.values())),
              "portfolio_snapshot_semantics": "current_state_as_of_source_retrievals_2026_10", "contract_awards_coverage": "ds00005_contracts_signed_fy2020_onwards",
              "silver_source_versions": versions, "gold_run_id": run_id, "transform_version": "gold_project_portfolio_v1.0.0"}
    out = {}
    for p in P:
        pid, sc = p["project_id"], p["geography_type"] == "single_country"
        cs, ns = per_p.get(pid, []), npp.get(pid, [])
        amounts = [a for c in cs for a in c["amounts"] if a is not None]
        iso3 = iso.get(p["wdi_country_code"]) if sc else None
        ic = inc.get((iso3, p["approval_fiscal_year"])) if sc else None
        if not sc:
            status = "regional_not_applicable"
        elif p["approval_fiscal_year"] is None:
            status = "approval_fiscal_year_missing"
        else:
            status = ic["classification_status"] if ic else "no_row_in_source"
        e, d = ieg.get(pid), pdv.get(pid)
        sig = [next(iter(c["dates"])) for c in cs if next(iter(c["dates"])) is not None]
        pub = [x["publication_date"] for x in ns if x["publication_date"] is not None]
        rec = {"project_id": pid, "project_name": p["project_name"], "region_name": p["region_name"], "geography_type": p["geography_type"],
               "source_country_code": p["country_code"], "source_country_name": p["country_name"], "wdi_country_code": p["wdi_country_code"] if sc else None,
               "wdi_country_iso3": iso3, "wdi_match_method": p["wdi_match_method"],
               "country_context_status": ("single_country_matched" if p["wdi_country_code"] else "single_country_unmatched") if sc else "regional_not_applicable",
               "income_group_code_at_approval": ic["income_group_code"] if ic else None, "income_group_status_at_approval": status,
               "approval_fiscal_year": p["approval_fiscal_year"], "board_approval_date": p["board_approval_date"], "project_status_snapshot": p["project_status"],
               "closing_date_snapshot": p["closing_date"], "current_total_commitment_usd_snapshot": p["current_total_commitment_usd"],
               "contract_award_count": len(cs), "contract_award_amount_usd": sum(amounts, Decimal("0.00")) if cs else None,
               "multi_supplier_contract_count": sum(len(c["amounts"]) > 1 for c in cs),
               "contracts_with_missing_line_amount_count": sum(any(a is None for a in c["amounts"]) for c in cs),
               "first_contract_signing_date": min(sig) if sig else None, "last_contract_signing_date": max(sig) if sig else None,
               "notice_count": len(ns), **{col: sum(x["notice_type"] == t for x in ns) for t, col in TYPES.items()},
               "first_notice_publication_date": min(pub) if pub else None, "last_notice_publication_date": max(pub) if pub else None,
               "notices_in_identical_metadata_groups": sum((x["identical_metadata_group_size"] or 0) > 1 for x in ns),
               "has_api_notice_details": pid in detail_ids, "api_notice_detail_count": detail_ids.get(pid),
               "ibrd_loan_count": ibrd.get(pid, 0), "ida_credit_count": ida.get(pid, 0), "has_ibrd_ida_financing": ibrd.get(pid, 0) + ida.get(pid, 0) > 0,
               "has_ieg_rating": e is not None, "ieg_evaluation_fiscal_year": e["evaluation_fiscal_year"] if e else None,
               "ieg_outcome_rating": e["outcome_rating"] if e else None, "ieg_outcome_score": e["outcome_score"] if e else None,
               "has_project_details_v3": d is not None, "product_line_code": d["product_line_code"] if d else None,
               "environmental_social_risk_rating": d["environmental_social_risk_rating"] if d else None,
               "results_indicator_count": d["indicator_count"] if d and not d["ind_null"] else None,
               "geo_location_count": d["geo_location_count"] if d and not d["geo_null"] else None,
               "loan_transaction_count": d["loan_transaction_count"] if d and d["loan_transaction_count"] > 0 else None,
               "projects_source_retrieved_at": epoch(p["source_retrieved_at"]), "projects_raw_record_sha256": p["raw_record_sha256"], **consts}
        out[pid] = rec
    facts = {"line_total": sum((ln["supplier_amount_usd"] for ln in lines if ln["supplier_amount_usd"] is not None), Decimal("0.00")),
             "contracts": len(contracts), "notices": len(notices)}
    return out, facts


def main() -> int:
    """Recompute the table (or the transform's frame, with --dry-run) independently and compare; 0 when all passed."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    baseline = json.loads((run_dir / "baseline_before_write.json").read_text())
    spark = get_spark(args.profile)
    rep = DQReport(TARGET + (" (dry run, independent)" if args.dry_run else " (post-write, independent)"))
    if args.dry_run:
        from gold.project_portfolio import transform as T
        dfs, versions = T.load_sources(spark)
        g = T.build(dfs, versions, args.run_id)
    else:
        g = spark.table(TARGET)
    rep.equals(f"schema_exact ({len(SCHEMA)} columns, names, order, types)", [(f.name, f.dataType.simpleString()) for f in g.schema.fields], SCHEMA)
    exp, facts = expected(spark, args.run_id)
    rep.equals("independent:contract_line_total_equals_audit_total", facts["line_total"], AUDIT_TOTAL)
    cols = [n for n, _ in SCHEMA if n != "gold_processed_at"]
    assert set(cols) == set(next(iter(exp.values()))), "verifier must recompute every column except gold_processed_at"
    got = {}
    for r in g.select(*cols).collect():
        x = r.asDict()
        for c in TIMESTAMPS:
            x[c] = epoch(x[c])
        got[x["project_id"]] = x
    with COHORT.open(newline="", encoding="utf-8") as fh:
        cohort = {r["project_id"] for r in csv.DictReader(fh)}
    rep.equals("rows_and_unique_project_ids", (g.count(), len(got)), (2000, 2000))
    rep.equals("project_ids_equal_cohort", sorted(set(got) ^ cohort)[:10], [])
    mism: dict = {}
    for pid, e in exp.items():
        for c in cols:
            if got.get(pid, {}).get(c) != e[c]:
                mism.setdefault(c, []).append(pid)
    rep.equals(f"field_mismatches ({len(cols)} columns recomputed in Python)", {c: v[:5] for c, v in mism.items()}, {})
    tot = {"contract_award_count": sum(e["contract_award_count"] for e in exp.values()),
           "contract_award_amount_usd": sum((e["contract_award_amount_usd"] for e in exp.values() if e["contract_award_amount_usd"] is not None), Decimal("0.00")),
           "notice_count": sum(e["notice_count"] for e in exp.values())}
    rep.equals("independent:totals (contracts from lines, notices)", tot, {"contract_award_count": facts["contracts"], "contract_award_amount_usd": AUDIT_TOTAL,
                                                                          "notice_count": facts["notices"]})
    rep.equals("gold_processed_at_not_null", g.filter("gold_processed_at IS NULL").count(), 0)
    vals = list(got.values())
    recon = {"contracts": sum(v["contract_award_count"] for v in vals),
             "contract_amount_usd": sum((v["contract_award_amount_usd"] for v in vals if v["contract_award_amount_usd"] is not None), Decimal("0.00")),
             "notices": sum(v["notice_count"] for v in vals), "ibrd_loans": sum(v["ibrd_loan_count"] for v in vals),
             "ida_credits": sum(v["ida_credit_count"] for v in vals), "ieg_rated": sum(v["has_ieg_rating"] for v in vals),
             "project_details_v3": sum(v["has_project_details_v3"] for v in vals),
             "single_country_matched": sum(v["geography_type"] == "single_country" and v["country_context_status"] == "single_country_matched"
                                           and v["wdi_country_code"] is not None and v["wdi_country_iso3"] is not None for v in vals),
             "regional_null_country_context": sum(v["geography_type"] != "single_country" and v["wdi_country_code"] is None and v["wdi_country_iso3"] is None
                                                  and v["income_group_code_at_approval"] is None for v in vals)}
    rep.equals("reconciliation:approved_totals", recon, {"contracts": 178921, "contract_amount_usd": AUDIT_TOTAL, "notices": 243533, "ibrd_loans": 612,
                                                         "ida_credits": 2073, "ieg_rated": 750, "project_details_v3": 184, "single_country_matched": 1861,
                                                         "regional_null_country_context": 139})
    strings = [n for n, t in SCHEMA if t == "string"]
    texts = [" ".join(str(v[c]) for c in strings if v[c] is not None) for v in vals]
    allf = [json.loads(r[0]) for r in spark.table("workspace.wbg_bronze.bronze_projects_v3_allfields").select("raw_record").collect()]
    names = {nm.strip() for r in allf for nm in (r.get("teamleadname") or []) if nm and len(nm.strip()) >= 4}
    upis = {str(r["teamleaderupi"]).strip() for r in allf if r.get("teamleaderupi")}
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    phone = re.compile(r"\b(?:tel|t[\u00e9e]l[\u00e9e]phone|phone|fax|mobile|cell)\b[^0-9+]{0,12}\+?[0-9][0-9 ()./-]{6,}", re.I)
    rep.equals("privacy:personal_values_in_output (counts only)", {"team_lead_names": sum(any(nm in x for nm in names) for x in texts),
                                                                   "staff_ids": sum(any(re.search(rf"(?<![0-9]){u}(?![0-9])", x) for u in upis) for x in texts),
                                                                   "emails": sum(bool(email.search(x)) for x in texts), "phones": sum(bool(phone.search(x)) for x in texts)},
               {"team_lead_names": 0, "staff_ids": 0, "emails": 0, "phones": 0})
    rep.equals("privacy:no_prohibited_columns", [n for n, _ in SCHEMA if re.search(r"supplier_(name|id|text)|description|notice_text|team|upi|email|phone|contact|borrower|"
                                                                                  r"implementing|abstract|objective|raw_record$", n)], [])
    if not args.dry_run:
        props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {TARGET}").collect()}
        rep.equals("constraints_present", sorted(k.split(".")[-1] for k in props if k.startswith("delta.constraints.")), CONSTRAINTS)
        rep.equals("run_id_property", props.get("gold.run_id"), args.run_id)
        rep.equals("project_id_not_null_enforced", [f.nullable for f in g.schema.fields if f.name == "project_id"], [False])
        rep.equals("not_null_columns_enforced", sum(1 for f in g.schema.fields if not f.nullable), 28)
        rep.add("table_properties (reported)", {k: v for k, v in props.items() if k.startswith("gold.")}, "reported", True, "warn")
        rep.add("columns_with_comments (reported)", sum(1 for r in spark.sql(f"DESCRIBE TABLE {TARGET}").collect() if r[0] and not r[0].startswith("#") and r[2]),
                "reported", True, "warn")
        rep.equals("gold_schema_contains_only_this_table", sorted(r.tableName for r in spark.sql("SHOW TABLES IN workspace.wbg_gold").collect()), ["gold_project_portfolio"])
        hist = [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {TARGET}").orderBy("version").collect()]
        rep.equals("history_first_operation_is_create", hist[0][1] if hist else None, "CREATE TABLE AS SELECT")
        rep.add("delta_version_after_write", hist[-1][0] if hist else None, "reported", True, "warn")
    else:
        exists = bool(spark.sql("SHOW SCHEMAS IN workspace LIKE 'wbg_gold'").collect())
        rep.equals("gold_schema_and_target_still_absent", exists, False)
    rep.equals("cohort_manifest_unchanged", hashlib.sha256(COHORT.read_bytes()).hexdigest(), baseline["cohort_manifest_sha256"])
    masks = sorted([r[0], r[1], r[2], r[3]] for r in spark.sql(
        "SELECT table_schema, table_name, column_name, mask_name FROM workspace.information_schema.column_masks").collect())
    rep.equals("column_masks_unchanged", masks, baseline["column_masks"])
    if "grants" in baseline:
        now = {tbl: sorted([str(x) for x in r] for r in spark.sql(f"SHOW GRANTS ON TABLE {tbl}").collect()) for tbl in baseline["grants"]}
        rep.equals("permissions_unchanged (SHOW GRANTS, 35 tables)", {t: v for t, v in now.items() if v != baseline["grants"][t]}, {})
    rep.equals("baseline_covers_17_bronze_18_silver", (len(baseline["bronze"]), len(baseline["silver"])), (17, 18))
    for tbl, before in {**baseline["bronze"], **baseline["silver"]}.items():
        h = spark.sql(f"DESCRIBE HISTORY {tbl} LIMIT 1").collect()[0]
        rep.equals(f"unchanged:{tbl}", {"version": h.version, "rows": spark.table(tbl).count()}, before)
    print(rep.render())
    out = run_dir / ("dry_run_independent_verification.json" if args.dry_run else "post_write_verification.json")
    out.write_text(json.dumps({"run_id": args.run_id, "dry_run": args.dry_run, "verification": rep.to_dict()}, indent=1, default=str))
    print(f"\nreport: {out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
