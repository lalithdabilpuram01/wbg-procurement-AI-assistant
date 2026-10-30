"""Stage 2B: cross-protocol comparison of the final runs (reads regression_metrics.json; no refitting).

Usage:  /opt/anaconda3/bin/python3 ml/stage2b/summarize.py <asof_run> <asof_fu365_run> <retro_run>
Writes ml/eda/stage2b_summary_<UTC>/ (comparison.csv, comparison.json, chart). Validation only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase1"))
from common import INK2, SERIES, caption, new_run_dir, style_mpl, write_json  # noqa: E402

LABELS = {"asof": "Primary: strictly as-of (462 rows)", "asof_fu365": "Sensitivity: as-of, >=365 d follow-up (372 rows; added post hoc)",
          "retro": "Secondary: retrospective (646 rows)"}
MODELS = ["B0", "B1", "B1m", "ols", "ridge", "lasso", "enet"]


def main(*runs: str) -> None:
    out = new_run_dir("stage2b_summary")
    rows = []
    for run in runs:
        r = json.loads((Path(run) / "regression_metrics.json").read_text())
        pc, pk = r["bootstrap_vs_B1_project_clusters"], r["bootstrap_vs_B1_package_clusters"]
        for m in MODELS:
            vm = r["validation_metrics"][m]
            det = r["model_details"].get(m, {})
            rows.append({"protocol": r["protocol"], "training_rows": r["training_rows"], "model": m, "MAE": vm["MAE"], "MedAE": vm["MedAE"],
                         "RMSE": vm["RMSE"], "bias": vm["bias_mean_pred_minus_actual"], "MAE_package_weighted": vm["MAE_package_weighted"],
                         "MAE_ci_project": pc["MAE_95ci"][f"pred_{m}"], "reduction_vs_B1_ci_project": pc["MAE_reduction_vs_ref_95ci"].get(f"pred_{m}"),
                         "reduction_vs_B1_ci_package": pk["MAE_reduction_vs_ref_95ci"].get(f"pred_{m}"),
                         "train_MAE": det.get("train_MAE_days"), "cv_MAE": det.get("cv_MAE_days"), "params": det.get("best_params"),
                         "nonzero_coef": det.get("nonzero_coefficients"), "P10_P90_coverage": det.get("P10_P90_interval_coverage_validation"),
                         "run": Path(run).name})
    t = pd.DataFrame(rows)
    t.to_csv(out / "comparison.csv", index=False)
    write_json(out / "comparison.json", rows)

    plt = style_mpl()
    fig, ax = plt.subplots(figsize=(10, 4.6))
    x = np.arange(len(MODELS))
    for i, p in enumerate(["asof", "asof_fu365", "retro"]):
        s = t[t.protocol == p].set_index("model").loc[MODELS]
        lo = [c[0] for c in s["MAE_ci_project"]]
        hi = [c[1] for c in s["MAE_ci_project"]]
        xs = x + (i - 1) * 0.25
        ax.vlines(xs, lo, hi, color=SERIES[i], lw=1.2, alpha=.6)
        ax.plot(xs, s["MAE"], "o", color=SERIES[i], ms=7, label=LABELS[p])
    ax.set_xticks(x, ["B0", "B1", "B1m", "OLS", "Ridge", "Lasso", "Elastic Net"])
    ax.set(title="Validation MAE by training protocol (95% project-cluster bootstrap CI)", ylabel="MAE (days)")
    ax.legend(frameon=False, fontsize=8)
    caption(fig, "Validation n=353 (tenders 2022-07-01 to 2023-06-30), 2,000-project cohort. Baselines are refitted per protocol. Validation only; test not scored")
    fig.tight_layout(rect=(0, .1, 1, 1)); fig.savefig(out / "mae_by_protocol.png"); plt.close(fig)
    print(out)
    print(t[["protocol", "model", "training_rows", "MAE", "MedAE", "RMSE", "bias", "reduction_vs_B1_ci_project", "reduction_vs_B1_ci_package", "train_MAE", "cv_MAE", "params"]].to_string())


if __name__ == "__main__":
    main(*sys.argv[1:])
