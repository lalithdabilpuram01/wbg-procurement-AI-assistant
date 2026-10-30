"""Model 1 final evaluation, part 1: refit B1 / Ridge / Random Forest on as-of 2023-06-30 data and freeze test predictions.

Usage (from procurement-intelligence/):  /opt/anaconda3/bin/python3 ml/final/final_refit.py

Reads only train + validation targets (split.load() strips test targets). Writes ml/final/frozen/:
  models/*.joblib, refit_details.json, test_predictions_frozen.csv (made from test FEATURES only) and
  FROZEN.json with SHA-256 of the config, inputs, models and predictions. Refuses to run if FROZEN.json exists.
"""
from __future__ import annotations

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
from sklearn.linear_model import Ridge
from sklearn.model_selection import GridSearchCV, GroupKFold, cross_val_predict

ROOT = Path(__file__).resolve().parents[1]
for sub in ("stage2c", "stage2b", "stage2a", "phase1"):
    sys.path.insert(0, str(ROOT / sub))
from advanced import LevelMean, choose_threshold  # noqa: E402
from baselines import B1_LEVELS, MedianBaseline  # noqa: E402
from regression import GRIDS, SCORER, make_pipeline, mae_days, prep_features  # noqa: E402
from split import FEATURES, LABELS, TARGET, file_sha, load  # noqa: E402

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "FINAL_EVAL_CONFIG.json"
FROZEN_DIR = HERE / "frozen"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    if (FROZEN_DIR / "FROZEN.json").exists():
        raise SystemExit("Models and test predictions are already frozen; refusing to refit.")
    cfg = json.loads(CONFIG.read_text())
    seed, TH = cfg["random_seed"], cfg["long_duration"]["threshold_days"]
    cut = pd.Timestamp(cfg["refit"]["historical_cutoff"])
    d = load()
    te = d["test_features_only"].reset_index(drop=True)
    assert TARGET not in te.columns and "contract_signing_date" not in te.columns
    ref = pd.concat([d["train"], d["validation"]])
    ref = ref[ref["contract_signing_date"] <= cut].reset_index(drop=True)
    audit = {"eligible_rows": len(ref), "from_train": int((ref["split"] == "train").sum()), "from_validation": int((ref["split"] == "validation").sum()),
             "projects": int(ref["project_id"].nunique()), "packages": int(ref["package_key"].nunique()),
             "max_signing": str(ref["contract_signing_date"].max().date()), "max_first_tender": str(ref["first_tender_date"].max().date()),
             "test_rows": len(te), "contract_overlap": len(set(ref["wb_contract_number"]) & set(te["wb_contract_number"])),
             "package_overlap": len(set(ref["package_key"]) & set(te["package_key"])), "long_positives_in_refit": int((ref[TARGET] > TH).sum())}
    exp = cfg["refit"]
    assert audit["eligible_rows"] == exp["eligible_rows_audited_before_test"] and audit["from_train"] == exp["eligible_breakdown"]["train"]
    assert audit["contract_overlap"] == 0 and audit["package_overlap"] == 0 and audit["test_rows"] == cfg["test_set"]["rows"]

    FROZEN_DIR.mkdir()
    (FROZEN_DIR / "models").mkdir()
    Xr, Xte = prep_features(ref), prep_features(te)
    yr = np.log(ref[TARGET].to_numpy().astype(float))
    yr_long = ref[TARGET].to_numpy() > TH
    groups = ref["project_id"].to_numpy()
    gkf = GroupKFold(n_splits=5)
    preds = pd.DataFrame({"wb_contract_number": te["wb_contract_number"], "project_id": te["project_id"], "package_key": te["package_key"]})
    details: dict = {"refit_audit": audit}

    # B1
    mb = MedianBaseline(B1_LEVELS, 1).fit(ref, ref[TARGET])
    lm = LevelMean(B1_LEVELS).fit(ref, yr_long.astype(float))
    oof_s = np.zeros(len(ref))
    for i, j in gkf.split(ref, yr, groups):
        oof_s[j] = LevelMean(B1_LEVELS).fit(ref.iloc[i], yr_long[i].astype(float)).predict(ref.iloc[j])
    thr = choose_threshold(yr_long, oof_s, 0.30)
    p, lvl = mb.predict(te)
    preds["pred_b1"], preds["score_b1"], preds["level_b1"] = p, lm.predict(te), lvl
    preds["alert_b1"] = preds["score_b1"] >= thr["t"]
    details["b1"] = {"global_median": mb.global_, "alert_threshold": thr}
    joblib.dump({"median": mb, "exceedance": lm}, FROZEN_DIR / "models" / "b1_final.joblib")

    # Ridge and Random Forest
    specs = {
        "ridge": (make_pipeline(Ridge(), False), GRIDS["ridge"]),
        "rf": (make_pipeline(RandomForestRegressor(n_estimators=500, random_state=seed, n_jobs=-1), False),
               {"model__max_depth": [4, 6, 10, None], "model__min_samples_leaf": [5, 10, 20], "model__max_features": [0.3, 0.6, 1.0]}),
    }
    for name, (pipe, grid) in specs.items():
        gs = GridSearchCV(pipe, grid, scoring=SCORER, cv=gkf)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            gs.fit(Xr, yr, groups=groups)
        best = gs.best_estimator_
        oof = cross_val_predict(clone(best), Xr, yr, groups=groups, cv=gkf)
        resid = yr - oof
        sigma = float(np.std(resid, ddof=1))
        q10, q90 = np.quantile(resid, [.1, .9])
        oof_score = 1 - norm.cdf((np.log(TH) - oof) / sigma)
        thr = choose_threshold(yr_long, oof_score, 0.30)
        pl = best.predict(Xte)
        preds[f"pred_{name}"] = np.exp(pl)
        preds[f"score_{name}"] = 1 - norm.cdf((np.log(TH) - pl) / sigma)
        preds[f"alert_{name}"] = preds[f"score_{name}"] >= thr["t"]
        preds[f"lo_{name}"], preds[f"hi_{name}"] = np.exp(pl + q10), np.exp(pl + q90)
        details[name] = {"best_params": {k.replace("model__", ""): (None if v is None else float(v) if isinstance(v, (int, float)) else v)
                                         for k, v in gs.best_params_.items()},
                         "cv_MAE_days": round(-gs.best_score_, 2), "train_MAE_days": round(mae_days(yr, best.predict(Xr)), 2),
                         "oof_MAE_days": round(mae_days(yr, oof), 2), "oof_log_residual_sd": round(sigma, 4),
                         "interval_log_quantiles": [round(float(q10), 4), round(float(q90), 4)], "alert_threshold": thr}
        joblib.dump(best, FROZEN_DIR / "models" / f"{name}_final.joblib")
        print(name, json.dumps(details[name], default=str), flush=True)
    for c in [c for c in preds.columns if c.startswith("alert_")]:
        preds[c] = preds[c].astype(bool)
    preds.to_csv(FROZEN_DIR / "test_predictions_frozen.csv", index=False)
    (FROZEN_DIR / "refit_details.json").write_text(json.dumps(details, indent=1, default=str))
    frozen = {"config_sha256": sha(CONFIG), "labels_sha256": file_sha(LABELS), "features_sha256": file_sha(FEATURES),
              "split_py_sha256": sha(ROOT / "stage2a" / "split.py"), "refit_py_sha256": sha(Path(__file__)),
              "models_sha256": {p.name: sha(p) for p in sorted((FROZEN_DIR / "models").glob("*.joblib"))},
              "test_predictions_sha256": sha(FROZEN_DIR / "test_predictions_frozen.csv"),
              "refit_details_sha256": sha(FROZEN_DIR / "refit_details.json"), "test_targets_read": False,
              "versions": {"sklearn": __import__("sklearn").__version__, "numpy": np.__version__, "pandas": pd.__version__}}
    (FROZEN_DIR / "FROZEN.json").write_text(json.dumps(frozen, indent=1))
    print(json.dumps(audit), "\nFROZEN", json.dumps(frozen, indent=1))


if __name__ == "__main__":
    main()
