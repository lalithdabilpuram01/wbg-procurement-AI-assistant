"""Models 2 / 3 final evaluation, part 2: ONE-TIME scoring of frozen test predictions against SYNTHETIC test labels.

Usage (from procurement-intelligence/ml/final23/):
  /opt/anaconda3/bin/python3 final_score23.py --smoke   # code-path test on validation rows (in-sample; no test labels; no lock)
  /opt/anaconda3/bin/python3 final_score23.py           # the single real scoring; writes frozen/TEST_SCORED.json and refuses to repeat
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import average_precision_score, brier_score_loss, precision_recall_curve, roc_auc_score

from common23 import DISCLAIMER, FEATURES, LABELS, ML
from classify import capacity, det
from common import INK2, SERIES, caption, style_mpl, write_json

HERE = Path(__file__).resolve().parent
FZ = HERE / "frozen"
LOCK = FZ / "TEST_SCORED.json"
SEED, N_BOOT = 20261025, 500


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def boot(df: pd.DataFrame, rng) -> dict:
    g = df.groupby("project_id").indices
    keys = list(g)
    y, s, a = df["y"].to_numpy().astype(bool), df["prob"].to_numpy(), df["alert"].to_numpy().astype(bool)
    acc = {k: [] for k in ("PR_AUC", "ROC_AUC", "F1", "recall", "precision", "prevalence")}
    for _ in range(N_BOOT):
        idx = np.concatenate([g[k] for k in rng.choice(keys, len(keys), replace=True)])
        if y[idx].all() or not y[idx].any():
            continue
        acc["PR_AUC"].append(average_precision_score(y[idx], s[idx]))
        acc["ROC_AUC"].append(roc_auc_score(y[idx], s[idx]))
        acc["prevalence"].append(y[idx].mean())
        d = det(y[idx], a[idx])
        for k in ("F1", "recall", "precision"):
            acc[k].append(d[k])
    return {k: [round(float(np.quantile(v, .025)), 4), round(float(np.quantile(v, .975)), 4)] for k, v in acc.items()}


def metrics(df: pd.DataFrame) -> dict:
    y, s = df["y"].to_numpy().astype(bool), df["prob"].to_numpy()
    frac, mp = calibration_curve(y, s, n_bins=10, strategy="quantile")
    return {"n": len(df), "projects": int(df["project_id"].nunique()), "realised_prevalence": round(float(y.mean()), 4),
            "mean_predicted_probability": round(float(s.mean()), 4), "PR_AUC": round(float(average_precision_score(y, s)), 4),
            "ROC_AUC": round(float(roc_auc_score(y, s)), 4), "Brier": round(float(brier_score_loss(y, s)), 4),
            "Brier_constant_prevalence": round(float(y.mean() * (1 - y.mean())), 4), "ECE": round(float(np.mean(np.abs(frac - mp))), 4),
            "calibration_bins": {"mean_predicted": [round(float(v), 4) for v in mp], "observed": [round(float(v), 4) for v in frac]},
            "at_frozen_threshold": det(y, df["alert"].to_numpy().astype(bool)), "capacity": capacity(y, s)}


def smoke_frame(m: str, s: str) -> pd.DataFrame:
    f = pd.read_parquet(FEATURES[m])
    va = f[f["split"] == "validation"].reset_index(drop=True)
    art = joblib.load(FZ / "models" / f"{m}_{s}_l2_final.joblib")
    pol = art["policy"]
    det_ = json.loads((FZ / "refit_details.json").read_text())[m]["scenarios"][s]
    p = art["pipeline"].predict_proba(va[pol["cat"] + pol["log"] + pol["num"]])[:, 1]
    out = va[["unit_id", "project_id", "procurement_method", "procurement_category", "region_name"]].copy()
    out["prob"], out["alert"] = p, p >= det_["threshold"]["t"]
    if m == "m3":
        b = det_["risk_bands"]
        out["band"] = np.where(p >= b["high_cutoff_q90"], "High", np.where(p >= b["medium_cutoff_q70"], "Medium", "Low"))
        out["factors"] = "[]"
    return out


def main() -> None:
    smoke = "--smoke" in sys.argv
    if LOCK.exists() and not smoke:
        raise SystemExit("Test sets already scored; refusing to score again.")
    fz = json.loads((FZ / "FROZEN.json").read_text())
    checks = {k: sha(FZ / k) == v for k, v in fz["artefacts_sha256"].items()}
    checks["config"] = sha(HERE / "FINAL_EVAL_CONFIG_M23.json") == fz["config_sha256"]
    checks["common23"] = sha(HERE / "common23.py") == fz["common23_sha256"]
    checks.update({f"features_{m}": sha(FEATURES[m]) == fz["features_sha256"][m] for m in FEATURES})
    checks.update({f"labels_{m}": sha(LABELS[m]) == fz["labels_sha256"][m] for m in LABELS})
    if not all(checks.values()):
        raise SystemExit(f"Frozen artefact mismatch: {[k for k, v in checks.items() if not v]}")
    rng = np.random.default_rng(SEED)
    out = Path(tempfile.mkdtemp(prefix="m23_smoke_")) if smoke else FZ / "test_results"
    out.mkdir(exist_ok=smoke)
    (out / "charts").mkdir(exist_ok=True)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    refit = json.loads((FZ / "refit_details.json").read_text())
    res: dict = {"scored_at_utc": started, "disclaimer": DISCLAIMER, "integrity_checks_all_pass": True, "models": {}}
    for m in ("m2", "m3"):
        f = pd.read_parquet(FEATURES[m])
        refit_projects = set(f.loc[f["split"].isin(["train", "validation"]), "project_id"])
        lab = pd.read_parquet(LABELS[m])
        res["models"][m] = {}
        for s in ("S2", "S0"):
            if smoke:
                df = smoke_frame(m, s)
                split = "validation"
            else:
                pr = pd.read_csv(FZ / f"{m}_test_predictions_frozen.csv", dtype={"unit_id": str})
                df = pr[["unit_id", "project_id"] + [c for c in pr.columns if c.endswith(f"_{s}")]].rename(columns=lambda c: c.replace(f"_{s}", ""))
                df = df.merge(f[["unit_id", "procurement_method", "procurement_category", "region_name"]], on="unit_id", validate="1:1")
                split = "test"
            ids = set(f.loc[f["split"] == split, "unit_id"])
            yl = lab[(lab.scenario == s) & lab.unit_id.isin(ids)][["unit_id", "sim_label"]]   # the single read of these labels
            df = df.merge(yl, on="unit_id", how="inner", validate="1:1").rename(columns={"sim_label": "y"})
            assert len(df) == len(ids)
            r = {"overall": metrics(df), "ci_project": boot(df, rng),
                 "assumed_population_prevalence": json.loads((FZ.parent / "FINAL_EVAL_CONFIG_M23.json").read_text())["models"][m]["assumed_prevalence"]}
            un = df[~df["project_id"].isin(refit_projects)]
            r["unseen_projects"] = {**metrics(un), "ci_project": boot(un, rng)} if len(un) and un["y"].any() else {"n": len(un)}
            seg = {}
            for col in ("procurement_method", "procurement_category", "region_name"):
                rows = []
                for k, gg in df.groupby(col):
                    if gg["y"].sum() >= 20 and (1 - gg["y"]).sum() >= 20:
                        rows.append({"segment": k, "n": len(gg), "positives": int(gg["y"].sum()), "PR_AUC": round(float(average_precision_score(gg["y"], gg["prob"])), 4),
                                     **det(gg["y"].to_numpy().astype(bool), gg["alert"].to_numpy().astype(bool))})
                seg[col] = rows
            r["segments"] = seg
            if m == "m3":
                bands = {}
                for b in ("Low", "Medium", "High"):
                    gb = df[df["band"] == b]
                    facs = pd.Series([x["label"] for v in gb.get("factors", pd.Series(dtype=str)) for x in json.loads(v)]).value_counts().head(3).to_dict() if len(gb) else {}
                    bands[b] = {"n": len(gb), "share": round(len(gb) / len(df), 4), "simulated_event_rate": round(float(gb["y"].mean()), 4) if len(gb) else None,
                                "most_frequent_displayed_factors": facs}
                rates = [bands[b]["simulated_event_rate"] for b in ("Low", "Medium", "High")]
                bands["ordered_low_lt_medium_lt_high"] = bool(all(v is not None for v in rates) and rates[0] < rates[1] < rates[2])
                bands["cutoffs"] = refit["m3"]["scenarios"][s]["risk_bands"]
                r["risk_bands"] = bands
            if s == "S0":
                ci = r["ci_project"]
                r["null_control"] = {"ROC_AUC_ci_contains_0.5": ci["ROC_AUC"][0] <= 0.5 <= ci["ROC_AUC"][1],
                                     "PR_AUC_ci_contains_prevalence": ci["PR_AUC"][0] <= r["overall"]["realised_prevalence"] <= ci["PR_AUC"][1]}
                r["null_control"]["pass"] = all(r["null_control"].values())
            res["models"][m][s] = r
            print(m, s, {k: r["overall"][k] for k in ("realised_prevalence", "mean_predicted_probability", "PR_AUC", "ROC_AUC", "Brier", "ECE")},
                  r["overall"]["at_frozen_threshold"], r.get("null_control", ""), flush=True)
    # charts
    plt = style_mpl()
    for m in ("m2", "m3"):
        fig, axs = plt.subplots(1, 3 if m == "m3" else 2, figsize=(15 if m == "m3" else 11, 4.5))
        for i, s in enumerate(("S2", "S0")):
            r = res["models"][m][s]["overall"]
            pr = pd.read_csv(FZ / f"{m}_test_predictions_frozen.csv", dtype={"unit_id": str}) if not smoke else None
            cb = r["calibration_bins"]
            axs[1].plot(cb["mean_predicted"], cb["observed"], "o-", color=SERIES[i], label=f"{s} (Brier {r['Brier']:.4f}, ECE {r['ECE']:.3f})")
            axs[0].bar(i, r["PR_AUC"], color=SERIES[i], width=.5)
            lo, hi = res["models"][m][s]["ci_project"]["PR_AUC"]
            axs[0].plot([i, i], [lo, hi], color=INK2)
            axs[0].plot([i - .3, i + .3], [r["realised_prevalence"]] * 2, ls="--", color=INK2, lw=1)
        axs[0].set_xticks([0, 1], ["S2 (primary)", "S0 (null control)"])
        axs[0].set(title="PR-AUC with 95% project CI (dashed = prevalence)", ylabel="PR-AUC")
        lim = max(0.45, max(res["models"][m]["S2"]["overall"]["calibration_bins"]["observed"]) + .05)
        axs[1].plot([0, lim], [0, lim], ls="--", color=INK2, lw=1)
        axs[1].set(xlim=(0, lim), ylim=(0, lim), title="Calibration (quantile bins)", xlabel="Mean predicted probability", ylabel="Observed simulated rate")
        axs[1].legend(frameon=False, fontsize=8)
        if m == "m3":
            for i, s in enumerate(("S2", "S0")):
                b = res["models"][m][s]["risk_bands"]
                axs[2].bar(np.arange(3) + (i - .5) * .35, [b[k]["simulated_event_rate"] or 0 for k in ("Low", "Medium", "High")], width=.33, color=SERIES[i], label=s)
            axs[2].set_xticks(range(3), ["Low", "Medium", "High"])
            axs[2].set(title="Simulated event rate by risk band", ylabel="Rate")
            axs[2].legend(frameon=False)
        fig.suptitle(f"{'Model 2 · simulated amendment (24 m)' if m == 'm2' else 'Model 3 · simulated re-bid (12 m)'} — {'SMOKE (validation, in-sample)' if smoke else 'final test (scored once)'}", fontweight="bold")
        caption(fig, DISCLAIMER, kind="SIMULATED")
        fig.tight_layout(rect=(0, .06, 1, .94)); fig.savefig(out / "charts" / f"{m}_final.png"); plt.close(fig)
    write_json(out / "final_test_metrics_m23.json", res)
    if smoke:
        print("SMOKE OK:", out)
        return
    LOCK.write_text(json.dumps({"scored_at_utc": started, "results_sha256": sha(out / "final_test_metrics_m23.json"), "note": "Scored once; do not re-score."}, indent=1))
    with (ML / "experiments" / "runs.jsonl").open("a") as fh:
        for m in ("m2", "m3"):
            for s in ("S2", "S0"):
                r = res["models"][m][s]
                fh.write(json.dumps({"logged_at": dt.datetime.now(dt.timezone.utc).isoformat(), "stage": "final_test_m23", "model": {"m2": "M2_amendment", "m3": "M3_rebid"}[m],
                                     "variant": "logreg_l2_final", "scenario": s, "label_source": "synthetic", "sim_version": "sim_v1", "test_scored": True,
                                     "test": {k: r["overall"][k] for k in ("realised_prevalence", "PR_AUC", "ROC_AUC", "Brier", "ECE", "at_frozen_threshold")},
                                     "artifact": f"ml/final23/frozen/models/{m}_{s}_l2_final.joblib", "disclaimer": DISCLAIMER}, default=str) + "\n")
    print("SCORED:", out)


if __name__ == "__main__":
    main()
