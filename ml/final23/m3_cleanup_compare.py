"""Model 3: ONE controlled validation comparison of the Stage 3A feature policy vs the cleaned policy (L2 logistic).

Usage (from procurement-intelligence/):  /opt/anaconda3/bin/python3 ml/final23/m3_cleanup_compare.py

Same splits, labels (sim_v1), C grid, grouped CV and threshold rule as Stage 3A; only the feature policy differs.
Pre-declared acceptance rule (fixed before running): adopt the cleaned policy unless, on primary scenario S2, validation
PR-AUC or F1 falls by more than 0.02 (absolute) WITHOUT a gain in explanation stability (share of the top-10 coefficients
with bootstrap sign agreement >= 0.9) or a reduction in the largest |coefficient| under null S0.
Writes ml/eda/stage3b_m3_cleanup_<UTC>/.
"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd
from sklearn.model_selection import GridSearchCV, GroupKFold, cross_val_predict
from statsmodels.stats.outliers_influence import variance_inflation_factor

from common23 import LABELS, FEATURES, POLICY, min_freq, pipeline
from classify import evaluate, f1_threshold  # Stage 3A helpers (unchanged)
from common import new_run_dir, write_json

SEED = 20261025
C_GRID = np.logspace(-3, 1, 9)


def main() -> None:
    out = new_run_dir("stage3b_m3_cleanup")
    rng = np.random.default_rng(SEED)
    f = pd.read_parquet(FEATURES["m3"])
    f = f[f["split"].isin(["train", "validation"])].reset_index(drop=True)
    lab = pd.read_parquet(LABELS["m3"])
    gkf = GroupKFold(n_splits=5)
    res = {"rule": __doc__.split("Pre-declared acceptance rule (fixed before running): ")[1].split("Writes")[0].strip(), "scenarios": {}}
    for s in ("S0", "S1", "S2"):
        d = f.merge(lab[lab.scenario == s][["unit_id", "sim_label"]], on="unit_id", validate="1:1")
        tr, va = d[d.split == "train"].reset_index(drop=True), d[d.split == "validation"].reset_index(drop=True)
        ytr, g = tr["sim_label"].to_numpy(), tr["project_id"].to_numpy()
        va["y"] = va["sim_label"].to_numpy()
        res["scenarios"][s] = {}
        for pol_name in ("m3_v1_stage3a", "m3_v2_clean"):
            pol = POLICY[pol_name]
            mf = min_freq(pol, ytr)
            cols = pol["cat"] + pol["log"] + pol["num"]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                gs = GridSearchCV(pipeline(pol, mf), {"model__C": C_GRID}, scoring="average_precision", cv=gkf).fit(tr[cols], ytr, groups=g)
                best = gs.best_estimator_
                oof = cross_val_predict(best, tr[cols], ytr, groups=g, cv=gkf, method="predict_proba")[:, 1]
                thr = f1_threshold(ytr.astype(bool), oof)
                va[f"score_{pol_name}"] = best.predict_proba(va[cols])[:, 1]
                va[f"alert_{pol_name}"] = va[f"score_{pol_name}"] >= thr["t"]
                names = best.named_steps["pre"].get_feature_names_out()
                coef = pd.Series(best.named_steps["model"].coef_[0], index=names)
                C = float(gs.best_params_["model__C"])
                boots, projects = [], np.unique(g)
                for _ in range(20):
                    idx = np.concatenate([np.where(g == p)[0] for p in rng.choice(projects, len(projects), replace=True)])
                    mb = pipeline(pol, mf, C).fit(tr[cols].iloc[idx], ytr[idx])
                    boots.append(pd.Series(mb.named_steps["model"].coef_[0], index=mb.named_steps["pre"].get_feature_names_out()).reindex(names))
                B = pd.concat(boots, axis=1)
                agree = np.sign(B).eq(np.sign(coef), axis=0).mean(axis=1)
                top = coef.abs().sort_values(ascending=False).head(10).index
                Zd = pipeline(pol, mf, None, drop_first=True).named_steps["pre"].fit(tr[cols]).transform(tr[cols])
                Zd = np.column_stack([np.ones(len(Zd)), Zd])
                vif = [variance_inflation_factor(Zd, i) for i in range(1, Zd.shape[1])]
            ev = evaluate(va, [pol_name])[pol_name]
            res["scenarios"][s][pol_name] = {
                "min_frequency": mf, "design_columns": int(len(names)), "C": C, "threshold": round(thr["t"], 4),
                "validation": {"PR_AUC": ev["PR_AUC"], "ROC_AUC": ev["ROC_AUC"], "Brier": ev["Brier"], "ECE": ev["ECE"], **{k: ev["at_threshold"][k] for k in ("recall", "precision", "F1", "alert_rate")}},
                "explanation": {"max_abs_coef": round(float(coef.abs().max()), 3), "top10_sign_agreement_ge_0.9_share": round(float((agree[top] >= 0.9).mean()), 2),
                                "columns_sign_agreement_lt_0.8": int((agree < 0.8).sum()), "max_vif_drop_first_design": round(float(np.max(vif)), 2),
                                "top5": {k: {"coef": round(float(coef[k]), 3), "sign_agreement": round(float(agree[k]), 2)} for k in top[:5]}},
            }
        a, b = res["scenarios"][s]["m3_v1_stage3a"], res["scenarios"][s]["m3_v2_clean"]
        res["scenarios"][s]["delta_clean_minus_original"] = {k: round(b["validation"][k] - a["validation"][k], 4) for k in ("PR_AUC", "ROC_AUC", "F1", "recall", "precision", "ECE")}
    s2, s0 = res["scenarios"]["S2"], res["scenarios"]["S0"]
    big_drop = s2["delta_clean_minus_original"]["PR_AUC"] < -0.02 or s2["delta_clean_minus_original"]["F1"] < -0.02
    expl_gain = (s2["m3_v2_clean"]["explanation"]["top10_sign_agreement_ge_0.9_share"] > s2["m3_v1_stage3a"]["explanation"]["top10_sign_agreement_ge_0.9_share"]
                 or s0["m3_v2_clean"]["explanation"]["max_abs_coef"] < s0["m3_v1_stage3a"]["explanation"]["max_abs_coef"])
    res["decision"] = {"S2_large_drop": bool(big_drop), "explanation_improved": bool(expl_gain),
                       "adopt_cleaned_policy": bool((not big_drop) or expl_gain) and not (big_drop and not expl_gain)}
    write_json(out / "cleanup_comparison.json", res)
    print(out)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
