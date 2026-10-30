"""Models 2 / 3 final evaluation, part 1: refit L2 logistic regression on train + validation and freeze test predictions.

Usage (from procurement-intelligence/):  /opt/anaconda3/bin/python3 ml/final23/final_refit23.py

Never reads test labels: features are filtered to the refit splits before labels are joined; test predictions are made
from test FEATURES only. Writes ml/final23/frozen/ (models, thresholds and bands, explanation policy, test predictions,
input schemas, example API outputs, FROZEN.json with SHA-256 of everything). Refuses to run if FROZEN.json exists.
"""
from __future__ import annotations

import hashlib
import json
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import GridSearchCV, GroupKFold, cross_val_predict

from common23 import DISCLAIMER, FEATURES, LABELS, POLICY, READABLE, contributions, min_freq, pipeline
from classify import f1_threshold

HERE = Path(__file__).resolve().parent
CFG = HERE / "FINAL_EVAL_CONFIG_M23.json"
FZ = HERE / "frozen"
SEED = 20261025
C_GRID = np.logspace(-3, 1, 9)
POL = {"m2": "m2_v1", "m3": "m3_v2_clean"}
BIDWIN = {"deadline_status", "has_positive_window", "window_days"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def display_factors(contrib: pd.DataFrame, k: int = 3) -> list[list[dict]]:
    c = contrib.copy()
    bw = [x for x in c.columns if x in BIDWIN]
    if bw:
        c["bid_window"] = c[bw].sum(axis=1)
        c = c.drop(columns=bw)
    hidden = [x for x in c.columns if x == "country_grp" or x.startswith("other")]
    c = c.drop(columns=hidden)
    out = []
    for _, row in c.iterrows():
        pos = row[row > 0].sort_values(ascending=False).head(k)
        out.append([{"feature": f, "label": "Bid window" if f == "bid_window" else READABLE.get(f, f), "log_odds_contribution": round(float(v), 3)} for f, v in pos.items()])
    return out


def main() -> None:
    if (FZ / "FROZEN.json").exists():
        raise SystemExit("Final Model 2/3 artefacts are already frozen; refusing to refit.")
    cfg = json.loads(CFG.read_text())
    FZ.mkdir()
    (FZ / "models").mkdir()
    rng = np.random.default_rng(SEED)
    gkf = GroupKFold(n_splits=5)
    details, frozen_files = {}, {}
    for m in ("m2", "m3"):
        pol = POLICY[POL[m]]
        cols = pol["cat"] + pol["log"] + pol["num"]
        f = pd.read_parquet(FEATURES[m])
        ref = f[f["split"].isin(["train", "validation"])].reset_index(drop=True)
        te = f[f["split"] == "test"].reset_index(drop=True)
        assert len(ref) == cfg["models"][m]["refit_rows"] and len(te) == cfg["models"][m]["test_rows"]
        assert not set(ref["unit_id"]) & set(te["unit_id"])
        lab = pd.read_parquet(LABELS[m])
        preds = te[["unit_id", "project_id"]].copy()
        details[m] = {"policy": POL[m], "refit_rows": len(ref), "refit_projects": int(ref["project_id"].nunique()),
                      "test_rows": len(te), "test_projects": int(te["project_id"].nunique()),
                      "test_projects_unseen_in_refit": int(len(set(te["project_id"]) - set(ref["project_id"]))), "scenarios": {}}
        for s in ("S2", "S0"):
            d = ref.merge(lab[lab.scenario == s][["unit_id", "sim_label"]], on="unit_id", how="left", validate="1:1")
            assert d["sim_label"].notna().all() and len(d) == len(ref)
            y, g = d["sim_label"].to_numpy(), d["project_id"].to_numpy()
            mf = min_freq(pol, y)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                gs = GridSearchCV(pipeline(pol, mf), {"model__C": C_GRID}, scoring="average_precision", cv=gkf).fit(d[cols], y, groups=g)
                best = gs.best_estimator_
                oof = cross_val_predict(best, d[cols], y, groups=g, cv=gkf, method="predict_proba")[:, 1]
                C = float(gs.best_params_["model__C"])
                names = best.named_steps["pre"].get_feature_names_out()
                coef = pd.Series(best.named_steps["model"].coef_[0], index=names)
                boots, projects = [], np.unique(g)
                for _ in range(20):
                    idx = np.concatenate([np.where(g == p)[0] for p in rng.choice(projects, len(projects), replace=True)])
                    mb = pipeline(pol, mf, C).fit(d[cols].iloc[idx], y[idx])
                    boots.append(pd.Series(mb.named_steps["model"].coef_[0], index=mb.named_steps["pre"].get_feature_names_out()).reindex(names))
            agree = np.sign(pd.concat(boots, axis=1)).eq(np.sign(coef), axis=0).mean(axis=1)
            unstable = set(agree[agree < 0.8].index)
            thr = f1_threshold(y.astype(bool), oof)
            sd = {"C": C, "min_frequency": mf, "refit_prevalence": round(float(y.mean()), 4), "threshold": thr,
                  "oof_PR_AUC_note": "refit out-of-fold only; not a test metric", "unstable_design_columns": sorted(unstable),
                  "coefficients": {k: {"coef": round(float(v), 4), "sign_agreement": round(float(agree[k]), 2)} for k, v in coef.sort_values(key=np.abs, ascending=False).items()}}
            p = best.predict_proba(te[cols])[:, 1]
            preds[f"prob_{s}"], preds[f"alert_{s}"] = p, p >= thr["t"]
            if m == "m3":
                q70, q90 = float(np.quantile(oof, 0.70)), float(np.quantile(oof, 0.90))
                sd["risk_bands"] = {"medium_cutoff_q70": round(q70, 5), "high_cutoff_q90": round(q90, 5), "rule": cfg["models"]["m3"]["risk_bands"]}
                preds[f"band_{s}"] = np.where(p >= q90, "High", np.where(p >= q70, "Medium", "Low"))
            fac = display_factors(contributions(best, te[cols], pol, unstable))
            preds[f"factors_{s}"] = [json.dumps(x) for x in fac]
            joblib.dump({"pipeline": best, "policy": pol, "min_frequency": mf, "unstable_design_columns": sorted(unstable)}, FZ / "models" / f"{m}_{s}_l2_final.joblib")
            details[m]["scenarios"][s] = sd
        preds.to_csv(FZ / f"{m}_test_predictions_frozen.csv", index=False)
        # input schema for future FastAPI integration (from refit data; categories outside these map to 'infrequent')
        schema = {"model": m, "title": cfg["models"][m]["unit"], "disclaimer": DISCLAIMER, "required": cols, "properties": {}}
        for c in pol["cat"]:
            schema["properties"][c] = {"type": "string", "description": READABLE.get(c, c), "known_values": sorted(map(str, ref[c].dropna().unique()))[:400],
                                       "unknown_or_rare_values": "accepted; grouped as 'infrequent'"}
        for c in pol["log"] + pol["num"]:
            v = ref[c]
            schema["properties"][c] = {"type": ["number", "null"], "description": READABLE.get(c, c), "refit_min": float(v.min()), "refit_max": float(v.max()),
                                       "missing_allowed": True, "imputation": "refit median"}
        (FZ / f"{m}_input_schema.json").write_text(json.dumps(schema, indent=1))
        # example API-ready outputs (test FEATURES only; no outcomes)
        ex = []
        for _, r in preds.head(5).iterrows():
            row = te.loc[te["unit_id"] == r["unit_id"], cols].iloc[0]
            item = {"unit_id": r["unit_id"], "inputs": {k: (None if pd.isna(v) else (v.item() if hasattr(v, "item") else v)) for k, v in row.items()},
                    "model": f"{m}_l2_logistic_sim_v1_S2", "scenario": "S2 (primary simulation)",
                    "simulated_probability_pct": round(100 * float(r["prob_S2"]), 1), "main_simulated_factors": json.loads(r["factors_S2"]),
                    "disclaimer": DISCLAIMER, "note": "Simulated drivers from an assumed data-generating process; not World Bank risk factors."}
            if m == "m3":
                item["risk_band"] = r["band_S2"]
                item["risk_band_note"] = "Relative rank within the simulated model population (High = top 10% of refit scores); not a validated absolute risk. Simulated — Demonstration Only."
            else:
                item["review_list_flag"] = bool(r["alert_S2"])
                item["review_list_note"] = "Demonstration review list: threshold set for <= 30% of refit contracts; not validated on real amendments."
            ex.append(item)
        (FZ / f"{m}_example_api_outputs.json").write_text(json.dumps(ex, indent=1, default=str))
    (FZ / "refit_details.json").write_text(json.dumps(details, indent=1, default=str))
    files = sorted(p for p in FZ.rglob("*") if p.is_file())
    frozen = {"config_sha256": sha(CFG), "common23_sha256": sha(HERE / "common23.py"), "refit_script_sha256": sha(Path(__file__)),
              "features_sha256": {m: sha(FEATURES[m]) for m in FEATURES}, "labels_sha256": {m: sha(LABELS[m]) for m in LABELS},
              "generator_config_sha256": sha(HERE.parent / "synthetic" / "configs" / "sim_v1.json"),
              "artefacts_sha256": {str(p.relative_to(FZ)): sha(p) for p in files}, "test_labels_read": False}
    (FZ / "FROZEN.json").write_text(json.dumps(frozen, indent=1))
    print(json.dumps({m: {k: v for k, v in details[m].items() if k != "scenarios"} | {s: {k: x[k] for k in ("C", "min_frequency", "refit_prevalence")}
                                                                                       | {"threshold": {k: x["threshold"][k] for k in ("t", "F1", "alert_rate")}}
                                                                                       | ({"bands": x["risk_bands"]} if "risk_bands" in x else {})
                                                                                       | {"n_unstable": len(x["unstable_design_columns"])}
                                                                                       for s, x in details[m]["scenarios"].items()} for m in details}, indent=1, default=str))


if __name__ == "__main__":
    main()
