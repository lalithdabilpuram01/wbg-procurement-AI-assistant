"""Stage 2E: survival-label feasibility audit for Model 1 (no survival model is fitted here).

Usage:  /opt/anaconda3/bin/python3 ml/stage2c/survival_feasibility.py

Question: at a historical cutoff, can each tender package be labelled as an observed signing event, a genuine
right-censored (still unfinished) observation, or a competing / non-informative outcome?
Unit: tender package (project + normalised reference; hashed key) in the 184 API-reference projects.
Reference cutoff: 2022-06-30 (end of the Model 1 training period); population: packages first published
2019-07-01..2022-06-30. The same audit is also reported for the whole API scope at the 2026-10-20 snapshot.
Inputs: Phase 1 extract (notice details with hashed package keys and boolean markers) and Phase 1 linked-label table.
Nothing is re-linked; unlinked packages are never assumed censored.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase1"))
from common import ML_ROOT, SNAPSHOT_DATE, TENDER_TYPES, new_run_dir, write_json  # noqa: E402

EX = ML_ROOT / "eda" / "extract_20261025T065554Z" / "data"
LAB = ML_ROOT / "eda" / "tender_labels_20261025T070133Z" / "tender_duration_label_candidates_cohort.parquet"
WIN = (pd.Timestamp("2019-07-01"), pd.Timestamp("2022-06-30"))


def classify(pk: pd.DataFrame, cutoff: pd.Timestamp) -> pd.Series:
    """Ordered outcome classes at `cutoff` (first matching rule wins)."""
    c = pd.Series("unknown_unlinked_no_signal", index=pk.index)
    c[pk["only_invalid_links"]] = "linked_but_invalid_backfilled"
    c[(~pk["linked_valid"]) & pk["any_revised"]] = "unknown_unlinked_revised_notice_only"
    c[(~pk["linked_valid"]) & pk["award_notice_exists"]] = "awarded_per_notice_no_ds00005_contract"
    c[(~pk["linked_valid"]) & pk["rebid_marker"]] = "rebid_or_replacement_marker"
    c[(~pk["linked_valid"]) & pk["any_cancelled"]] = "cancelled_status"
    c[pk["linked_valid"] & (pk["first_signing"] > cutoff)] = "signed_after_cutoff_known_unfinished"
    c[pk["linked_valid"] & (pk["first_signing"] <= cutoff)] = "event_signed_by_cutoff"
    return c


def main() -> None:
    out = new_run_dir("stage2e_survival_feasibility")
    nd = pd.read_parquet(EX / "notice_details_keys.parquet")
    nd["notice_date"] = pd.to_datetime(nd["notice_date"])
    gn = pd.read_parquet(EX / "gold_procurement_notices.parquet")[["notice_op_id", "procurement_method", "procurement_category", "region_name"]]
    lab = pd.read_parquet(LAB)
    lab["contract_signing_date"] = pd.to_datetime(lab["contract_signing_date"])

    tn = nd[nd["is_tender_type"] & nd["package_key"].notna()].merge(gn, on="notice_op_id", how="left")
    aw = nd[(nd["notice_type"] == "Contract Award")]
    award_keys = set(aw["package_key"].dropna()) | set(aw["award_text_package_key"].dropna())
    pk = tn.sort_values("notice_date").groupby("package_key").agg(
        project_id=("project_id", "first"), first_pub=("notice_date", "min"), n_tender_notices=("notice_op_id", "nunique"),
        method=("procurement_method", "first"), category=("procurement_category", "first"), region=("region_name", "first"),
        any_cancelled=("notice_status", lambda s: (s == "Cancelled").any()), any_revised=("notice_status", lambda s: (s == "Revised").any()),
        rebid_marker=("desc_rebid_marker", "max"), rebid_ref_marker=("ref_rebid_marker", "max"))
    pk["rebid_marker"] = pk["rebid_marker"] | pk["rebid_ref_marker"]
    pk["award_notice_exists"] = pk.index.isin(award_keys)
    v = lab[lab["is_valid"]].groupby("package_key")["contract_signing_date"].min().rename("first_signing")
    pk = pk.join(v)
    pk["linked_valid"] = pk["first_signing"].notna()
    pk["only_invalid_links"] = (~pk["linked_valid"]) & pk.index.isin(set(lab["package_key"]))

    res: dict = {"unit": "tender package (hashed project + normalised reference), 184 API-reference projects",
                 "note": "Classes are ordered: valid link > cancelled status > re-bid marker > award notice without DS00005 contract > revised only > no signal"}
    # A. reference cutoff 2022-06-30, training-period packages
    win = pk[(pk["first_pub"] >= WIN[0]) & (pk["first_pub"] <= WIN[1])].copy()
    win["class_at_cutoff"] = classify(win, WIN[1])
    win["class_at_snapshot"] = classify(win, pd.Timestamp(SNAPSHOT_DATE))
    res["A_training_window_cutoff_2022_06_30"] = {
        "packages": int(len(win)),
        "classes_at_cutoff": win["class_at_cutoff"].value_counts().to_dict(),
        "what_was_visible_at_the_cutoff": {
            "signed_by_cutoff": int((win["class_at_cutoff"] == "event_signed_by_cutoff").sum()),
            "open_at_cutoff_all_other_classes": int((win["class_at_cutoff"] != "event_signed_by_cutoff").sum()),
            "of_which_later_signed_known_only_in_hindsight": int((win["class_at_cutoff"] == "signed_after_cutoff_known_unfinished").sum()),
        },
    }
    # B. whole API scope at the snapshot (DS00005 window only)
    sc = pk[pk["first_pub"] >= WIN[0]].copy()
    sc["class"] = classify(sc, pd.Timestamp(SNAPSHOT_DATE))
    sc["age_days"] = (pd.Timestamp(SNAPSHOT_DATE) - sc["first_pub"]).dt.days
    sc["age_band"] = pd.cut(sc["age_days"], [-1, 180, 365, 730, 10_000], labels=["<=180d", "181-365d", "366-730d", ">730d"])
    res["B_api_scope_at_snapshot_2026_10_20"] = {
        "packages_published_from_2019_07_01": int(len(sc)),
        "classes": sc["class"].value_counts().to_dict(),
        "unknown_no_signal_by_age": sc[sc["class"] == "unknown_unlinked_no_signal"]["age_band"].value_counts().sort_index().astype(int).to_dict(),
    }
    # C. is linkage failure informative? (unlinked share by method / region / category, training window)
    win["unlinked"] = ~win["linked_valid"]
    inf = {}
    for col in ("method", "category", "region"):
        t = win.groupby(col).agg(packages=("unlinked", "size"), unlinked_share=("unlinked", "mean"))
        t = t[t["packages"] >= 20].sort_values("unlinked_share")
        t.round(3).to_csv(out / f"unlinked_share_by_{col}_training_window.csv")
        inf[col] = {k: {"packages": int(r.packages), "unlinked_share": round(float(r.unlinked_share), 3)} for k, r in t.iterrows()}
    res["C_unlinked_share_by_group_training_window"] = inf
    win[["project_id", "first_pub", "method", "category", "region", "n_tender_notices", "first_signing", "class_at_cutoff", "class_at_snapshot"]] \
        .reset_index(drop=True).to_csv(out / "training_window_packages_classified.csv", index=False)

    a = res["A_training_window_cutoff_2022_06_30"]
    n_open = a["what_was_visible_at_the_cutoff"]["open_at_cutoff_all_other_classes"]
    n_later = a["what_was_visible_at_the_cutoff"]["of_which_later_signed_known_only_in_hindsight"]
    res["assessment"] = {
        "observed_events_exist": True,
        "genuine_right_censoring_identifiable_at_cutoff": False,
        "reason": (f"At 2022-06-30, {n_open} training-window packages were unsigned. Only {n_later} of them are later seen to sign; "
                   "the rest are cancelled, re-tendered, awarded without a DS00005 record, or carry no signal at all. At the cutoff these "
                   "groups are indistinguishable, so an 'unsigned at cutoff' indicator mixes true censoring with competing events and "
                   "linkage failures."),
        "competing_events_recorded": "cancellation status and re-bid markers are rare and under-recorded (they cannot measure the competing-risk rate)",
        "informative_linkage": "the unlinked share differs strongly by method / region (table C), so missing events are not ignorable",
        "conditional_variant": ("Restricting to packages that are eventually signed and treating the later-signed ones as censored is "
                                "fittable, but selecting that population uses post-cutoff knowledge; it estimates the same conditional "
                                "quantity as the regression models and is a retrospective analysis, not a deployable survival model."),
        "decision": "FAIL for a deployment-realistic survival model; survival branch stopped (no model fitted).",
    }
    write_json(out / "survival_feasibility.json", res)
    print(out)
    import json
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
