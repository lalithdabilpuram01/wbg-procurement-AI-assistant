"""Phase 1: feature inventory with measured missingness and point-in-time decisions.

Usage:  /opt/anaconda3/bin/python3 ml/phase1/s05_feature_inventory.py <extract_dir> <eda_dir>

Writes ml/reports/ML_FEATURE_INVENTORY.csv (and a copy in a new ml/eda/feature_inventory_<UTC>/ run folder).
Missingness is measured on each model's own population:
  M1 tender time   -> valid tender-duration labels (EDA population L, 2,114)
  M2 amendment     -> cohort contracts (Gold gold_contract_analytics, 178,921)
  M3 re-bidding    -> cohort tender notices (DS00979 IFB/REOI/IFP, 55,223)
Decisions: include = leakage-safe primary experiments; investigate = excluded from primary experiments,
allowed only in a labelled sensitivity run; exclude = never a feature; unavailable = no cohort source.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ML_ROOT, TENDER_TYPES, new_run_dir  # noqa: E402

CUR = "Current-vintage value (WDI/CPIA 2026-10-08, WGI 2026-09-25); history is revised, so the t-2 value was not necessarily the value available at prediction time"
GC, GN, GP, GX, SP = ("wbg_gold.gold_contract_analytics", "wbg_gold.gold_procurement_notices", "wbg_gold.gold_project_portfolio",
                      "wbg_gold.gold_country_context", "wbg_silver.projects")


def main(extract_dir: str, eda_dir: str) -> None:
    ex = Path(extract_dir) / "data"
    run = new_run_dir("feature_inventory")
    L = pd.read_parquet(Path(eda_dir) / "tender_label_features_eda.parquet")
    C = pd.read_parquet(ex / "gold_contract_analytics.parquet")
    gn = pd.read_parquet(ex / "gold_procurement_notices.parquet")
    T = gn[gn["notice_type"].isin(TENDER_TYPES)].copy()
    pj = pd.read_parquet(ex / "silver_projects.parquet")
    cc = pd.read_parquet(ex / "gold_country_context.parquet")
    T = T.merge(pj[["project_id", "board_approval_date", "primary_major_sector"]], on="project_id", how="left")
    T["project_age_days_at_publication"] = (pd.to_datetime(T["publication_date"]) - pd.to_datetime(T["board_approval_date"])).dt.days
    lag = cc[["country_code", "year", "gdp_growth_annual_pct", "wgi_government_effectiveness_est", "cpia_irai_overall"]].copy()
    lag["year"] += 2
    T = T.merge(lag.rename(columns={"country_code": "wdi_country_code", "year": "publication_calendar_year"}),
                on=["wdi_country_code", "publication_calendar_year"], how="left")
    C["signing_calendar_year"] = C["contract_signed_calendar_year"]
    C = C.merge(lag.rename(columns={"country_code": "wdi_country_code", "year": "signing_calendar_year"}),
                on=["wdi_country_code", "signing_calendar_year"], how="left")
    C = C.merge(pj[["project_id", "primary_major_sector"]], on="project_id", how="left")

    def miss(df, col):
        return round(float(df[col].isna().mean() * 100), 2) if col in df.columns else None

    rows = []

    def add(model, name, family, table, column, dtype, df, col, timing, relevance, transform, encoding, leakage, decision, note=""):
        rows.append({"model": model, "feature_name": name, "family": family, "source_table": table, "source_column": column,
                     "data_type": dtype, "missing_pct": miss(df, col) if df is not None else None,
                     "missing_measured_on": {id(L): "M1 population L (2,114 valid labels)", id(C): "M2 cohort contracts (178,921)",
                                             id(T): "M3 cohort tender notices (55,223)"}.get(id(df), "not measured: no cohort source"),
                     "prediction_time_availability": timing, "business_relevance": relevance, "proposed_transformation": transform,
                     "proposed_encoding": encoding, "leakage_risk": leakage, "decision": decision, "notes": note})

    # ---------------- Model 1: tender completion time (prediction at first tender publication) ----------------
    m = "M1_tender_time"
    add(m, "procurement_method", "procurement", GC, "procurement_method", "string", L, "procurement_method", "Known at planning (package method in the plan)",
        "Strongest segment effect (Kruskal-Wallis p<1e-100; QCBS median 375 d vs RFQ 72 d)", "Group methods with <20 labels into 'Other'", "One-hot", "Low", "include")
    add(m, "procurement_category", "procurement", GC, "procurement_category", "string", L, "procurement_category", "Known at planning", "Works/Goods/Consulting/Non-consulting differ", "-", "One-hot", "Low", "include")
    add(m, "review_type", "procurement", GC, "review_type", "string", L, "review_type", "Known at planning (set from estimated value thresholds)",
        "Prior review median 324 d vs Post 123 d", "Missing -> 'Unknown'", "Binary", "Low; partly a proxy for estimated value", "include",
        "Prior review is assigned from value thresholds, so it carries value information legitimately")
    add(m, "first_tender_notice_type", "procurement", "wbg_silver.procurement_notice_details", "notice_type", "string", L, "first_tender_type",
        "Known at publication", "REOI (consulting) vs IFB differ; REOI-to-signing includes shortlisting and RFP", "Prequalification (n=7) -> merge with IFB or drop", "One-hot", "Low", "include")
    add(m, "published_bid_window_days", "procurement", "wbg_silver.procurement_notice_details", "submission_deadline_date - notice_date", "int", L, "api_window_days",
        "Known at publication only (not at planning)", "Spearman 0.31 with ln(duration)", "log1p; cap review >180 d (flag, do not delete)", "Numeric (scaled for linear models)",
        "Low at publication stage; must be dropped for a planning-stage model", "include", "Use only if the approved prediction point is tender publication")
    add(m, "region", "project", GP, "region_name", "string", L, "region_name", "Known (project attribute)", "LAC median 235 d vs MENA 104 d", "-", "One-hot", "Low", "include")
    add(m, "country", "project", GP, "wdi_country_code", "string", L, "wdi_country_code", "Known", "Large country differences (Pakistan 71 d vs Mozambique 227 d)",
        "Regional projects -> 'REGIONAL'; countries with <20 labels -> 'Other'", "One-hot (grouped) or out-of-fold target encoding", "Medium: few projects per country, risk of memorising projects", "include",
        "79 countries; top-5 = 36.9% of labels")
    add(m, "primary_major_sector", "project", SP, "major_sectors (largest summed sector_percent)", "string", L, "primary_major_sector", "Known at approval",
        "Transport 231 d vs Social Protection 103 d", "Strip 'FY17 - ' prefix before grouping (taxonomy variants)", "One-hot", "Low", "include", "Sector is the current mapping; low revision risk")
    add(m, "project_age_days_at_tender", "project", SP, "first_tender_date - board_approval_date", "int", L, "project_age_days_at_tender", "Known",
        "Weak (rho -0.07); 17 pre-approval tenders", "Keep negatives; spline/bins for linear models", "Numeric", "Low", "include")
    add(m, "project_approval_fiscal_year", "project", GP, "approval_fiscal_year", "int", L, "approval_fiscal_year", "Known", "Cohort/vintage effect", "-", "Numeric", "Low", "include")
    add(m, "project_prior_tender_notices", "history", GN, "count(publication_date < tender date)", "int", L, "project_prior_tender_notices",
        "Known (as-of count of published notices)", "Proxy for implementing-agency experience (rho ~0)", "log1p", "Numeric", "Low if computed strictly before the prediction date", "include")
    add(m, "income_group_at_tender_fy", "economic", GX, "income_group_fy_starting_in_year (year = FY-1)", "string", L, "income_group_at_tender_fy",
        "Point-in-time by construction (OGHIST)", "Context", "Missing (regional) -> 'REGIONAL'", "One-hot (ordinal L<LM<UM<H)", "Low", "include")
    add(m, "tender_fiscal_year", "time", "derived", "first_tender_date", "int", L, "tender_fiscal_year", "Known", "Captures drift (median falls from 176 d FY2020 to 124 d FY2025)",
        "Use only within the temporal split; extrapolates poorly", "Numeric", "Medium (trend extrapolation)", "investigate")
    for c, nm in [("gdp_growth_annual_pct", "gdp_growth_t_minus_2"), ("inflation_consumer_prices_annual_pct", "inflation_t_minus_2"),
                  ("gdp_per_capita_current_usd", "gdp_per_capita_t_minus_2")]:
        add(m, nm, "economic", GX, c, "double", L, c, "Year t-2 per D-30, but current vintage", "Weak (|rho| <= 0.10)", "log for GDP pc; winsorise for model fitting only",
            "Numeric", CUR, "investigate", "Excluded from primary experiments; sensitivity run only unless archived WDI vintages are approved")
    for c in ["wgi_government_effectiveness_est", "wgi_regulatory_quality_est", "wgi_rule_of_law_est", "wgi_control_of_corruption_est",
              "wgi_political_stability_est", "wgi_voice_accountability_est"]:
        add(m, c.replace("_est", "_t_minus_2"), "governance", GX, c, "double", L, c, "Year t-2 per D-30, but current vintage (whole history re-estimated each release)",
            "rho 0.07-0.19; the 4 core WGI are inter-correlated (rho 0.68-0.79; VIF up to 5.1)", "Use one composite or PCA within training folds", "Numeric",
            CUR, "investigate", "Assessment asks for governance indicators: run as a declared sensitivity experiment")
    add(m, "cpia_irai_overall_t_minus_2", "governance", GX, "cpia_irai_overall", "double", L, "cpia_irai_overall", "IDA countries only", "Context",
        "Missing indicator + impute in fold", "Numeric + missing flag", "Structurally missing (26.4%; MNAR by income group)", "investigate")
    add(m, "project_commitment_usd", "project", GP, "current_total_commitment_usd_snapshot", "decimal", L, "log_commitment_snapshot",
        "Snapshot (changes with additional financing / cancellations)", "rho 0.24", "log", "Numeric", "High: today's commitment, not the value at tender date", "investigate",
        "Original commitment at approval would be leakage-safe; not in Gold")
    add(m, "estimated_package_amount", "procurement", "procurement plans (not in Silver)", "Estimated Amount (US$)", "decimal", None, None, "Known at planning",
        "Expected strong predictor", "log", "Numeric", "Low", "unavailable", "Only in parsed STEP plans (25 cohort projects in p09; Module 1 pilot); needs plan extraction at scale (D-11)")
    add(m, "market_approach / planned dates", "procurement", "procurement plans (not in Silver)", "Market Approach; planned milestone dates", "string/date", None, None,
        "Known at planning", "Planned-vs-actual benchmark", "-", "-", "Low; later plan versions leak actual dates", "unavailable")
    for nm, col, why in [("contract_amount_usd", "contract_amount_usd", "Signed amount: post-award"),
                         ("supplier_* / is_multi_supplier / supplier_country_relation", "supplier_line_count", "Post-award; supplier identity is also D-14b restricted"),
                         ("n_tender_notices / contracts per package (lots)", "n_tender_notices", "Counted up to the snapshot, includes later notices and awards"),
                         ("award notice date / linked award notices", "award_pub_bidref", "Post-award event"),
                         ("contracts_sharing_borrower_reference_in_project", "contracts_sharing_borrower_reference_in_project", "Snapshot-wide aggregate"),
                         ("ISR / IEG / ICR ratings", "isr_ratings_snapshot", "Latest values; after the tender date")]:
        add(m, nm, "excluded", GC if "contract" in col else "various", col, "-", L if col in L.columns else None, col, "After prediction time", "-", "-", "-", why, "exclude")

    # ---------------- Model 2: contract amendment (prediction at contract signing) ----------------
    m = "M2_amendment"
    for col, rel, enc in [("procurement_method", "Method complexity", "One-hot (grouped)"), ("procurement_category", "Works more amendment-prone (domain prior)", "One-hot"),
                          ("review_type", "Prior review = larger / riskier contracts", "Binary"), ("region_name", "Context", "One-hot"),
                          ("wdi_country_code", "Context", "Grouped one-hot / out-of-fold target encoding"), ("income_group_code_at_signing_fy", "Point-in-time OGHIST", "One-hot"),
                          ("project_approval_fiscal_year", "Vintage", "Numeric"), ("days_from_board_approval_to_signing", "Implementation stage", "Numeric (spline)"),
                          ("signed_before_board_approval", "Early contracting", "Binary"), ("primary_major_sector", "Sector", "One-hot"),
                          ("global_practices", "GP mapping (as published)", "Multi-hot")]:
        add(m, col, "procurement" if col in ("procurement_method", "procurement_category", "review_type") else "project",
            SP if col == "primary_major_sector" else GC, col, str(C[col].dtype) if col in C.columns else "-", C, col, "Known at signing", rel, "-", enc, "Low", "include",
            "global_practices may reflect current mapping" if col == "global_practices" else "")
    add(m, "contract_amount_usd", "procurement", GC, "contract_amount_usd", "decimal", C, "contract_amount_usd", "Known at signing (original value)",
        "Size is the main amendment driver in the literature", "log10 (skew 64 -> 0.02)", "Numeric", "Medium: DS00005 is one snapshot; may already include later value revisions (unverified)", "investigate",
        "Promote to include only if snapshot diffing (D-20) shows amounts are not revised in place")
    add(m, "is_multi_supplier / supplier_line_count", "procurement", GC, "is_multi_supplier", "boolean", C, "is_multi_supplier", "Known at signing (JV structure)",
        "JV contracts", "-", "Binary", "Medium: snapshot", "investigate")
    add(m, "supplier_country_relation", "procurement", GC, "supplier_country_relation", "string", C, "supplier_country_relation", "Known at signing",
        "Foreign vs domestic supplier", "-", "One-hot", "Medium: snapshot; 26,456 undetermined", "investigate", "Country only; no supplier identity")
    add(m, "macro / WGI / CPIA t-2", "economic/governance", GX, "gdp_growth_annual_pct; wgi_*; cpia_*", "double", C, "gdp_growth_annual_pct", "t-2, current vintage", "Context", "-", "Numeric", CUR, "investigate")
    for nm, col, why in [("supplier_ids / supplier_names", "-", "Identity (D-14b); never a model input"),
                         ("contract_description / amendment wording", "-", "Free text, contains contact data in some rows, and is the label-generating source"),
                         ("contracts_sharing_borrower_reference_in_project", "contracts_sharing_borrower_reference_in_project", "Snapshot-wide; amendment records share references -> label leakage"),
                         ("award notice version / link columns", "award_notice_link_status", "Post-signing publication events")]:
        add(m, nm, "excluded", GC, col, "-", C if col in C.columns else None, col, "After prediction time / restricted", "-", "-", "-", why, "exclude")

    # ---------------- Model 3: re-bidding (prediction at first tender publication) ----------------
    m = "M3_rebidding"
    for col, rel, enc in [("notice_type", "IFB vs REOI process", "One-hot"), ("procurement_method", "Method", "One-hot (grouped)"),
                          ("procurement_category", "Category", "One-hot"), ("region_name", "Context", "One-hot"),
                          ("wdi_country_code", "Context", "Grouped one-hot / out-of-fold target encoding"),
                          ("income_group_code_at_publication_fy", "Point-in-time OGHIST", "One-hot"),
                          ("publication_fiscal_year", "Drift", "Numeric (temporal split only)"),
                          ("published_response_window_days", "Short windows may deter bidders (hypothesis)", "log1p + missing flag"),
                          ("deadline_status", "No usable window is informative", "One-hot"), ("project_age_days_at_publication", "Implementation stage", "Numeric"),
                          ("primary_major_sector", "Sector", "One-hot")]:
        add(m, col, "procurement" if col in ("notice_type", "procurement_method", "procurement_category", "published_response_window_days", "deadline_status") else "project",
            SP if col in ("primary_major_sector",) else GN, col, str(T[col].dtype) if col in T.columns else "-", T, col, "Known at publication", rel, "-", enc, "Low",
            "investigate" if col == "publication_fiscal_year" else "include",
            "NULL when no positive window (18.3%): keep as informative missingness" if col == "published_response_window_days" else "")
    add(m, "macro / WGI / CPIA t-2", "economic/governance", GX, "gdp_growth_annual_pct; wgi_*; cpia_*", "double", T, "wgi_government_effectiveness_est", "t-2, current vintage", "Context", "-", "Numeric", CUR, "investigate")
    for nm, col, why in [("bid_description re-bid marker (p09 REBID_STRONG)", "-", "Label-generating evidence; using it is direct target leakage"),
                         ("project prior re-bid-marked notices", "-", "Derived from the label marker; leaks project-level label rate unless strictly as-of and verified"),
                         ("api_notice_status / award version / reference sharing", "api_notice_status", "Current status reflects later revisions; 184-project coverage only"),
                         ("identical_metadata_group_size", "identical_metadata_group_size", "Snapshot-wide aggregate"),
                         ("linked_contract_count / contract_link_status", "linked_contract_count", "Post-award"),
                         ("bidder counts", "-", "Post-award (award-notice text)"),
                         ("later plan versions / plan Process Status", "-", "Reveal the outcome")]:
        add(m, nm, "excluded", GN, col, "-", T if col in T.columns else None, col, "After prediction time or label source", "-", "-", "-", why, "exclude")

    inv = pd.DataFrame(rows)
    (ML_ROOT / "reports").mkdir(exist_ok=True)
    dest = ML_ROOT / "reports" / "ML_FEATURE_INVENTORY.csv"
    inv.to_csv(run / "ML_FEATURE_INVENTORY.csv", index=False)
    shutil.copyfile(run / "ML_FEATURE_INVENTORY.csv", dest)
    print(run, dest)
    print(inv.groupby(["model", "decision"]).size().unstack(fill_value=0))
    print(inv[["model", "feature_name", "missing_pct"]].dropna().to_string())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
