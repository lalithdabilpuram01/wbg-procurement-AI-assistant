"""Independent verification of gold_procurement_notices (read-only).

Collects the needed Silver columns and recomputes every Gold column per notice in plain Python with its own copy of the
rules (no reuse of the Spark transform or checks): notice classes and flags, deadline status and windows, country and
income context, API revision metadata and bid-reference sharing counts, link aggregates and lineage. Bid references are
held in memory only to count sharing and are never printed or written. Also checks the exact schema, per-project and
per-type reconciliation with gold_project_portfolio and the unchanged Bronze/Silver/Gold/grants/mask/cohort state.

Usage (from procurement-intelligence/):
  python -m gold.procurement_notices.verify_procurement_notices_gold --dry-run --run-id gold_procurement_notices_<ts>
  python -m gold.procurement_notices.verify_procurement_notices_gold --run-id gold_procurement_notices_<ts> --approved-dry-run <dry_run_id>
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from gold.common import baseline as B
from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "gold" / "runs"
TARGET = "workspace.wbg_gold.gold_procurement_notices"
PORTFOLIO = "workspace.wbg_gold.gold_project_portfolio"
S = "workspace.wbg_silver."
SNAPSHOT = date(2026, 10, 21)
SOURCES = sorted(["procurement_notices", "projects", "wdi_country", "income_classification_history", "procurement_notice_details", "notice_contract_links"])
CLASSES = {"Invitation for Bids": "tender_invitation", "Invitation for Prequalification": "prequalification_invitation",
           "Request for Expression of Interest": "expression_of_interest", "Contract Award": "contract_award_announcement",
           "Small Contracts Award": "contract_award_announcement", "General Procurement Notice": "general_procurement_notice"}
SOLICIT = {"Invitation for Bids", "Invitation for Prequalification", "Request for Expression of Interest"}
AWARD = {"Contract Award", "Small Contracts Award"}
PORTFOLIO_TYPES = {"Contract Award": "contract_award_notice_count", "Invitation for Bids": "invitation_for_bids_notice_count",
                   "Request for Expression of Interest": "expression_of_interest_notice_count", "General Procurement Notice": "general_procurement_notice_count",
                   "Invitation for Prequalification": "prequalification_notice_count", "Small Contracts Award": "small_contracts_award_notice_count"}
SCHEMA = [("notice_op_id", "string"), ("notice_id", "string"), ("project_id", "string"), ("project_name", "string"), ("region_name", "string"),
          ("geography_type", "string"), ("project_country_name", "string"), ("wdi_country_code", "string"), ("wdi_country_iso3", "string"),
          ("country_context_status", "string"), ("notice_country_code_published", "string"), ("notice_country_name_published", "string"),
          ("notice_country_code_status", "string"), ("notice_region_name_published", "string"), ("income_group_code_at_publication_fy", "string"),
          ("income_group_status_at_publication_fy", "string"), ("notice_type", "string"), ("notice_class", "string"), ("is_tender_invitation", "boolean"),
          ("is_solicitation_notice", "boolean"), ("is_award_announcement", "boolean"), ("procurement_category", "string"), ("procurement_method", "string"),
          ("sectors", "array<string>"), ("publication_date", "date"), ("publication_fiscal_year", "int"), ("publication_calendar_year", "int"),
          ("deadline_date_published", "date"), ("deadline_status", "string"), ("published_response_window_days", "int"), ("deadline_after_source_snapshot", "boolean"),
          ("identical_metadata_group_size", "bigint"), ("has_api_detail", "boolean"), ("api_notice_status", "string"), ("api_award_notice_version_no", "int"),
          ("api_submission_deadline_date", "date"), ("api_notices_sharing_bid_ref_count", "bigint"), ("api_same_type_notices_sharing_bid_ref_count", "bigint"),
          ("linked_contract_count", "bigint"), ("contract_link_status", "string"), ("source_dataset", "string"), ("source_retrieved_at", "timestamp"),
          ("source_row_sha256", "string"), ("bronze_source_file", "string"), ("bronze_input_file_sha256", "string"), ("source_population_sha256", "string"),
          ("notice_snapshot_semantics", "string"), ("silver_notices_run_id", "string"), ("silver_source_versions", "string"), ("gold_run_id", "string"),
          ("gold_processed_at", "timestamp"), ("transform_version", "string")]


def epoch(v) -> int | None:
    """Timezone-free instant (Databricks Connect collect() returns naive local-time datetimes)."""
    return None if v is None else int(v.timestamp())


def rows(spark, table: str, *cols: str) -> list[dict]:
    return [r.asDict() for r in spark.table(S + table).select(*cols).collect()]


def expected(spark, run_id: str) -> dict:
    """Every expected Gold row, keyed by notice_op_id, recomputed in plain Python from the Silver tables."""
    N = rows(spark, "procurement_notices", "notice_op_id", "notice_id", "project_id", "notice_type", "country_code", "country_name", "region_name_source",
             "procurement_category", "procurement_method", "sectors", "publication_date", "publication_fiscal_year", "publication_calendar_year", "deadline_date",
             "identical_metadata_group_size", "has_api_detail", "source_dataset", "source_retrieved_at", "source_row_sha256", "bronze_source_file",
             "bronze_input_file_sha256", "source_population_sha256", "silver_run_id")
    P = {r["project_id"]: r for r in rows(spark, "projects", "project_id", "project_name", "region_name", "geography_type", "country_name", "wdi_country_code")}
    iso = {r["country_code"]: r["country_wb_id"] for r in rows(spark, "wdi_country", "country_code", "country_wb_id")}
    inc = {(r["iso3_code"], r["fiscal_year"]): r for r in rows(spark, "income_classification_history", "iso3_code", "fiscal_year", "income_group_code", "classification_status")}
    D = rows(spark, "procurement_notice_details", "notice_op_id", "project_id", "notice_type", "notice_status", "award_notice_version_no", "submission_deadline_date",
             "bid_reference_normalized")
    ref = Counter((d["project_id"], d["bid_reference_normalized"]) for d in D if d["bid_reference_normalized"] is not None)
    ref_t = Counter((d["project_id"], d["bid_reference_normalized"], d["notice_type"]) for d in D if d["bid_reference_normalized"] is not None)
    det = {d["notice_op_id"]: d for d in D}
    covered = {d["project_id"] for d in D}
    links: dict = defaultdict(list)
    for k in rows(spark, "notice_contract_links", "notice_op_id", "ambiguity_status"):
        links[k["notice_op_id"]].append(k["ambiguity_status"])
    versions = json.dumps({t: spark.sql(f"DESCRIBE HISTORY {S}{t} LIMIT 1").collect()[0].version for t in SOURCES}, separators=(",", ":"))
    out = {}
    for n in N:
        p = P[n["project_id"]]
        single = p["geography_type"] == "single_country"
        w = p["wdi_country_code"] if single else None
        cc = n["country_code"]
        ic = inc.get((iso.get(w), n["publication_fiscal_year"])) if single else None
        t, sol, award = n["notice_type"], n["notice_type"] in SOLICIT, n["notice_type"] in AWARD
        days = (n["deadline_date"] - n["publication_date"]).days if n["deadline_date"] else None
        if not sol:
            status = "not_applicable_notice_type"
        elif days is None:
            status = "missing"
        elif days == 0:
            status = "equals_publication_date_no_window"
        elif days < 0:
            status = "before_publication_date_invalid"
        else:
            status = "positive_window_over_180_days_review" if days > 180 else "positive_window"
        win = days if sol and days is not None and days > 0 else None
        d = det.get(n["notice_op_id"])
        dd: dict = d or {}
        r_ = dd.get("bid_reference_normalized")
        cov, ks = n["project_id"] in covered, links.get(n["notice_op_id"], [])
        out[n["notice_op_id"]] = {
            "notice_op_id": n["notice_op_id"], "notice_id": n["notice_id"], "project_id": n["project_id"], "project_name": p["project_name"],
            "region_name": p["region_name"], "geography_type": p["geography_type"], "project_country_name": p["country_name"], "wdi_country_code": w,
            "wdi_country_iso3": iso.get(w) if single else None,
            "country_context_status": ("single_country_matched" if w else "single_country_unmatched") if single else "regional_not_applicable",
            "notice_country_code_published": cc, "notice_country_name_published": n["country_name"],
            "notice_country_code_status": "regional_project" if not single else "missing_in_source" if cc is None else
                                          "equal_to_project_wdi_code" if cc == p["wdi_country_code"] else "differs_from_project_wdi_code",
            "notice_region_name_published": n["region_name_source"],
            "income_group_code_at_publication_fy": ic["income_group_code"] if ic else None,
            "income_group_status_at_publication_fy": "regional_not_applicable" if not single else (ic["classification_status"] if ic else "no_row_in_source"),
            "notice_type": t, "notice_class": CLASSES.get(t), "is_tender_invitation": t == "Invitation for Bids", "is_solicitation_notice": sol,
            "is_award_announcement": award, "procurement_category": n["procurement_category"], "procurement_method": n["procurement_method"], "sectors": n["sectors"],
            "publication_date": n["publication_date"], "publication_fiscal_year": n["publication_fiscal_year"], "publication_calendar_year": n["publication_calendar_year"],
            "deadline_date_published": n["deadline_date"], "deadline_status": status, "published_response_window_days": win,
            "deadline_after_source_snapshot": None if win is None else n["deadline_date"] > SNAPSHOT,
            "identical_metadata_group_size": n["identical_metadata_group_size"], "has_api_detail": n["has_api_detail"],
            "api_notice_status": d["notice_status"] if d else None, "api_award_notice_version_no": d["award_notice_version_no"] if d else None,
            "api_submission_deadline_date": d["submission_deadline_date"] if d else None,
            "api_notices_sharing_bid_ref_count": ref[(dd["project_id"], r_)] if r_ is not None else None,
            "api_same_type_notices_sharing_bid_ref_count": ref_t[(dd["project_id"], r_, dd["notice_type"])] if r_ is not None else None,
            "linked_contract_count": len(ks) if award and cov else None,
            "contract_link_status": "not_award_notice" if not award else "not_covered" if not cov else "no_link_found" if not ks else
                                    "ambiguous" if "ambiguous" in ks else "unambiguous",
            "source_dataset": n["source_dataset"], "source_retrieved_at": epoch(n["source_retrieved_at"]), "source_row_sha256": n["source_row_sha256"],
            "bronze_source_file": n["bronze_source_file"], "bronze_input_file_sha256": n["bronze_input_file_sha256"], "source_population_sha256": n["source_population_sha256"],
            "notice_snapshot_semantics": "ds00979_snapshot_retrieved_2026_10_21", "silver_notices_run_id": n["silver_run_id"], "silver_source_versions": versions,
            "gold_run_id": run_id, "transform_version": "gold_procurement_notices_v1.0.0"}
    return out


def main() -> int:
    """Recompute the table (or the transform's frame, with --dry-run) independently and compare; 0 when all passed."""
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
        from gold.procurement_notices import transform as T
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
        got[x["notice_op_id"]] = x
    rep.equals("rows_and_unique_notices", (g.count(), len(got), len({v["notice_id"] for v in got.values()})), (243533, 243533, 243533))
    rep.equals("notices_equal_silver", sorted(set(got) ^ set(exp))[:10], [])
    mism: dict = {}
    for k, e in exp.items():
        for c in cols:
            if got.get(k, {}).get(c) != e[c]:
                mism.setdefault(c, []).append(k)
    rep.equals(f"field_mismatches ({len(cols)} columns recomputed in Python)", {c: v[:5] for c, v in mism.items()}, {})
    with B.COHORT.open(newline="", encoding="utf-8") as fh:
        cohort = {r["project_id"] for r in csv.DictReader(fh)}
    per = Counter((v["project_id"], v["notice_type"]) for v in got.values())
    rep.equals("projects_in_cohort", len({v["project_id"] for v in got.values()} - cohort), 0)
    port = spark.table(PORTFOLIO).select("project_id", *PORTFOLIO_TYPES.values()).collect()
    rep.equals("per_project_per_type_equal_gold_project_portfolio",
               sorted(r["project_id"] for r in port if any(per.get((r["project_id"], t), 0) != r[c] for t, c in PORTFOLIO_TYPES.items()))[:10], [])
    rep.equals("award_and_gpn_never_get_a_window", sum(1 for v in got.values() if not v["is_solicitation_notice"] and v["published_response_window_days"] is not None), 0)
    texts = [" ".join(" ".join(v[c]) if isinstance(v[c], list) else str(v[c]) for c, t in SCHEMA if c in v and t in ("string", "array<string>")
                      and not c.endswith("_sha256") and v[c] is not None) for v in got.values()]
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    phone = re.compile(r"\b(?:tel|t[ée]l[ée]phone|phone|fax|mobile|cell)\b[^0-9+]{0,12}\+?[0-9][0-9 ()./-]{6,}", re.I)
    rep.equals("privacy:emails_phones_in_output (counts)", (sum(bool(email.search(x)) for x in texts), sum(bool(phone.search(x)) for x in texts)), (0, 0))
    rep.equals("privacy:no_free_text_or_identity_columns", [n for n, _ in SCHEMA if re.search(
        r"notice_text|description|bid_reference$|award_reference|notice_url|supplier_(name|id|text)|contact|email|phone|team|upi", n)], [])
    rep.equals("gold_processed_at_not_null", g.filter("gold_processed_at IS NULL").count(), 0)
    if not args.dry_run:
        props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {TARGET}").collect()}
        rep.equals("constraints_present_count", len([k for k in props if k.startswith("delta.constraints.")]), 17)
        rep.equals("run_id_property", props.get("gold.run_id"), args.run_id)
        rep.equals("not_null_columns_enforced", sum(1 for f in g.schema.fields if not f.nullable), 27)
        hist = [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {TARGET}").orderBy("version").collect()]
        rep.equals("history_first_operation_is_create", hist[0][1] if hist else None, "CREATE TABLE AS SELECT")
        rep.add("delta_version_after_write", hist[-1][0] if hist else None, "reported", True, "warn")
    else:
        rep.equals("target_still_absent", spark.catalog.tableExists(TARGET), False)
    if args.approved_dry_run:
        appr = json.loads((RUNS / args.approved_dry_run / "baseline_before_write.json").read_text())
        keys = ("bronze", "silver", "gold", "grants", "column_masks", "cohort_manifest_sha256")
        rep.equals("baseline_equals_approved_dry_run_baseline", {k: base[k] == appr[k] for k in keys}, {k: True for k in keys})
    B.check_unchanged(spark, base, rep, (17, 18, 2))
    print(rep.render())
    out = run_dir / ("dry_run_independent_verification.json" if args.dry_run else "post_write_verification.json")
    out.write_text(json.dumps({"run_id": args.run_id, "dry_run": args.dry_run, "verification": rep.to_dict(),
                               "verified_at": datetime.now(timezone.utc).isoformat()}, indent=1, default=str))
    print(f"\nreport: {out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
