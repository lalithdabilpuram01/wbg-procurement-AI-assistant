"""Stage 3A: class-prior, rule and Logistic Regression baselines for Models 2 and 3 on SYNTHETIC outcomes.

Usage (from procurement-intelligence/):  /opt/anaconda3/bin/python3 ml/stage3a/classify.py --model m2|m3

Reads ONLY ml/features/<model>_*_features_v1.parquet and ml/synthetic/<model>_sim_v1/labels/labels.parquet.
Test rows are dropped on load (the test split stays sealed). Pre-declared rules:
- preprocessing fitted on training folds only (one-hot min_frequency 50; median imputation; standardisation);
- L2 strength C from logspace(-3, 1, 9) by 5-fold GroupKFold on project, scoring PR-AUC;
- operational threshold = max F1 on training out-of-fold probabilities subject to alert rate <= 30%;
- Model 3 risk bands from training out-of-fold probabilities: High = top 10% (>= q90), Medium = next 20% (q70..q90), Low = rest
  (v2; v1 left Medium empty when the F1 threshold exceeded q90).
Writes ml/eda/stage3a_<model>_<UTC>/ and appends to ml/experiments/runs.jsonl.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, precision_recall_curve, roc_auc_score
from sklearn.model_selection import GridSearchCV, GroupKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

ML = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML / "phase1"))
from common import INK2, SERIES, caption, new_run_dir, style_mpl, write_json  # noqa: E402

SEED = 20261025
N_BOOT = 500
MAX_ALERT = 0.30
DISCLAIMER = "SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY"
SPEC = {
    "m2": {"features": ML / "features" / "m2_contract_features_v1.parquet", "labels": ML / "synthetic" / "m2_sim_v1" / "labels" / "labels.parquet",
           "cat": ["procurement_method", "procurement_category", "review_type", "region_name", "country_grp", "sector"],
           "log": [], "num": ["days_from_approval", "signed_before_approval", "approval_fiscal_year", "income_ordinal"],
           "rule": "Works category OR prior review", "title": "Model 2 · simulated contract amendment within 24 months"},
    "m3": {"features": ML / "features" / "m3_tender_features_v1.parquet", "labels": ML / "synthetic" / "m3_sim_v1" / "labels" / "labels.parquet",
           "cat": ["notice_type", "procurement_method", "procurement_category", "region_name", "country_grp", "sector", "deadline_status"],
           "log": ["window_days"], "num": ["has_positive_window", "project_age_days", "income_ordinal"],
           "rule": "Works category OR (window < 14 days or no usable window)", "title": "Model 3 · simulated re-bid within 12 months"},
}


def rule_score(m: str, f: pd.DataFrame) -> np.ndarray:
    if m == "m2":
        return ((f["procurement_category"] == "Works") | (f["review_type"] == "Prior")).astype(float).to_numpy()
    return ((f["procurement_category"] == "Works") | (f["window_days"] < 14) | (f["has_positive_window"] == 0)).astype(float).to_numpy()


def pipeline(sp: dict, clf, drop_first: bool) -> Pipeline:
    pre = ColumnTransformer([
        ("cat", OneHotEncoder(min_frequency=50, handle_unknown="infrequent_if_exist", drop="first" if drop_first else None, sparse_output=False), sp["cat"]),
        ("log", Pipeline([("imp", SimpleImputer(strategy="median")), ("l", FunctionTransformer(np.log1p, feature_names_out="one-to-one")), ("s", StandardScaler())]), sp["log"]),
        ("num", Pipeline([("imp", SimpleImputer(strategy="median")), ("s", StandardScaler())]), sp["num"]),
    ], verbose_feature_names_out=False)
    return Pipeline([("pre", pre), ("model", clf)])


def det(y: np.ndarray, alert: np.ndarray) -> dict:
    tp, fp = int((alert & y).sum()), int((alert & ~y).sum())
    fn, tn = int((~alert & y).sum()), int((~alert & ~y).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"TP": tp, "FP": fp, "FN": fn, "TN": tn, "precision": round(p, 4), "recall": round(r, 4),
            "F1": round(2 * p * r / (p + r), 4) if p + r else 0.0, "alert_rate": round(float(alert.mean()), 4)}


def f1_threshold(y: np.ndarray, s: np.ndarray) -> dict:
    best = {"t": float("inf"), "F1": 0.0}
    qs = np.unique(np.quantile(s, np.linspace(0.5, 0.999, 300)))
    for t in qs[::-1]:
        a = s >= t
        if a.mean() > MAX_ALERT:
            continue
        d = det(y, a)
        if d["F1"] > best["F1"] + 1e-12:
            best = {"t": float(t), **d}
    return best


def capacity(y: np.ndarray, s: np.ndarray) -> dict:
    out = {}
    order = np.argsort(-s, kind="stable")
    for r in (0.05, 0.10, 0.20, 0.30):
        a = np.zeros(len(s), dtype=bool)
        a[order[: int(np.ceil(r * len(s)))]] = True
        out[f"{int(r * 100)}%"] = det(y, a)
    return out


def boot(df: pd.DataFrame, cols: list[str], rng) -> dict:
    g = df.groupby("project_id").indices
    keys = list(g)
    y = df["y"].to_numpy().astype(bool)
    acc = {c: {"PR_AUC": [], "ROC_AUC": [], "F1": [], "recall": [], "precision": []} for c in cols}
    for _ in range(N_BOOT):
        idx = np.concatenate([g[k] for k in rng.choice(keys, size=len(keys), replace=True)])
        yy = y[idx]
        if yy.all() or not yy.any():
            continue
        for c in cols:
            s = df[f"score_{c}"].to_numpy()[idx]
            acc[c]["PR_AUC"].append(average_precision_score(yy, s))
            acc[c]["ROC_AUC"].append(roc_auc_score(yy, s) if len(np.unique(s)) > 1 else 0.5)
            d = det(yy, df[f"alert_{c}"].to_numpy()[idx])
            for k in ("F1", "recall", "precision"):
                acc[c][k].append(d[k])
    return {c: {k: [round(float(np.quantile(v, .025)), 4), round(float(np.quantile(v, .975)), 4)] for k, v in m.items()} for c, m in acc.items()}


def evaluate(df: pd.DataFrame, cols: list[str]) -> dict:
    y = df["y"].to_numpy().astype(bool)
    out = {}
    for c in cols:
        s = df[f"score_{c}"].to_numpy()
        frac, mean_pred = calibration_curve(y, np.clip(s, 0, 1), n_bins=10, strategy="quantile") if len(np.unique(s)) > 2 else ([], [])
        out[c] = {"prevalence": round(float(y.mean()), 4), "PR_AUC": round(float(average_precision_score(y, s)), 4),
                  "ROC_AUC": round(float(roc_auc_score(y, s)), 4) if len(np.unique(s)) > 1 else 0.5,
                  "Brier": round(float(brier_score_loss(y, np.clip(s, 0, 1))), 4), "at_threshold": det(y, df[f"alert_{c}"].to_numpy()),
                  "capacity": capacity(y, s), "calibration_bins": {"mean_predicted": [round(float(v), 4) for v in mean_pred], "observed": [round(float(v), 4) for v in frac]},
                  "ECE": round(float(np.mean(np.abs(np.array(frac) - np.array(mean_pred)))), 4) if len(frac) else None}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["m2", "m3"], required=True)
    m = ap.parse_args().model
    sp = SPEC[m]
    out = new_run_dir(f"stage3a_{m}")
    ch, md = out / "charts", out / "models"
    ch.mkdir()
    md.mkdir()
    rng = np.random.default_rng(SEED)
    f = pd.read_parquet(sp["features"])
    f = f[f["split"].isin(["train", "validation"])].reset_index(drop=True)  # test sealed: never loaded into the modelling frame
    lab = pd.read_parquet(sp["labels"])
    X_cols = sp["cat"] + sp["log"] + sp["num"]
    gkf = GroupKFold(n_splits=5)
    res: dict = {"model": m, "disclaimer": DISCLAIMER, "test_scored": False, "rows": f["split"].value_counts().to_dict(), "scenarios": {},
                 "pre_declared": {"max_alert_rate": MAX_ALERT, "C_grid": list(np.logspace(-3, 1, 9)), "cv": "GroupKFold(5) by project", "rule": sp["rule"]}}
    plt = style_mpl()
    for s in ("S0", "S1", "S2"):
        d = f.merge(lab[lab.scenario == s][["unit_id", "sim_label"]], on="unit_id", how="left", validate="1:1")
        assert d["sim_label"].notna().all()
        tr, va = d[d.split == "train"].reset_index(drop=True), d[d.split == "validation"].reset_index(drop=True)
        ytr, yva = tr["sim_label"].to_numpy().astype(int), va["sim_label"].to_numpy().astype(int)
        g = tr["project_id"].to_numpy()
        va["y"] = yva
        r: dict = {"prevalence_train": round(float(ytr.mean()), 4), "prevalence_validation": round(float(yva.mean()), 4), "details": {}}
        # baselines
        prior = float(ytr.mean())
        va["score_prior"] = prior
        va["alert_prior"] = False
        r["details"]["prior"] = {"constant_probability": round(prior, 4), "note": "no alerts; PR-AUC equals prevalence by construction"}
        va["score_rule"] = rule_score(m, va)
        va["alert_rule"] = va["score_rule"] >= 1
        r["details"]["rule"] = {"definition": sp["rule"], "training_alert_rate": round(float(rule_score(m, tr).mean()), 4),
                                "training": det(ytr.astype(bool), rule_score(m, tr) >= 1)}
        # logistic regression (unpenalised) and L2-tuned
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            lr = pipeline(sp, LogisticRegression(penalty=None, max_iter=5000), True).fit(tr[X_cols], ytr)
            gs = GridSearchCV(pipeline(sp, LogisticRegression(max_iter=5000), False), {"model__C": np.logspace(-3, 1, 9)},
                              scoring="average_precision", cv=gkf).fit(tr[X_cols], ytr, groups=g)
            l2 = gs.best_estimator_
            for name, est in (("logreg", lr), ("logreg_l2", l2)):
                oof = cross_val_predict(est, tr[X_cols], ytr, groups=g, cv=gkf, method="predict_proba")[:, 1]
                thr = f1_threshold(ytr.astype(bool), oof)
                pv = est.predict_proba(va[X_cols])[:, 1]
                va[f"score_{name}"], va[f"alert_{name}"] = pv, pv >= thr["t"]
                ptr = est.predict_proba(tr[X_cols])[:, 1]
                r["details"][name] = {"threshold_from_training_oof": thr,
                                      "PR_AUC_train_insample": round(float(average_precision_score(ytr, ptr)), 4),
                                      "PR_AUC_train_oof": round(float(average_precision_score(ytr, oof)), 4),
                                      "ROC_AUC_train_oof": round(float(roc_auc_score(ytr, oof)), 4), "n_design_columns": int(len(est.named_steps["pre"].get_feature_names_out()))}
                if name == "logreg_l2":
                    r["details"][name]["C"] = float(gs.best_params_["model__C"])
                    r["details"][name]["cv_PR_AUC_by_C"] = {f"{c:.4g}": round(float(v), 4) for c, v in zip(gs.cv_results_["param_model__C"], gs.cv_results_["mean_test_score"])}
                    l2_oof = oof
                joblib.dump(est, md / f"{name}_{s}.joblib")
            # label-shuffle check: 20 independent shuffles of the training labels (clustered labels widen the null spread)
            aucs = []
            for _ in range(20):
                shm = pipeline(sp, LogisticRegression(C=float(gs.best_params_["model__C"]), max_iter=5000), False).fit(tr[X_cols], rng.permutation(ytr))
                aucs.append(float(roc_auc_score(yva, shm.predict_proba(va[X_cols])[:, 1])))
            r["label_shuffle_validation_ROC_AUC"] = {"n": 20, "mean": round(float(np.mean(aucs)), 4), "sd": round(float(np.std(aucs)), 4),
                                                     "min": round(float(np.min(aucs)), 4), "max": round(float(np.max(aucs)), 4)}
        cols = ["prior", "rule", "logreg", "logreg_l2"]
        r["validation"] = evaluate(va, cols)
        r["validation_ci_project"] = boot(va, cols, rng)
        trp = set(tr["project_id"])
        unseen = va[~va["project_id"].isin(trp)]
        r["project_held_out_validation"] = {"rows": len(unseen), "projects": int(unseen["project_id"].nunique()), **evaluate(unseen, ["rule", "logreg_l2"])}
        r["project_held_out_ci"] = boot(unseen, ["logreg_l2"], rng)
        seg = {}
        for col in ("procurement_method", "procurement_category", "region_name"):
            rows = []
            for k, gg in va.groupby(col):
                yy = gg["y"].to_numpy().astype(bool)
                if yy.sum() < 10 or (~yy).sum() < 10:
                    continue
                dd = det(yy, gg["alert_logreg_l2"].to_numpy())
                rows.append({"segment": k, "n": len(gg), "positives": int(yy.sum()), "PR_AUC": round(float(average_precision_score(yy, gg["score_logreg_l2"])), 4), **dd})
            seg[col] = rows
            pd.DataFrame(rows).to_csv(out / f"segments_{s}_{col}.csv", index=False)
        r["segments_logreg_l2"] = seg
        # coefficient stability: 20 project-bootstrap refits at the chosen C
        names = l2.named_steps["pre"].get_feature_names_out()
        coefs = []
        projects = np.unique(g)
        for b in range(20):
            pick = rng.choice(projects, size=len(projects), replace=True)
            idx = np.concatenate([np.where(g == p)[0] for p in pick])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                mb = pipeline(sp, LogisticRegression(C=float(gs.best_params_["model__C"]), max_iter=5000), False).fit(tr[X_cols].iloc[idx], ytr[idx])
            cb = pd.Series(mb.named_steps["model"].coef_[0], index=mb.named_steps["pre"].get_feature_names_out())
            coefs.append(cb.reindex(names))
        C = pd.concat(coefs, axis=1)
        stab = pd.DataFrame({"coef": l2.named_steps["model"].coef_[0], "boot_mean": C.mean(axis=1).to_numpy(), "boot_sd": C.std(axis=1).to_numpy(),
                             "sign_agreement": (np.sign(C).eq(np.sign(l2.named_steps["model"].coef_[0]), axis=0)).mean(axis=1).to_numpy()}, index=names)
        stab = stab.sort_values("coef", key=np.abs, ascending=False)
        stab.round(4).to_csv(out / f"coefficients_l2_{s}.csv")
        r["coefficients_top"] = {k: {"coef": round(float(v.coef), 3), "boot_sd": round(float(v.boot_sd), 3), "sign_agreement": round(float(v.sign_agreement), 2)}
                                 for k, v in stab.head(10).iterrows()}
        # Model 3 risk bands from training OOF probabilities
        if m == "m3":
            # v2 rule (v1 "Medium >= F1 threshold, High >= max(F1 threshold, q90)" left Medium empty whenever the F1
            # threshold exceeded the training q90, visible in training OOF alone): fixed training-OOF quantiles
            t_med, t_high = float(np.quantile(l2_oof, 0.70)), float(np.quantile(l2_oof, 0.90))
            band = np.where(va["score_logreg_l2"] >= t_high, "High", np.where(va["score_logreg_l2"] >= t_med, "Medium", "Low"))
            va["risk_band"] = band
            r["risk_bands"] = {"rule": "v2: High = top 10% of training out-of-fold probabilities (>= q90), Medium = next 20% (q70..q90), Low = rest; "
                                       "the alert threshold (max F1, alert rate <= 30%) is reported separately",
                               "cutoffs": {"medium": round(t_med, 4), "high": round(t_high, 4)},
                               "training_oof_share": {"High": round(float((l2_oof >= t_high).mean()), 4), "Medium": round(float(((l2_oof >= t_med) & (l2_oof < t_high)).mean()), 4)},
                               "validation": {b: {"n": int((band == b).sum()), "share": round(float((band == b).mean()), 4),
                                                  "simulated_event_rate": round(float(yva[band == b].mean()), 4) if (band == b).any() else None} for b in ("Low", "Medium", "High")}}
            pre, coef = l2.named_steps["pre"], l2.named_steps["model"].coef_[0]
            Z = pre.transform(va[X_cols])
            contrib = Z * coef
            nm = pre.get_feature_names_out()
            top = [", ".join(nm[j] for j in np.argsort(-row)[:3] if row[j] > 0) for row in contrib]
            demo = va[["unit_id", "project_id", "notice_type", "procurement_method", "procurement_category"]].assign(
                simulated_probability=va["score_logreg_l2"].round(4), risk_band=band, main_simulated_factors=top,
                disclaimer="Simulated — Demonstration Only")
            demo.to_csv(out / f"risk_band_examples_{s}.csv", index=False)
        res["scenarios"][s] = r
        # charts
        fig, axs = plt.subplots(1, 3, figsize=(15, 4.6))
        y = va["y"].to_numpy()
        for i, c in enumerate(cols):
            if c == "prior":
                axs[0].axhline(y.mean(), color=INK2, ls="--", lw=1, label=f"prior / prevalence {y.mean():.3f}")
                continue
            pr, rc, _ = precision_recall_curve(y, va[f"score_{c}"])
            axs[0].plot(rc, pr, color=SERIES[i], lw=2, label=f"{c} (PR-AUC {r['validation'][c]['PR_AUC']:.3f})")
            dd = r["validation"][c]["at_threshold"]
            axs[0].plot(dd["recall"], dd["precision"], "o", color=SERIES[i], ms=8, markeredgecolor="white")
        axs[0].set(xlim=(0, 1), ylim=(0, 1), xlabel="Recall", ylabel="Precision", title=f"{s}: precision-recall (validation)")
        axs[0].legend(frameon=False, fontsize=7.5)
        for i, c in enumerate(["logreg", "logreg_l2"]):
            cb = r["validation"][c]["calibration_bins"]
            axs[1].plot(cb["mean_predicted"], cb["observed"], "o-", color=SERIES[i + 2], label=f"{c} (Brier {r['validation'][c]['Brier']:.4f})")
        lim = max(0.5, max(r["validation"]["logreg_l2"]["calibration_bins"]["observed"] + [0.1]))
        axs[1].plot([0, lim], [0, lim], ls="--", color=INK2, lw=1)
        axs[1].set(xlim=(0, lim), ylim=(0, lim), xlabel="Mean predicted probability", ylabel="Observed simulated rate", title="Calibration (quantile bins)")
        axs[1].legend(frameon=False, fontsize=7.5)
        for i, c in enumerate(["rule", "logreg", "logreg_l2"]):
            cap = r["validation"][c]["capacity"]
            axs[2].plot([5, 10, 20, 30], [cap[k]["recall"] for k in ("5%", "10%", "20%", "30%")], "o-", color=SERIES[i + 1], label=c)
        axs[2].plot([5, 10, 20, 30], [.05, .10, .20, .30], ls="--", color=INK2, lw=1, label="random")
        axs[2].set(xlabel="Review capacity: % of units flagged", ylabel="Recall (share of simulated events caught)", title="Recall vs review capacity", ylim=(0, 1))
        axs[2].legend(frameon=False, fontsize=7.5)
        fig.suptitle(f"{sp['title']} — scenario {s}", fontweight="bold")
        caption(fig, f"{DISCLAIMER} · validation n={len(va):,} · dots = training-OOF F1 threshold (alert rate <= 30%)", kind="SIMULATED")
        fig.tight_layout(rect=(0, .06, 1, .95)); fig.savefig(ch / f"{s}_pr_calibration_capacity.png"); plt.close(fig)
        print(m, s, "prev", r["prevalence_validation"], {c: (r["validation"][c]["PR_AUC"], r["validation"][c]["ROC_AUC"], r["validation"][c]["at_threshold"]["F1"]) for c in cols}, flush=True)

    write_json(out / "stage3a_metrics.json", res)
    with (ML / "experiments" / "runs.jsonl").open("a") as fh:
        for s, r in res["scenarios"].items():
            for c in ("prior", "rule", "logreg", "logreg_l2"):
                fh.write(json.dumps({"logged_at": dt.datetime.now(dt.timezone.utc).isoformat(), "stage": "3A", "model": {"m2": "M2_amendment", "m3": "M3_rebid"}[m],
                                     "variant": c, "scenario": s, "label_source": "synthetic", "sim_version": "sim_v1", "run_dir": str(out.relative_to(ML.parent)),
                                     "params": {"C": r["details"].get(c, {}).get("C")} if c == "logreg_l2" else {}, "seed": SEED,
                                     "validation": {k: r["validation"][c][k] for k in ("prevalence", "PR_AUC", "ROC_AUC", "Brier", "at_threshold")},
                                     "disclaimer": DISCLAIMER, "test_scored": False}, default=str) + "\n")
    print(out)


if __name__ == "__main__":
    main()
