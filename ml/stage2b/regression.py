"""Stage 2B: OLS / Ridge / Lasso / Elastic Net for Model 1 (tender duration), scored on VALIDATION only.

Usage (from procurement-intelligence/):
  /opt/anaconda3/bin/python3 ml/stage2b/regression.py --protocol asof  --upto ols|ridge|lasso|enet
  /opt/anaconda3/bin/python3 ml/stage2b/regression.py --protocol retro --upto enet

Protocols
  asof   PRIMARY. Training = training-period labels whose contract was signed by 2022-06-30 (462 rows), i.e. only
         outcomes observable at the historical cutoff. Baselines are refitted on the same rows.
  retro  SECONDARY, retrospective. All 646 training-period labels (some outcomes became known only later).
Target: ln(duration_days); point prediction exp(yhat) (conditional median, MAE-oriented); a smearing-corrected mean
prediction is reported as a secondary column. Features: inventory `include` set (first notice type is dropped
because it is identical to category: every REOI is consulting). All preprocessing lives in one sklearn Pipeline
fitted on training folds only; hyperparameters by 5-fold GroupKFold on project inside the training rows.
Models are added in order (baselines -> OLS -> Ridge -> Lasso -> Elastic Net) with `--upto`, so each can be audited
before the next. The test set is never loaded with targets.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, Lasso, LinearRegression, Ridge
from sklearn.metrics import make_scorer
from sklearn.model_selection import GridSearchCV, GroupKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage2a"))
sys.path.insert(0, str(ROOT / "phase1"))
from common import INK2, ML_ROOT, SERIES, caption, new_run_dir, style_mpl, write_json  # noqa: E402
from baselines import B1_LEVELS, B1M_LEVELS, MedianBaseline, metrics  # noqa: E402
from split import FEATURES, LABELS, SPLITS, TARGET, file_sha, load  # noqa: E402

SEED = 20261025
N_BOOT = 2000
ORDER = ["ols", "ridge", "lasso", "enet"]
CAT = ["procurement_method", "procurement_category", "review_type", "region_name", "country_grp", "sector"]
NUM_LOG = ["api_window_days", "project_prior_tender_notices"]
# income group is ordinal (L<LM<UM<H); regional projects have none and are median-imputed, their effect is carried
# by country_grp=REGIONAL (a separate REGIONAL income level duplicated that dummy exactly: OLS audit run 1)
NUM = ["project_age_days_at_tender", "approval_fiscal_year", "income_ordinal"]
INCOME_ORD = {"L": 1.0, "LM": 2.0, "UM": 3.0, "H": 4.0}
MIN_FREQ = 20
GRIDS = {
    "ridge": {"model__alpha": np.logspace(-3, 3, 25)},
    "lasso": {"model__alpha": np.logspace(-4, 0, 25)},
    "enet": {"model__alpha": np.logspace(-4, 1, 21), "model__l1_ratio": [0.1, 0.3, 0.5, 0.7, 0.9]},
}


def prep_features(df: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame(index=df.index)
    x["procurement_method"] = df["procurement_method"].str.replace(r"\s+", " ", regex=True)
    x["procurement_category"] = df["procurement_category"]
    x["review_type"] = df["review_type"].fillna("Unknown")
    x["region_name"] = df["region_name"]
    x["country_grp"] = df["wdi_country_code"].fillna("REGIONAL")
    x["sector"] = df["primary_major_sector"].fillna("Unknown").str.replace(r"^FY17\s*-\s*", "", regex=True)
    for c in NUM_LOG + NUM:
        if c != "income_ordinal":
            x[c] = df[c].astype(float)
    x["income_ordinal"] = df["income_group_at_tender_fy"].map(INCOME_ORD).astype(float)
    return x


def make_pipeline(model, drop_first: bool) -> Pipeline:
    ohe = OneHotEncoder(min_frequency=MIN_FREQ, handle_unknown="infrequent_if_exist", drop="first" if drop_first else None,
                        sparse_output=False)
    pre = ColumnTransformer([
        ("cat", ohe, CAT),
        ("log", Pipeline([("imp", SimpleImputer(strategy="median")), ("log1p", FunctionTransformer(np.log1p, feature_names_out="one-to-one")),
                          ("sc", StandardScaler())]), NUM_LOG),
        ("num", Pipeline([("imp", SimpleImputer(strategy="median")), ("sc", StandardScaler())]), NUM),
    ], verbose_feature_names_out=False)
    return Pipeline([("pre", pre), ("model", model)])


def mae_days(y_log, p_log) -> float:
    return float(np.mean(np.abs(np.exp(p_log) - np.exp(y_log))))


SCORER = make_scorer(mae_days, greater_is_better=False)


def paired_boot(df: pd.DataFrame, cols: list[str], ref: str, cluster: str, rng) -> dict:
    g = df.groupby(cluster).indices
    keys = list(g)
    ae = {c: np.abs(df[c].to_numpy() - df[TARGET].to_numpy()) for c in cols}
    mae = {c: [] for c in cols}
    red = {c: [] for c in cols if c != ref}
    for _ in range(N_BOOT):
        idx = np.concatenate([g[k] for k in rng.choice(keys, size=len(keys), replace=True)])
        r = ae[ref][idx].mean()
        for c in cols:
            m = ae[c][idx].mean()
            mae[c].append(m)
            if c != ref:
                red[c].append(r - m)
    ci = lambda v: [round(float(np.quantile(v, .025)), 1), round(float(np.quantile(v, .975)), 1)]  # noqa: E731
    return {"clusters": len(keys), "reference": ref, "MAE_95ci": {c: ci(v) for c, v in mae.items()},
            "MAE_reduction_vs_ref_95ci": {c: ci(v) for c, v in red.items()},
            "share_of_resamples_reduction_le_0": {c: round(float(np.mean(np.array(v) <= 0)), 4) for c, v in red.items()}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", choices=["asof", "asof_fu365", "retro"], required=True)
    ap.add_argument("--upto", choices=ORDER, required=True)
    a = ap.parse_args()
    models_to_run = ORDER[: ORDER.index(a.upto) + 1]
    out = new_run_dir(f"stage2b_{a.protocol}_{a.upto}")
    ch = out / "charts"
    ch.mkdir()
    rng = np.random.default_rng(SEED)
    d = load()
    assert TARGET not in d["test_features_only"].columns
    tr_all, va = d["train"].reset_index(drop=True), d["validation"].reset_index(drop=True)
    cutoff = SPLITS["train"][1]
    if a.protocol == "asof":
        tr = tr_all[tr_all["contract_signing_date"] <= cutoff]
    elif a.protocol == "asof_fu365":  # sensitivity: as-of, and only tenders with >= 365 days of follow-up at the cutoff
        tr = tr_all[(tr_all["contract_signing_date"] <= cutoff) & (tr_all["first_tender_date"] <= cutoff - pd.Timedelta(days=365))]
    else:
        tr = tr_all
    tr = tr.reset_index(drop=True)
    res: dict = {"run": out.name, "protocol": a.protocol, "models": ["B0", "B1", "B1m"] + models_to_run, "seed": SEED,
                 "test_scored": False, "inputs": {"labels_sha256": file_sha(LABELS), "features_sha256": file_sha(FEATURES)},
                 "training_rows": len(tr), "training_projects": int(tr["project_id"].nunique()),
                 "training_packages": int(tr["package_key"].nunique()), "validation_rows": len(va),
                 "training_definition": {"asof": "training-period labels signed on/before 2022-06-30 (observable at the cutoff)",
                                         "asof_fu365": "SENSITIVITY: as-of 2022-06-30 and first tender on/before 2021-06-30 (>= 365 days of follow-up at the cutoff)",
                                         "retro": "all training-period labels (RETROSPECTIVE: 184 outcomes became observable after the cutoff)"}[a.protocol],
                 "max_training_signing_date": str(tr["contract_signing_date"].max().date()),
                 "features": {"categorical": CAT, "log1p_numeric": NUM_LOG, "numeric": NUM, "rare_level_min_frequency": MIN_FREQ,
                              "excluded_include_feature": {"first_tender_type": "identical to procurement_category (REOI = consulting); exact collinearity"}}}
    if a.protocol == "asof":
        assert len(tr) == 462 and (tr["contract_signing_date"] <= cutoff).all()
    Xtr, Xva = prep_features(tr), prep_features(va)
    ytr = np.log(tr[TARGET].to_numpy().astype(float))
    yva_days = va[TARGET].to_numpy().astype(float)
    groups = tr["project_id"].to_numpy()
    gkf = GroupKFold(n_splits=5)

    # ---- baselines refitted on the same training rows ----
    for name, mdl in (("B0", MedianBaseline([], 1)), ("B1", MedianBaseline(B1_LEVELS, 1)), ("B1m", MedianBaseline(B1M_LEVELS, 1))):
        mdl.fit(tr, tr[TARGET])
        va[f"pred_{name}"], _ = mdl.predict(va)
    res["B0_training_median"] = float(np.median(tr[TARGET]))

    # ---- linear models, in order ----
    fitted, details = {}, {}
    for name in models_to_run:
        if name == "ols":
            pipe = make_pipeline(LinearRegression(), drop_first=True)
            pipe.fit(Xtr, ytr)
            best = {}
            cv_mae = -np.mean([SCORER(make_pipeline(LinearRegression(), True).fit(Xtr.iloc[i], ytr[i]), Xtr.iloc[j], ytr[j])
                               for i, j in gkf.split(Xtr, ytr, groups)])
        else:
            base = {"ridge": Ridge(), "lasso": Lasso(max_iter=50000), "enet": ElasticNet(max_iter=50000)}[name]
            gs = GridSearchCV(make_pipeline(base, drop_first=False), GRIDS[name], scoring=SCORER, cv=gkf, n_jobs=1)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                gs.fit(Xtr, ytr, groups=groups)
            pipe, best, cv_mae = gs.best_estimator_, {k.replace("model__", ""): float(v) for k, v in gs.best_params_.items()}, -gs.best_score_
            cvr = pd.DataFrame(gs.cv_results_)
            cvr["mean_cv_mae_days"] = -cvr["mean_test_score"]
            cvr[[c for c in cvr.columns if c.startswith("param_")] + ["mean_cv_mae_days", "std_test_score"]].to_csv(out / f"cv_curve_{name}.csv", index=False)
            details.setdefault(name, {})["cv_mae_at_grid_edges"] = {"first": round(float(cvr["mean_cv_mae_days"].iloc[0]), 2), "last": round(float(cvr["mean_cv_mae_days"].iloc[-1]), 2)}
        fitted[name] = pipe
        oof = cross_val_predict(make_pipeline(type(pipe.named_steps["model"])(**pipe.named_steps["model"].get_params()), name == "ols"),
                                Xtr, ytr, groups=groups, cv=gkf)
        resid_oof = ytr - oof
        q10, q90 = np.quantile(resid_oof, [.1, .9])
        smear = float(np.mean(np.exp(resid_oof)))
        p_log = pipe.predict(Xva)
        va[f"pred_{name}"] = np.exp(p_log)
        va[f"predmean_{name}"] = np.exp(p_log) * smear
        va[f"lo_{name}"], va[f"hi_{name}"] = np.exp(p_log + q10), np.exp(p_log + q90)
        train_mae = mae_days(ytr, pipe.predict(Xtr))
        names = pipe.named_steps["pre"].get_feature_names_out()
        coef = pd.Series(pipe.named_steps["model"].coef_, index=names)
        coef.sort_values(key=np.abs, ascending=False).to_csv(out / f"coefficients_{name}.csv", header=["coef_log_scale"])
        det = details.setdefault(name, {})
        det.update({"best_params": best, "train_MAE_days": round(train_mae, 2), "cv_MAE_days": round(float(cv_mae), 2),
                    "validation_MAE_days": round(float(np.mean(np.abs(va[f'pred_{name}'] - yva_days))), 2),
                    "design_columns": int(len(names)), "nonzero_coefficients": int((coef.abs() > 1e-10).sum()),
                    "intercept": float(pipe.named_steps["model"].intercept_), "smearing_factor": round(smear, 4),
                    "oof_log_residual_q10_q90": [round(float(q10), 4), round(float(q90), 4)],
                    "P10_P90_interval_coverage_validation": round(float(((yva_days >= va[f"lo_{name}"]) & (yva_days <= va[f"hi_{name}"])).mean()), 3),
                    "median_interval_width_days": round(float((va[f"hi_{name}"] - va[f"lo_{name}"]).median()), 1),
                    "top_coefficients": {k: round(float(v), 3) for k, v in coef.sort_values(key=np.abs, ascending=False).head(12).items()}})
        if name == "ols":
            import statsmodels.api as sm
            from statsmodels.stats.outliers_influence import variance_inflation_factor
            Z = pd.DataFrame(pipe.named_steps["pre"].transform(Xtr), columns=names)
            ols = sm.OLS(ytr, sm.add_constant(Z)).fit()
            tab = pd.DataFrame({"coef": ols.params, "se": ols.bse, "p": ols.pvalues}).round(4)
            tab.to_csv(out / "ols_statsmodels_coefficients.csv")
            Zc = sm.add_constant(Z).to_numpy()
            vif = {c: round(float(variance_inflation_factor(Zc, i + 1)), 2) for i, c in enumerate(Z.columns)}
            det.update({"rank": int(np.linalg.matrix_rank(Zc)), "n_params": int(Zc.shape[1]), "condition_number": round(float(ols.condition_number), 1),
                        "r2_train_log": round(float(ols.rsquared), 3), "adj_r2_train_log": round(float(ols.rsquared_adj), 3),
                        "max_vif": max(vif.values()), "vif_gt_5": {k: v for k, v in vif.items() if v > 5},
                        "significant_p_lt_0.05": int((tab["p"] < 0.05).sum() - (tab.loc["const", "p"] < 0.05))})
        # rolling-origin diagnostic (expanding window, as-of within each fold) for the chosen settings
        ro = []
        for c0, c1 in (("2020-06-30", "2021-06-30"), ("2021-06-30", "2022-06-30")):
            c0, c1 = pd.Timestamp(c0), pd.Timestamp(c1)
            fold_tr = tr[tr["contract_signing_date"] <= c0]
            fold_va = tr[(tr["first_tender_date"] > c0) & (tr["first_tender_date"] <= c1) & (tr["contract_signing_date"] <= c1)]
            if fold_va.empty:  # e.g. asof_fu365 has no training tenders after 2021-06-30
                ro.append({"origin": str(c0.date()), "train_rows": len(fold_tr), "eval_rows": 0})
                continue
            m = make_pipeline(type(pipe.named_steps["model"])(**pipe.named_steps["model"].get_params()), name == "ols")
            m.fit(prep_features(fold_tr), np.log(fold_tr[TARGET].astype(float)))
            pr = np.exp(m.predict(prep_features(fold_va)))
            b1 = MedianBaseline(B1_LEVELS, 1).fit(fold_tr, fold_tr[TARGET]).predict(fold_va)[0]
            ro.append({"origin": str(c0.date()), "train_rows": len(fold_tr), "eval_rows": len(fold_va),
                       "MAE_model": round(float(np.mean(np.abs(pr - fold_va[TARGET]))), 1), "MAE_B1": round(float(np.mean(np.abs(b1 - fold_va[TARGET]))), 1)})
        det["rolling_origin_check"] = ro

    # ---- validation evaluation ----
    cols = ["B0", "B1", "B1m"] + models_to_run
    w = 1 / va.groupby("package_key")["package_key"].transform("size").to_numpy()
    res["validation_metrics"] = {c: metrics(yva_days, va[f"pred_{c}"].to_numpy(), w) for c in cols}
    res["validation_metrics_smearing_mean_prediction"] = {c: metrics(yva_days, va[f"predmean_{c}"].to_numpy())["MAE"] for c in models_to_run}
    b0, b1 = res["validation_metrics"]["B0"]["MAE"], res["validation_metrics"]["B1"]["MAE"]
    res["MAE_improvement_pct"] = {c: {"vs_B0": round(100 * (b0 - res["validation_metrics"][c]["MAE"]) / b0, 2),
                                      "vs_B1": round(100 * (b1 - res["validation_metrics"][c]["MAE"]) / b1, 2)} for c in cols}
    pc = [f"pred_{c}" for c in cols]
    res["bootstrap_vs_B1_project_clusters"] = paired_boot(va, pc, "pred_B1", "project_id", rng)
    res["bootstrap_vs_B1_package_clusters"] = paired_boot(va, pc, "pred_B1", "package_key", rng)
    res["model_details"] = details
    res["predictions_strictly_positive"] = {c: bool((va[f"pred_{c}"] > 0).all()) for c in cols}
    res["prediction_range_days"] = {c: [round(float(va[f"pred_{c}"].min()), 1), round(float(va[f"pred_{c}"].max()), 1)] for c in cols}

    # ---- segment and residual audit ----
    trp = set(tr["project_id"])
    va["project_seen_in_training"] = va["project_id"].isin(trp)
    va["quarter"] = va["first_tender_date"].dt.to_period("Q").astype(str)
    p25, p75 = np.quantile(tr[TARGET], [.25, .75])
    va["duration_band"] = np.where(va[TARGET] > p75, "long (> train p75)", np.where(va[TARGET] < p25, "short (< train p25)", "middle"))
    seg = {}
    for col in ("procurement_method", "procurement_category", "review_type", "region_name", "project_country_name", "project_seen_in_training", "quarter", "duration_band"):
        rows = []
        for k, g in va.groupby(col):
            r = {"segment": str(k), "n": len(g), "projects": int(g["project_id"].nunique()), "actual_median": float(g[TARGET].median())}
            for c in cols:
                e = g[f"pred_{c}"] - g[TARGET]
                r[f"MAE_{c}"], r[f"bias_{c}"] = round(float(e.abs().mean()), 1), round(float(e.mean()), 1)
            r["sparse_n_lt_20"] = len(g) < 20
            rows.append(r)
        t = pd.DataFrame(rows).sort_values("n", ascending=False)
        t.to_csv(out / f"segments_by_{col}.csv", index=False)
        seg[col] = t.to_dict("records")
    res["segments"] = seg
    top5 = va["project_id"].value_counts().head(5).index
    ex = va[~va["project_id"].isin(top5)]
    res["MAE_excluding_top5_validation_projects"] = {c: round(float(np.abs(ex[f"pred_{c}"] - ex[TARGET]).mean()), 1) for c in cols}

    va[["wb_contract_number", "project_id", "procurement_method", "procurement_category", "review_type", "region_name", "first_tender_date", TARGET]
       + [f"pred_{c}" for c in cols] + [f"{p}_{m}" for m in models_to_run for p in ("lo", "hi", "predmean")]].to_csv(out / "validation_predictions.csv", index=False)

    # ---- charts ----
    plt = style_mpl()
    last = models_to_run[-1]
    best_lin = min(models_to_run, key=lambda c: res["validation_metrics"][c]["MAE"])
    ci = res["bootstrap_vs_B1_project_clusters"]["MAE_95ci"]
    fig, ax = plt.subplots(figsize=(9, 0.5 * len(cols) + 1.5))
    yy = np.arange(len(cols))[::-1]
    for i, c in zip(yy, cols):
        m = res["validation_metrics"][c]["MAE"]
        lo, hi = ci[f"pred_{c}"]
        ax.plot([lo, hi], [i, i], color=INK2, lw=1.5)
        ax.plot(m, i, "o", color=SERIES[0] if c.startswith("B") else SERIES[1], ms=8)
        ax.text(hi + 2, i, f"{m:.1f}", va="center", fontsize=8.5, color=INK2)
    ax.set_yticks(yy, cols)
    ax.set(title=f"Validation MAE with 95% project-cluster bootstrap CI ({a.protocol})", xlabel="MAE (days)")
    ax.grid(axis="y", visible=False)
    caption(fig, f"Validation n=353 (tenders 2022-07-01 to 2023-06-30); training {len(tr)} rows ({ {'asof': 'strictly as-of 2022-06-30', 'asof_fu365': 'as-of, >=365 d follow-up (sensitivity)', 'retro': 'RETROSPECTIVE'}[a.protocol] }). Not final performance")
    fig.tight_layout(rect=(0, .1, 1, 1)); fig.savefig(ch / "01_mae_comparison.png"); plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(11.5, 4.6))
    axs[0].scatter(yva_days, va[f"pred_{best_lin}"], s=12, color=SERIES[1], alpha=.55, edgecolor="none")
    axs[0].plot((5, 1500), (5, 1500), ls="--", color=INK2, lw=1)
    axs[0].set(xscale="log", yscale="log", xlim=(5, 1500), ylim=(5, 1500), xlabel="Actual days (log)", ylabel="Predicted days (log)",
               title=f"{best_lin.upper()}: predicted vs actual")
    axs[1].scatter(np.log(va[f"pred_{best_lin}"]), np.log(yva_days) - np.log(va[f"pred_{best_lin}"]), s=12, color=SERIES[1], alpha=.55, edgecolor="none")
    axs[1].axhline(0, color=INK2, lw=1)
    axs[1].set(xlabel="Fitted ln(days)", ylabel="Residual ln(actual) - ln(predicted)", title="Residual vs fitted (log scale)")
    caption(fig, f"Validation n=353; best linear model on validation MAE = {best_lin}; protocol {a.protocol}")
    fig.tight_layout(rect=(0, .1, 1, 1)); fig.savefig(ch / "02_best_linear_pred_vs_actual_residuals.png"); plt.close(fig)

    co = pd.read_csv(out / f"coefficients_{last}.csv", index_col=0)["coef_log_scale"]
    co = co[co.abs() > 1e-10].sort_values(key=np.abs, ascending=False).head(20)[::-1]
    fig, ax = plt.subplots(figsize=(9, 0.32 * len(co) + 1.6))
    ax.barh(co.index, co.values, color=[SERIES[1] if v > 0 else SERIES[0] for v in co.values], height=.6)
    ax.axvline(0, color=INK2, lw=1)
    ax.set(title=f"{last.upper()}: 20 largest coefficients", xlabel="Coefficient on ln(days); numeric features standardised (exp(b) = multiplicative effect)")
    ax.grid(axis="y", visible=False)
    caption(fig, f"Fitted on {len(tr)} training rows ({a.protocol}); associations, not causal effects")
    fig.tight_layout(rect=(0, .1, 1, 1)); fig.savefig(ch / f"03_coefficients_{last}.png"); plt.close(fig)

    t = pd.DataFrame(seg["review_type"])
    fig, ax = plt.subplots(figsize=(9, 3.4))
    xs = np.arange(len(t))
    for i, c in enumerate(["B1"] + models_to_run):
        ax.bar(xs + (i - len(models_to_run) / 2) * 0.16, t[f"bias_{c}"], width=.15, color=SERIES[i], label=c)
    ax.axhline(0, color=INK2, lw=1)
    ax.set_xticks(xs, [f"{s} (n={n})" for s, n in zip(t["segment"], t["n"])])
    ax.set(title="Mean bias by review type (predicted - actual)", ylabel="Days")
    ax.legend(frameon=False, fontsize=8, ncol=5)
    caption(fig, f"Validation n=353; protocol {a.protocol}")
    fig.tight_layout(rect=(0, .1, 1, 1)); fig.savefig(ch / "04_bias_by_review_type.png"); plt.close(fig)

    write_json(out / "regression_metrics.json", res)
    exp = ML_ROOT / "experiments"
    exp.mkdir(exist_ok=True)
    with (exp / "runs.jsonl").open("a") as fh:
        for c in cols:
            fh.write(json.dumps({
                "logged_at": dt.datetime.now(dt.timezone.utc).isoformat(), "stage": "2B", "model": "M1_tender_time", "variant": c,
                "family": "median_baseline" if c.startswith("B") else "linear_regression", "protocol": a.protocol,
                "run_dir": str(out.relative_to(ML_ROOT.parent)), "label_rule": "tender_label_rule_p02_v1", "label_source": "real",
                "data": res["inputs"] | {"cohort_manifest_sha256": "82a43185f427b3e647e062086cfea812018964e4f8cbb91877346e9a02dac940"},
                "training_rows": len(tr), "target": "ln(duration_days)" if not c.startswith("B") else "duration_days (median)",
                "features": [] if c == "B0" else (["procurement_method", "procurement_category"] if c == "B1" else (["procurement_method"] if c == "B1m" else CAT + NUM_LOG + NUM)),
                "preprocessing": None if c.startswith("B") else f"OneHot(min_frequency={MIN_FREQ}, drop={'first' if c == 'ols' else None}); median impute; log1p {NUM_LOG}; standardise numeric",
                "params": details.get(c, {}).get("best_params", {}), "cv": "GroupKFold(5) by project on training rows",
                "seed": SEED, "validation_metrics": res["validation_metrics"][c], "test_scored": False}, default=str) + "\n")
    print(out)
    show = {k: res[k] for k in ("training_rows", "training_projects", "max_training_signing_date", "B0_training_median", "MAE_improvement_pct",
                                "bootstrap_vs_B1_project_clusters", "bootstrap_vs_B1_package_clusters", "validation_metrics_smearing_mean_prediction",
                                "MAE_excluding_top5_validation_projects", "prediction_range_days")}
    print(json.dumps(show, indent=1, default=str))
    print(pd.DataFrame(res["validation_metrics"]).T[["MAE", "MedAE", "RMSE", "bias_mean_pred_minus_actual", "abs_error_p90", "share_within_30d", "share_within_50pct", "MAE_package_weighted"]].to_string())
    print(json.dumps(details, indent=1, default=str))


if __name__ == "__main__":
    main()
