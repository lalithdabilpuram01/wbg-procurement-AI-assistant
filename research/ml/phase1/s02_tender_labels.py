"""Phase 1, Task 2: validate tender-duration labels for the 2,000-project cohort (offline, from the s01 extract).

Usage:  /opt/anaconda3/bin/python3 ml/phase1/s02_tender_labels.py <extract_dir>

Target candidate (proposed, awaiting approval): days from the FIRST tender notice (IFB / REOI / prequalification)
published for a contract's package to the contract signing date. This is notice-to-signing, not the bid
response window (notice -> deadline) and not the full lifecycle (planning -> completion).

Linking: same project and equal normalised reference (Silver `notice_contract_link_candidates`, tender types),
re-derived independently from the hashed package keys. The documented validity rule (discovery/scripts/p02,
Phase 1) is re-applied unchanged:
    first tender < signing  AND  an API submission deadline exists and max(deadline) > first tender
    AND  (no reference-matched award notice OR first tender < earliest award-notice publication)
Exclusion reasons are assigned in that order so the funnel adds up. DS00979's deadline is reported only as a
sensitivity variant; it does not change the primary rule.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SNAPSHOT_DATE, TENDER_TYPES, new_run_dir, pct, wb_fiscal_year, write_json  # noqa: E402

DS00005_START = pd.Timestamp("2019-07-01")  # earliest contract signing date in DS00005 (FY2020)


def q(s: pd.Series) -> dict:
    s = s.dropna()
    if s.empty:
        return {"n": 0}
    return {"n": int(len(s)), "min": float(s.min()), "p01": float(s.quantile(.01)), "p05": float(s.quantile(.05)),
            "p25": float(s.quantile(.25)), "p50": float(s.median()), "p75": float(s.quantile(.75)),
            "p95": float(s.quantile(.95)), "p99": float(s.quantile(.99)), "max": float(s.max()),
            "mean": round(float(s.mean()), 1), "skew": round(float(s.skew()), 3)}


def km_curve(durations: np.ndarray, observed: np.ndarray) -> pd.DataFrame:
    """Kaplan-Meier survival estimate (no external dependency)."""
    order = np.argsort(durations)
    d, e = durations[order], observed[order]
    times = np.unique(d[e == 1])
    surv, s = [], 1.0
    for t in times:
        at_risk = (d >= t).sum()
        events = ((d == t) & (e == 1)).sum()
        s *= 1 - events / at_risk
        surv.append((t, s, at_risk))
    return pd.DataFrame(surv, columns=["days", "survival", "at_risk"])


def main(extract_dir: str) -> None:
    ex = Path(extract_dir) / "data"
    out = new_run_dir("tender_labels")
    ca = pd.read_parquet(ex / "gold_contract_analytics.parquet")
    ck = pd.read_parquet(ex / "contract_keys.parquet")
    nd = pd.read_parquet(ex / "notice_details_keys.parquet")
    lc = pd.read_parquet(ex / "link_candidates.parquet")
    gn = pd.read_parquet(ex / "gold_procurement_notices.parquet")[["notice_op_id", "deadline_date_published",
                                                                    "procurement_method", "procurement_category"]]
    res: dict = {"snapshot_date": str(SNAPSHOT_DATE), "extract": str(extract_dir)}

    c = ca.merge(ck, on=["wb_contract_number", "project_id"], how="left", validate="1:1")
    assert c["wb_contract_number"].is_unique
    for col in ("contract_signing_date",):
        c[col] = pd.to_datetime(c[col])

    # ---- tender candidates: Silver table vs independent re-derivation from package keys ----
    tn = nd[nd["is_tender_type"]].copy()
    tn["notice_date"] = pd.to_datetime(tn["notice_date"])
    tn["submission_deadline_date"] = pd.to_datetime(tn["submission_deadline_date"])
    tn = tn.merge(gn[["notice_op_id", "deadline_date_published"]], on="notice_op_id", how="left")
    tn["deadline_date_published"] = pd.to_datetime(tn["deadline_date_published"])
    sil = lc[lc["notice_type"].isin(TENDER_TYPES)][["notice_op_id", "wb_contract_number"]]
    rederived = tn.dropna(subset=["package_key"]).merge(
        c[["wb_contract_number", "project_id", "package_key"]].dropna(subset=["package_key"]),
        on=["project_id", "package_key"])[["notice_op_id", "wb_contract_number"]]
    a, b = set(map(tuple, sil.values)), set(map(tuple, rederived.values))
    res["link_check"] = {"silver_tender_candidate_pairs": len(a), "rederived_pairs": len(b),
                         "identical": a == b, "only_silver": len(a - b), "only_rederived": len(b - a)}
    pairs = rederived.merge(tn[["notice_op_id", "notice_type", "notice_status", "notice_date",
                                "submission_deadline_date", "deadline_date_published"]], on="notice_op_id")

    # ---- award-notice candidates (documented rule: matched on bid reference; sensitivity: + award-text ref) ----
    aw = lc[lc["notice_type"] == "Contract Award"].copy()
    aw["notice_date"] = pd.to_datetime(aw["notice_date"])
    aw_bid = aw[aw["matched_on_bid_reference_no"]].groupby("wb_contract_number")["notice_date"].min().rename("award_pub_bidref")
    aw_any = aw.groupby("wb_contract_number")["notice_date"].min().rename("award_pub_anyref")

    g = pairs.groupby("wb_contract_number")
    agg = pd.DataFrame({
        "first_tender_date": g["notice_date"].min(),
        "n_tender_notices": g["notice_op_id"].nunique(),
        "max_api_deadline": g["submission_deadline_date"].max(),
        "max_ds00979_deadline": g["deadline_date_published"].max(),
        "tender_types": g["notice_type"].agg(lambda x: "|".join(sorted(set(x)))),
        "any_tender_cancelled_or_revised": g["notice_status"].agg(lambda x: bool(set(x) & {"Cancelled", "Revised"})),
    })
    first_type = pairs.sort_values(["notice_date", "notice_op_id"]).groupby("wb_contract_number").first()
    agg["first_tender_type"] = first_type["notice_type"]
    agg["first_tender_notice_op_id"] = first_type["notice_op_id"]
    lab = c.merge(agg, left_on="wb_contract_number", right_index=True, how="inner")
    lab = lab.merge(aw_bid, left_on="wb_contract_number", right_index=True, how="left")
    lab = lab.merge(aw_any, left_on="wb_contract_number", right_index=True, how="left")
    lab["duration_days"] = (lab["contract_signing_date"] - lab["first_tender_date"]).dt.days
    lab["api_window_days"] = (lab["max_api_deadline"] - lab["first_tender_date"]).dt.days

    def reason(r, deadline_col="max_api_deadline", award_col="award_pub_bidref"):
        if pd.isna(r["first_tender_date"]):
            return "no_tender_publication_date"
        if not r["first_tender_date"] < r["contract_signing_date"]:
            return "tender_not_before_signing"
        if pd.isna(r[deadline_col]):
            return "no_submission_deadline"
        if not r[deadline_col] > r["first_tender_date"]:
            return "deadline_not_after_publication"
        if pd.notna(r[award_col]) and not r["first_tender_date"] < r[award_col]:
            return "tender_not_before_award_notice_backfill"
        return "valid"

    lab["validity"] = lab.apply(reason, axis=1)
    lab["validity_sens_ds00979_deadline"] = lab.apply(lambda r: reason(r, "max_ds00979_deadline"), axis=1)
    lab["validity_sens_any_award_ref"] = lab.apply(lambda r: reason(r, award_col="award_pub_anyref"), axis=1)
    lab["is_valid"] = lab["validity"] == "valid"
    lab["tender_fiscal_year"] = lab["first_tender_date"].map(lambda d: wb_fiscal_year(d) if pd.notna(d) else None)

    n_all = len(c)
    api = c[c["in_api_project"]]
    funnel = [
        ("cohort contracts (DS00005, Gold #2)", n_all),
        ("in the 184 projects with notice references (procnotices API)", len(api)),
        ("with a borrower contract reference", int(api["has_reference"].sum())),
        ("linked to >=1 tender notice (same project + normalised reference)", len(lab)),
        ("valid after the documented rule", int(lab["is_valid"].sum())),
    ]
    res["funnel"] = [{"step": s, "contracts": n, "pct_of_cohort": pct(n, n_all), "pct_of_api_scope": pct(n, len(api))} for s, n in funnel]
    res["not_assessable_outside_api_scope"] = {"contracts": n_all - len(api),
                                               "reason": "DS00979 publishes no reference number; description similarity is not evidence (Silver notices report §6)"}
    res["exclusions"] = lab["validity"].value_counts().to_dict()
    res["sensitivity"] = {
        "valid_if_ds00979_deadline_used": int((lab["validity_sens_ds00979_deadline"] == "valid").sum()),
        "valid_if_award_text_references_also_used": int((lab["validity_sens_any_award_ref"] == "valid").sum()),
    }
    v = lab[lab["is_valid"]].copy()
    res["valid_projects"] = int(v["project_id"].nunique())
    res["valid_packages"] = int(v["package_key"].nunique())
    res["duration_days_valid"] = q(v["duration_days"])
    res["duration_days_all_linked_before_rule"] = q(lab["duration_days"])
    res["log_duration_valid_skew"] = round(float(np.log(v["duration_days"]).skew()), 3)
    res["extremes_valid"] = {"<=7d": int((v.duration_days <= 7).sum()), ">365d": int((v.duration_days > 365).sum()),
                             ">730d": int((v.duration_days > 730).sum()), ">1095d": int((v.duration_days > 1095).sum())}
    res["negative_or_zero_before_rule"] = int((lab["duration_days"] <= 0).sum())
    res["duration_by_first_tender_type"] = {k: q(x["duration_days"]) for k, x in v.groupby("first_tender_type")}
    res["duration_by_method"] = {k: q(x["duration_days"]) for k, x in v.groupby("procurement_method") if len(x) >= 20}
    res["duration_by_category"] = {k: q(x["duration_days"]) for k, x in v.groupby("procurement_category")}

    # ---- packages and lots ----
    ps = v.groupby("package_key").size()
    res["packages_valid"] = {"packages": int(len(ps)), "contracts_in_multi_contract_packages": int(ps[ps > 1].sum()),
                             "multi_contract_packages": int((ps > 1).sum()), "max_contracts_per_package": int(ps.max()),
                             "share_contracts_in_multi_pct": pct(int(ps[ps > 1].sum()), len(v))}
    res["valid_with_more_than_one_tender_notice"] = int((v["n_tender_notices"] > 1).sum())
    res["valid_with_mixed_tender_types"] = int(v["tender_types"].str.contains(r"\|").sum())
    res["valid_with_cancelled_or_revised_tender_notice"] = int(v["any_tender_cancelled_or_revised"].sum())
    res["valid_signing_dates_after_snapshot"] = int((v["contract_signing_date"] > pd.Timestamp(SNAPSHOT_DATE)).sum())

    # ---- right-censoring: tender packages in API scope with no linked signed contract ----
    tp = tn.dropna(subset=["package_key"]).groupby(["project_id", "package_key"]).agg(
        first_pub=("notice_date", "min"), first_type=("notice_type", "first")).reset_index()
    signed = lab.groupby("package_key")["contract_signing_date"].min().rename("first_signing")
    tp = tp.merge(signed, left_on="package_key", right_index=True, how="left")
    tp["linked"] = tp["first_signing"].notna()
    tp["age_days_at_snapshot"] = (pd.Timestamp(SNAPSHOT_DATE) - tp["first_pub"]).dt.days
    tp["pub_fy"] = tp["first_pub"].map(lambda d: wb_fiscal_year(d) if pd.notna(d) else None)
    res["tender_packages_api_scope"] = {
        "packages": int(len(tp)), "with_linked_contract": int(tp["linked"].sum()),
        "without_linked_contract": int((~tp["linked"]).sum()),
        "without_link_by_age": {
            "<180d": int(((~tp.linked) & (tp.age_days_at_snapshot < 180)).sum()),
            "180-365d": int(((~tp.linked) & tp.age_days_at_snapshot.between(180, 365)).sum()),
            "366-730d": int(((~tp.linked) & tp.age_days_at_snapshot.between(366, 730)).sum()),
            ">730d": int(((~tp.linked) & (tp.age_days_at_snapshot > 730)).sum())},
        "interpretation": "An unlinked package is still open, cancelled, re-tendered under a new reference, signed "
                          "under a different reference, or a contract outside DS00005 (FY2020+ only). These cannot be "
                          "told apart from the data, so 'censored' is an upper bound on truly open tenders.",
    }
    res["link_rate_by_tender_publication_fy"] = (
        tp.groupby("pub_fy").agg(packages=("linked", "size"), linked=("linked", "sum"))
        .assign(linked_pct=lambda d: (100 * d.linked / d.packages).round(1)).reset_index().to_dict("records"))
    tp["event"] = tp["linked"].astype(int)
    tp["t"] = np.where(tp["linked"], (tp["first_signing"] - tp["first_pub"]).dt.days, tp["age_days_at_snapshot"])
    # Only packages first published on/after the DS00005 window start (2019-07-01) can be followed: an earlier
    # package signed before FY2020 is unobservable, not censored.
    km_in = tp[(tp["t"] > 0) & (tp["first_pub"] >= DS00005_START)]
    km = km_curve(km_in["t"].to_numpy(), km_in["event"].to_numpy())
    km.to_csv(out / "km_tender_package_to_first_signing.csv", index=False)
    res["km_note"] = ("Package-level Kaplan-Meier over packages first published on/after 2019-07-01 (event = first "
                      "linked signing; unlinked packages censored at the snapshot). Diagnostic only: unlinked packages "
                      "include cancellations and link failures, which are not censoring, so the curve is biased "
                      "toward longer durations.")

    # ---- observation-window truncation of the label set ----
    v_ = lab[lab["is_valid"]]
    left = v_["first_tender_date"] < DS00005_START
    res["truncation"] = {
        "left_truncated_valid_labels_tender_before_2019_07_01": int(left.sum()),
        "left_truncated_median_days": float(v_.loc[left, "duration_days"].median()) if left.any() else None,
        "note_left": "A tender published before 2019-07-01 appears only if its contract was signed on/after 2019-07-01, "
                     "so these labels over-represent long durations.",
        "note_right": "A tender published recently appears only if already signed, so recent labels over-represent "
                      "short durations (right truncation / censoring).",
    }
    for horizon in (365, 540, 730):
        cut = pd.Timestamp(SNAPSHOT_DATE) - pd.Timedelta(days=horizon)
        win = v_[(v_["first_tender_date"] >= DS00005_START) & (v_["first_tender_date"] <= cut)]
        res["truncation"][f"valid_with_full_{horizon}d_follow_up"] = {
            "tender_published_between": [str(DS00005_START.date()), str(cut.date())],
            "valid_labels": int(len(win)), "projects": int(win["project_id"].nunique()),
            "durations_over_horizon": int((win["duration_days"] > horizon).sum())}
    res["km_packages_used"] = int(len(km_in))
    res["km_survival_at"] = {str(d): (float(km[km.days <= d]["survival"].iloc[-1]) if (km.days <= d).any() else 1.0)
                             for d in (90, 180, 365, 730)}

    # ---- coverage by segment (valid labels / contracts in segment) ----
    def cov(col, scope):
        base = scope.groupby(col).size().rename("contracts")
        val = v.groupby(col).size().rename("valid_labels")
        d = pd.concat([base, val], axis=1).fillna(0).astype(int)
        d["coverage_pct"] = (100 * d.valid_labels / d.contracts).round(2)
        return d.sort_values("contracts", ascending=False).reset_index()

    for col in ("contract_fiscal_year", "region_name", "procurement_method", "procurement_category", "review_type"):
        cov(col, c).to_csv(out / f"coverage_by_{col}_vs_cohort.csv", index=False)
        cov(col, api).to_csv(out / f"coverage_by_{col}_vs_api_scope.csv", index=False)
    cov("project_country_name", api).to_csv(out / "coverage_by_country_vs_api_scope.csv", index=False)
    res["coverage_by_country"] = {"countries_in_api_scope": int(api["project_country_name"].nunique()),
                                  "countries_with_valid_labels": int(v["project_country_name"].nunique()),
                                  "top5_share_of_valid_pct": pct(int(v["project_country_name"].value_counts().head(5).sum()), len(v)),
                                  "projects_contributing_top10_share_pct": pct(int(v["project_id"].value_counts().head(10).sum()), len(v))}
    res["valid_by_tender_fy"] = v["tender_fiscal_year"].value_counts().sort_index().to_dict()
    res["valid_by_signing_fy"] = v["contract_fiscal_year"].value_counts().sort_index().to_dict()

    res["historical_comparison"] = {
        "phase1_276_project_sample": {"valid": 3782, "contracts": 22402, "candidates": 4452,
                                      "source": "discovery/reports/ML_FEASIBILITY_REPORT.md §1 (2026-10-20)"},
        "gold_notices_report_estimate": {"value": "about 1,271 contracts",
                                         "source": "gold/runs/gold_procurement_notices_20261024T154709Z/GOLD_DESIGN_AND_DRY_RUN_REPORT.md §4"},
    }
    keep = ["wb_contract_number", "project_id", "package_key", "region_name", "project_country_name", "wdi_country_code",
            "procurement_method", "procurement_category", "review_type", "first_tender_type", "first_tender_notice_op_id",
            "n_tender_notices", "first_tender_date", "max_api_deadline", "max_ds00979_deadline", "award_pub_bidref",
            "contract_signing_date", "contract_fiscal_year", "tender_fiscal_year", "duration_days", "api_window_days",
            "validity", "validity_sens_ds00979_deadline", "validity_sens_any_award_ref", "is_valid"]
    lab[keep].to_parquet(out / "tender_duration_label_candidates_cohort.parquet", index=False)
    lab[keep].to_csv(out / "tender_duration_label_candidates_cohort.csv", index=False)
    tp.drop(columns=["package_key"]).to_csv(out / "tender_packages_api_scope.csv", index=False)
    write_json(out / "tender_label_funnel.json", res)
    print(out)
    print({k: res[k] for k in ("link_check", "exclusions", "sensitivity")})
    for f in res["funnel"]:
        print(f)
    print(res["duration_days_valid"])


if __name__ == "__main__":
    main(sys.argv[1])
