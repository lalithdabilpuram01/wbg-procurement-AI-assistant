"""Stage 2A, Steps 2-4: B0 / B1 median baselines for Model 1, scored on VALIDATION only.

Usage (from procurement-intelligence/):  /opt/anaconda3/bin/python3 ml/stage2a/baselines.py

B0  overall training median.
B1  median by procurement method x category, with fallback  method x category -> method -> category -> B0,
    using a level only when it has >= k training contracts. k is chosen by 5-fold project-grouped CV inside the
    training period (training data only); ties go to the larger k (simpler model).
B1m method-only median (same fallback and k-selection), reported because method x category cells are sparse.
Sensitivities: package-deduplicated medians (one row per package); strictly as-of medians (only training outcomes
signed by the end of the training period).

The test set is never loaded with targets (split.load() strips them) and is not scored.
Outputs: ml/eda/stage2a_baselines_<UTC>/ and one record per variant appended to ml/experiments/runs.jsonl.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase1"))
from common import INK2, ML_ROOT, SERIES, caption, new_run_dir, style_mpl, write_json  # noqa: E402
from split import FEATURES, LABELS, SPLITS, TARGET, file_sha, load  # noqa: E402

K_GRID = [1, 3, 5, 10, 15, 20, 30, 50]
N_BOOT = 2000
SEED = 20261025
POP = "Validation set (n=353 contracts; first tenders 2022-07-01 to 2023-06-30), Model 1 population, 2,000-project cohort"


class MedianBaseline:
    """Hierarchical median lookup; `levels` are column tuples tried in order, then the global median."""

    def __init__(self, levels: list[tuple[str, ...]], k: int = 1):
        self.levels, self.k = levels, k

    def fit(self, df: pd.DataFrame, y: pd.Series):
        self.global_ = float(np.median(y))
        self.tables_ = []
        for cols in self.levels:
            g = pd.DataFrame({**{c: df[c].to_numpy() for c in cols}, "y": y.to_numpy()}).groupby(list(cols))["y"]
            t = pd.DataFrame({"median": g.median(), "n": g.size()})
            self.tables_.append((cols, t[t["n"] >= self.k]))
        return self

    def predict(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        pred = np.full(len(df), np.nan)
        level = np.full(len(df), "global", dtype=object)
        for cols, t in self.tables_:
            key = pd.MultiIndex.from_frame(df[list(cols)]) if len(cols) > 1 else pd.Index(df[cols[0]])
            v = t["median"].reindex(key).to_numpy()
            take = np.isnan(pred) & ~np.isnan(v)
            pred[take], level[take] = v[take], "x".join(cols)
        pred[np.isnan(pred)] = self.global_
        return pred, level


B1_LEVELS = [("procurement_method", "procurement_category"), ("procurement_method",), ("procurement_category",)]
B1M_LEVELS = [("procurement_method",)]


def choose_k(train: pd.DataFrame, levels) -> dict:
    gkf = GroupKFold(n_splits=5)
    scores = {}
    for k in K_GRID:
        maes = []
        for tr, te in gkf.split(train, groups=train["project_id"]):
            mdl = MedianBaseline(levels, k).fit(train.iloc[tr], train[TARGET].iloc[tr])
            p, _ = mdl.predict(train.iloc[te])
            maes.append(np.mean(np.abs(p - train[TARGET].iloc[te].to_numpy())))
        scores[k] = float(np.mean(maes))
    best = min(scores.values())
    k = max(kk for kk, s in scores.items() if s <= best + 1e-9)
    return {"k": k, "cv_mae_by_k": {str(a): round(b, 2) for a, b in scores.items()}}


def metrics(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None) -> dict:
    e = p - y
    a = np.abs(e)
    out = {"n": int(len(y)), "MAE": float(a.mean()), "MedAE": float(np.median(a)), "RMSE": float(np.sqrt((e ** 2).mean())),
           "bias_mean_pred_minus_actual": float(e.mean()), "median_signed_error": float(np.median(e)),
           "abs_error_p75": float(np.quantile(a, .75)), "abs_error_p90": float(np.quantile(a, .9)), "abs_error_p95": float(np.quantile(a, .95)),
           "MAE_log_ratio": float(np.mean(np.abs(np.log(p) - np.log(y)))),
           "share_within_30d": float((a <= 30).mean()), "share_within_50pct": float((a <= 0.5 * y).mean())}
    if w is not None:
        out["MAE_package_weighted"] = float(np.average(a, weights=w))
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in out.items()}


def cluster_boot(df: pd.DataFrame, cols: list[str], cluster: str, rng) -> dict:
    """Cluster bootstrap (resample whole clusters) for MAE of each prediction column and paired differences vs B0."""
    groups = df.groupby(cluster).indices
    keys = list(groups)
    ae = {c: np.abs(df[c].to_numpy() - df[TARGET].to_numpy()) for c in cols}
    draws = {c: [] for c in cols}
    diffs = {c: [] for c in cols if c != "pred_B0"}
    for _ in range(N_BOOT):
        idx = np.concatenate([groups[k] for k in rng.choice(keys, size=len(keys), replace=True)])
        m0 = ae["pred_B0"][idx].mean()
        for c in cols:
            mc = ae[c][idx].mean()
            draws[c].append(mc)
            if c != "pred_B0":
                diffs[c].append(m0 - mc)
    ci = lambda x: [round(float(np.quantile(x, .025)), 2), round(float(np.quantile(x, .975)), 2)]  # noqa: E731
    return {"clusters": len(keys), "MAE_95ci": {c: ci(v) for c, v in draws.items()},
            "MAE_reduction_vs_B0_95ci": {c: ci(v) for c, v in diffs.items()},
            "P_reduction_le_0": {c: round(float(np.mean(np.array(v) <= 0)), 4) for c, v in diffs.items()}}


def main() -> None:
    out = new_run_dir("stage2a_baselines")
    ch = out / "charts"
    ch.mkdir()
    rng = np.random.default_rng(SEED)
    d = load()
    tr, va = d["train"].reset_index(drop=True), d["validation"].reset_index(drop=True)
    assert TARGET not in d["test_features_only"].columns
    res: dict = {"run": out.name, "seed": SEED, "test_scored": False,
                 "inputs": {"labels_sha256": file_sha(LABELS), "features_sha256": file_sha(FEATURES)},
                 "splits": {k: [str(a.date()), str(b.date())] for k, (a, b) in SPLITS.items()},
                 "n": {"train": len(tr), "validation": len(va)}}

    # ---- fit (training data only) ----
    k_b1, k_b1m = choose_k(tr, B1_LEVELS), choose_k(tr, B1M_LEVELS)
    res["k_selection_training_cv"] = {"B1": k_b1, "B1m": k_b1m, "method": "5-fold GroupKFold by project inside the training period"}
    models = {"B0": MedianBaseline([], 1), "B1": MedianBaseline(B1_LEVELS, k_b1["k"]), "B1m": MedianBaseline(B1M_LEVELS, k_b1m["k"])}
    for mdl in models.values():
        mdl.fit(tr, tr[TARGET])
    # sensitivities
    pk = tr.groupby("package_key").agg(**{TARGET: (TARGET, "median"), "procurement_method": ("procurement_method", "first"),
                                          "procurement_category": ("procurement_category", "first")}).reset_index()
    models["B1_package_dedup"] = MedianBaseline(B1_LEVELS, k_b1["k"]).fit(pk, pk[TARGET])
    asof = tr[tr["contract_signing_date"] <= SPLITS["train"][1]]
    models["B0_asof"] = MedianBaseline([], 1).fit(asof, asof[TARGET])
    models["B1_asof"] = MedianBaseline(B1_LEVELS, k_b1["k"]).fit(asof, asof[TARGET])
    res["fitted"] = {"B0_train_median_days": models["B0"].global_, "B0_asof_median_days": models["B0_asof"].global_,
                     "asof_training_rows": int(len(asof)),
                     "B1_groups_used": {"x".join(c): {"|".join(map(str, i if isinstance(i, tuple) else (i,))): {"median": float(r["median"]), "n": int(r["n"])}
                                                      for i, r in t.iterrows()} for c, t in models["B1"].tables_}}

    # ---- predict validation ----
    for name, mdl in models.items():
        p, lvl = mdl.predict(va)
        va[f"pred_{name}"], va[f"level_{name}"] = p, lvl
    w = 1 / va.groupby("package_key")["package_key"].transform("size").to_numpy()
    y = va[TARGET].to_numpy().astype(float)
    res["validation_metrics"] = {name: metrics(y, va[f"pred_{name}"].to_numpy(), w) for name in models}
    b0 = res["validation_metrics"]["B0"]
    res["improvement_vs_B0_pct"] = {name: {m: round(100 * (b0[m] - v[m]) / b0[m], 2) for m in ("MAE", "MedAE", "RMSE", "MAE_package_weighted")}
                                    for name, v in res["validation_metrics"].items() if name != "B0"}
    res["predictions_strictly_positive"] = {name: bool((va[f"pred_{name}"] > 0).all()) for name in models}
    res["prediction_range"] = {name: [float(va[f"pred_{name}"].min()), float(va[f"pred_{name}"].max())] for name in models}
    res["B1_fallback_levels_on_validation"] = va["level_B1"].value_counts().to_dict()
    res["MAE_by_fallback_level"] = {lv: metrics(g[TARGET].to_numpy(), g["pred_B1"].to_numpy())["MAE"] for lv, g in va.groupby("level_B1")}

    # ---- uncertainty (cluster bootstrap) ----
    pc = ["pred_B0", "pred_B1", "pred_B1m"]
    res["bootstrap_project_clusters"] = cluster_boot(va, pc, "project_id", rng)
    res["bootstrap_package_clusters"] = cluster_boot(va, pc, "package_key", rng)

    # ---- segments ----
    seg = {}
    for col in ("procurement_method", "procurement_category", "region_name", "project_country_name", "first_tender_type", "review_type"):
        rows = []
        for k, g in va.groupby(col):
            r = {"segment": k, "n": len(g), "projects": g["project_id"].nunique(), "actual_median": float(g[TARGET].median())}
            for nme in ("B0", "B1", "B1m"):
                mm = metrics(g[TARGET].to_numpy(), g[f"pred_{nme}"].to_numpy())
                r[f"MAE_{nme}"], r[f"bias_{nme}"] = mm["MAE"], mm["bias_mean_pred_minus_actual"]
            r["sparse_n_lt_20"] = len(g) < 20
            rows.append(r)
        t = pd.DataFrame(rows).sort_values("n", ascending=False)
        t.to_csv(out / f"segments_by_{col}.csv", index=False)
        seg[col] = t.to_dict("records")
    res["segments"] = seg

    # ---- Step 4 audit ----
    p25, p75 = np.quantile(tr[TARGET], [.25, .75])
    long_, short_ = va[va[TARGET] > p75], va[va[TARGET] < p25]
    res["audit"] = {
        "train_quartiles_days": [float(p25), float(p75)],
        "long_actual_gt_train_p75": {"n": len(long_), **{nme: {"MAE": round(float(np.abs(long_[f"pred_{nme}"] - long_[TARGET]).mean()), 1),
                                                                "share_underpredicted": round(float((long_[f"pred_{nme}"] < long_[TARGET]).mean()), 3)} for nme in ("B0", "B1")}},
        "short_actual_lt_train_p25": {"n": len(short_), **{nme: {"MAE": round(float(np.abs(short_[f"pred_{nme}"] - short_[TARGET]).mean()), 1),
                                                                  "share_overpredicted": round(float((short_[f"pred_{nme}"] > short_[TARGET]).mean()), 3)} for nme in ("B0", "B1")}},
        "over_365d": {"n": int((va[TARGET] > 365).sum()), "B1_MAE": round(float(np.abs(va.loc[va[TARGET] > 365, "pred_B1"] - va.loc[va[TARGET] > 365, TARGET]).mean()), 1)},
    }
    top5 = va["project_id"].value_counts().head(5).index
    ex = va[~va["project_id"].isin(top5)]
    res["audit"]["project_concentration"] = {
        "top5_projects_rows": int(va["project_id"].isin(top5).sum()),
        "MAE_excluding_top5_projects": {nme: metrics(ex[TARGET].to_numpy(), ex[f"pred_{nme}"].to_numpy())["MAE"] for nme in ("B0", "B1", "B1m")},
        "MAE_per_top5_project": {str(i + 1): {"n": int((va.project_id == p).sum()), "B0": round(float(np.abs(va.loc[va.project_id == p, "pred_B0"] - va.loc[va.project_id == p, TARGET]).mean()), 1),
                                              "B1": round(float(np.abs(va.loc[va.project_id == p, "pred_B1"] - va.loc[va.project_id == p, TARGET]).mean()), 1)} for i, p in enumerate(top5)},
    }
    trp = set(tr["project_id"])
    for lab, g in (("validation_rows_in_train_projects", va[va.project_id.isin(trp)]), ("validation_rows_in_new_projects", va[~va.project_id.isin(trp)])):
        res["audit"][lab] = {nme: metrics(g[TARGET].to_numpy(), g[f"pred_{nme}"].to_numpy())["MAE"] for nme in ("B0", "B1", "B1m")} | {"n": len(g)}
    va["quarter"] = va["first_tender_date"].dt.to_period("Q").astype(str)
    res["audit"]["temporal"] = {
        "train_median_by_fy": tr.groupby("tender_fiscal_year")[TARGET].median().to_dict(),
        "validation_median": float(va[TARGET].median()),
        "MAE_by_validation_quarter": {q: {"n": len(g), **{nme: round(float(np.abs(g[f"pred_{nme}"] - g[TARGET]).mean()), 1) for nme in ("B0", "B1")},
                                          "actual_median": float(g[TARGET].median())} for q, g in va.groupby("quarter")},
    }
    lots = va.groupby("package_key").filter(lambda g: len(g) > 1)
    res["audit"]["lots"] = {"validation_contracts_in_multi_packages": len(lots),
                            "MAE_B1_multi_package_rows": round(float(np.abs(lots["pred_B1"] - lots[TARGET]).mean()), 1),
                            "MAE_B1_single_package_rows": round(float(np.abs(va.drop(lots.index)["pred_B1"] - va.drop(lots.index)[TARGET]).mean()), 1)}

    # ---- charts ----
    plt = style_mpl()
    fig, axs = plt.subplots(1, 2, figsize=(11.5, 4.8), sharey=True)
    lim = (5, 1500)
    for a, nme, col in ((axs[0], "B0", SERIES[0]), (axs[1], "B1", SERIES[1])):
        jitter = np.exp(rng.normal(0, 0.03, len(va)))
        a.scatter(va[TARGET], va[f"pred_{nme}"] * jitter, s=12, color=col, alpha=.55, edgecolor="none")
        a.plot(lim, lim, color=INK2, lw=1, ls="--")
        a.set(xscale="log", yscale="log", xlim=lim, ylim=lim, xlabel="Actual days (log)", title=f"{nme}: predicted vs actual")
        mm = res["validation_metrics"][nme]
        a.text(.03, .97, f"MAE {mm['MAE']:.0f} d · MedAE {mm['MedAE']:.0f} d", transform=a.transAxes, va="top", fontsize=8.5, color=INK2)
    axs[0].set_ylabel("Predicted days (log; slight jitter)")
    caption(fig, POP + ". Dashed line = perfect prediction. Validation only; not final performance")
    fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(ch / "01_pred_vs_actual.png"); plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(11.5, 4.2))
    bins = np.linspace(-900, 400, 66)
    for i, nme in enumerate(("B0", "B1")):
        axs[0].hist((va[f"pred_{nme}"] - va[TARGET]).clip(-900, 400), bins=bins, color=SERIES[i], alpha=.6, label=nme, edgecolor="white", linewidth=.3)
    axs[0].axvline(0, color=INK2, lw=1)
    axs[0].set(title="Residuals (predicted - actual)", xlabel="Days (clipped to -900..400)", ylabel="Contracts")
    axs[0].legend(frameon=False)
    for i, nme in enumerate(("B0", "B1")):
        axs[1].scatter(va[TARGET], va[f"pred_{nme}"] - va[TARGET], s=10, color=SERIES[i], alpha=.5, label=nme, edgecolor="none")
    axs[1].axhline(0, color=INK2, lw=1)
    axs[1].set(xscale="log", title="Residual vs actual duration", xlabel="Actual days (log)", ylabel="Predicted - actual (days)")
    axs[1].legend(frameon=False)
    caption(fig, POP + ". Negative = under-prediction (long tenders)")
    fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(ch / "02_residuals.png"); plt.close(fig)

    t = pd.DataFrame(seg["procurement_method"])
    t = t.sort_values("n")
    fig, a = plt.subplots(figsize=(10, 0.5 * len(t) + 1.6))
    yy = np.arange(len(t))
    a.barh(yy - .2, t["MAE_B0"], height=.38, color=SERIES[0], label="B0")
    a.barh(yy + .2, t["MAE_B1"], height=.38, color=SERIES[1], label="B1")
    a.set_yticks(yy, [f"{s}  (n={n})" for s, n in zip(t["segment"], t["n"])])
    a.set(title="Validation MAE by procurement method", xlabel="MAE (days)")
    a.legend(frameon=False, loc="lower right"); a.grid(axis="y", visible=False)
    caption(fig, POP + ". Segments with n < 20 are unreliable")
    fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(ch / "03_mae_by_method.png"); plt.close(fig)

    t = pd.DataFrame(seg["procurement_category"]).sort_values("n")
    fig, a = plt.subplots(figsize=(9, 3.4))
    yy = np.arange(len(t))
    a.barh(yy - .2, t["MAE_B0"], height=.38, color=SERIES[0], label="B0")
    a.barh(yy + .2, t["MAE_B1"], height=.38, color=SERIES[1], label="B1")
    a.set_yticks(yy, [f"{s}  (n={n})" for s, n in zip(t["segment"], t["n"])])
    a.set(title="Validation MAE by procurement category", xlabel="MAE (days)")
    a.legend(frameon=False, loc="lower right"); a.grid(axis="y", visible=False)
    caption(fig, POP)
    fig.tight_layout(rect=(0, .1, 1, 1)); fig.savefig(ch / "04_mae_by_category.png"); plt.close(fig)

    q = res["audit"]["temporal"]["MAE_by_validation_quarter"]
    fig, a = plt.subplots(figsize=(8, 3.6))
    xs = list(q)
    for i, nme in enumerate(("B0", "B1")):
        a.plot(xs, [q[x][nme] for x in xs], marker="o", color=SERIES[i], lw=2, label=nme)
    for x in xs:
        a.annotate(f"n={q[x]['n']}", (x, q[x]["B0"]), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=7.5, color=INK2)
    a.set(title="Validation MAE by quarter of first tender notice", ylabel="MAE (days)", xlabel="Calendar quarter")
    a.legend(frameon=False)
    caption(fig, POP)
    fig.tight_layout(rect=(0, .1, 1, 1)); fig.savefig(ch / "05_mae_by_quarter.png"); plt.close(fig)

    va.drop(columns=[c for c in va.columns if c.startswith(("wgi_", "gdp_", "inflation", "cpia"))]) \
        [["wb_contract_number", "project_id", "procurement_method", "procurement_category", "region_name", "first_tender_date", TARGET,
          "pred_B0", "pred_B1", "level_B1", "pred_B1m", "pred_B1_package_dedup", "pred_B0_asof", "pred_B1_asof"]] \
        .to_csv(out / "validation_predictions.csv", index=False)
    write_json(out / "baseline_metrics.json", res)

    # ---- local experiment tracking ----
    exp = ML_ROOT / "experiments"
    exp.mkdir(exist_ok=True)
    with (exp / "runs.jsonl").open("a") as fh:
        for name, mdl in models.items():
            fh.write(json.dumps({
                "logged_at": dt.datetime.now(dt.timezone.utc).isoformat(), "stage": "2A", "model": "M1_tender_time",
                "variant": name, "family": "median_baseline", "run_dir": str(out.relative_to(ML_ROOT.parent)),
                "label_rule": "tender_label_rule_p02_v1", "label_source": "real",
                "data": {"labels_sha256": res["inputs"]["labels_sha256"], "features_sha256": res["inputs"]["features_sha256"],
                         "cohort_manifest_sha256": "82a43185f427b3e647e062086cfea812018964e4f8cbb91877346e9a02dac940",
                         "gold_delta_versions": {"gold_contract_analytics": 73, "gold_procurement_notices": 68}},
                "split": res["splits"], "features": ["procurement_method", "procurement_category"] if "B1" in name else [],
                "params": {"levels": ["x".join(c) for c in mdl.levels], "min_support_k": mdl.k,
                           "fit_population": "package_dedup" if "dedup" in name else ("asof_signed_by_train_end" if "asof" in name else "train")},
                "seed": SEED, "validation_metrics": res["validation_metrics"][name], "test_scored": False,
            }, default=str) + "\n")
    print(out)
    print(json.dumps({k: res[k] for k in ("k_selection_training_cv", "validation_metrics", "improvement_vs_B0_pct", "bootstrap_project_clusters",
                                          "bootstrap_package_clusters", "B1_fallback_levels_on_validation", "MAE_by_fallback_level",
                                          "predictions_strictly_positive", "prediction_range", "audit")}, indent=1, default=str))
    print(json.dumps(res["fitted"]["B1_groups_used"], indent=0)[:2500])


if __name__ == "__main__":
    main()
