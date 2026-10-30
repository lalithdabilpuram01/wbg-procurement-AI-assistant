"""Stage 3A audit: do the learned L2 logistic coefficients agree with the documented generator assumptions?

Usage:  /opt/anaconda3/bin/python3 ml/stage3a/recovery_audit.py <m2_run_dir> <m3_run_dir>
Audit-only (reads the generator config; training code never does). For each categorical driver, compares the learned
contrast between levels with the assumed one (Spearman over the levels the config names plus an unnamed reference level
assumed 0); numeric drivers are compared by sign. Writes ml/eda/stage3a_recovery_<UTC>/recovery.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr

ML = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML / "phase1"))
from common import new_run_dir, write_json  # noqa: E402

NUMERIC = {"m2": {"z_days_from_approval": "days_from_approval", "signed_before_approval": "signed_before_approval"},
           "m3": {"z_log_window": "window_days"}}


def main(m2_run: str, m3_run: str) -> None:
    cfg = json.loads((ML / "synthetic" / "configs" / "sim_v1.json").read_text())
    out = new_run_dir("stage3a_recovery")
    res = {}
    for m, run, key in (("m2", m2_run, "m2_amendment"), ("m3", m3_run, "m3_rebid")):
        eff = cfg[key]["S1"]["effects"]
        res[m] = {}
        for s in ("S0", "S1", "S2"):
            co = pd.read_csv(Path(run) / f"coefficients_l2_{s}.csv", index_col=0)["coef"]
            r = {}
            for var, levels in eff.items():
                if not isinstance(levels, dict) or var == "income_ordinal":
                    continue
                learned = {lv: float(co.get(f"{var}_{lv}", float("nan"))) for lv in levels}
                learned = {k: v for k, v in learned.items() if v == v}
                if len(learned) >= 2:
                    rho = spearmanr([levels[k] for k in learned], list(learned.values()))[0]
                    r[var] = {"assumed": {k: levels[k] for k in learned}, "learned": {k: round(v, 3) for k, v in learned.items()}, "spearman": round(float(rho), 3)}
            for gen, feat in NUMERIC[m].items():
                if gen in eff and feat in co.index:
                    r[feat] = {"assumed_sign": 1 if eff[gen] > 0 else -1, "learned": round(float(co[feat]), 3), "sign_agrees": (eff[gen] > 0) == (co[feat] > 0)}
            if "income_ordinal" in co.index:
                r["income_ordinal"] = {"assumed": "decreasing risk with higher income", "learned": round(float(co["income_ordinal"]), 3), "sign_agrees": float(co["income_ordinal"]) < 0}
            res[m][s] = r
    write_json(out / "recovery.json", res)
    print(out)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
