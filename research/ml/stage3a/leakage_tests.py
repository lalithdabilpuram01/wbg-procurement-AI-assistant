"""Stage 3A pre-training leakage and reproducibility tests for the synthetic outcomes (no classifier is fitted).

Usage:  /opt/anaconda3/bin/python3 ml/stage3a/leakage_tests.py
This audit script is the ONLY Stage 3A code allowed to read generator internals. Writes ml/eda/stage3a_leakage_<UTC>/.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ML = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML / "phase1"))
from common import new_run_dir, write_json  # noqa: E402

FEAT = {"m2": ML / "features" / "m2_contract_features_v1.parquet", "m3": ML / "features" / "m3_tender_features_v1.parquet"}
CAT = {"m2": ["procurement_method", "procurement_category", "review_type", "region_name", "sector"],
       "m3": ["notice_type", "procurement_method", "procurement_category", "region_name", "sector", "deadline_status"]}
NUM = {"m2": ["days_from_approval", "approval_fiscal_year", "income_ordinal"], "m3": ["window_days", "project_age_days", "income_ordinal"]}
FORBIDDEN = re.compile(r"sim_|logit|u_project|v_country|^p$|scenario|seed", re.I)


def main() -> None:
    out = new_run_dir("stage3a_leakage")
    man = json.loads((ML / "features" / "FEATURES_MANIFEST.json").read_text())
    res: dict = {}
    rep = subprocess.run([sys.executable, str(ML / "synthetic" / "generator.py"), "--verify"], capture_output=True, text=True)
    res["reproducibility"] = {"output": rep.stdout.strip().splitlines(), "pass": rep.stdout.count("reproducible: True") == 2}
    src = (ML / "stage3a" / "classify.py").read_text() if (ML / "stage3a" / "classify.py").exists() else ""
    res["training_code_never_reads_internals"] = {"pass": ("internals" not in src) and ("generator/" not in src) and ("constants.json" not in src),
                                                 "checked_file": "ml/stage3a/classify.py", "exists": bool(src)}
    for m in ("m2", "m3"):
        f = pd.read_parquet(FEAT[m])
        f = f[f["split"] != "out_of_window"].reset_index(drop=True)
        lab = pd.read_parquet(ML / "synthetic" / f"{m}_sim_v1" / "labels" / "labels.parquet")
        inn = pd.read_parquet(ML / "synthetic" / f"{m}_sim_v1" / "generator" / "internals.parquet")
        r: dict = {"feature_file_unchanged": hashlib.sha256(FEAT[m].read_bytes()).hexdigest() == man["outputs_sha256"][FEAT[m].name],
                   "feature_columns_free_of_label_or_latent_names": not any(FORBIDDEN.search(c) for c in f.columns),
                   "forbidden_feature_columns": [c for c in f.columns if FORBIDDEN.search(c)],
                   "label_units_equal_feature_units": all(set(lab[lab.scenario == s].unit_id) == set(f.unit_id) for s in ("S0", "S1", "S2"))}
        tr = f[f["split"] == "train"]
        for s in ("S0", "S1", "S2"):
            y = tr[["unit_id", "project_id"] + CAT[m] + NUM[m]].merge(lab[lab.scenario == s][["unit_id", "sim_label"]], on="unit_id")
            pv = {}
            for c in CAT[m]:
                ct = pd.crosstab(y[c], y["sim_label"])
                pv[c] = float(stats.chi2_contingency(ct)[1]) if ct.shape[0] > 1 else None
            for c in NUM[m]:
                a, b = y.loc[y.sim_label == 1, c].dropna(), y.loc[y.sim_label == 0, c].dropna()
                pv[c] = float(stats.mannwhitneyu(a, b).pvalue)
            g = y.groupby("project_id")["sim_label"].agg(["mean", "size"])
            g = g[g["size"] >= 10]
            pbar = y["sim_label"].mean()
            expected_var = float(np.mean(pbar * (1 - pbar) / g["size"]))
            r[s] = {"train_rows": len(y), "prevalence_train": round(float(y.sim_label.mean()), 4),
                    "p_values_label_vs_feature": {k: (round(v, 4) if v is not None else None) for k, v in pv.items()},
                    "tests_with_p_lt_0.05": int(sum(1 for v in pv.values() if v is not None and v < 0.05)), "tests": len(pv),
                    "project_rate_overdispersion_ratio": round(float(g["mean"].var() / expected_var), 3), "projects_with_ge10_rows": int(len(g))}
            if s != "S0":
                ii = f[["unit_id"] + NUM[m]].merge(inn[inn.scenario == s][["unit_id", "logit"]], on="unit_id")
                r[s]["max_abs_spearman_numeric_feature_vs_logit"] = round(float(max(abs(stats.spearmanr(ii[c], ii["logit"], nan_policy="omit")[0]) for c in NUM[m])), 3)
        s0 = r["S0"]
        r["S0_null_check"] = {"pass": s0["tests_with_p_lt_0.05"] <= 1 and 0.7 <= s0["project_rate_overdispersion_ratio"] <= 1.3,
                              "rule": "S0: at most 1 of the label-vs-feature tests below p 0.05 (chance level) and project-rate overdispersion 0.7-1.3 (binomial)"}
        r["correlation_guard_pass"] = all(r[s]["max_abs_spearman_numeric_feature_vs_logit"] < 0.95 for s in ("S1", "S2"))
        res[m] = r
    res["all_pass"] = (res["reproducibility"]["pass"] and all(res[m]["feature_file_unchanged"] and res[m]["feature_columns_free_of_label_or_latent_names"]
                                                             and res[m]["label_units_equal_feature_units"] and res[m]["S0_null_check"]["pass"]
                                                             and res[m]["correlation_guard_pass"] for m in ("m2", "m3")))
    write_json(out / "leakage_tests.json", res)
    print(out)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
