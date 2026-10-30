"""Stage 2C/2D: B1, Ridge, Random Forest and XGBoost for Model 1 under a frozen pre-registration (validation only).

Usage (from procurement-intelligence/):
  /opt/anaconda3/bin/python3 ml/stage2c/advanced.py --protocol asof|cfw365 --models b1,ridge,rf[,xgb]

Everything that could be tuned is fixed in ml/stage2c/PREREGISTRATION.json (threshold, scores, alert rules, budgets);
the script refuses to run if the file's SHA-256 differs from the one recorded on first use.
- Target ln(duration_days); point prediction exp(yhat). Same Stage 2B features and train-only Pipeline.
- Hyperparameters by 5-fold GroupKFold on project inside the training rows (MAE in days).
- Long-duration score P(duration > 365): B1 = cell exceedance share; regression models = log-normal with the
  model's out-of-fold residual SD. Primary alert threshold chosen on training out-of-fold scores (max F1, alert
  rate <= 30%); secondary rule = point prediction > 365; capacity view = top 20% of scores.
- Validation: the same 353 contracts. Test set never loaded with targets.
Outputs: ml/eda/stage2c_<protocol>_<models>_<UTC>/ (metrics JSON/CSV, predictions, charts, models/*.joblib) and
records appended to ml/experiments/runs.jsonl.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.base import clone
from sklearn.ensemble import RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.linear_model import Ridge
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score
from sklearn.model_selection import GridSearchCV, GroupKFold, ParameterSampler, cross_val_predict
from sklearn.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage2b"))
sys.path.insert(0, str(ROOT / "stage2a"))
sys.path.insert(0, str(ROOT / "phase1"))
from common import INK2, ML_ROOT, SERIES, caption, new_run_dir, style_mpl, write_json  # noqa: E402
from baselines import B1_LEVELS, MedianBaseline, metrics  # noqa: E402
from regression import CAT, GRIDS, NUM, NUM_LOG, SCORER, make_pipeline, mae_days, paired_boot, prep_features  # noqa: E402
from split import FEATURES, LABELS, SPLITS, TARGET, file_sha, load  # noqa: E402

PREREG = Path(__file__).resolve().parent / "PREREGISTRATION.json"
PREREG_LOCK = Path(__file__).resolve().parent / "PREREGISTRATION.sha256"
SEED = 20261025
N_BOOT = 2000
ORDER = ["b1", "ridge", "rf", "xgb"]
LABEL = {"b1": "B1", "ridge": "Ridge", "rf": "RandomForest", "xgb": "XGBoost"}


def check_prereg() -> dict:
    sha = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    if PREREG_LOCK.exists():
        if PREREG_LOCK.read_text().strip() != sha:
            raise SystemExit("PREREGISTRATION.json changed after it was locked; refusing to run")
    else:
        PREREG_LOCK.write_text(sha + "\n")
    p = json.loads(PREREG.read_text())
    p["_sha256"] = sha
    return p


class LevelMean:
    """Hierarchical cell mean (same levels/fallback as B1) used for B1's long-duration exceedance score."""

    def __init__(self, levels):
        self.levels = levels

    def fit(self, df, v):
        self.global_ = float(np.mean(v))
        self.tables_ = []
        for cols in self.levels:
            g = pd.DataFrame({**{c: df[c].to_numpy() for c in cols}, "v": np.asarray(v)}).groupby(list(cols))["v"].mean()
            self.tables_.append((cols, g))
        return self

    def predict(self, df):
        out = np.full(len(df), np.nan)
        for cols, t in self.tables_:
            key = pd.MultiIndex.from_frame(df[list(cols)]) if len(cols) > 1 else pd.Index(df[cols[0]])
            v = t.reindex(key).to_numpy()
            take = np.isnan(out) & ~np.isnan(v)
            out[take] = v[take]
        out[np.isnan(out)] = self.global_
        return out


def choose_threshold(y_long: np.ndarray, score: np.ndarray, max_alert: float) -> dict:
    best = {"t": 1.01, "f1": 0.0, "alert_rate": 0.0, "precision": 0.0, "recall": 0.0}
    for t in np.unique(score)[::-1]:  # descending, so ties keep the higher t
        alert = score >= t
        ar = alert.mean()
        if ar > max_alert:
            continue
        tp = (alert & y_long).sum()
        prec = tp / alert.sum() if alert.sum() else 0.0
        rec = tp / y_long.sum() if y_long.sum() else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        if f1 > best["f1"] + 1e-12:
            best = {"t": float(t), "f1": float(f1), "alert_rate": float(ar), "precision": float(prec), "recall": float(rec)}
    return best


