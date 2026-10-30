"""Model 1 final evaluation, part 2: ONE-TIME scoring of the frozen test predictions against the test targets.

Usage (from procurement-intelligence/):  /opt/anaconda3/bin/python3 ml/final/final_score.py

Verifies every SHA-256 in ml/final/frozen/FROZEN.json, refuses to run if ml/final/frozen/TEST_SCORED.json exists,
reads the 616 test targets once, computes all pre-declared metrics and writes ml/final/frozen/test_results/ plus the
lock file. No model is fitted or adjusted here.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
for sub in ("stage2c", "stage2b", "stage2a", "phase1"):
    sys.path.insert(0, str(ROOT / sub))
from advanced import boot_detection, detection  # noqa: E402
from baselines import metrics  # noqa: E402
from common import INK2, ML_ROOT, SERIES, caption, style_mpl, write_json  # noqa: E402
from regression import paired_boot  # noqa: E402
from split import FEATURES, LABELS, TARGET, file_sha, load_all_valid  # noqa: E402

HERE = Path(__file__).resolve().parent
FZ = HERE / "frozen"
LOCK = FZ / "TEST_SCORED.json"
MODELS = ["b1", "ridge", "rf"]
LABEL = {"b1": "B1", "ridge": "Ridge", "rf": "Random Forest"}
COL = {"b1": SERIES[0], "ridge": SERIES[1], "rf": SERIES[2]}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def smoke_frame(cfg, det_cfg):
    """Validation rows with predictions from the frozen models (code-path test only; never touches test targets)."""
    import joblib
    from scipy.stats import norm
    from regression import prep_features
    from split import load
    va = load()["validation"].reset_index(drop=True)
    X = prep_features(va)
    TH = cfg["long_duration"]["threshold_days"]
    b1 = joblib.load(FZ / "models" / "b1_final.joblib")
    va["pred_b1"], va["score_b1"] = b1["median"].predict(va)[0], b1["exceedance"].predict(va)
    va["alert_b1"] = va["score_b1"] >= det_cfg["b1"]["alert_threshold"]["t"]
    for m in ("ridge", "rf"):
        pl = joblib.load(FZ / "models" / f"{m}_final.joblib").predict(X)
        sd, (q10, q90) = det_cfg[m]["oof_log_residual_sd"], det_cfg[m]["interval_log_quantiles"]
        va[f"pred_{m}"], va[f"score_{m}"] = np.exp(pl), 1 - norm.cdf((np.log(TH) - pl) / sd)
        va[f"alert_{m}"] = va[f"score_{m}"] >= det_cfg[m]["alert_threshold"]["t"]
        va[f"lo_{m}"], va[f"hi_{m}"] = np.exp(pl + q10), np.exp(pl + q90)
    return va


def main() -> None:
    smoke = "--smoke" in sys.argv
    if LOCK.exists() and not smoke:
        raise SystemExit(f"The test set has already been scored ({LOCK}); refusing to score again.")
    fz = json.loads((FZ / "FROZEN.json").read_text())
    checks = {"config": sha(HERE / "FINAL_EVAL_CONFIG.json") == fz["config_sha256"], "labels": file_sha(LABELS) == fz["labels_sha256"],
              "features": file_sha(FEATURES) == fz["features_sha256"], "split_py": sha(ROOT / "stage2a" / "split.py") == fz["split_py_sha256"],
              "predictions": sha(FZ / "test_predictions_frozen.csv") == fz["test_predictions_sha256"],
              "refit_details": sha(FZ / "refit_details.json") == fz["refit_details_sha256"],
              **{f"model_{k}": sha(FZ / "models" / k) == v for k, v in fz["models_sha256"].items()}}
    if not all(checks.values()):
        raise SystemExit(f"Frozen artefact mismatch: {checks}")
    cfg = json.loads((HERE / "FINAL_EVAL_CONFIG.json").read_text())
    det_cfg = json.loads((FZ / "refit_details.json").read_text())
    TH, seed = cfg["long_duration"]["threshold_days"], cfg["random_seed"]
    rng = np.random.default_rng(seed)
    started = dt.datetime.now(dt.timezone.utc).isoformat()

    allv = load_all_valid()
    if smoke:  # code-path test on validation rows; test targets are not read
        allv = allv[allv["split"] != "test"]
        df = smoke_frame(cfg, det_cfg)
    else:
        # ---- the single read of test targets ----
        test = allv[allv["split"] == "test"].copy()
        pr = pd.read_csv(FZ / "test_predictions_frozen.csv", dtype={"wb_contract_number": str})
        df = test.merge(pr.drop(columns=["project_id", "package_key"]), on="wb_contract_number", how="inner", validate="1:1")
        assert len(df) == 616 == len(test) == len(pr)
    refit_projects = set(allv[(allv["split"].isin(["train", "validation"])) & (allv["contract_signing_date"] <= pd.Timestamp(cfg["refit"]["historical_cutoff"]))]["project_id"])
    refit_dur = allv[(allv["split"].isin(["train", "validation"])) & (allv["contract_signing_date"] <= pd.Timestamp(cfg["refit"]["historical_cutoff"]))][TARGET]
    y = df[TARGET].to_numpy().astype(float)
    df["y_long"] = y > TH
    out = Path(__import__("tempfile").mkdtemp(prefix="m1_smoke_")) if smoke else FZ / "test_results"
    out.mkdir(exist_ok=smoke)
    ch = out / "charts"
    ch.mkdir()
    res: dict = {"scored_at_utc": started, "frozen_sha256": sha(FZ / "FROZEN.json"), "integrity_checks": checks, "test_rows": len(df),
                 "test_projects": int(df["project_id"].nunique()), "test_packages": int(df["package_key"].nunique()),
                 "test_long_positives": int(df["y_long"].sum()), "threshold_days": TH}

    # ---- duration prediction ----
    w = 1 / df.groupby("package_key")["package_key"].transform("size").to_numpy()
    res["regression"] = {m: metrics(y, df[f"pred_{m}"].to_numpy(), w) for m in MODELS}
    for m in ("ridge", "rf"):
        res["regression"][m]["P10_P90_coverage"] = round(float(((y >= df[f"lo_{m}"]) & (y <= df[f"hi_{m}"])).mean()), 3)
        res["regression"][m]["median_interval_width_days"] = round(float((df[f"hi_{m}"] - df[f"lo_{m}"]).median()), 1)
    b1 = res["regression"]["b1"]["MAE"]
    res["MAE_improvement_vs_B1_pct"] = {m: round(100 * (b1 - res["regression"][m]["MAE"]) / b1, 2) for m in MODELS}
    pc = [f"pred_{m}" for m in MODELS]
    for ref in ("b1", "ridge"):
        for cl, col in (("project", "project_id"), ("package", "package_key")):
            res[f"boot_vs_{ref}_{cl}"] = paired_boot(df, pc, f"pred_{ref}", col, rng)

    # ---- long-duration detection ----
    yl = df["y_long"].to_numpy()
    k = int(np.ceil(0.2 * len(df)))
    res["long_detection"] = {}
    for m in MODELS:
        s = df[f"score_{m}"].to_numpy()
        top = np.zeros(len(df), dtype=bool)
        top[np.argsort(-s, kind="stable")[:k]] = True
        res["long_detection"][m] = {"frozen_threshold": det_cfg[m]["alert_threshold"]["t"], "PR_AUC": round(float(average_precision_score(yl, s)), 3),
                                    "ROC_AUC": round(float(roc_auc_score(yl, s)), 3), "prevalence": round(float(yl.mean()), 3),
                                    "at_frozen_threshold": detection(yl, df[f"alert_{m}"].to_numpy().astype(bool)),
                                    "point_prediction_gt_365": detection(yl, df[f"pred_{m}"].to_numpy() > TH),
                                    "top20pct_capacity": detection(yl, top)}
        df[f"alert_{m}"] = df[f"alert_{m}"].astype(bool)
    res["long_detection_ci_project"] = boot_detection(df, [f"alert_{m}" for m in MODELS], "project_id", rng)
    res["long_detection_ci_package"] = boot_detection(df, [f"alert_{m}" for m in MODELS], "package_key", rng)

    # ---- reliability and representativeness ----
    p25, p75 = np.quantile(refit_dur, [.25, .75])
    df["duration_band"] = np.where(y > TH, "long (> 365 d)", np.where(y > p75, "upper (refit p75..365]", np.where(y < p25, "short (< refit p25)", "middle")))
    df["project_seen_in_refit"] = df["project_id"].isin(refit_projects)
    df["quarter"] = df["first_tender_date"].dt.to_period("Q").astype(str)
    seg = {}
    for col in ("duration_band", "review_type", "procurement_method", "procurement_category", "region_name", "project_country_name",
                "project_seen_in_refit", "quarter", "project_id"):
        rows = []
        for key, g in df.groupby(col):
            r = {"segment": str(key), "n": len(g), "projects": int(g["project_id"].nunique()), "actual_median": float(g[TARGET].median()),
                 "actual_long": int(g["y_long"].sum())}
            for m in MODELS:
                e = g[f"pred_{m}"] - g[TARGET]
                r[f"MAE_{m}"], r[f"bias_{m}"] = round(float(e.abs().mean()), 1), round(float(e.mean()), 1)
                dd = detection(g["y_long"].to_numpy(), g[f"alert_{m}"].to_numpy())
                r[f"recall_{m}"], r[f"precision_{m}"], r[f"F1_{m}"], r[f"alerts_{m}"] = dd["recall"], dd["precision"], dd["F1"], dd["alerts"]
            r["sparse_n_lt_20"] = len(g) < 20
            rows.append(r)
        t = pd.DataFrame(rows).sort_values("n", ascending=False)
        t.to_csv(out / f"segments_by_{col}.csv", index=False)
        seg[col] = t.to_dict("records") if col != "project_id" else t.head(10).to_dict("records")
    res["segments"] = seg
    top5 = df["project_id"].value_counts().head(5).index
    ex = df[~df["project_id"].isin(top5)]
    res["concentration"] = {"top5_projects_rows": int(df["project_id"].isin(top5).sum()), "top5_share_pct": round(100 * df["project_id"].isin(top5).mean(), 1),
                            "MAE_excluding_top5": {m: round(float(np.abs(ex[f"pred_{m}"] - ex[TARGET]).mean()), 1) for m in MODELS},
                            "long_F1_excluding_top5": {m: detection(ex["y_long"].to_numpy(), ex[f"alert_{m}"].to_numpy())["F1"] for m in MODELS},
                            "project_MAE_wins": {m: int(sum(1 for r in pd.read_csv(out / "segments_by_project_id.csv").to_dict("records")
                                                            if r[f"MAE_{m}"] == min(r["MAE_b1"], r["MAE_ridge"], r["MAE_rf"]))) for m in MODELS}}
    exs = df[df["project_id"].isin(top5)]
    res["concentration"]["MAE_top5_only"] = {m: round(float(np.abs(exs[f"pred_{m}"] - exs[TARGET]).mean()), 1) for m in MODELS}

    # ---- mechanical application of the frozen selection rule ----
    vr_p, vr_k = res["boot_vs_ridge_project"]["MAE_reduction_vs_ref_95ci"]["pred_rf"], res["boot_vs_ridge_package"]["MAE_reduction_vs_ref_95ci"]["pred_rf"]
    unseen = {r["segment"]: r for r in seg["project_seen_in_refit"]}
    u = unseen.get("False", {})
    rf_conv = vr_p[0] > 0 and vr_k[0] > 0 and u.get("MAE_rf", 1e9) <= u.get("MAE_ridge", -1) and \
        res["long_detection"]["rf"]["at_frozen_threshold"]["F1"] >= res["long_detection"]["ridge"]["at_frozen_threshold"]["F1"]
    rb_p, rb_k = res["boot_vs_b1_project"]["MAE_reduction_vs_ref_95ci"]["pred_ridge"], res["boot_vs_b1_package"]["MAE_reduction_vs_ref_95ci"]["pred_ridge"]
    ridge_not_worse = rb_p[1] >= 0 and rb_k[1] >= 0  # Ridge not significantly worse than B1
    res["selection_rule_outcome"] = {"rf_convincing_vs_ridge": bool(rf_conv), "ridge_not_significantly_worse_than_b1": bool(ridge_not_worse),
                                     "rule_selects": "Random Forest" if rf_conv else ("Ridge" if ridge_not_worse else "B1")}

    df[["wb_contract_number", "project_id", "procurement_method", "procurement_category", "review_type", "region_name", "first_tender_date", TARGET, "y_long",
        "duration_band", "project_seen_in_refit"] + [c for c in df.columns if c.split("_")[0] in ("pred", "score", "alert", "lo", "hi")]] \
        .to_csv(out / "test_predictions_with_outcomes.csv", index=False)

    # ---- charts ----
    plt = style_mpl()
    cap = "HELD-OUT TEST (scored once) · 616 contracts, tenders 2023-07-01..2024-10-20, 59 projects (top 5 = {:.0f}% of rows); linked signed contracts only".format(res["concentration"]["top5_share_pct"])
    ci = res["boot_vs_b1_project"]["MAE_95ci"]
    fig, ax = plt.subplots(figsize=(8, 2.8))
    for i, m in enumerate(MODELS[::-1]):
        lo, hi = ci[f"pred_{m}"]
        ax.plot([lo, hi], [i, i], color=INK2, lw=1.4)
        ax.plot(res["regression"][m]["MAE"], i, "o", ms=9, color=COL[m])
        ax.text(hi + 1.5, i, f"{res['regression'][m]['MAE']:.1f}", va="center", fontsize=9, color=INK2)
    ax.set_yticks(range(3), [LABEL[m] for m in MODELS[::-1]])
    ax.set(title="Test MAE with 95% project-cluster bootstrap CI", xlabel="MAE (days)")
    ax.grid(axis="y", visible=False)
    caption(fig, cap, kind="REAL OBSERVED DATA")
    fig.tight_layout(rect=(0, .14, 1, 1)); fig.savefig(ch / "01_test_mae_ci.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 5))
    for m in MODELS:
        p, r, _ = precision_recall_curve(yl, df[f"score_{m}"])
        ax.plot(r, p, color=COL[m], lw=2, label=f"{LABEL[m]} (PR-AUC {res['long_detection'][m]['PR_AUC']:.2f})")
        d0 = res["long_detection"][m]["at_frozen_threshold"]
        ax.plot(d0["recall"], d0["precision"], "o", ms=9, color=COL[m], markeredgecolor="white")
    ax.axhline(yl.mean(), color=INK2, ls="--", lw=1)
    ax.text(.98, yl.mean() + .01, f"prevalence {yl.mean():.2f}", ha="right", fontsize=8, color=INK2)
    ax.set(xlim=(0, 1), ylim=(0, 1.02), xlabel="Recall", ylabel="Precision", title="Test: detecting tenders > 365 days")
    ax.legend(frameon=False, fontsize=8)
    caption(fig, cap + ". Dots = frozen training-OOF thresholds", kind="REAL OBSERVED DATA")
    fig.tight_layout(rect=(0, .12, 1, 1)); fig.savefig(ch / "02_test_pr_curves.png"); plt.close(fig)

    fig, axs = plt.subplots(1, 3, figsize=(11, 3.6))
    for a, m in zip(axs, MODELS):
        d0 = res["long_detection"][m]["at_frozen_threshold"]
        mat = np.array([[d0["TN"], d0["FP"]], [d0["FN"], d0["TP"]]])
        a.imshow(mat, cmap="Blues")
        for (i, j), v in np.ndenumerate(mat):
            a.text(j, i, str(v), ha="center", va="center", fontsize=12, color="white" if v > mat.max() * .6 else "#0b0b0b")
        a.set_xticks([0, 1], ["no alert", "alert"]); a.set_yticks([0, 1], ["≤ 365 d", "> 365 d"]); a.grid(False)
        a.set_title(f"{LABEL[m]}: R {d0['recall']:.2f} · P {d0['precision']:.2f} · F1 {d0['F1']:.2f}", fontsize=9.5)
    caption(fig, cap, kind="REAL OBSERVED DATA")
    fig.tight_layout(rect=(0, .14, 1, 1)); fig.savefig(ch / "03_test_confusion_matrices.png"); plt.close(fig)

    fig, axs = plt.subplots(1, 3, figsize=(12, 4), sharey=True)
    for a, m in zip(axs, MODELS):
        a.scatter(y, df[f"pred_{m}"], s=9, alpha=.5, color=COL[m], edgecolor="none")
        a.plot((5, 2000), (5, 2000), ls="--", color=INK2, lw=1)
        a.set(xscale="log", yscale="log", xlim=(5, 2000), ylim=(5, 2000), xlabel="Actual days", title=f"{LABEL[m]} · MAE {res['regression'][m]['MAE']:.1f}")
    axs[0].set_ylabel("Predicted days")
    caption(fig, cap, kind="REAL OBSERVED DATA")
    fig.tight_layout(rect=(0, .12, 1, 1)); fig.savefig(ch / "04_test_pred_vs_actual.png"); plt.close(fig)

    t = pd.DataFrame(seg["duration_band"]).set_index("segment")
    fig, ax = plt.subplots(figsize=(9, 3.8))
    xs = np.arange(len(t))
    for i, m in enumerate(MODELS):
        ax.bar(xs + (i - 1) * .26, t[f"bias_{m}"], width=.25, color=COL[m], label=LABEL[m])
    ax.axhline(0, color=INK2, lw=1)
    ax.set_xticks(xs, [f"{s}\n(n={n})" for s, n in zip(t.index, t["n"])], fontsize=8)
    ax.set(title="Test: mean bias by actual-duration band (predicted - actual)", ylabel="Days")
    ax.legend(frameon=False, fontsize=8)
    caption(fig, cap, kind="REAL OBSERVED DATA")
    fig.tight_layout(rect=(0, .14, 1, 1)); fig.savefig(ch / "05_test_bias_by_band.png"); plt.close(fig)

    t = pd.DataFrame(pd.read_csv(out / "segments_by_project_id.csv")).head(10)
    fig, ax = plt.subplots(figsize=(9, 4.4))
    xs = np.arange(len(t))
    for i, m in enumerate(MODELS):
        ax.bar(xs + (i - 1) * .26, t[f"MAE_{m}"], width=.25, color=COL[m], label=LABEL[m])
    ax.set_xticks(xs, [f"P{i + 1}\n(n={n})" for i, n in enumerate(t["n"])], fontsize=8)
    ax.set(title="Test MAE for the 10 largest test projects (anonymised order by size)", ylabel="MAE (days)")
    ax.legend(frameon=False, fontsize=8)
    caption(fig, cap, kind="REAL OBSERVED DATA")
    fig.tight_layout(rect=(0, .14, 1, 1)); fig.savefig(ch / "06_test_mae_top10_projects.png"); plt.close(fig)

    write_json(out / "final_test_metrics.json", res)
    if smoke:
        print("SMOKE OK (validation rows; no test targets read; no lock written):", out)
        return
    LOCK.write_text(json.dumps({"scored_at_utc": started, "results_sha256": sha(out / "final_test_metrics.json"),
                                "frozen_sha256": res["frozen_sha256"], "note": "Test set scored once; do not re-score."}, indent=1))
    with (ML_ROOT / "experiments" / "runs.jsonl").open("a") as fh:
        for m in MODELS:
            fh.write(json.dumps({"logged_at": dt.datetime.now(dt.timezone.utc).isoformat(), "stage": "final_test", "model": "M1_tender_time",
                                 "variant": LABEL[m], "protocol": "asof_2023-06-30_refit", "refit_rows": cfg["refit"]["eligible_rows_audited_before_test"],
                                 "params": det_cfg.get(m, {}).get("best_params", {}), "frozen_sha256": res["frozen_sha256"], "test_scored": True,
                                 "test_regression": res["regression"][m], "test_long_detection": res["long_detection"][m]["at_frozen_threshold"],
                                 "test_PR_AUC": res["long_detection"][m]["PR_AUC"], "artifact": f"ml/final/frozen/models/{m}_final.joblib"}, default=str) + "\n")
    print(json.dumps({k: res[k] for k in ("integrity_checks", "regression", "MAE_improvement_vs_B1_pct", "selection_rule_outcome", "concentration")}, indent=1, default=str))
    for ref in ("b1", "ridge"):
        for cl in ("project", "package"):
            print(f"vs {ref} {cl}", res[f"boot_vs_{ref}_{cl}"]["MAE_reduction_vs_ref_95ci"], res[f"boot_vs_{ref}_{cl}"]["MAE_95ci"] if ref == "b1" else "")
    for m in MODELS:
        print(m, json.dumps(res["long_detection"][m]))
    print("det CI project", res["long_detection_ci_project"])
    print("det CI package", res["long_detection_ci_package"])
    for s in ("duration_band", "review_type", "procurement_method", "project_seen_in_refit", "region_name", "quarter"):
        print("==", s)
        for r in seg[s]:
            if r["n"] >= 10:
                print("  ", r["segment"][:30], r["n"], r["actual_long"], "MAE", [r[f"MAE_{m}"] for m in MODELS], "bias", [r[f"bias_{m}"] for m in MODELS],
                      "F1", [r[f"F1_{m}"] for m in MODELS])


if __name__ == "__main__":
    main()
