"""Phase 1 EDA: statistics and charts on real observed data (offline, from the s01 extract and s02 labels).

Usage:  /opt/anaconda3/bin/python3 ml/phase1/s04_eda.py <extract_dir> <tender_labels_dir>

Populations (named on every chart):
  L  valid tender-duration labels, cohort, 184 API-reference projects (s02)
  C  cohort contracts, DS00005 FY2020+ (Gold #2, 178,921)
  T  cohort tender notices, DS00979 IFB/REOI/prequalification (Gold #3)
Point-in-time features for L are computed as of the first tender publication date. Country indicators are
joined with the approved two-year lag (D-30) but are CURRENT-VINTAGE values (not as-published history).
No amendment or re-bidding outcome charts are produced: no verified real labels exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INK2, SERIES, SNAPSHOT_DATE, TENDER_TYPES, caption, new_run_dir, style_mpl, write_json  # noqa: E402

BLUE = SERIES[0]
POP_L = "Population L: valid tender-duration labels, 2,000-project cohort (184 projects with notice references)"
POP_C = "Population C: cohort contracts, DS00005 snapshot 2026-10-20 (Gold gold_contract_analytics)"
POP_T = "Population T: cohort tender notices (IFB/REOI/IFP), DS00979 retrieved 2026-10-21 (Gold gold_procurement_notices)"
WGI = ["wgi_government_effectiveness_est", "wgi_regulatory_quality_est", "wgi_rule_of_law_est",
       "wgi_control_of_corruption_est", "wgi_political_stability_est", "wgi_voice_accountability_est"]
MACRO = ["gdp_growth_annual_pct", "inflation_consumer_prices_annual_pct", "gdp_per_capita_current_usd"]


def describe(s: pd.Series) -> dict:
    s = pd.to_numeric(s, errors="coerce")
    n_all = len(s)
    s = s.dropna()
    if s.empty:
        return {"n": 0, "missing_pct": 100.0}
    q1, q3 = s.quantile(.25), s.quantile(.75)
    iqr = q3 - q1
    return {"n": int(len(s)), "missing_pct": round(100 * (1 - len(s) / n_all), 2), "mean": round(float(s.mean()), 3),
            "std": round(float(s.std()), 3), "min": float(s.min()), "p01": float(s.quantile(.01)), "p25": float(q1),
            "p50": float(s.median()), "p75": float(q3), "p99": float(s.quantile(.99)), "max": float(s.max()),
            "skew": round(float(s.skew()), 3), "iqr_outliers_1.5": int(((s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)).sum())}


def build_features(ex: Path, labels: pd.DataFrame) -> pd.DataFrame:
    pj = pd.read_parquet(ex / "silver_projects.parquet")
    pf = pd.read_parquet(ex / "gold_project_portfolio.parquet")
    cc = pd.read_parquet(ex / "gold_country_context.parquet")
    gn = pd.read_parquet(ex / "gold_procurement_notices.parquet")
    L = labels.merge(pj[["project_id", "board_approval_date", "approval_fiscal_year", "primary_major_sector",
                         "environmental_assessment_category"]], on="project_id", how="left")
    L = L.merge(pf[["project_id", "current_total_commitment_usd_snapshot", "geography_type"]], on="project_id", how="left")
    L["first_tender_date"] = pd.to_datetime(L["first_tender_date"])
    L["board_approval_date"] = pd.to_datetime(L["board_approval_date"])
    L["project_age_days_at_tender"] = (L["first_tender_date"] - L["board_approval_date"]).dt.days
    L["tender_calendar_year"] = L["first_tender_date"].dt.year
    # as-of historical activity: project tender notices published strictly before this tender (DS00979 dates are
    # publication dates, so they were knowable at the time)
    tn = gn[gn["notice_type"].isin(TENDER_TYPES)][["project_id", "publication_date"]].copy()
    tn["publication_date"] = pd.to_datetime(tn["publication_date"])
    tn = tn.sort_values("publication_date")
    by_p = {p: g["publication_date"].to_numpy() for p, g in tn.groupby("project_id")}
    L["project_prior_tender_notices"] = [int(np.searchsorted(by_p.get(p, np.array([], dtype="datetime64[ns]")), np.datetime64(d), "left"))
                                         for p, d in zip(L["project_id"], L["first_tender_date"])]
    # country context with the D-30 two-year lag (calendar year of tender - 2); income group point-in-time at tender FY
    lag = cc[["country_code", "year"] + MACRO + WGI + ["cpia_irai_overall", "wgi_coverage_status", "cpia_coverage_status"]].copy()
    lag["year"] = lag["year"] + 2
    L = L.merge(lag.rename(columns={"country_code": "wdi_country_code", "year": "tender_calendar_year"}),
                on=["wdi_country_code", "tender_calendar_year"], how="left")
    # FY F is the fiscal year *starting* in calendar year F-1, so FY2026 resolves from the 2025 grid row
    inc = cc[["country_code", "year", "income_group_fy_starting_in_year"]].copy()
    inc["tender_fiscal_year"] = inc["year"] + 1
    inc = inc.drop(columns="year").rename(columns={"country_code": "wdi_country_code",
                                                   "income_group_fy_starting_in_year": "income_group_at_tender_fy"})
    L = L.merge(inc, on=["wdi_country_code", "tender_fiscal_year"], how="left")
    L["log_duration"] = np.log(L["duration_days"])
    L["log_commitment_snapshot"] = np.log(L["current_total_commitment_usd_snapshot"].where(L["current_total_commitment_usd_snapshot"] > 0))
    return L


def main(extract_dir: str, labels_dir: str) -> None:
    ex = Path(extract_dir) / "data"
    out = new_run_dir("eda")
    ch = out / "charts"
    ch.mkdir()
    plt = style_mpl()
    res: dict = {"populations": {"L": POP_L, "C": POP_C, "T": POP_T}, "snapshot": str(SNAPSHOT_DATE)}
    lab = pd.read_parquet(Path(labels_dir) / "tender_duration_label_candidates_cohort.parquet")
    L = build_features(ex, lab[lab["is_valid"]].copy())
    L.drop(columns=["package_key"]).to_parquet(out / "tender_label_features_eda.parquet", index=False)
    ca = pd.read_parquet(ex / "gold_contract_analytics.parquet")
    gn = pd.read_parquet(ex / "gold_procurement_notices.parquet")
    T = gn[gn["notice_type"].isin(TENDER_TYPES)].copy()

    # ---------- A. dataset overview ----------
    ov = {}
    for name, df, key in [("gold_contract_analytics", ca, "wb_contract_number"), ("gold_procurement_notices", gn, "notice_op_id"),
                          ("tender_label_set_valid", L, "wb_contract_number")]:
        ov[name] = {"rows": int(len(df)), "columns": int(df.shape[1]), "key_unique": bool(df[key].is_unique),
                    "projects": int(df["project_id"].nunique()),
                    "missing_pct_top": (df.isna().mean() * 100).round(2).sort_values(ascending=False).head(12).to_dict(),
                    "dtypes": df.dtypes.astype(str).value_counts().to_dict()}
    ov["gold_procurement_notices"]["identical_metadata_rows"] = int((gn["identical_metadata_group_size"] > 1).sum())
    ov["tender_notices_T"] = {"rows": int(len(T)), "by_type": T["notice_type"].value_counts().to_dict(),
                              "projects": int(T["project_id"].nunique()), "with_api_detail": int(T["has_api_detail"].sum())}
    ov["tender_label_set_valid"]["packages"] = int(lab[lab["is_valid"]]["package_key"].nunique())
    ov["grain_checks"] = {
        "contract_to_project": "N:1 (project_id unique in portfolio; verified in Gold)",
        "multi_contract_packages_in_L_pct": round(100 * (lab[lab.is_valid].groupby("package_key").size().pipe(lambda s: s[s > 1].sum()) / lab.is_valid.sum()), 2),
        "country_context_key_unique_after_lag_join": bool(L["wb_contract_number"].is_unique),
        "supplier_lines_per_contract_factor": "x1.269 (Silver audit) - never join lines before aggregating",
    }
    res["overview"] = ov
    res["coverage_C"] = {c: ca[c].value_counts(dropna=False).head(25).to_dict() for c in
                         ("contract_fiscal_year", "region_name", "procurement_category", "procurement_method", "review_type")}
    res["coverage_T"] = {c: T[c].value_counts(dropna=False).head(25).to_dict() for c in
                         ("publication_fiscal_year", "region_name", "procurement_category", "procurement_method", "notice_type")}

    # ---------- B. numerical ----------
    num = ["duration_days", "log_duration", "api_window_days", "project_age_days_at_tender", "project_prior_tender_notices",
           "log_commitment_snapshot"] + MACRO + WGI + ["cpia_irai_overall"]
    res["numeric_L"] = {c: describe(L[c]) for c in num}
    res["numeric_C"] = {"contract_amount_usd": describe(ca["contract_amount_usd"]),
                        "log10_contract_amount_usd": describe(np.log10(ca["contract_amount_usd"].where(ca["contract_amount_usd"] > 0))),
                        "days_from_board_approval_to_signing": describe(ca["days_from_board_approval_to_signing"])}
    res["numeric_T"] = {"published_response_window_days": describe(T["published_response_window_days"])}

    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    ax[0].hist(L["duration_days"], bins=60, color=BLUE, edgecolor="white", linewidth=0.5)
    ax[0].set(title="Tender notice to contract signing (days)", xlabel="Days", ylabel="Contracts")
    ax[0].axvline(L["duration_days"].median(), color=INK2, ls="--", lw=1)
    ax[0].text(L["duration_days"].median(), ax[0].get_ylim()[1] * .92, f" median {L['duration_days'].median():.0f} d", color=INK2, fontsize=8)
    ax[1].hist(L["log_duration"], bins=50, color=BLUE, edgecolor="white", linewidth=0.5)
    ax[1].set(title="Same target, natural-log scale", xlabel="ln(days)", ylabel="Contracts")
    fig.suptitle(f"Distribution of the tender-duration target (n = {len(L):,})", fontweight="bold")
    caption(fig, POP_L)
    fig.tight_layout(rect=(0, .08, 1, .95)); fig.savefig(ch / "01_duration_distribution.png"); plt.close(fig)

    feats = [("project_age_days_at_tender", "Project age at tender (days since approval)"),
             ("api_window_days", "Published bid window (days)"),
             ("project_prior_tender_notices", "Prior tender notices in project (count)"),
             ("gdp_growth_annual_pct", "GDP growth, t-2 (%)"), ("inflation_consumer_prices_annual_pct", "Inflation, t-2 (%)"),
             ("wgi_government_effectiveness_est", "WGI government effectiveness, t-2"),
             ("wgi_rule_of_law_est", "WGI rule of law, t-2"), ("wgi_control_of_corruption_est", "WGI control of corruption, t-2"),
             ("log_commitment_snapshot", "ln(project commitment US$) - snapshot")]
    fig, axs = plt.subplots(3, 3, figsize=(12, 9))
    for a, (c, t) in zip(axs.flat, feats):
        x = pd.to_numeric(L[c], errors="coerce").dropna()
        a.hist(x, bins=40, color=BLUE, edgecolor="white", linewidth=0.4)
        a.set_title(t, fontsize=9.5); a.set_ylabel("Contracts")
        a.text(.98, .95, f"n={len(x):,}\nmissing {100 * (1 - len(x) / len(L)):.1f}%\nskew {x.skew():.2f}",
               transform=a.transAxes, ha="right", va="top", fontsize=7.5, color=INK2)
    fig.suptitle("Candidate numeric features at tender publication (country values: current vintage, lagged 2 years)", fontweight="bold")
    caption(fig, POP_L)
    fig.tight_layout(rect=(0, .08, 1, .96)); fig.savefig(ch / "02_numeric_feature_histograms.png"); plt.close(fig)

    # ---------- C/D. categorical + relationships ----------
    def box_by(col, fname, title, min_n=20, top=None):
        g = L.groupby(col)["duration_days"]
        keep = g.size()[g.size() >= min_n].sort_values(ascending=False)
        if top:
            keep = keep.head(top)
        order = L[L[col].isin(keep.index)].groupby(col)["duration_days"].median().sort_values().index
        fig, a = plt.subplots(figsize=(10, 0.45 * len(order) + 1.6))
        data = [L.loc[L[col] == k, "duration_days"].to_numpy() for k in order]
        bp = a.boxplot(data, vert=False, patch_artist=True, showfliers=True, widths=.55,
                       flierprops=dict(marker="o", markersize=2.5, markerfacecolor=INK2, markeredgecolor="none", alpha=.4),
                       medianprops=dict(color="white", linewidth=1.6))
        for p in bp["boxes"]:
            p.set_facecolor(BLUE); p.set_edgecolor(BLUE)
        a.set_yticks(range(1, len(order) + 1), [f"{k}  (n={int(keep[k]):,})" for k in order])
        a.set_xscale("log"); a.set_xlabel("Days from first tender notice to signing (log scale)")
        a.set_title(title)
        caption(fig, POP_L + f"; groups with n >= {min_n}")
        fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(ch / fname); plt.close(fig)
        kw = stats.kruskal(*data) if len(data) > 1 else None
        return {"groups": {k: {"n": int(keep[k]), "median": float(L.loc[L[col] == k, "duration_days"].median())} for k in order},
                "kruskal_wallis_p": float(kw.pvalue) if kw else None}

    res["relationships"] = {
        "by_method": box_by("procurement_method", "03_duration_by_method.png", "Tender duration by procurement method"),
        "by_category": box_by("procurement_category", "04_duration_by_category.png", "Tender duration by procurement category"),
        "by_region": box_by("region_name", "05_duration_by_region.png", "Tender duration by region"),
        "by_first_tender_type": box_by("first_tender_type", "06_duration_by_notice_type.png", "Tender duration by first notice type", min_n=5),
        "by_country_top15": box_by("project_country_name", "07_duration_by_country_top15.png", "Tender duration, 15 countries with most labels", top=15),
        "by_review_type": box_by("review_type", "08_duration_by_review_type.png", "Tender duration by review type", min_n=5),
        "by_sector": box_by("primary_major_sector", "09_duration_by_sector.png", "Tender duration by project primary sector", min_n=20),
    }
    res["categorical_L_frequency"] = {c: L[c].value_counts(dropna=False).to_dict() for c in
                                      ("procurement_method", "procurement_category", "review_type", "region_name", "first_tender_type",
                                       "primary_major_sector", "income_group_at_tender_fy", "tender_fiscal_year")}
    res["categorical_L_cardinality"] = {c: int(L[c].nunique()) for c in ("project_country_name", "project_id", "primary_major_sector")}
    res["rare_categories_L_lt20"] = {c: int((L[c].value_counts() < 20).sum()) for c in ("procurement_method", "project_country_name", "primary_major_sector")}

    # FY trend with truncation shading
    fy = L.groupby("tender_fiscal_year")["duration_days"].agg(["size", "median", lambda s: s.quantile(.25), lambda s: s.quantile(.75)])
    fy.columns = ["n", "median", "p25", "p75"]
    fig, a = plt.subplots(figsize=(9, 4))
    a.fill_between(fy.index, fy["p25"], fy["p75"], color=BLUE, alpha=.18, linewidth=0, label="Interquartile range")
    a.plot(fy.index, fy["median"], color=BLUE, lw=2, marker="o", ms=5, label="Median")
    for x, r in fy.iterrows():
        a.annotate(f"n={int(r.n)}", (x, r["median"]), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=7.5, color=INK2)
    a.axvspan(fy.index.min() - .5, 2019.5, color=INK2, alpha=.08)
    a.axvspan(2025.5, fy.index.max() + .5, color=INK2, alpha=.08)
    a.text(2018.6, a.get_ylim()[1] * .9, "left-truncated", fontsize=8, color=INK2)
    a.text(2025.7, a.get_ylim()[1] * .9, "right-truncated", fontsize=8, color=INK2)
    a.set(title="Tender duration by fiscal year of first tender notice", xlabel="WB fiscal year of first tender notice", ylabel="Days")
    a.legend(loc="upper center", frameon=False, fontsize=8)
    caption(fig, POP_L + ". Shaded years are biased by the DS00005 window (FY2020+) and the 2026-10-20 snapshot")
    fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(ch / "10_duration_by_fiscal_year.png"); plt.close(fig)
    res["by_tender_fy"] = fy.round(1).reset_index().to_dict("records")

    # KM (package level) from s02
    km = pd.read_csv(Path(labels_dir) / "km_tender_package_to_first_signing.csv")
    fig, a = plt.subplots(figsize=(8, 4))
    a.step(km["days"], km["survival"], where="post", color=BLUE, lw=2)
    a.set(title="Share of tender packages not yet linked to a signed contract", xlabel="Days since first tender notice",
          ylabel="Share still unsigned / unlinked", ylim=(0, 1.02), xlim=(0, 1500))
    a.text(1480, .95, "Unlinked includes cancelled, re-referenced\nand non-DS00005 contracts, not only open tenders",
           ha="right", va="top", fontsize=7.5, color=INK2)
    caption(fig, "Tender packages first published 2019-07-01 or later, 184 cohort projects with references (Kaplan-Meier, diagnostic)")
    fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(ch / "11_km_package_to_signing.png"); plt.close(fig)

    # Funnel + coverage
    import json
    fun = json.loads((Path(labels_dir) / "tender_label_funnel.json").read_text())
    steps = fun["funnel"]
    fig, a = plt.subplots(figsize=(10, 3.4))
    lbls = ["Cohort contracts", "In 184 reference projects", "With reference", "Linked to tender notice", "Valid (documented rule)"]
    vals = [s["contracts"] for s in steps]
    a.barh(range(len(vals))[::-1], vals, color=BLUE, height=.6)
    a.set_yticks(range(len(vals))[::-1], lbls); a.set_xscale("log"); a.set_xlabel("Contracts (log scale)")
    for i, v in zip(range(len(vals))[::-1], vals):
        a.text(v * 1.05, i, f"{v:,}", va="center", fontsize=8.5, color=INK2)
    a.set_title("Tender-duration label funnel"); a.grid(axis="y", visible=False)
    caption(fig, "2,000-project cohort; DS00005 2026-10-20; procnotices API references exist for 184 projects only")
    fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(ch / "12_label_funnel.png"); plt.close(fig)

    covm = pd.read_csv(Path(labels_dir) / "coverage_by_procurement_method_vs_api_scope.csv")
    covm = covm[covm["contracts"] >= 20].sort_values("coverage_pct")
    fig, a = plt.subplots(figsize=(9, 0.38 * len(covm) + 1.4))
    a.barh(covm["procurement_method"], covm["coverage_pct"], color=BLUE, height=.6)
    for i, (p, n) in enumerate(zip(covm["coverage_pct"], covm["contracts"])):
        a.text(p + .8, i, f"{p:.1f}% of {n:,}", va="center", fontsize=8, color=INK2)
    a.set(title="Share of contracts with a valid tender-duration label, by method", xlabel="% of contracts", xlim=(0, 100))
    a.grid(axis="y", visible=False)
    caption(fig, "Contracts in the 184 cohort projects with notice references; methods with >= 20 contracts")
    fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(ch / "13_label_coverage_by_method.png"); plt.close(fig)

    # Correlations / multicollinearity (Spearman; descriptive only)
    cols = ["log_duration", "api_window_days", "project_age_days_at_tender", "project_prior_tender_notices", "log_commitment_snapshot",
            "gdp_growth_annual_pct", "inflation_consumer_prices_annual_pct", "gdp_per_capita_current_usd"] + WGI[:4]
    corr = L[cols].corr(method="spearman")
    corr.round(3).to_csv(out / "spearman_correlations_L.csv")
    fig, a = plt.subplots(figsize=(9, 7.5))
    im = a.imshow(corr.to_numpy(), cmap="RdBu_r", vmin=-1, vmax=1)
    short = ["ln duration", "bid window", "project age", "prior tenders", "ln commitment*", "GDP growth", "inflation", "GDP pc",
             "WGI gov eff", "WGI reg qual", "WGI rule law", "WGI corruption"]
    a.set_xticks(range(len(cols)), short, rotation=45, ha="right"); a.set_yticks(range(len(cols)), short)
    for i in range(len(cols)):
        for j in range(len(cols)):
            a.text(j, i, f"{corr.iloc[i, j]:.2f}", ha="center", va="center", fontsize=6.5,
                   color="white" if abs(corr.iloc[i, j]) > .6 else "#0b0b0b")
    a.grid(False); fig.colorbar(im, fraction=.04, label="Spearman rho")
    a.set_title("Rank correlations of candidate numeric features (descriptive, not causal)")
    caption(fig, POP_L + "; * snapshot value; country values current vintage, t-2")
    fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(ch / "14_spearman_correlations.png"); plt.close(fig)
    res["spearman_with_log_duration"] = corr["log_duration"].drop("log_duration").round(3).to_dict()
    from statsmodels.stats.outliers_influence import variance_inflation_factor
    X = L[cols[1:]].dropna()
    X = (X - X.mean()) / X.std()
    X.insert(0, "const", 1.0)
    res["vif_complete_cases"] = {"n": int(len(X)), **{c: round(float(variance_inflation_factor(X.to_numpy(), i)), 2)
                                                      for i, c in enumerate(X.columns) if c != "const"}}
    wg = L[WGI].dropna()
    res["wgi_internal_correlation_min"] = round(float(wg.corr().to_numpy()[np.triu_indices(6, 1)].min()), 3)

    # ---------- E. data quality / missingness ----------
    miss_cols = ["api_window_days", "project_age_days_at_tender", "review_type", "wdi_country_code", "gdp_growth_annual_pct",
                 "inflation_consumer_prices_annual_pct", "wgi_government_effectiveness_est", "cpia_irai_overall",
                 "income_group_at_tender_fy", "primary_major_sector", "log_commitment_snapshot"]
    mp = (L[miss_cols].isna().mean() * 100).sort_values()
    fig, a = plt.subplots(figsize=(9, 4.4))
    a.barh(mp.index, mp.values, color=BLUE, height=.6)
    for i, v in enumerate(mp.values):
        a.text(v + .8, i, f"{v:.1f}%", va="center", fontsize=8, color=INK2)
    a.set(title="Missing values in candidate features", xlabel="% of labelled contracts missing", xlim=(0, 100))
    a.grid(axis="y", visible=False)
    caption(fig, POP_L)
    fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(ch / "15_feature_missingness.png"); plt.close(fig)
    res["missingness_L_pct"] = mp.round(2).to_dict()
    res["missingness_by_geography"] = L.groupby("geography_type")[["gdp_growth_annual_pct", "wgi_government_effectiveness_est"]].apply(
        lambda d: d.isna().mean().round(3)).to_dict("index")
    res["cpia_missing_by_income"] = L.groupby("income_group_at_tender_fy", dropna=False)["cpia_irai_overall"].apply(lambda s: round(float(s.isna().mean()), 3)).to_dict()
    res["review_type_vs_duration"] = L.groupby("review_type")["duration_days"].median().to_dict()
    res["representation"] = {
        "labels_per_project": describe(L.groupby("project_id").size()),
        "top10_projects_share_pct": round(100 * L["project_id"].value_counts().head(10).sum() / len(L), 2),
        "pre_approval_tenders": int((L["project_age_days_at_tender"] < 0).sum()),
    }

    # Contract population (Model 2 unit) and tender-notice population (Model 3 unit) overview charts
    fig, axs = plt.subplots(1, 2, figsize=(12, 4))
    c_fy = ca["contract_fiscal_year"].value_counts().sort_index()
    axs[0].bar(c_fy.index.astype(str), c_fy.values, color=BLUE, width=.7)
    axs[0].set(title="Cohort contracts by signing fiscal year", xlabel="WB fiscal year", ylabel="Contracts")
    t_fy = T.groupby(["publication_fiscal_year", "notice_type"]).size().unstack(fill_value=0)
    t_fy = t_fy[[c for c in TENDER_TYPES if c in t_fy.columns]]
    bottom = np.zeros(len(t_fy))
    for k, c in enumerate(t_fy.columns):
        axs[1].bar(t_fy.index.astype(str), t_fy[c], bottom=bottom, color=SERIES[k], width=.7, label=c, edgecolor="white", linewidth=1)
        bottom += t_fy[c].to_numpy()
    axs[1].set(title="Cohort tender notices by publication fiscal year", xlabel="WB fiscal year", ylabel="Notices")
    axs[1].tick_params(axis="x", rotation=90); axs[1].legend(frameon=False, fontsize=7.5)
    caption(fig, "Populations C (left) and T (right); FY2027 is partial (snapshot October 2026)")
    fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(ch / "16_contract_and_tender_populations_by_fy.png"); plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(12, 4))
    amt = np.log10(ca["contract_amount_usd"].where(ca["contract_amount_usd"] > 0).dropna())
    axs[0].hist(amt, bins=60, color=BLUE, edgecolor="white", linewidth=0.4)
    axs[0].set(title="Contract amount (signed, snapshot)", xlabel="log10(US$)", ylabel="Contracts")
    w = T["published_response_window_days"].dropna()
    axs[1].hist(w.clip(upper=120), bins=60, color=BLUE, edgecolor="white", linewidth=0.4)
    axs[1].set(title="Published bid / response window (clipped at 120 d)", xlabel="Days (deadline - publication)", ylabel="Notices")
    caption(fig, "Population C (left; Model 2 unit) and T (right; Model 3 unit). Not tender duration.")
    fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(ch / "17_contract_amount_and_bid_window.png"); plt.close(fig)

    write_json(out / "eda_summary.json", res)
    print(out)
    print({k: res[k] for k in ("spearman_with_log_duration", "vif_complete_cases", "missingness_L_pct", "representation")})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
