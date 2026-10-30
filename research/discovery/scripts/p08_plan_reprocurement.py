"""Alignment review (2026-10-21): re-procurement after cancellation in STEP procurement plans.

Assessment Module 2 asks for re-bidding risk. Public notices rarely flag cancellations (4 of
30,977), but procurement plans carry a per-activity Process Status. This script measures, within
each parsed plan (offline, from profiling/procurement_plan_activities.csv produced by p05):

  * cancelled activities whose normalised reference re-appears on a non-cancelled activity (EN)
  * cancelled activities with a near-identical description on another activity (P, rapidfuzz >= 90)
  * whether that candidate successor was signed or completed

No identifier links a cancelled activity to its successor, so every result is a *candidate*
re-procurement signal. Output: profiling/procurement_plan_reprocurement_candidates.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

from wbdisc.config import SETTINGS  # noqa: E402
from wbdisc.normalize import normalize_ref  # noqa: E402
from wbdisc.procurement_plan import header_key, normalize_plan_status  # noqa: E402

THRESHOLD = 90


def main() -> None:
    df = pd.read_csv(SETTINGS.profiling_dir / "procurement_plan_activities.csv", dtype=str)
    ref_col = next(c for c in df.columns if header_key(c).startswith("activityreference"))
    status_col = next(c for c in df.columns if header_key(c).startswith("processstatus"))
    df["_status"] = df[status_col].map(normalize_plan_status)
    df["_ref"] = df[ref_col].fillna("").map(lambda v: normalize_ref(v.split(" / ")[0]))
    df["_desc"] = df[ref_col].fillna("").map(lambda v: v.split(" / ", 1)[1] if " / " in v else v).str.strip()

    res = {"plans": int(df["_wds_id"].nunique()), "activities": len(df), "cancelled": 0,
           "exact_ref_successor": 0, "exact_ref_successor_signed_or_completed": 0,
           "similar_description_successor": 0, "similar_successor_signed_or_completed": 0,
           "ambiguous_multiple_similar": 0, "examples": []}
    for _, g in df.groupby("_wds_id"):
        others = g[g["_status"] != "cancelled"]
        for _, r in g[g["_status"] == "cancelled"].iterrows():
            res["cancelled"] += 1
            if others.empty:
                continue
            en = others[others["_ref"].notna() & (others["_ref"] == r["_ref"])]
            if len(en):
                res["exact_ref_successor"] += 1
                res["exact_ref_successor_signed_or_completed"] += int(en["_status"].isin(["signed", "completed"]).any())
            if not r["_desc"]:
                continue
            scores = others["_desc"].map(lambda d: fuzz.token_set_ratio(r["_desc"], d))
            hits = others[scores >= THRESHOLD]
            if len(hits):
                res["similar_description_successor"] += 1
                res["ambiguous_multiple_similar"] += int(len(hits) > 1)
                res["similar_successor_signed_or_completed"] += int(hits["_status"].isin(["signed", "completed"]).any())
                if len(res["examples"]) < 5:
                    res["examples"].append({"cancelled": r[ref_col][:90], "candidate": hits.iloc[0][ref_col][:90],
                                            "candidate_status": hits.iloc[0]["_status"]})
    n = max(1, res["cancelled"])
    res["exact_ref_successor_pct"] = round(100 * res["exact_ref_successor"] / n, 1)
    res["similar_description_successor_pct"] = round(100 * res["similar_description_successor"] / n, 1)
    res["note"] = ("Within-plan only (latest plan version per project). Successors on other plan versions or "
                   "in later plans are not captured, so these are lower bounds. Labels are candidates (EN / P), "
                   "not verified re-bids.")
    (SETTINGS.profiling_dir / "procurement_plan_reprocurement_candidates.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
