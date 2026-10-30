"""Model 1 advanced comparison: consistent validation tables across models and training protocols (no refitting).

Usage:  /opt/anaconda3/bin/python3 ml/stage2c/compare.py <asof_run_dir> <cfw365_run_dir>
Writes ml/eda/stage2c_comparison_<UTC>/: regression_metrics.csv, long_detection.csv, protocol_effects.csv, charts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase1"))
from common import INK2, SERIES, caption, new_run_dir, style_mpl  # noqa: E402

MODELS = ["b1", "ridge", "rf", "xgb"]
LABEL = {"b1": "B1", "ridge": "Ridge", "rf": "Random Forest", "xgb": "XGBoost"}


def main(*runs: str) -> None:
    out = new_run_dir("stage2c_comparison")
    reg, det = [], []
    for run in runs:
        r = json.loads((Path(run) / "advanced_metrics.json").read_text())
        p = r["protocol"]
        for m in MODELS:
            g, d = r["regression"][m], r["details"][m]
            seen = {s["segment"]: s for s in r["segments"]["project_seen_in_training"]}
            band = {s["segment"]: s for s in r["segments"]["duration_band"]}
            rev = {s["segment"]: s for s in r["segments"]["review_type"]}
            reg.append({"protocol": p, "model": LABEL[m], "training_rows": r["training_rows"], "train_MAE": d.get("train_MAE_days"),
                        "cv_MAE": d.get("cv_MAE_days"), "val_MAE": g["MAE"], "val_MAE_ci_project": r["boot_vs_b1_project"]["MAE_95ci"][f"pred_{m}"],
                        "MedAE": g["MedAE"], "RMSE": g["RMSE"], "bias": g["bias_mean_pred_minus_actual"], "MAE_package_weighted": g["MAE_package_weighted"],
                        "reduction_vs_B1_ci_project": r["boot_vs_b1_project"]["MAE_reduction_vs_ref_95ci"].get(f"pred_{m}"),
                        "reduction_vs_B1_ci_package": r["boot_vs_b1_package"]["MAE_reduction_vs_ref_95ci"].get(f"pred_{m}"),
                        "reduction_vs_Ridge_ci_project": r["boot_vs_ridge_project"]["MAE_reduction_vs_ref_95ci"].get(f"pred_{m}"),
                        "reduction_vs_Ridge_ci_package": r["boot_vs_ridge_package"]["MAE_reduction_vs_ref_95ci"].get(f"pred_{m}"),
                        "MAE_unseen_projects": seen.get("False", {}).get(f"MAE_{m}"), "n_unseen": seen.get("False", {}).get("n"),
                        "MAE_seen_projects": seen.get("True", {}).get(f"MAE_{m}"),
                        "bias_long_gt365": band["long (> 365 d)"][f"bias_{m}"], "MAE_prior_review": rev["Prior"][f"MAE_{m}"], "bias_prior_review": rev["Prior"][f"bias_{m}"],
                        "P10_P90_coverage": d.get("P10_P90_coverage_validation"), "params": json.dumps(d.get("best_params", {}), default=str)})
            ld = r["long_detection"][m]
            for rule, key in (("primary (training-OOF threshold)", "primary_training_oof_threshold"), ("fixed: prediction > 365 d", "secondary_point_prediction_gt_365"),
                              ("capacity: top 20% of scores", "capacity_top20pct")):
                x = ld[key]
                row = {"protocol": p, "model": LABEL[m], "rule": rule, "PR_AUC": ld["PR_AUC"], "ROC_AUC": ld["ROC_AUC"], **{k: x[k] for k in ("actual_long", "TP", "FN", "FP", "TN", "recall", "precision", "F1", "alert_rate")}}
                if key == "primary_training_oof_threshold":
                    row["recall_ci_project"] = r["long_detection_boot_project"][f"alert_{m}"]["recall"]
                    row["precision_ci_project"] = r["long_detection_boot_project"][f"alert_{m}"]["precision"]
                    row["F1_ci_project"] = r["long_detection_boot_project"][f"alert_{m}"]["F1"]
                    row["threshold"] = round(d["alert_threshold_from_training_oof"]["t"], 4)
                det.append(row)
    R, D = pd.DataFrame(reg), pd.DataFrame(det)
    R.to_csv(out / "regression_metrics.csv", index=False)
    D.to_csv(out / "long_detection.csv", index=False)
    pe = R.pivot_table(index="model", columns="protocol", values="val_MAE")
    pe["protocol_effect_cfw365_minus_asof"] = pe["cfw365"] - pe["asof"]
    pe.round(2).to_csv(out / "protocol_effects.csv")

    plt = style_mpl()
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.4))
    for i, p in enumerate(["asof", "cfw365"]):
        s = R[R.protocol == p].set_index("model").loc[[LABEL[m] for m in MODELS]]
        x = np.arange(len(s)) + (i - .5) * .3
        axs[0].vlines(x, [c[0] for c in s["val_MAE_ci_project"]], [c[1] for c in s["val_MAE_ci_project"]], color=SERIES[i], alpha=.6)
        axs[0].plot(x, s["val_MAE"], "o", ms=8, color=SERIES[i], label=p)
    axs[0].set_xticks(range(len(MODELS)), [LABEL[m] for m in MODELS])
    axs[0].set(title="Validation MAE (95% project-cluster CI)", ylabel="MAE (days)")
    axs[0].legend(frameon=False, title="training protocol", fontsize=8)
    P = D[D.rule.str.startswith("primary")]
    w = .2
    for i, p in enumerate(["asof", "cfw365"]):
        s = P[P.protocol == p].set_index("model").loc[[LABEL[m] for m in MODELS]]
        x = np.arange(len(s))
        axs[1].bar(x + (i - .5) * 2 * w - w / 2, s["recall"], width=w, color=SERIES[i], alpha=.9, label=f"recall · {p}")
        axs[1].bar(x + (i - .5) * 2 * w + w / 2, s["precision"], width=w, color=SERIES[i], alpha=.45, label=f"precision · {p}")
        for xx, f1 in zip(x + (i - .5) * 2 * w, s["F1"]):
            axs[1].text(xx, 0.97, f"F1 {f1:.2f}", ha="center", fontsize=7, color=INK2, rotation=90, va="top")
    axs[1].set_xticks(range(len(MODELS)), [LABEL[m] for m in MODELS])
    axs[1].set(title="Long-tender (> 365 d) detection at the frozen alert rule", ylim=(0, 1))
    axs[1].legend(frameon=False, fontsize=7, ncol=2, loc="lower right")
    caption(fig, "Validation n=353 contracts (45 long). Thresholds frozen on training out-of-fold scores (max F1, alert rate <= 30%). Not final performance; test unscored")
    fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(out / "model_protocol_comparison.png"); plt.close(fig)
    print(out)
    print(R[["protocol", "model", "train_MAE", "cv_MAE", "val_MAE", "reduction_vs_B1_ci_project", "reduction_vs_Ridge_ci_project", "MAE_unseen_projects", "bias_long_gt365", "P10_P90_coverage"]].to_string())
    print(P[["protocol", "model", "PR_AUC", "TP", "FN", "FP", "recall", "precision", "F1", "alert_rate"]].to_string())
    print(pe.round(1))


if __name__ == "__main__":
    main(*sys.argv[1:])
