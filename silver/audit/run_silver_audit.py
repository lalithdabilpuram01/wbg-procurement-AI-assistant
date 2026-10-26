"""Consolidated Silver quality audit (READ ONLY): inventory, unchanged-state, cross-table relationships, reference-data
joins, financing, IEG, ML-feasibility probes and privacy scans over all 18 Silver tables.

Only SELECT / DESCRIBE / SHOW statements are issued; nothing is created, altered or written in Databricks. Results go to
silver/runs/silver_audit_<ts>/ (audit_results.json, dq_report.json). Personal data is never printed: privacy scans
return row counts only, notice_text is only compared with the mask placeholder, and supplier names are never selected
for output.

Usage (from procurement-intelligence/):
  python -m silver.audit.run_silver_audit --baseline-run project_details_20261024T041006Z
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.notices.transform import EMAIL_RE
from silver.notices.transform import PHONE_RE as _PHONE

S = "workspace.wbg_silver."
REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "silver" / "runs"
COHORT = REPO / "cohort" / "poc_cohort_2000_manifest.csv"
PHONE_RE = "(?i)" + _PHONE  # Java regex is case-sensitive by default; the shared detector is written lower-case
MASK_PLACEHOLDER = "[RESTRICTED: requires membership of wbg_restricted_notice_text]"
# table: (business key columns, expected rows, grain)
TABLES: dict[str, tuple[list[str], int, str]] = {
    "projects": (["project_id"], 2000, "project"),
    "project_details_v3": (["project_id"], 184, "project (Phase 1 cluster subset)"),
    "project_loan_transactions": (["transaction_key"], 16643, "published loan transaction (array position)"),
    "contract_award_lines": (["line_key"], 186907, "contract x supplier line"),
    "contract_awards": (["wb_contract_number"], 178921, "contract (FY2020+)"),
    "contract_awards_legacy": (["contract_number"], 6448, "contract (FY2006-2016, prior review only)"),
    "procurement_notices": (["notice_op_id"], 243533, "notice (DS00979)"),
    "procurement_notice_details": (["notice_op_id"], 20305, "notice detail (API, 184 projects)"),
    "notice_contract_link_candidates": (["candidate_key"], 57247, "notice x contract candidate"),
    "notice_contract_links": (["link_key"], 12039, "evidence-supported notice x contract link"),
    "ieg_project_ratings": (["project_id"], 750, "project ICR review"),
    "ibrd_loans": (["loan_number"], 612, "IBRD loan"),
    "ida_credits": (["credit_number"], 2073, "IDA credit/grant"),
    "wdi_country": (["country_code"], 296, "WDI entity"),
    "wdi_indicator_observations": (["country_code", "indicator_code", "year"], 20748, "entity x indicator x year"),
    "wgi_estimates": (["entity_key", "indicator_code", "year"], 34992, "entity x indicator x year"),
    "cpia_ratings": (["country_code", "indicator_code", "year"], 105336, "entity x indicator x year"),
    "income_classification_history": (["iso3_code", "fiscal_year"], 8736, "economy x fiscal year"),
}


def t(name: str) -> DataFrame:
    return get_spark().table(S + name)


def n(df: DataFrame, cond=None) -> int:
    return (df.filter(cond) if cond is not None else df).count()


def dec(v) -> str | None:
    return None if v is None else str(v)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline-run", required=True, help="run folder whose baseline_before_write.json is the reference state")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    spark = get_spark(args.profile)
    run_id = "silver_audit_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    rep = DQReport("Consolidated Silver audit (read-only)")
    R: dict = {"run_id": run_id, "baseline_run": args.baseline_run}
    with COHORT.open(newline="", encoding="utf-8") as fh:
        cohort = {r["project_id"] for r in csv.DictReader(fh)}
    rep.equals("cohort_manifest_project_ids", len(cohort), 2000)

    # ---- 1. inventory -------------------------------------------------------------------------------------------
    listed = sorted(r.tableName for r in spark.sql("SHOW TABLES IN workspace.wbg_silver").collect())
    rep.equals("silver_tables_exactly_the_18_planned", listed, sorted(TABLES))
    inv = {}
    for name, (key, rows, grain) in TABLES.items():
        df = t(name)
        hist = spark.sql(f"DESCRIBE HISTORY {S}{name}").orderBy("version").collect()
        props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {S}{name}").collect()}
        cnt = df.count()
        e = {"rows": cnt, "columns": len(df.columns), "grain": grain, "key": key, "delta_version": hist[-1].version,
             "first_operation": hist[0].operation, "silver_run_id": props.get("silver.run_id"), "transform_version": props.get("silver.transform_version"),
             "check_constraints": sorted(k.split(".")[-1] for k in props if k.startswith("delta.constraints.")),
             "not_null_columns": sorted(f.name for f in df.schema.fields if not f.nullable),
             "lineage_columns": sorted(c for c in df.columns if c.startswith(("bronze_", "source_")) or c in ("raw_record_sha256", "silver_run_id", "transform_version")),
             "schema": [(f.name, f.dataType.simpleString()) for f in df.schema.fields]}
        e["distinct_keys"] = df.select(*key).distinct().count()
        e["null_key_rows"] = n(df, " OR ".join(f"`{k}` IS NULL" for k in key))
        if "project_id" in df.columns:
            ids = {r[0] for r in df.select("project_id").distinct().collect()}
            e["distinct_project_ids"] = len(ids)
            e["project_ids_outside_cohort"] = len(ids - cohort - {None})
            e["rows_with_null_project_id"] = n(df, "project_id IS NULL")
            rep.equals(f"inventory:{name}:project_ids_in_cohort", e["project_ids_outside_cohort"], 0)
        rep.equals(f"inventory:{name}:rows", cnt, rows)
        rep.equals(f"inventory:{name}:business_key_unique_and_not_null", (e["distinct_keys"], e["null_key_rows"]), (cnt, 0))
        rep.equals(f"inventory:{name}:run_id_property_and_lineage_columns", (e["silver_run_id"] is not None, "silver_run_id" in df.columns and "transform_version" in df.columns),
                   (True, True))
        inv[name] = e
    R["inventory"] = inv

    # ---- 2. unchanged state ---------------------------------------------------------------------------------------
    base = json.loads((RUNS / args.baseline_run / "baseline_before_write.json").read_text())
    now_b = {}
    for tbl, before in base["bronze"].items():
        h = spark.sql(f"DESCRIBE HISTORY {tbl} LIMIT 1").collect()[0]
        now_b[tbl] = {"version": h.version, "rows": spark.table(tbl).count()}
        rep.equals(f"unchanged:bronze:{tbl.split('.')[-1]}", now_b[tbl], before)
    rep.equals("unchanged:bronze_table_count", len(base["bronze"]), 17)
    for tbl, before in base["silver"].items():
        rep.equals(f"unchanged:silver:{tbl.split('.')[-1]}", {"version": inv[tbl.split(".")[-1]]["delta_version"], "rows": inv[tbl.split(".")[-1]]["rows"]}, before)
    rep.equals("unchanged:new_silver_tables_at_post_write_versions", (inv["project_details_v3"]["delta_version"], inv["project_loan_transactions"]["delta_version"]), (32, 31))
    rep.equals("unchanged:cohort_manifest_sha256", hashlib.sha256(COHORT.read_bytes()).hexdigest(), base["cohort_manifest_sha256"])
    masks = sorted([r[0], r[1], r[2], r[3]] for r in spark.sql(
        "SELECT table_schema, table_name, column_name, mask_name FROM workspace.information_schema.column_masks").collect())
    rep.equals("unchanged:column_masks", masks, base["column_masks"])
    R["bronze_now"] = now_b

    # ---- 3. projects / contracts / notices / links ------------------------------------------------------------------
    P, L, A, LG = t("projects"), t("contract_award_lines"), t("contract_awards"), t("contract_awards_legacy")
    N, D, C, K = t("procurement_notices"), t("procurement_notice_details"), t("notice_contract_link_candidates"), t("notice_contract_links")
    rel: dict = {}
    agg = L.groupBy("wb_contract_number").agg(F.count("*").alias("lines"), F.sum("supplier_amount_usd").alias("line_sum"),
                                              F.countDistinct("project_id").alias("line_projects"), F.first("project_id").alias("line_project"))
    j = A.join(agg, "wb_contract_number", "full_outer")
    rel["lines_without_contract"] = n(j, "contract_amount_usd IS NULL AND project_id IS NULL")
    rel["contracts_without_lines"] = n(j, "lines IS NULL")
    rel["contract_amount_ne_sum_of_lines"] = n(j, "NOT (contract_amount_usd <=> line_sum)")
    rel["supplier_line_count_ne_lines"] = n(j, "NOT (supplier_line_count <=> lines)")
    rel["contract_project_ne_line_project"] = n(j, "line_projects > 1 OR NOT (project_id <=> line_project)")
    tot = A.agg(F.sum("contract_amount_usd")).collect()[0][0]
    lt = L.agg(F.sum("supplier_amount_usd")).collect()[0][0]
    rel["total_contract_amount_usd"], rel["total_line_amount_usd"] = dec(tot), dec(lt)
    inflated = A.join(L.select("wb_contract_number"), "wb_contract_number").agg(F.sum("contract_amount_usd")).collect()[0][0]
    rel["contract_amount_summed_after_join_to_lines_usd"] = dec(inflated)
    rel["double_count_factor_if_contract_amount_summed_over_lines"] = float(Decimal(inflated) / Decimal(tot))
    rel["multi_supplier_contracts"] = n(A, "is_multi_supplier")
    rel["contracts_with_missing_line_amounts"] = n(A, "supplier_lines_missing_amount > 0")
    rel["contracts_with_null_amount"] = n(A, "contract_amount_usd IS NULL")
    rel["contracts_sharing_reference_in_project_gt1"] = n(A, "contracts_sharing_reference_in_project > 1")
    rep.equals("contracts:every_line_has_contract_and_vice_versa", (rel["lines_without_contract"], rel["contracts_without_lines"]), (0, 0))
    rep.equals("contracts:contract_amount_equals_sum_of_lines (exact, null-safe)", rel["contract_amount_ne_sum_of_lines"], 0)
    rep.equals("contracts:supplier_line_count_consistent", rel["supplier_line_count_ne_lines"], 0)
    rep.equals("contracts:line_project_equals_contract_project", rel["contract_project_ne_line_project"], 0)
    rep.equals("contracts:grand_total_lines_equals_contracts", rel["total_line_amount_usd"], rel["total_contract_amount_usd"])
    rep.add("contracts:double_count_factor_if_contract_amount_joined_to_lines (must not be summed)", rel["double_count_factor_if_contract_amount_summed_over_lines"],
            "reported", True, "warn")
    pids = {r[0] for r in P.select("project_id").collect()}
    for name, df in (("contract_awards", A), ("contract_award_lines", L), ("contract_awards_legacy", LG), ("procurement_notices", N),
                     ("procurement_notice_details", D), ("notice_contract_links", K), ("ibrd_loans", t("ibrd_loans")), ("ida_credits", t("ida_credits")),
                     ("ieg_project_ratings", t("ieg_project_ratings")), ("project_details_v3", t("project_details_v3")), ("project_loan_transactions", t("project_loan_transactions"))):
        ids = {r[0] for r in df.select("project_id").distinct().collect()} - {None}
        rel[f"{name}:projects_covered"] = len(ids)
        rep.equals(f"relationships:{name}:project_id_in_projects", len(ids - pids), 0)
    leg = {r[0] for r in LG.select("contract_number").collect()}
    cur = {r[0] for r in A.select("wb_contract_number").collect()}
    rel["legacy_contract_numbers_also_in_ds00005"] = len(leg & cur)
    rel["legacy_fiscal_years"], rel["ds00005_fiscal_years"] = LG.agg(F.min("fiscal_year"), F.max("fiscal_year")).collect()[0][:], A.agg(F.min("fiscal_year"), F.max("fiscal_year")).collect()[0][:]
    rel["projects_with_both_legacy_and_ds00005"] = len({r[0] for r in LG.select("project_id").distinct().collect()} & {r[0] for r in A.select("project_id").distinct().collect()})
    rep.equals("contracts:legacy_and_ds00005_contract_numbers_disjoint", rel["legacy_contract_numbers_also_in_ds00005"], 0)
    # notices <-> details
    nd = D.join(N.select("notice_op_id", F.col("project_id").alias("n_project"), F.col("notice_type").alias("n_type"), "has_api_detail"), "notice_op_id", "left")
    rel["details_without_ds00979_notice"] = n(nd, "n_project IS NULL")
    rel["details_project_ne_notice_project"] = n(nd, "n_project IS NOT NULL AND NOT (project_id <=> n_project)")
    rel["details_type_ne_notice_type"] = n(nd, "n_project IS NOT NULL AND NOT (notice_type <=> n_type)")
    rel["notices_flagged_has_api_detail"] = n(N, "has_api_detail")
    rel["notices_flagged_without_detail_row"] = n(N.filter("has_api_detail").join(D.select("notice_op_id"), "notice_op_id", "left_anti"))
    rel["notice_details_projects"] = D.select("project_id").distinct().count()
    rep.equals("notices:has_api_detail_flag_consistent_with_details", rel["notices_flagged_without_detail_row"], 0)
    rep.equals("notices:details_project_equals_notice_project", rel["details_project_ne_notice_project"], 0)
    rep.add("notices:details_not_in_ds00979 (API-only notices)", rel["details_without_ds00979_notice"], "reported", True, "warn")
    rel["ds00979_identical_metadata_rows_gt1"] = n(N, "identical_metadata_group_size > 1")
    rel["details_identical_text_rows_gt1"] = n(D, "identical_text_group_size > 1")
    # candidates / links
    ck = C.select("notice_op_id", "wb_contract_number").distinct()
    rel["candidates_notice_not_in_details"] = n(C.join(D.select("notice_op_id"), "notice_op_id", "left_anti"))
    rel["candidates_contract_not_in_awards"] = n(C.join(A.select("wb_contract_number"), "wb_contract_number", "left_anti"))
    rel["candidates_project_ne_contract_project"] = n(C.join(A.select("wb_contract_number", F.col("project_id").alias("a_p")), "wb_contract_number"), "project_id <> a_p")
    rel["candidates_project_ne_notice_project"] = n(C.join(D.select("notice_op_id", F.col("project_id").alias("d_p")), "notice_op_id"), "project_id <> d_p")
    rel["links_not_in_candidates"] = n(K.join(ck, ["notice_op_id", "wb_contract_number"], "left_anti"))
    rel["links_duplicate_pairs"] = K.count() - K.select("notice_op_id", "wb_contract_number").distinct().count()
    rel["links_contract_not_in_awards"] = n(K.join(A.select("wb_contract_number"), "wb_contract_number", "left_anti"))
    rel["links_project_ne_contract_project"] = n(K.join(A.select("wb_contract_number", F.col("project_id").alias("a_p")), "wb_contract_number"), "project_id <> a_p")
    for c, v in rel.items():
        if c.startswith(("candidates_", "links_")):
            rep.equals(f"links:{c}", v, 0)
    rel["link_distinct_notices"] = K.select("notice_op_id").distinct().count()
    rel["link_distinct_contracts"] = K.select("wb_contract_number").distinct().count()
    rel["link_notice_types"] = {r[0]: r[1] for r in K.groupBy("notice_type").count().collect()}
    linked_amt = K.join(A.select("wb_contract_number", "contract_amount_usd"), "wb_contract_number")
    s_all = linked_amt.agg(F.sum("contract_amount_usd")).collect()[0][0]
    s_dist = linked_amt.select("wb_contract_number", "contract_amount_usd").distinct().agg(F.sum("contract_amount_usd")).collect()[0][0]
    rel["linked_contract_amount_summed_over_links_usd"], rel["linked_contract_amount_distinct_contracts_usd"] = dec(s_all), dec(s_dist)
    rel["double_count_factor_if_amount_summed_over_links"] = float(Decimal(s_all) / Decimal(s_dist))
    one = linked_amt.filter("ambiguity_status = 'unambiguous'")
    rel["unambiguous_links_double_count_factor"] = float(Decimal(one.agg(F.sum("contract_amount_usd")).collect()[0][0])
                                                         / Decimal(one.select("wb_contract_number", "contract_amount_usd").distinct().agg(F.sum("contract_amount_usd")).collect()[0][0]))
    rep.equals("links:one_to_one_links_do_not_multiply_amounts", rel["unambiguous_links_double_count_factor"], 1.0)
    rep.add("links:amount_multiplication_if_all_links_summed (must aggregate per distinct contract)", rel["double_count_factor_if_amount_summed_over_links"], "reported", True, "warn")
    rel["contracts_with_any_link_share"] = round(rel["link_distinct_contracts"] / A.count(), 4)
    rel["contracts_in_detail_projects"] = A.join(D.select("project_id").distinct(), "project_id").count()
    R["relationships"] = rel

    # ---- 4. country reference data ----------------------------------------------------------------------------------
    W, WC, G, CP, IC = t("wdi_indicator_observations"), t("wdi_country"), t("wgi_estimates"), t("cpia_ratings"), t("income_classification_history")
    sc = P.filter("geography_type = 'single_country'").select("project_id", "approval_fiscal_year", "wdi_country_code")
    ref: dict = {"single_country_projects": sc.count(), "regional_projects": n(P, "geography_type <> 'single_country'"),
                 "regional_projects_with_wdi_code": n(P, "geography_type <> 'single_country' AND wdi_country_code IS NOT NULL"),
                 "regional_code_collisions": n(P, "regional_code_collides_with_wdi_code")}
    ref["single_country_without_wdi_code"] = n(sc, "wdi_country_code IS NULL")
    ref["single_country_code_not_in_wdi_country"] = n(sc.join(WC.select(F.col("country_code").alias("wdi_country_code")), "wdi_country_code", "left_anti"))
    ref["project_countries"] = sc.select("wdi_country_code").distinct().count()
    rep.equals("reference:single_country_projects_matched_to_wdi_country", (ref["single_country_without_wdi_code"], ref["single_country_code_not_in_wdi_country"]), (0, 0))
    rep.equals("reference:regional_projects_have_no_country_code (no fabricated country)", ref["regional_projects_with_wdi_code"], 0)
    iso = WC.select(F.col("country_code").alias("wdi_country_code"), F.col("country_wb_id").alias("iso3_code"))
    lag = sc.withColumn("data_year", F.col("approval_fiscal_year") - 2)  # conservative: data year published before approval FY start
    avail: dict = {}
    for code in [r[0] for r in W.select("indicator_code").distinct().collect()]:
        w = W.filter(F.col("indicator_code") == code).select(F.col("country_code").alias("wdi_country_code"), F.col("year").alias("data_year"), "value")
        x = lag.join(w, ["wdi_country_code", "data_year"], "left")
        avail[f"WDI {code}"] = {"projects": x.count(), "value_present": n(x, "value IS NOT NULL")}
    for code in [r[0] for r in G.select("indicator_short_code").distinct().collect()]:
        g = G.filter(F.col("indicator_short_code") == code).select(F.col("country_code").alias("wdi_country_code"), F.col("year").alias("data_year"), "value")
        x = lag.join(g, ["wdi_country_code", "data_year"], "left")
        avail[f"WGI {code}"] = {"projects": x.count(), "value_present": n(x, "value IS NOT NULL")}
    cp = CP.filter("indicator_code = 'IQ.CPA.IRAI.XQ'").select(F.col("country_code").alias("wdi_country_code"), F.col("year").alias("data_year"), "value")
    x = lag.join(cp, ["wdi_country_code", "data_year"], "left")
    avail["CPIA IQ.CPA.IRAI.XQ (overall)"] = {"projects": x.count(), "value_present": n(x, "value IS NOT NULL")}
    ic = lag.join(iso, "wdi_country_code", "left").join(IC.select("iso3_code", F.col("fiscal_year").alias("approval_fiscal_year"), "income_group_code", "classification_status"),
                                                         ["iso3_code", "approval_fiscal_year"], "left")
    avail["Income class (approval FY)"] = {"projects": ic.count(), "value_present": n(ic, "income_group_code IS NOT NULL"),
                                           "status": {str(r[0]): r[1] for r in ic.groupBy("classification_status").count().collect()}}
    for k, v in avail.items():
        rep.equals(f"reference:{k}:join_does_not_multiply_projects", v["projects"], ref["single_country_projects"])
    ref["availability_at_approval_fy_minus_2"] = avail
    ref["wgi_unmatched_entities"] = G.filter("wdi_match_status <> 'matched'").select("entity_key").distinct().count()
    ref["wgi_years_missing"] = sorted(set(range(1996, 2026)) - {r[0] for r in G.select("year").distinct().collect()})
    ref["wdi_missing_share_2000_2025"] = round(n(W, "is_value_missing") / W.count(), 4)
    ref["cpia_missing_share"] = round(n(CP, "is_value_missing") / CP.count(), 4)
    ref["wgi_missing_share"] = round(n(G, "is_value_missing") / G.count(), 4)
    ref["income_class_status"] = {r[0]: r[1] for r in IC.groupBy("classification_status").count().collect()}
    ref["approval_fy_beyond_latest_data"] = {"WDI_max_year": W.agg(F.max("year")).collect()[0][0], "projects_needing_year_gt_2025": n(lag, "data_year > 2025")}
    R["reference"] = ref

    # ---- 5. financing -------------------------------------------------------------------------------------------------
    IB, ID, TX, PD = t("ibrd_loans"), t("ida_credits"), t("project_loan_transactions"), t("project_details_v3")
    fin_all = IB.select(F.col("loan_number").alias("financing_number"), "project_id", "original_principal_amount_usd", F.lit("IBRD").alias("lender")).unionByName(
        ID.select(F.col("credit_number").alias("financing_number"), "project_id", "original_principal_amount_usd", F.lit("IDA").alias("lender")))
    fin: dict = {"financing_numbers": fin_all.count(), "distinct_financing_numbers": fin_all.select("financing_number").distinct().count(),
                 "projects_with_ibrd_or_ida": fin_all.select("project_id").distinct().count(),
                 "ibrd_ida_numbers_overlap": IB.join(ID, IB.loan_number == ID.credit_number).count()}
    rep.equals("financing:ibrd_and_ida_numbers_disjoint", fin["ibrd_ida_numbers_overlap"], 0)
    per_p = fin_all.groupBy("project_id").agg(F.count("*").alias("k"))
    fin["financings_per_project"] = {str(r[0]): r[1] for r in per_p.groupBy("k").count().orderBy("k").collect()}
    tj = TX.select("financing_number", "project_id", "transaction_type", "amount").join(fin_all.select("financing_number", F.col("project_id").alias("f_p"), "original_principal_amount_usd"),
                                                                                         "financing_number", "left")
    fin["transaction_rows_matched"] = n(tj, "f_p IS NOT NULL")
    fin["transaction_rows_unmatched"] = n(tj, "f_p IS NULL")
    fin["transaction_matched_project_mismatch"] = n(tj, "f_p IS NOT NULL AND f_p <> project_id")
    fin["transaction_rows_after_join"] = tj.count()
    rep.equals("financing:transaction_join_does_not_multiply_rows", fin["transaction_rows_after_join"], TX.count())
    rep.equals("financing:matched_transactions_same_project", fin["transaction_matched_project_mismatch"], 0)
    com = tj.filter("transaction_type = 'LOAN_COMMITMENT' AND f_p IS NOT NULL").groupBy("financing_number").agg(F.sum("amount").alias("c"), F.first("original_principal_amount_usd").alias("o"))
    fin["commitment_sum_equals_original_principal"] = {"loans": com.count(), "equal": n(com, "c = o")}
    fin["transaction_projects_without_any_ibrd_ida_row"] = TX.select("project_id").distinct().join(fin_all.select("project_id").distinct(), "project_id", "left_anti").count()
    # financing vs contract awards: different measures -- evidence only
    pf = fin_all.groupBy("project_id").agg(F.sum("original_principal_amount_usd").alias("principal"))
    pa = A.groupBy("project_id").agg(F.sum("contract_amount_usd").alias("awards"))
    pc = pf.join(pa, "project_id")
    fin["projects_with_both_financing_and_awards"] = pc.count()
    fin["projects_awards_exceed_ibrd_ida_principal"] = n(pc, "awards > principal")
    pp = P.select("project_id", "current_ibrd_commitment_usd", "current_ida_commitment_usd").join(pf, "project_id")
    fin["projects_principal_equals_current_ibrd_plus_ida_commitment"] = {"projects": pp.count(), "equal": n(
        pp, "principal = coalesce(current_ibrd_commitment_usd,0) + coalesce(current_ida_commitment_usd,0)")}
    fin["financing_status"] = {r[0]: r[1] for r in IB.select("financing_status").unionByName(ID.select("financing_status")).groupBy("financing_status").count().collect()}
    fin["details_projects_without_transactions"] = n(PD, "loan_transaction_count = 0")
    R["financing"] = fin

    # ---- 6. IEG ---------------------------------------------------------------------------------------------------------
    IE = t("ieg_project_ratings")
    ie = IE.join(P.select("project_id", "project_status", F.col("approval_fiscal_year").alias("p_afy"), "closing_date"), "project_id")
    ieg: dict = {"rows": IE.count(), "status_of_rated_projects": {r[0]: r[1] for r in ie.groupBy("project_status").count().collect()},
                 "closed_projects_in_cohort": n(P, "project_status = 'Closed'"),
                 "rating_available_from_date_null": n(IE, "rating_available_from_date IS NULL"),
                 "evaluation_lag_fy": {k: v for k, v in zip(["min", "p50", "max"], IE.agg(F.min("evaluation_lag_fiscal_years"),
                                                                                             F.percentile_approx("evaluation_lag_fiscal_years", 0.5), F.max("evaluation_lag_fiscal_years")).collect()[0])},
                 "evaluation_precedes_final_closing": n(IE, "evaluation_precedes_final_closing"),
                 "approval_fy_mismatch_vs_projects": n(ie, "NOT (approval_fiscal_year <=> p_afy)")}
    by = P.withColumn("rated", F.col("project_id").isin([r[0] for r in IE.select("project_id").collect()])).filter("project_status = 'Closed'")
    ieg["closed_projects_rated_share_by_approval_fy"] = {int(r[0]): round(r[1], 3) for r in by.groupBy("approval_fiscal_year").agg(F.avg(F.col("rated").cast("int"))).orderBy("approval_fiscal_year").collect() if r[0] is not None}
    ieg["closed_projects_rated_share_by_region"] = {r[0]: round(r[1], 3) for r in by.groupBy("region_name").agg(F.avg(F.col("rated").cast("int"))).collect()}
    snap = {r[0] for r in P.filter("size(ieg_ratings_snapshot) > 0").select("project_id").collect()}
    rated = {r[0] for r in IE.select("project_id").collect()}
    ieg["outcome_vs_projects_ieg_snapshot"] = {"projects_with_snapshot": len(snap), "rated_in_both": len(snap & rated), "only_in_projects_snapshot": len(snap - rated),
                                               "only_in_ieg_table": len(rated - snap)}
    ieg["rated_active_projects_note"] = "Active status is as of the 2026-10-21 projects snapshot"
    rep.equals("ieg:rating_available_from_date_null_by_design", ieg["rating_available_from_date_null"], IE.count())
    rep.equals("ieg:approval_fy_consistent_with_projects", ieg["approval_fy_mismatch_vs_projects"], 0)
    R["ieg"] = ieg

    # ---- 7. ML feasibility probes ------------------------------------------------------------------------------------
    ml: dict = {}
    k1 = K.filter("ambiguity_status = 'unambiguous'").join(N.select("notice_op_id", "publication_date", F.col("notice_type").alias("nt")), "notice_op_id")
    dur = k1.withColumn("days", F.datediff("contract_signing_date", "publication_date"))
    ml["tender_duration_label_by_notice_type"] = {r[0]: {"links": r[1], "negative_days": r[2], "p50_days": r[3], "p90_days": r[4]} for r in dur.groupBy("nt").agg(
        F.count("*"), F.sum(F.when(F.col("days") < 0, 1).otherwise(0)), F.percentile_approx("days", 0.5), F.percentile_approx("days", 0.9)).collect()}
    ml["tender_duration_projects"] = dur.select("project_id").distinct().count()
    bid = D.filter("notice_type IN ('Invitation for Bids','Request for Expression of Interest','Invitation for Prequalification')").withColumn(
        "window_days", F.datediff("submission_deadline_date", "notice_date"))
    ml["bidding_window_label"] = {"notices": bid.count(), "with_deadline": n(bid, "window_days IS NOT NULL"), "negative": n(bid, "window_days < 0"),
                                  "p50": bid.agg(F.percentile_approx("window_days", 0.5)).collect()[0][0]}
    ml["contract_value_label"] = {"contracts": A.count(), "amount_present": n(A, "contract_amount_usd IS NOT NULL"), "amount_zero": n(A, "contract_amount_usd = 0"),
                                  "amount_negative": n(A, "contract_amount_usd < 0"), "projects": A.select("project_id").distinct().count(),
                                  "with_procurement_method": n(A, "procurement_method IS NOT NULL"), "with_category": n(A, "procurement_category IS NOT NULL"),
                                  "linked_to_pre_award_notice": K.filter("notice_type <> 'Contract Award'").select("wb_contract_number").distinct().count()}
    # pre-award notice -> award notice -> contract chain, via the same normalised bid reference within the project (feasibility only)
    pre = D.filter("notice_type IN ('Invitation for Bids','Request for Expression of Interest','Invitation for Prequalification') AND bid_reference_normalized IS NOT NULL") \
        .select("project_id", "bid_reference_normalized", F.col("notice_op_id").alias("pre_op"), F.col("notice_date").alias("pre_date"), F.col("notice_type").alias("pre_type"))
    ca = D.filter("notice_type = 'Contract Award' AND bid_reference_normalized IS NOT NULL").select("project_id", "bid_reference_normalized", F.col("notice_op_id").alias("notice_op_id"))
    chain = pre.join(ca, ["project_id", "bid_reference_normalized"]).join(K.filter("ambiguity_status = 'unambiguous'").select("notice_op_id", "wb_contract_number", "contract_signing_date"),
                                                                         "notice_op_id")
    chain = chain.withColumn("days", F.datediff("contract_signing_date", "pre_date"))
    ml["pre_award_notice_chain"] = {"pre_award_notices_with_reference": pre.count(), "pre_award_refs_shared_by_gt1_pre_notice": pre.groupBy("project_id", "bid_reference_normalized").count().filter("count > 1").count(),
                                    "pre_notices_reaching_a_contract": chain.select("pre_op").distinct().count(), "contracts_reached": chain.select("wb_contract_number").distinct().count(),
                                    "chain_rows": chain.count(), "negative_days": n(chain, "days < 0"),
                                    "p50_days": chain.agg(F.percentile_approx("days", 0.5)).collect()[0][0], "p90_days": chain.agg(F.percentile_approx("days", 0.9)).collect()[0][0],
                                    "by_pre_type": {r[0]: r[1] for r in chain.groupBy("pre_type").agg(F.countDistinct("wb_contract_number")).collect()}}
    ml["award_notice_lag_after_signing"] = {"p50_days": dur.agg(F.percentile_approx(-F.col("days"), 0.5)).collect()[0][0]}
    ml["contract_amount_quantiles_usd"] = [dec(x) for x in A.agg(F.percentile_approx("contract_amount_usd", [0.01, 0.5, 0.99])).collect()[0][0]] + [dec(A.agg(F.max("contract_amount_usd")).collect()[0][0])]
    ml["contracts_anonymized_individual_only"] = n(A, F.array_contains("supplier_id_types", "anonymized_individual_consultant") & (F.size("supplier_id_types") == 1))
    ml["ds00979_deadline"] = {"rows": N.count(), "deadline_present": n(N, "deadline_date IS NOT NULL"), "deadline_before_publication": n(N, "deadline_date < publication_date"),
                              "by_type_deadline_present": {r[0]: r[1] for r in N.groupBy("notice_type").agg(F.count("deadline_date")).collect()}}
    ml["contracts_signed_before_project_approval"] = n(A.join(P.select("project_id", "board_approval_date"), "project_id"), "contract_signing_date < board_approval_date")
    ml["notices_published_before_project_approval"] = n(N.join(P.select("project_id", "board_approval_date"), "project_id"), "publication_date < board_approval_date")
    R["ml"] = ml

    # ---- 8. privacy (counts only) -------------------------------------------------------------------------------------
    priv: dict = {"email_or_phone_rows_by_column": {}}
    for name in TABLES:
        df = t(name)
        cols = [f for f in df.schema.fields if f.dataType.simpleString() in ("string", "array<string>")
                and f.name not in ("notice_text", "bronze_raw_record") and not f.name.endswith(("_sha256", "_key"))]
        exprs = []
        for f in cols:
            c = F.col(f"`{f.name}`") if f.dataType.simpleString() == "string" else F.concat_ws(" ", F.col(f"`{f.name}`"))
            exprs += [F.sum(F.when(c.rlike(EMAIL_RE), 1).otherwise(0)).alias(f"e|{f.name}"), F.sum(F.when(c.rlike(PHONE_RE), 1).otherwise(0)).alias(f"p|{f.name}")]
        row = df.agg(*exprs).collect()[0].asDict()
        for f in cols:
            e_, p_ = row[f"e|{f.name}"] or 0, row[f"p|{f.name}"] or 0
            if e_ or p_:
                priv["email_or_phone_rows_by_column"][f"{name}.{f.name}"] = {"email": e_, "phone": p_}
    nt = D.filter("notice_text IS NOT NULL")
    priv["notice_text"] = {"non_null": nt.count(), "equal_to_mask_placeholder_for_this_identity": n(nt, F.col("notice_text") == MASK_PLACEHOLDER),
                           "rows_flagged_email": n(D, "notice_text_contains_email"), "rows_flagged_phone": n(D, "notice_text_contains_phone")}
    rep.equals("privacy:notice_text_masked_for_current_identity", priv["notice_text"]["equal_to_mask_placeholder_for_this_identity"], priv["notice_text"]["non_null"])
    raw = P.select(F.col("bronze_raw_record").alias("r"))
    priv["projects_bronze_raw_record"] = {"rows_with_teamleadname_key": n(raw, F.get_json_object("r", "$.teamleadname").isNotNull()),
                                          "rows_with_teamleaderupi_key": n(raw, F.get_json_object("r", "$.teamleaderupi").isNotNull()),
                                          "rows_with_email": n(raw, F.col("r").rlike(EMAIL_RE))}
    for name in ("cpia_ratings", "wgi_estimates"):
        priv[f"{name}_bronze_raw_record_email_rows"] = n(t(name), F.col("bronze_raw_record").rlike(EMAIL_RE))
    priv["supplier_id_types"] = {r[0]: r[1] for r in L.groupBy("supplier_id_type").agg(F.countDistinct("supplier_id")).collect()}
    R["privacy"] = priv

    print(rep.render())
    (run_dir / "audit_results.json").write_text(json.dumps(R, indent=1, default=str))
    (run_dir / "dq_report.json").write_text(json.dumps({"run_id": run_id, "dq": rep.to_dict()}, indent=1, default=str))
    print(f"\nreports: {run_dir}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
