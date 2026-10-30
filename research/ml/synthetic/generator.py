"""Synthetic outcome generator for Models 2 and 3 (semi-synthetic: real feature rows, simulated labels only).

Usage (from procurement-intelligence/):  /opt/anaconda3/bin/python3 ml/synthetic/generator.py [--verify]

Reads the frozen config ml/synthetic/configs/sim_v1.json and the real feature tables in ml/features/.
Writes, per model:
  ml/synthetic/<model>_sim_v1/labels/labels.parquet       unit_id, scenario, sim_label, seed, sim_version, data_kind
  ml/synthetic/<model>_sim_v1/generator/internals.parquet unit_id, scenario, logit, p, u_project, v_country  (NEVER read by training code)
  ml/synthetic/<model>_sim_v1/generator/constants.json    intercepts, standardisation constants, achieved prevalence
  ml/synthetic/<model>_sim_v1/PROVENANCE.json             inputs / outputs SHA-256, config SHA-256, seeds
Labels are generated for all in-window rows (train, validation and test) so the test split exists; nothing is fitted.
--verify regenerates in memory and checks the labels are byte-identical (reproducibility).
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import expit

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "synthetic" / "configs" / "sim_v1.json"
FEAT = {"m2": ROOT / "features" / "m2_contract_features_v1.parquet", "m3": ROOT / "features" / "m3_tender_features_v1.parquet"}
KEY = {"m2": "m2_amendment", "m3": "m3_rebid"}
SCEN = ["S0", "S1", "S2"]


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def drivers(model: str, f: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    d, const = pd.DataFrame(index=f.index), {}
    if model == "m2":
        x = f["days_from_approval"].astype(float)
        const["days_from_approval"] = {"mean": float(x.mean()), "sd": float(x.std())}
        # a missing driver contributes nothing (0 on the standardised / indicator scale); counted in constants
        d["z_days_from_approval"] = ((x - x.mean()) / x.std()).fillna(0.0)
        d["signed_before_approval"] = f["signed_before_approval"].astype(float).fillna(0.0)
        const["rows_with_missing_days_from_approval"] = int(x.isna().sum())
    else:
        lw = np.log1p(f["window_days"])
        const["log1p_window_days"] = {"mean": float(lw.mean()), "sd": float(lw.std())}
        d["z_log_window"] = ((lw - lw.mean()) / lw.std()).fillna(0.0)
        d["short_window_lt_14d"] = (f["window_days"] < 14).astype(float)
        d["no_positive_window"] = (f["has_positive_window"] == 0).astype(float)
        d["early_project_age_lt_365d"] = (f["project_age_days"] < 365).astype(float)
    return d, const


def linear_logit(model: str, f: pd.DataFrame, d: pd.DataFrame, eff: dict) -> np.ndarray:
    z = np.zeros(len(f))
    for k, v in eff.items():
        if isinstance(v, dict):
            col = f["income_ordinal"].map(lambda q: str(int(q)) if pd.notna(q) else None) if k == "income_ordinal" else f[k]
            z += col.map(v).fillna(0.0).to_numpy()
        else:
            z += v * d[k].to_numpy()
    return z


def generate(model: str, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    c = cfg[KEY[model]]
    f = pd.read_parquet(FEAT[model])
    f = f[f["split"] != "out_of_window"].reset_index(drop=True)
    d, const = drivers(model, f)
    target = c["target_prevalence_central"]
    labels, internals, consts = [], [], {"standardisation": const, "scenarios": {}}
    for i, s in enumerate(SCEN):
        sc = c[s]
        seed = cfg["master_seed"] + 1000 * (1 if model == "m2" else 2) + i
        rng = np.random.default_rng(seed)
        if s == "S0":
            base = np.zeros(len(f))
        else:
            base = linear_logit(model, f, d, c["S1"]["effects"])
            if s == "S2":
                a = sc["adds"]
                if model == "m2":
                    base += a["works_x_prior_review"] * ((f["procurement_category"] == "Works") & (f["review_type"] == "Prior")).to_numpy()
                    base += a["z_days_from_approval_squared_minus_1"] * (d["z_days_from_approval"].to_numpy() ** 2 - 1)
                else:
                    base += a["works_x_short_or_no_window"] * ((f["procurement_category"] == "Works") & ((d["short_window_lt_14d"] == 1) | (d["no_positive_window"] == 1))).to_numpy()
                    base += a["z_log_window_squared_minus_1"] * (d["z_log_window"].to_numpy() ** 2 - 1)
        projects, countries = np.sort(f["project_id"].unique()), np.sort(f["country_grp"].unique())
        u = dict(zip(projects, rng.normal(0, sc["sigma_project"], len(projects)) if sc["sigma_project"] > 0 else np.zeros(len(projects))))
        v = dict(zip(countries, rng.normal(0, sc["sigma_country"], len(countries)) if sc["sigma_country"] > 0 else np.zeros(len(countries))))
        up, vc = f["project_id"].map(u).to_numpy(), f["country_grp"].map(v).to_numpy()
        lin = base + up + vc
        b0 = brentq(lambda b: expit(b + lin).mean() - target, -20, 20)
        p = expit(b0 + lin)
        y = (rng.random(len(f)) < p).astype(np.int8)
        labels.append(pd.DataFrame({"unit_id": f["unit_id"], "scenario": s, "sim_label": y, "seed": seed, "sim_version": cfg["sim_version"], "data_kind": "SYNTHETIC"}))
        internals.append(pd.DataFrame({"unit_id": f["unit_id"], "scenario": s, "logit": b0 + lin, "p": p, "u_project": up, "v_country": vc}))
        consts["scenarios"][s] = {"seed": seed, "intercept_b0": float(b0), "target_prevalence": target, "mean_p": float(p.mean()),
                                  "realised_prevalence_all": float(y.mean()),
                                  "realised_prevalence_by_split": {k: float(y[f["split"].to_numpy() == k].mean()) for k in ("train", "validation", "test")},
                                  "sd_logit": float(np.std(b0 + lin))}
    return pd.concat(labels, ignore_index=True), pd.concat(internals, ignore_index=True), consts


def parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


def main() -> None:
    cfg = json.loads(CFG.read_text())
    verify = "--verify" in sys.argv
    for model in ("m2", "m3"):
        lab, inn, consts = generate(model, cfg)
        base = ROOT / "synthetic" / f"{model}_sim_v1"
        lb = parquet_bytes(lab)
        if verify:
            stored = (base / "labels" / "labels.parquet").read_bytes()
            same = pd.read_parquet(io.BytesIO(stored)).equals(lab)
            print(model, "reproducible:", same)
            continue
        (base / "labels").mkdir(parents=True, exist_ok=False)
        (base / "generator").mkdir()
        (base / "labels" / "labels.parquet").write_bytes(lb)
        inn.to_parquet(base / "generator" / "internals.parquet", index=False)
        (base / "generator" / "constants.json").write_text(json.dumps(consts, indent=1))
        prov = {"sim_version": cfg["sim_version"], "model": model, "config_sha256": sha_bytes(CFG.read_bytes()),
                "features_input": str(FEAT[model].relative_to(ROOT.parent)), "features_sha256": sha_bytes(FEAT[model].read_bytes()),
                "labels_sha256": sha_bytes((base / "labels" / "labels.parquet").read_bytes()),
                "internals_sha256": sha_bytes((base / "generator" / "internals.parquet").read_bytes()),
                "rows_per_scenario": int(len(lab) // len(SCEN)), "scenarios": SCEN, "data_kind": "SYNTHETIC",
                "statement": "Simulated outcomes for demonstration only; not World Bank amendment or re-bidding records."}
        (base / "PROVENANCE.json").write_text(json.dumps(prov, indent=1))
        print(model, json.dumps(consts["scenarios"], indent=1))


if __name__ == "__main__":
    main()