def detection(y_long: np.ndarray, alert: np.ndarray) -> dict:
    tp, fp = int((alert & y_long).sum()), int((alert & ~y_long).sum())
    fn, tn = int((~alert & y_long).sum()), int((~alert & ~y_long).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {"actual_long": int(y_long.sum()), "TP": tp, "FN": fn, "FP": fp, "TN": tn, "recall": round(rec, 3), "precision": round(prec, 3),
            "F1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0, "alert_rate": round(float(alert.mean()), 3),
            "alerts": int(alert.sum())}


def boot_detection(df: pd.DataFrame, cols: list[str], cluster: str, rng) -> dict:
    g = df.groupby(cluster).indices
    keys = list(g)
    y = df["y_long"].to_numpy()
    out = {c: {"recall": [], "precision": [], "F1": []} for c in cols}
    for _ in range(N_BOOT):
        idx = np.concatenate([g[k] for k in rng.choice(keys, size=len(keys), replace=True)])
        for c in cols:
            d = detection(y[idx], df[c].to_numpy()[idx])
            for k in ("recall", "precision", "F1"):
                out[c][k].append(d[k])
    return {c: {k: [round(float(np.quantile(v, .025)), 3), round(float(np.quantile(v, .975)), 3)] for k, v in m.items()} for c, m in out.items()}


def fit_xgb(Xtr, ytr, groups, budget: dict, details: dict):
    from xgboost import XGBRegressor

    space = {"max_depth": [2, 3, 4], "learning_rate": [0.03, 0.05, 0.1], "min_child_weight": [5, 10, 20], "subsample": [0.7, 0.9],
             "colsample_bytree": [0.5, 0.8], "reg_lambda": [1, 5, 10], "reg_alpha": [0, 0.5]}
    gkf = GroupKFold(n_splits=5)
    folds = list(gkf.split(Xtr, ytr, groups))
    pre0 = make_pipeline(Ridge(), False).named_steps["pre"]
    fold_data = []
    for i, j in folds:
        pre = clone(pre0).fit(Xtr.iloc[i])
        fold_data.append((pre.transform(Xtr.iloc[i]), ytr[i], pre.transform(Xtr.iloc[j]), ytr[j], j))
    trials = []
    for cfg in ParameterSampler(space, n_iter=30, random_state=SEED):
        curves, models = [], []
        for A, ya, B, yb, _ in fold_data:
            m = XGBRegressor(n_estimators=1000, objective="reg:squarederror", tree_method="hist", random_state=SEED, n_jobs=4,
                             eval_metric="rmse", **cfg)
            m.fit(A, ya, eval_set=[(B, yb)], verbose=False)
            curves.append(m.evals_result()["validation_0"]["rmse"])
            models.append(m)
        mean_curve = np.mean(curves, axis=0)
        best_n, best_v, wait = 1, np.inf, 0
        for k, v in enumerate(mean_curve, start=1):  # patience-50 early stopping on the averaged CV curve
            if v < best_v - 1e-12:
                best_n, best_v, wait = k, v, 0
            else:
                wait += 1
                if wait >= 50:
                    break
        oof = np.zeros(len(ytr))
        for (A, ya, B, yb, j), m in zip(fold_data, models):
            oof[j] = m.predict(B, iteration_range=(0, best_n))
        trials.append({"cfg": cfg, "n_estimators": best_n, "cv_mae_days": mae_days(ytr, oof), "oof": oof})
    best = min(trials, key=lambda r: r["cv_mae_days"])
    pipe = Pipeline([("pre", clone(pre0)), ("model", XGBRegressor(n_estimators=best["n_estimators"], objective="reg:squarederror", tree_method="hist",
                                                                   random_state=SEED, n_jobs=4, **best["cfg"]))])
    pipe.fit(Xtr, ytr)
    details["best_params"] = {**{k: (float(v) if isinstance(v, float) else v) for k, v in best["cfg"].items()}, "n_estimators": best["n_estimators"]}
    details["cv_MAE_days"] = round(best["cv_mae_days"], 2)
    details["search"] = sorted([{"cv_mae_days": round(t["cv_mae_days"], 2), "n_estimators": t["n_estimators"], **t["cfg"]} for t in trials],
                               key=lambda r: r["cv_mae_days"])[:10]
    details["n_estimators_at_cap"] = sum(t["n_estimators"] >= 1000 for t in trials)
    return pipe, best["oof"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", choices=["asof", "cfw365"], required=True)
    ap.add_argument("--models", required=True)
    a = ap.parse_args()
    models = [m for m in ORDER if m in a.models.split(",")]
    pr = check_prereg()
    TH = pr["long_duration"]["threshold_days"]
    out = new_run_dir(f"stage2c_{a.protocol}_{'_'.join(models)}")
    ch, md = out / "charts", out / "models"
    ch.mkdir()
    md.mkdir()
    rng = np.random.default_rng(SEED)
    d = load()
    assert TARGET not in d["test_features_only"].columns
    tr_all, va = d["train"].reset_index(drop=True), d["validation"].reset_index(drop=True)
    cut = SPLITS["train"][1]
    tr = tr_all[tr_all["contract_signing_date"] <= cut]
    if a.protocol == "cfw365":
        tr = tr[tr["first_tender_date"] <= cut - pd.Timedelta(days=365)]
    tr = tr.reset_index(drop=True)
    assert len(tr) == {"asof": 462, "cfw365": 372}[a.protocol]
    Xtr, Xva = prep_features(tr), prep_features(va)
    ytr = np.log(tr[TARGET].to_numpy().astype(float))
    yva = va[TARGET].to_numpy().astype(float)
    groups = tr["project_id"].to_numpy()
    gkf = GroupKFold(n_splits=5)
    ytr_long = tr[TARGET].to_numpy() > TH
    va["y_long"] = yva > TH
    res: dict = {"run": out.name, "protocol": a.protocol, "models": models, "prereg_sha256": pr["_sha256"], "threshold_days": TH,
                 "training_rows": len(tr), "training_long_positives": int(ytr_long.sum()), "validation_rows": len(va),
                 "validation_long_positives": int(va["y_long"].sum()), "test_scored": False, "seed": SEED,
                 "inputs": {"labels_sha256": file_sha(LABELS), "features_sha256": file_sha(FEATURES)}, "details": {}}

    for name in models:
        det: dict = {}
        if name == "b1":
            mb = MedianBaseline(B1_LEVELS, 1).fit(tr, tr[TARGET])
            lm = LevelMean(B1_LEVELS).fit(tr, ytr_long.astype(float))
            va["pred_b1"], va["score_b1"] = mb.predict(va)[0], lm.predict(va)
            oof_score, oof_pred = np.zeros(len(tr)), np.zeros(len(tr))
            for i, j in gkf.split(tr, ytr, groups):
                oof_pred[j] = MedianBaseline(B1_LEVELS, 1).fit(tr.iloc[i], tr[TARGET].iloc[i]).predict(tr.iloc[j])[0]
                oof_score[j] = LevelMean(B1_LEVELS).fit(tr.iloc[i], ytr_long[i].astype(float)).predict(tr.iloc[j])
            det.update({"cv_MAE_days": round(float(np.mean(np.abs(oof_pred - tr[TARGET]))), 2),
                        "train_MAE_days": round(float(np.mean(np.abs(mb.predict(tr)[0] - tr[TARGET]))), 2)})
            joblib.dump({"median": mb, "exceedance": lm}, md / f"b1_{a.protocol}.joblib")
            fitted = None
        else:
            if name == "ridge":
                gs = GridSearchCV(make_pipeline(Ridge(), False), GRIDS["ridge"], scoring=SCORER, cv=gkf)
                gs.fit(Xtr, ytr, groups=groups)
                fitted, det["best_params"], det["cv_MAE_days"] = gs.best_estimator_, {"alpha": float(gs.best_params_["model__alpha"])}, round(-gs.best_score_, 2)
                oof_log = cross_val_predict(clone(fitted), Xtr, ytr, groups=groups, cv=gkf)
            elif name == "rf":
                grid = {"model__max_depth": [4, 6, 10, None], "model__min_samples_leaf": [5, 10, 20], "model__max_features": [0.3, 0.6, 1.0]}
                rf = RandomForestRegressor(n_estimators=500, random_state=SEED, n_jobs=-1)
                gs = GridSearchCV(make_pipeline(rf, False), grid, scoring=SCORER, cv=gkf)
                gs.fit(Xtr, ytr, groups=groups)
                fitted, det["cv_MAE_days"] = gs.best_estimator_, round(-gs.best_score_, 2)
                det["best_params"] = {k.replace("model__", ""): v for k, v in gs.best_params_.items()}
                cvr = pd.DataFrame(gs.cv_results_)
                cvr["cv_mae_days"] = -cvr["mean_test_score"]
                cvr[[c for c in cvr.columns if c.startswith("param_")] + ["cv_mae_days", "std_test_score"]].sort_values("cv_mae_days").to_csv(out / "cv_grid_rf.csv", index=False)
                det["cv_grid_top5"] = cvr.sort_values("cv_mae_days")[[c for c in cvr.columns if c.startswith("param_")] + ["cv_mae_days"]].head(5).astype(str).to_dict("records")
                oof_log = cross_val_predict(clone(fitted), Xtr, ytr, groups=groups, cv=gkf)
            else:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    fitted, oof_log = fit_xgb(Xtr, ytr, groups, pr["experiment_budget"], det)
            sigma = float(np.std(ytr - oof_log, ddof=1))
            p_log = fitted.predict(Xva)
            va[f"pred_{name}"] = np.exp(p_log)
            va[f"score_{name}"] = 1 - norm.cdf((np.log(TH) - p_log) / sigma)
            oof_score = 1 - norm.cdf((np.log(TH) - oof_log) / sigma)
            q10, q90 = np.quantile(ytr - oof_log, [.1, .9])
            va[f"lo_{name}"], va[f"hi_{name}"] = np.exp(p_log + q10), np.exp(p_log + q90)
            det.update({"train_MAE_days": round(mae_days(ytr, fitted.predict(Xtr)), 2), "oof_log_residual_sd": round(sigma, 4),
                        "P10_P90_coverage_validation": round(float(((yva >= va[f"lo_{name}"]) & (yva <= va[f"hi_{name}"])).mean()), 3),
                        "median_interval_width_days": round(float((va[f"hi_{name}"] - va[f"lo_{name}"]).median()), 1)})
            joblib.dump(fitted, md / f"{name}_{a.protocol}.joblib")
        thr = choose_threshold(ytr_long, oof_score, 0.30)
        det["alert_threshold_from_training_oof"] = thr
        va[f"alert_{name}"] = va[f"score_{name}"] >= thr["t"]
        va[f"alertfixed_{name}"] = va[f"pred_{name}"] > TH
        k = int(np.ceil(0.2 * len(va)))
        top = np.zeros(len(va), dtype=bool)
        top[np.argsort(-va[f"score_{name}"].to_numpy(), kind="stable")[:k]] = True
        va[f"alerttop20_{name}"] = top
        det["validation_MAE_days"] = round(float(np.mean(np.abs(va[f"pred_{name}"] - yva))), 2)
        res["details"][name] = det
        print(name, json.dumps(det, default=str)[:900], flush=True)

    # ---------------- regression evaluation ----------------
    w = 1 / va.groupby("package_key")["package_key"].transform("size").to_numpy()
    res["regression"] = {m: metrics(yva, va[f"pred_{m}"].to_numpy(), w) for m in models}
    pc = [f"pred_{m}" for m in models]
    for ref in ("b1", "ridge"):
        if ref in models:
            res[f"boot_vs_{ref}_project"] = paired_boot(va, pc, f"pred_{ref}", "project_id", rng)
            res[f"boot_vs_{ref}_package"] = paired_boot(va, pc, f"pred_{ref}", "package_key", rng)
    trp = set(tr["project_id"])
    va["project_seen_in_training"] = va["project_id"].isin(trp)
    p25, p75 = np.quantile(tr[TARGET], [.25, .75])
    va["duration_band"] = np.where(yva > TH, f"long (> {TH} d)", np.where(yva > p75, "upper (train p75..365]", np.where(yva < p25, "short (< train p25)", "middle")))
    seg = {}
    for col in ("procurement_method", "procurement_category", "review_type", "region_name", "project_country_name", "project_seen_in_training", "duration_band"):
        rows = []
        for key, g in va.groupby(col):
            r = {"segment": str(key), "n": len(g), "projects": int(g["project_id"].nunique()), "actual_median": float(g[TARGET].median()),
                 "actual_long": int(g["y_long"].sum())}
            for m in models:
                e = g[f"pred_{m}"] - g[TARGET]
                r[f"MAE_{m}"], r[f"bias_{m}"] = round(float(e.abs().mean()), 1), round(float(e.mean()), 1)
                if g["y_long"].sum():
                    r[f"recall_{m}"] = round(float((g[f"alert_{m}"] & g["y_long"]).sum() / g["y_long"].sum()), 3)
                r[f"alerts_{m}"] = int(g[f"alert_{m}"].sum())
            r["sparse_n_lt_20"] = len(g) < 20
            rows.append(r)
        t = pd.DataFrame(rows).sort_values("n", ascending=False)
        t.to_csv(out / f"segments_by_{col}.csv", index=False)
        seg[col] = t.to_dict("records")
    res["segments"] = seg

    # ---------------- long-duration detection ----------------
    y_long = va["y_long"].to_numpy()
    res["long_detection"] = {}
    for m in models:
        s = va[f"score_{m}"].to_numpy()
        res["long_detection"][m] = {
            "PR_AUC": round(float(average_precision_score(y_long, s)), 3), "ROC_AUC": round(float(roc_auc_score(y_long, s)), 3),
            "prevalence": round(float(y_long.mean()), 3),
            "primary_training_oof_threshold": detection(y_long, va[f"alert_{m}"].to_numpy()),
            "secondary_point_prediction_gt_365": detection(y_long, va[f"alertfixed_{m}"].to_numpy()),
            "capacity_top20pct": detection(y_long, va[f"alerttop20_{m}"].to_numpy()),
            "by_method_primary": {k: detection(g["y_long"].to_numpy(), g[f"alert_{m}"].to_numpy()) for k, g in va.groupby("procurement_method") if len(g) >= 20},
        }
    res["long_detection_boot_project"] = boot_detection(va, [f"alert_{m}" for m in models], "project_id", rng)
    res["long_detection_boot_package"] = boot_detection(va, [f"alert_{m}" for m in models], "package_key", rng)

    # ---------------- importance / explanations ----------------
    for m in models:
        if m in ("rf", "xgb"):
            fitted = joblib.load(md / f"{m}_{a.protocol}.joblib")
            sc = lambda est, X, y: -float(np.mean(np.abs(np.exp(est.predict(X)) - np.exp(y))))  # noqa: E731
            pi = permutation_importance(fitted, Xva, np.log(yva), scoring=sc, n_repeats=10, random_state=SEED)
            imp = pd.DataFrame({"feature": Xva.columns, "MAE_increase_days_mean": pi.importances_mean, "sd": pi.importances_std}).sort_values("MAE_increase_days_mean", ascending=False)
            imp.to_csv(out / f"permutation_importance_{m}_validation.csv", index=False)
            res["details"][m]["permutation_importance_validation_days"] = {r.feature: round(r.MAE_increase_days_mean, 2) for r in imp.itertuples()}
            if m == "xgb":
                import shap
                pre = fitted.named_steps["pre"]
                Z = pre.transform(Xva)
                names = list(pre.get_feature_names_out())
                sv = shap.TreeExplainer(fitted.named_steps["model"]).shap_values(Z)
                def orig(n):
                    for c in CAT + NUM_LOG + NUM:
                        if n == c or n.startswith(c + "_"):
                            return c
                    return n
                agg = pd.DataFrame(np.abs(sv), columns=names).T.groupby(orig).sum().T.mean().sort_values(ascending=False)
                agg.to_csv(out / "shap_mean_abs_by_feature_xgb_validation.csv", header=["mean_abs_shap_log_days"])
                pd.DataFrame(sv, columns=names).to_csv(out / "shap_values_xgb_validation.csv", index=False)
                res["details"][m]["shap_mean_abs_by_feature"] = {k: round(float(v), 4) for k, v in agg.items()}
                res["details"][m]["shap_additivity_max_error"] = float(np.max(np.abs(sv.sum(1) + shap.TreeExplainer(fitted.named_steps["model"]).expected_value - fitted.named_steps["model"].predict(Z))))

    keep = ["wb_contract_number", "project_id", "package_key", "procurement_method", "procurement_category", "review_type", "region_name",
            "first_tender_date", TARGET, "y_long"] + [c for c in va.columns if c.split("_")[0] in ("pred", "score", "alert", "alertfixed", "alerttop20", "lo", "hi")]
    va[keep].to_csv(out / "validation_predictions.csv", index=False)

    # ---------------- charts ----------------
    plt = style_mpl()
    cols = SERIES
    ci = res["boot_vs_b1_project"]["MAE_95ci"] if "b1" in models else {}
    fig, ax = plt.subplots(figsize=(8.5, 0.6 * len(models) + 1.6))
    for i, m in enumerate(models[::-1]):
        v = res["regression"][m]["MAE"]
        lo, hi = ci.get(f"pred_{m}", [v, v])
        ax.plot([lo, hi], [i, i], color=INK2, lw=1.4)
        ax.plot(v, i, "o", color=cols[ORDER.index(m)], ms=9)
        ax.text(hi + 1.5, i, f"{v:.1f}", va="center", fontsize=8.5, color=INK2)
    ax.set_yticks(range(len(models)), [LABEL[m] for m in models[::-1]])
    ax.set(title=f"Validation MAE (95% project-cluster CI) · {a.protocol}", xlabel="MAE (days)")
    ax.grid(axis="y", visible=False)
    caption(fig, f"Validation n=353 contracts (tenders 2022-07-01..2023-06-30); training {len(tr)} rows ({a.protocol}). Not final performance; test unscored")
    fig.tight_layout(rect=(0, .1, 1, 1)); fig.savefig(ch / "01_mae_ci.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 5))
    for m in models:
        p, r, _ = precision_recall_curve(y_long, va[f"score_{m}"])
        ax.plot(r, p, color=cols[ORDER.index(m)], lw=2, label=f"{LABEL[m]} (PR-AUC {res['long_detection'][m]['PR_AUC']:.2f})")
        dd = res["long_detection"][m]["primary_training_oof_threshold"]
        ax.plot(dd["recall"], dd["precision"], "o", color=cols[ORDER.index(m)], ms=9, markeredgecolor="white")
    ax.axhline(y_long.mean(), color=INK2, ls="--", lw=1)
    ax.text(0.98, y_long.mean() + .01, f"prevalence {y_long.mean():.2f}", ha="right", fontsize=8, color=INK2)
    ax.set(xlim=(0, 1), ylim=(0, 1.02), xlabel="Recall (long tenders caught)", ylabel="Precision (alerts that are truly long)",
           title=f"Detecting tenders > {TH} days · {a.protocol}")
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    caption(fig, f"Validation n=353 ({int(y_long.sum())} long). Dots = alert threshold frozen on training out-of-fold scores (max F1, alert rate <= 30%)")
    fig.tight_layout(rect=(0, .1, 1, 1)); fig.savefig(ch / "02_pr_curves_long.png"); plt.close(fig)

    fig, axs = plt.subplots(1, len(models), figsize=(3.6 * len(models), 3.9), sharey=True)
    for ax, m in zip(np.atleast_1d(axs), models):
        ax.scatter(yva, va[f"pred_{m}"], s=9, alpha=.5, color=cols[ORDER.index(m)], edgecolor="none")
        ax.plot((5, 1500), (5, 1500), ls="--", color=INK2, lw=1)
        ax.axvline(TH, color=INK2, lw=.6, ls=":")
        ax.axhline(TH, color=INK2, lw=.6, ls=":")
        ax.set(xscale="log", yscale="log", xlim=(10, 1500), ylim=(10, 1500), title=f"{LABEL[m]} · MAE {res['regression'][m]['MAE']:.1f}",
               xlabel="Actual days")
    np.atleast_1d(axs)[0].set_ylabel("Predicted days")
    caption(fig, f"Validation n=353; dotted lines = {TH}-day threshold; protocol {a.protocol}")
    fig.tight_layout(rect=(0, .1, 1, 1)); fig.savefig(ch / "03_pred_vs_actual.png"); plt.close(fig)

    t = pd.DataFrame(seg["duration_band"]).set_index("segment")
    fig, ax = plt.subplots(figsize=(9, 3.8))
    xs = np.arange(len(t))
    for i, m in enumerate(models):
        ax.bar(xs + (i - (len(models) - 1) / 2) * .2, t[f"bias_{m}"], width=.19, color=cols[ORDER.index(m)], label=LABEL[m])
    ax.axhline(0, color=INK2, lw=1)
    ax.set_xticks(xs, [f"{s}\n(n={n})" for s, n in zip(t.index, t["n"])], fontsize=8)
    ax.set(title="Mean bias by actual-duration band (predicted - actual)", ylabel="Days")
    ax.legend(frameon=False, fontsize=8, ncol=4)
    caption(fig, f"Validation n=353; protocol {a.protocol}")
    fig.tight_layout(rect=(0, .1, 1, 1)); fig.savefig(ch / "04_bias_by_band.png"); plt.close(fig)

    for m in ("rf", "xgb"):
        if m not in models:
            continue
        key = "shap_mean_abs_by_feature" if m == "xgb" else "permutation_importance_validation_days"
        s = pd.Series(res["details"][m][key]).sort_values()
        fig, ax = plt.subplots(figsize=(8, 0.4 * len(s) + 1.4))
        ax.barh(s.index, s.values, color=cols[ORDER.index(m)], height=.6)
        ax.set(title=f"{LABEL[m]}: {'mean |SHAP| (ln-days)' if m == 'xgb' else 'permutation importance (MAE increase, days)'}",
               xlabel="mean |SHAP| on ln(days)" if m == "xgb" else "MAE increase when permuted (days)")
        ax.grid(axis="y", visible=False)
        caption(fig, f"Validation n=353; protocol {a.protocol}; explanations of model behaviour, not causal effects")
        fig.tight_layout(rect=(0, .1, 1, 1)); fig.savefig(ch / f"05_importance_{m}.png"); plt.close(fig)

    write_json(out / "advanced_metrics.json", res)
    card = {"protocol": a.protocol, "training_rows": len(tr), "target": "ln(duration_days)", "prediction": "exp(yhat) days",
            "features": {"categorical": CAT, "log1p_numeric": NUM_LOG, "numeric": NUM}, "prereg_sha256": pr["_sha256"],
            "versions": {"sklearn": __import__("sklearn").__version__, "xgboost": __import__("xgboost").__version__, "numpy": np.__version__},
            "models": {m: res["details"][m].get("best_params", {}) for m in models}}
    write_json(md / "MODEL_CARD.json", card)
    exp = ML_ROOT / "experiments"
    with (exp / "runs.jsonl").open("a") as fh:
        for m in models:
            fh.write(json.dumps({"logged_at": dt.datetime.now(dt.timezone.utc).isoformat(), "stage": "2C/2D", "model": "M1_tender_time",
                                 "variant": LABEL[m], "protocol": a.protocol, "run_dir": str(out.relative_to(ML_ROOT.parent)),
                                 "prereg_sha256": pr["_sha256"], "label_source": "real", "training_rows": len(tr),
                                 "params": res["details"][m].get("best_params", {}), "cv_MAE_days": res["details"][m].get("cv_MAE_days"),
                                 "validation_regression": res["regression"][m], "validation_long_detection": res["long_detection"][m]["primary_training_oof_threshold"],
                                 "validation_PR_AUC": res["long_detection"][m]["PR_AUC"], "artifact": str((md / f"{m}_{a.protocol}.joblib").relative_to(ML_ROOT.parent)),
                                 "seed": SEED, "test_scored": False}, default=str) + "\n")
    print(out)
    print(pd.DataFrame(res["regression"]).T[["MAE", "MedAE", "RMSE", "bias_mean_pred_minus_actual", "MAE_package_weighted"]].to_string())
    for m in models:
        ld = res["long_detection"][m]
        print(m, "PR-AUC", ld["PR_AUC"], "primary", ld["primary_training_oof_threshold"], "| fixed>365", ld["secondary_point_prediction_gt_365"], "| top20", ld["capacity_top20pct"])
    for ref in ("b1", "ridge"):
        if f"boot_vs_{ref}_project" in res:
            print("vs", ref, "project", res[f"boot_vs_{ref}_project"]["MAE_reduction_vs_ref_95ci"], "package", res[f"boot_vs_{ref}_package"]["MAE_reduction_vs_ref_95ci"])
    print("det CI project", res["long_detection_boot_project"])


if __name__ == "__main__":
    main()
