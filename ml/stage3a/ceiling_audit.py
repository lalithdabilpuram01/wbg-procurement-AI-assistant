"""Stage 3A audit: achievable discrimination on VALIDATION if the true simulated probabilities were known.

Usage:  /opt/anaconda3/bin/python3 ml/stage3a/ceiling_audit.py <m2_run_dir> <m3_run_dir>
Audit-only (reads generator internals; training code never does). 'oracle_full' uses the generator probability p
(includes project / country random effects, which no feature can reveal); 'oracle_observable' removes only the project random effect (country is a model feature)
(the best a feature-based model could hope for). The gap to the fitted L2 model shows remaining headroom.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import expit
from sklearn.metrics import average_precision_score, roc_auc_score

ML = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML / "phase1"))
from common import new_run_dir, write_json  # noqa: E402

FEAT = {"m2": "m2_contract_features_v1.parquet", "m3": "m3_tender_features_v1.parquet"}


def main(m2_run: str, m3_run: str) -> None:
    out = new_run_dir("stage3a_ceiling")
    res = {}
    for m, run in (("m2", m2_run), ("m3", m3_run)):
        f = pd.read_parquet(ML / "features" / FEAT[m])
        va_ids = f.loc[f["split"] == "validation", "unit_id"]
        lab = pd.read_parquet(ML / "synthetic" / f"{m}_sim_v1" / "labels" / "labels.parquet")
        inn = pd.read_parquet(ML / "synthetic" / f"{m}_sim_v1" / "generator" / "internals.parquet")
        met = json.loads((Path(run) / "stage3a_metrics.json").read_text())
        res[m] = {}
        for s in ("S0", "S1", "S2"):
            d = lab[(lab.scenario == s) & lab.unit_id.isin(va_ids)].merge(inn[inn.scenario == s], on=["unit_id", "scenario"])
            y = d["sim_label"].to_numpy()
            p_obs = expit(d["logit"] - d["u_project"])  # country is a model feature, so only the project effect is unobservable
            res[m][s] = {"validation_rows": len(d),
                         "oracle_full": {"PR_AUC": round(float(average_precision_score(y, d["p"])), 4), "ROC_AUC": round(float(roc_auc_score(y, d["p"])), 4)},
                         "oracle_observable": {"PR_AUC": round(float(average_precision_score(y, p_obs)), 4), "ROC_AUC": round(float(roc_auc_score(y, p_obs)), 4)},
                         "logreg_l2": {k: met["scenarios"][s]["validation"]["logreg_l2"][k] for k in ("PR_AUC", "ROC_AUC")},
                         "rule": {k: met["scenarios"][s]["validation"]["rule"][k] for k in ("PR_AUC", "ROC_AUC")}}
    write_json(out / "ceiling.json", res)
    print(out)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
