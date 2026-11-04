"""Independent verification of the proposed KPI views (read-only).

Collects the needed columns of the five Gold tables and recomputes every row and column of every proposed view in plain
Python (no reuse of the SQL): grouping, counts, exact Decimal sums, discrete percentiles (percentile_disc definition:
first value whose cumulative share >= p), grouping-set levels, per-source country aggregates and lineage columns. Each
SQL result (re-executed read-only) must equal the recomputation cell by cell. Also re-checks the unchanged state.

Usage (from procurement-intelligence/):
  python -m gold.procurement_kpis.verify_procurement_kpis --run-id gold_procurement_kpis_dryrun_<ts>
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from typing import Any, Callable
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from gold.common import baseline as B
from gold.procurement_kpis import kpi_queries as Q
from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "gold" / "runs"
G = "workspace.wbg_gold."
PCTS = (("p25", 0.25), ("p50", 0.5), ("p75", 0.75), ("p90", 0.9))


def norm(v):
    """Comparable value: timestamps as epoch seconds, floats that are whole numbers as int."""
    if isinstance(v, datetime):
        return int(v.timestamp())
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def pdisc(values: list, p: float):
    vals = sorted(v for v in values if v is not None)
    return vals[max(math.ceil(p * len(vals)) - 1, 0)] if vals else None


def rows(spark, table: str, *cols: str) -> list[dict]:
    return [r.asDict() for r in spark.table(G + table).select(*cols).collect()]


def mx(values):
    vals = [v for v in values if v is not None]
    return max(vals) if vals else None


def expected(spark) -> dict[str, dict]:
    """Expected rows of every KPI view, keyed by view name, recomputed in plain Python from the Gold tables."""
    pf = rows(spark, "gold_project_portfolio", "project_id", "geography_type", "wdi_country_code", "has_api_notice_details", "has_project_details_v3",
              "has_ieg_rating", "projects_source_retrieved_at", "gold_run_id")
    ca = rows(spark, "gold_contract_analytics", "wb_contract_number", "project_id", "contract_fiscal_year", "region_name", "geography_type", "wdi_country_code",
              "project_country_name", "procurement_category", "procurement_method", "contract_amount_usd", "review_type", "is_multi_supplier",
              "has_anonymized_individual_consultant", "supplier_country_relation", "signed_before_board_approval", "contract_signing_date", "source_as_of_date",
              "gold_run_id")
    pn = rows(spark, "gold_procurement_notices", "project_id", "publication_fiscal_year", "notice_type", "notice_class", "region_name", "geography_type",
              "wdi_country_code", "project_country_name", "identical_metadata_group_size", "has_api_detail", "is_solicitation_notice", "is_tender_invitation",
              "procurement_method", "deadline_status", "published_response_window_days", "source_retrieved_at", "gold_run_id")
    fa = rows(spark, "gold_financing_analytics", "project_id", "lender", "board_approval_fiscal_year", "region_name", "geography_type", "wdi_country_code",
              "financing_status", "approved_before_project_approval", "has_negative_published_amount", "loan_transaction_coverage", "source_end_of_period", "gold_run_id")
    cc = rows(spark, "gold_country_context", "country_code", "country_iso3", "country_name", "year", "income_fy_starting_in_year", "income_group_fy_starting_in_year",
              "gold_run_id")
    single = lambda r: r["geography_type"] == "single_country"  # noqa: E731
    E: dict[str, dict] = {}

    E["vw_kpi_overview"] = {(): {
        "total_projects": len(pf), "single_country_projects": sum(single(r) for r in pf), "regional_projects": sum(not single(r) for r in pf),
        "projects_with_api_notice_details": sum(r["has_api_notice_details"] is True for r in pf),
        "projects_with_project_details_v3": sum(r["has_project_details_v3"] is True for r in pf), "projects_with_ieg_rating": sum(r["has_ieg_rating"] is True for r in pf),
        "projects_source_retrieved_at": mx(r["projects_source_retrieved_at"] for r in pf), "portfolio_gold_run_id": mx(r["gold_run_id"] for r in pf),
        "contract_awards": len(ca), "distinct_contract_awards": len({r["wb_contract_number"] for r in ca}),
        "total_contract_award_value_usd": sum((r["contract_amount_usd"] for r in ca), Decimal(0)), "projects_with_contract_awards": len({r["project_id"] for r in ca}),
        "first_contract_signing_date": min(r["contract_signing_date"] for r in ca), "last_contract_signing_date": max(r["contract_signing_date"] for r in ca),
        "contracts_source_as_of_date": mx(r["source_as_of_date"] for r in ca), "contracts_gold_run_id": mx(r["gold_run_id"] for r in ca),
        "procurement_notices": len(pn), **{f"{k}": sum(r["notice_class"] == c for r in pn) for k, c in (
            ("tender_invitation_notices", "tender_invitation"), ("prequalification_notices", "prequalification_invitation"),
            ("expression_of_interest_notices", "expression_of_interest"), ("contract_award_announcement_notices", "contract_award_announcement"),
            ("general_procurement_notices", "general_procurement_notice"))},
        "projects_with_notices": len({r["project_id"] for r in pn}), "notices_source_retrieved_at": mx(r["source_retrieved_at"] for r in pn),
        "notices_gold_run_id": mx(r["gold_run_id"] for r in pn),
        "ibrd_loan_instruments": sum(r["lender"] == "IBRD" for r in fa), "ida_credit_instruments": sum(r["lender"] == "IDA" for r in fa),
        "projects_with_ibrd_ida_instruments": len({r["project_id"] for r in fa}), "financing_source_end_of_period": mx(r["source_end_of_period"] for r in fa),
        "financing_gold_run_id": mx(r["gold_run_id"] for r in fa),
        "economies_in_country_context": len({r["country_code"] for r in cc}), "country_context_first_year": min(r["year"] for r in cc),
        "country_context_last_year": max(r["year"] for r in cc), "country_context_gold_run_id": mx(r["gold_run_id"] for r in cc)}}

    grp: dict = defaultdict(list)
    for r in ca:
        grp[(r["contract_fiscal_year"], r["region_name"], r["geography_type"], r["wdi_country_code"], r["procurement_category"], r["procurement_method"])].append(r)
    E["vw_kpi_contract_awards"] = {k: {
        "contract_fiscal_year": k[0], "region_name": k[1], "geography_type": k[2], "wdi_country_code": k[3],
        "country_name": v[0]["project_country_name"] if k[2] == "single_country" else None, "procurement_category": k[4], "procurement_method": k[5],
        "contract_awards": len(v), "contract_award_value_usd": sum((r["contract_amount_usd"] for r in v), Decimal(0)),
        "prior_review_contracts": sum(r["review_type"] == "Prior" for r in v), "post_review_contracts": sum(r["review_type"] == "Post" for r in v),
        "review_type_unknown_contracts": sum(r["review_type"] is None for r in v), "multi_supplier_contracts": sum(r["is_multi_supplier"] is True for r in v),
        "individual_consultant_contracts": sum(r["has_anonymized_individual_consultant"] is True for r in v),
        "same_country_supplier_contracts": sum(r["supplier_country_relation"] == "all_suppliers_same_country" for r in v),
        "other_country_supplier_contracts": sum(r["supplier_country_relation"] == "includes_other_country" for r in v),
        "supplier_country_undetermined_contracts": sum(r["supplier_country_relation"] == "undetermined" for r in v),
        "contracts_signed_before_board_approval": sum(r["signed_before_board_approval"] is True for r in v),
        "source_as_of_date": mx(r["source_as_of_date"] for r in v), "source_gold_run_id": mx(r["gold_run_id"] for r in v)} for k, v in grp.items()}

    vd: dict = {}
    keyf: Callable[[dict], tuple[Any, Any]]
    for level, keyf in (("fiscal_year_x_category", lambda r: (r["contract_fiscal_year"], r["procurement_category"])),
                        ("fiscal_year", lambda r: (r["contract_fiscal_year"], None)), ("category", lambda r: (None, r["procurement_category"])),
                        ("all_contracts", lambda r: (None, None))):
        g2: dict = defaultdict(list)
        for r in ca:
            g2[keyf(r)].append(r)
        for (fy, cat), v in g2.items():
            amts = [r["contract_amount_usd"] for r in v]
            vd[(level, fy, cat)] = {"grouping_level": level, "contract_fiscal_year": fy, "procurement_category": cat, "contract_awards": len(v),
                                    "contract_award_value_usd": sum(amts, Decimal(0)), "min_contract_value_usd": min(amts),
                                    **{f"{n}_contract_value_usd": pdisc(amts, q) for n, q in PCTS}, "max_contract_value_usd": max(amts),
                                    "source_as_of_date": mx(r["source_as_of_date"] for r in v), "source_gold_run_id": mx(r["gold_run_id"] for r in v)}
    E["vw_kpi_contract_value_distribution"] = vd

    g3: dict = defaultdict(list)
    for r in pn:
        g3[(r["publication_fiscal_year"], r["notice_type"], r["region_name"], r["geography_type"], r["wdi_country_code"])].append(r)
    E["vw_kpi_notice_activity"] = {k: {
        "publication_fiscal_year": k[0], "notice_type": k[1], "notice_class": v[0]["notice_class"], "region_name": k[2], "geography_type": k[3], "wdi_country_code": k[4],
        "country_name": v[0]["project_country_name"] if k[3] == "single_country" else None, "procurement_notices": len(v),
        "notices_in_identical_metadata_groups": sum((r["identical_metadata_group_size"] or 0) > 1 for r in v), "notices_with_api_detail": sum(r["has_api_detail"] is True for r in v),
        "source_retrieved_at": mx(r["source_retrieved_at"] for r in v), "source_gold_run_id": mx(r["gold_run_id"] for r in v)} for k, v in g3.items()}

    rw: dict = {}
    sol = [r for r in pn if r["is_solicitation_notice"]]
    wkey: Callable[[dict], tuple[Any, Any, Any]]
    for level, wkey in (("type_x_fiscal_year_x_method", lambda r: (r["notice_type"], r["publication_fiscal_year"], r["procurement_method"])),
                        ("type_x_fiscal_year", lambda r: (r["notice_type"], r["publication_fiscal_year"], None)),
                        ("type_x_method", lambda r: (r["notice_type"], None, r["procurement_method"])), ("type", lambda r: (r["notice_type"], None, None))):
        g4: dict = defaultdict(list)
        for r in sol:
            g4[wkey(r)].append(r)
        for (t, fy, m), v in g4.items():
            w = [r["published_response_window_days"] for r in v if r["deadline_status"] == "positive_window"]
            st = lambda s: sum(r["deadline_status"] == s for r in v)  # noqa: E731
            rw[(level, t, fy, m)] = {"grouping_level": level, "notice_type": t, "publication_fiscal_year": fy, "procurement_method": m,
                                     "eligible_solicitation_notices": len(v), "positive_window_notices": st("positive_window"),
                                     "over_180_day_review_notices": st("positive_window_over_180_days_review"), "no_window_notices": st("equals_publication_date_no_window"),
                                     "invalid_window_notices": st("before_publication_date_invalid"), "missing_deadline_notices": st("missing"),
                                     "min_window_days": min(w) if w else None, **{f"{n}_window_days": pdisc(w, q) for n, q in PCTS},
                                     "max_window_days": max(w) if w else None, "source_retrieved_at": mx(r["source_retrieved_at"] for r in v),
                                     "source_gold_run_id": mx(r["gold_run_id"] for r in v)}
    E["vw_kpi_response_windows"] = rw

    g5: dict = defaultdict(list)
    for r in fa:
        g5[(r["lender"], r["board_approval_fiscal_year"], r["region_name"], r["geography_type"], r["wdi_country_code"], r["financing_status"])].append(r)
    E["vw_kpi_financing_instruments"] = {k: {
        "lender": k[0], "board_approval_fiscal_year": k[1], "region_name": k[2], "geography_type": k[3], "wdi_country_code": k[4], "financing_status": k[5],
        "financing_instruments": len(v), "instruments_approved_before_project_approval": sum(r["approved_before_project_approval"] is True for r in v),
        "instruments_with_negative_published_amount": sum(r["has_negative_published_amount"] is True for r in v),
        "instruments_with_transaction_coverage": sum(r["loan_transaction_coverage"] is True for r in v),
        "source_end_of_period": mx(r["source_end_of_period"] for r in v), "source_gold_run_id": mx(r["gold_run_id"] for r in v)} for k, v in g5.items()}

    k25 = {r["country_code"]: r for r in cc if r["year"] == 2025}
    key = lambda r: (r["geography_type"], r["wdi_country_code"])  # noqa: E731
    agg: dict = defaultdict(lambda: {"projects": 0, "contract_awards": 0, "contract_award_value_usd": None, "procurement_notices": 0, "tender_invitation_notices": 0,
                                     "tender_invitations_with_positive_window": 0, "ibrd_loan_instruments": 0, "ida_credit_instruments": 0})
    for r in pf:
        agg[key(r)]["projects"] += 1
    for r in ca:
        a = agg[key(r)]
        a["contract_awards"] += 1
        a["contract_award_value_usd"] = (a["contract_award_value_usd"] or Decimal(0)) + r["contract_amount_usd"]
    for r in pn:
        a = agg[key(r)]
        a["procurement_notices"] += 1
        a["tender_invitation_notices"] += r["is_tender_invitation"] is True
        a["tender_invitations_with_positive_window"] += r["is_tender_invitation"] is True and r["deadline_status"] == "positive_window"
    for r in fa:
        agg[key(r)]["ibrd_loan_instruments" if r["lender"] == "IBRD" else "ida_credit_instruments"] += 1
    E["vw_kpi_country_comparison"] = {k: {"geography_type": k[0], "wdi_country_code": k[1], "country_iso3": k25.get(k[1], {}).get("country_iso3"),
                                          "country_name": k25.get(k[1], {}).get("country_name"), "latest_income_fiscal_year": k25.get(k[1], {}).get("income_fy_starting_in_year"),
                                          "latest_income_group": k25.get(k[1], {}).get("income_group_fy_starting_in_year"), **v} for k, v in agg.items()}
    return E


def main() -> int:
    """Recompute every KPI view in plain Python and compare (the created views with --post-create); 0 if all passed."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--post-create", action="store_true", help="verify the created views (read them from the catalog)")
    ap.add_argument("--approved-dry-run", help="post-create: dry run whose reviewed schemas and row counts must match")
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    base = json.loads((run_dir / "baseline_before_write.json").read_text())
    spark = get_spark(DEFAULT_PROFILE)
    rep = DQReport("proposed procurement KPI views (independent, read-only)")
    E = expected(spark)
    if args.post_create:
        views = sorted(r.viewName for r in spark.sql(f"SHOW VIEWS IN {Q.VIEW_SCHEMA}").collect())
        rep.equals("post_create:exactly_the_7_approved_views_exist", views, sorted(Q.QUERIES))
        appr = json.loads((RUNS / args.approved_dry_run / "dq_report.json").read_text())["summary"]
    for name in Q.QUERIES:
        df = spark.table(f"{Q.VIEW_SCHEMA}.{name}") if args.post_create else spark.sql(Q.bound(name))
        if args.post_create:
            rep.equals(f"{name}:schema_equals_approved", [[f.name, f.dataType.simpleString()] for f in df.schema.fields], [list(x) for x in appr["schemas"][name]])
            rep.equals(f"{name}:row_count_equals_reviewed", df.count(), appr["row_counts"][name])
            ext = {r[0]: r[1] for r in spark.sql(f"DESCRIBE TABLE EXTENDED {Q.VIEW_SCHEMA}.{name}").collect()}
            rep.equals(f"{name}:is_view_with_approved_comment", (ext.get("Type"), ext.get("Comment")), ("VIEW", Q.VIEW_COMMENTS[name]))
        cols = [f.name for f in df.schema.fields]
        got = {tuple(r[c] for c in Q.GRAINS[name]): {c: norm(r[c]) for c in cols} for r in (x.asDict() for x in df.collect())}
        exp = {k: {c: norm(v.get(c)) for c in cols} for k, v in E[name].items()}
        rep.equals(f"{name}:columns_recomputed_equal_expected_set", sorted(set(cols) ^ set(next(iter(E[name].values())))), [])
        rep.equals(f"{name}:row_keys_equal ({len(exp)} rows)", (len(got), sorted(map(str, set(got) ^ set(exp)))[:5]), (len(exp), []))
        mism: dict = {}
        for k, e in exp.items():
            for c in cols:
                if got.get(k, {}).get(c) != e[c]:
                    mism.setdefault(c, []).append(str(k))
        rep.equals(f"{name}:cell_mismatches ({len(cols)} columns)", {c: v[:3] for c, v in mism.items()}, {})
    B.check_unchanged(spark, base, rep, (17, 18, 5))
    tables = [r.tableName for r in spark.sql("SHOW TABLES IN workspace.wbg_gold").collect()]
    view_names = {r.viewName for r in spark.sql("SHOW VIEWS IN workspace.wbg_gold").collect()}
    if args.post_create:
        rep.equals("post_create:gold_physical_tables_still_exactly_5", sorted(t for t in tables if t not in view_names), sorted(Q.SOURCES.values()))
        o = next(iter(spark.table(f"{Q.VIEW_SCHEMA}.vw_kpi_overview").collect())).asDict()
        rep.equals("post_create:headline_totals_exact", (o["total_projects"], o["contract_awards"], o["total_contract_award_value_usd"], o["procurement_notices"],
                                                         o["ibrd_loan_instruments"], o["ida_credit_instruments"]),
                   (2000, 178921, Decimal("85248987799.45"), 243533, 612, 2073))
        ca = spark.table(f"{Q.VIEW_SCHEMA}.vw_kpi_contract_awards").groupBy().sum("contract_award_value_usd", "contract_awards").collect()[0]
        cc = spark.table(f"{Q.VIEW_SCHEMA}.vw_kpi_country_comparison").groupBy().sum("contract_award_value_usd", "contract_awards").collect()[0]
        src = spark.table(f"{Q.VIEW_SCHEMA}.gold_contract_analytics").groupBy().sum("contract_amount_usd").collect()[0][0]
        rep.equals("post_create:financial_sums_not_inflated (cube, country view, Gold source)", (ca[0], ca[1], cc[0], cc[1], src),
                   (Decimal("85248987799.45"), 178921, Decimal("85248987799.45"), 178921, Decimal("85248987799.45")))
    else:
        rep.equals("no_kpi_objects_created", sorted(t for t in tables if "kpi" in t), [])
    print(rep.render())
    out = run_dir / ("post_create_verification.json" if args.post_create else "independent_verification.json")
    out.write_text(json.dumps({"run_id": args.run_id, "verification": rep.to_dict(), "verified_at": datetime.now(timezone.utc).isoformat()}, indent=1, default=str))
    print(f"\nreport: {out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
