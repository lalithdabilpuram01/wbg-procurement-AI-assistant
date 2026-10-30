"""Stage 2A, Step 1: audit the chronological split for Model 1 before any baseline is fitted.

Usage (from procurement-intelligence/):  /opt/anaconda3/bin/python3 ml/stage2a/split_audit.py

Offline (Phase 1 extract and label runs; no Databricks access). Test-set targets are never read: target-dependent
checks use train and validation only; test is audited on keys, dates of prediction, counts and features.
Writes ml/eda/stage2a_split_audit_<UTC>/ (split_audit.json, counts_*.csv, split_assignment.csv, chart).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase1"))
from common import SERIES, SNAPSHOT_DATE, TENDER_TYPES, caption, new_run_dir, style_mpl, write_json  # noqa: E402
from split import FEATURES, FOLLOW_UP_DAYS, LABELS, NOTICES, SPLITS, WINDOW_END, WINDOW_START, file_sha, load_all_valid  # noqa: E402

ORDER = ["train", "validation", "test"]


def norm(s) -> str | None:
    return None if s is None or (isinstance(s, float) and np.isnan(s)) else re.sub(r"\s+", " ", str(s)).strip().lower()


def main() -> None:
    out = new_run_dir("stage2a_split_audit")
    df = load_all_valid()
    res: dict = {"inputs": {"labels": str(LABELS), "labels_sha256": file_sha(LABELS), "features": str(FEATURES),
                            "features_sha256": file_sha(FEATURES)},
                 "test_targets_read": False, "checks": {}}
    chk = res["checks"]
    m = df[df["split"].isin(ORDER)]
    ok = True

    # 1. boundaries
    b = {}
    for k in ORDER:
        d = m.loc[m["split"] == k, "first_tender_date"]
        b[k] = {"rule": [str(SPLITS[k][0].date()), str(SPLITS[k][1].date())], "observed_min": str(d.min().date()),
                "observed_max": str(d.max().date()), "within_rule": bool(d.min() >= SPLITS[k][0] and d.max() <= SPLITS[k][1])}
    contiguous = all((SPLITS[ORDER[i + 1]][0] - SPLITS[ORDER[i]][1]).days == 1 for i in range(2))
    chk["1_boundaries"] = {"splits": b, "contiguous_no_gap_no_overlap": contiguous,
                           "pass": contiguous and all(v["within_rule"] for v in b.values())}

    # 2. totals
    counts = m["split"].value_counts().reindex(ORDER).astype(int).to_dict()
    chk["2_totals"] = {"counts": counts, "total": int(len(m)), "unique_contracts": int(m["wb_contract_number"].nunique()),
                       "expected": {"train": 646, "validation": 353, "test": 616, "total": 1615},
                       "pass": counts == {"train": 646, "validation": 353, "test": 616} and m["wb_contract_number"].nunique() == 1615}

    # 3. the other valid labels
    exc = df.loc[~df["split"].isin(ORDER), "split"].value_counts().to_dict()
    chk["3_excluded_valid_labels"] = {"valid_labels": int(len(df)), "excluded": exc, "excluded_total": int(sum(exc.values())),
                                      "reasons": {"excluded_left_truncated": "first tender before 2019-07-01: the contract enters DS00005 only if signed on/after 2019-07-01, so these over-represent long durations",
                                                  "excluded_insufficient_follow_up": f"first tender after {WINDOW_END.date()}: fewer than {FOLLOW_UP_DAYS} days of follow-up before the {SNAPSHOT_DATE} snapshot, so only quickly-signed tenders appear"},
                                      "pass": sum(exc.values()) == len(df) - 1615 == 499}

    # 4. duplicates
    chk["4_duplicate_contract_ids"] = {"duplicates_overall": int(m["wb_contract_number"].duplicated().sum()),
                                       "pass": not m["wb_contract_number"].duplicated().any()}

    # 5. package overlap
    pk = m.groupby("package_key")["split"].nunique()
    chk["5_package_overlap"] = {"packages": int(len(pk)), "packages_in_more_than_one_split": int((pk > 1).sum()),
                                "packages_per_split": m.groupby("split")["package_key"].nunique().reindex(ORDER).astype(int).to_dict(),
                                "why": "first_tender_date is a package-level value (minimum over the package's tender notices), so all lots of a package fall in the same split",
                                "pass": int((pk > 1).sum()) == 0}

    # 6. project overlap
    P = {k: set(m.loc[m["split"] == k, "project_id"]) for k in ORDER}
    rows_in = lambda k, other: int(m[(m["split"] == k) & m["project_id"].isin(other)].shape[0])  # noqa: E731
    chk["6_project_overlap"] = {
        "projects_per_split": {k: len(v) for k, v in P.items()},
        "train_and_validation": len(P["train"] & P["validation"]), "train_and_test": len(P["train"] & P["test"]),
        "validation_and_test": len(P["validation"] & P["test"]), "all_three": len(P["train"] & P["validation"] & P["test"]),
        "validation_rows_in_train_projects": rows_in("validation", P["train"]),
        "test_rows_in_train_or_validation_projects": rows_in("test", P["train"] | P["validation"]),
        "test_rows_in_train_projects": rows_in("test", P["train"]),
        "validation_rows_in_new_projects": int(counts["validation"] - rows_in("validation", P["train"])),
        "test_rows_in_projects_unseen_in_train_and_validation": int(counts["test"] - rows_in("test", P["train"] | P["validation"])),
        "assessment": "Allowed for a temporal design (new tenders in ongoing projects is the real use case); "
                      "reported transparently with a project-held-out sensitivity view",
        "pass": True,
    }

    # 7. counts by fiscal year, method, category, country, project (counts only)
    for col, name in [("tender_fiscal_year", "fiscal_year"), ("procurement_method", "method"), ("procurement_category", "category"),
                      ("region_name", "region"), ("project_country_name", "country"), ("project_id", "project")]:
        t = m.pivot_table(index=col, columns="split", values="wb_contract_number", aggfunc="count", fill_value=0).reindex(columns=ORDER, fill_value=0)
        t["total"] = t.sum(axis=1)
        t.sort_values("total", ascending=False).to_csv(out / f"counts_by_{name}.csv")
    pc = m.groupby(["split", "project_id"]).size()
    chk["7_counts"] = {
        "methods_per_split": {k: m.loc[m.split == k, "procurement_method"].value_counts().to_dict() for k in ORDER},
        "categories_per_split": {k: m.loc[m.split == k, "procurement_category"].value_counts().to_dict() for k in ORDER},
        "countries_per_split": {k: int(m.loc[m.split == k, "project_country_name"].nunique()) for k in ORDER},
        "top5_project_share_pct": {k: round(100 * pc[k].sort_values(ascending=False).head(5).sum() / pc[k].sum(), 1) for k in ORDER},
        "largest_project_rows": {k: int(pc[k].max()) for k in ORDER},
        "methods_absent_from_train_but_present_later": sorted(set(m.loc[m.split != "train", "procurement_method"]) - set(m.loc[m.split == "train", "procurement_method"])),
        "files": "counts_by_{fiscal_year,method,category,region,country,project}.csv",
        "pass": True,
    }

    # 8. feature availability at tender publication
    gn = pd.read_parquet(NOTICES)[["notice_op_id", "procurement_method", "procurement_category", "publication_date", "notice_type"]]
    gn = gn.rename(columns={"procurement_method": "notice_method", "procurement_category": "notice_category", "publication_date": "notice_pub", "notice_type": "notice_type_ds"})
    j = m.merge(gn, left_on="first_tender_notice_op_id", right_on="notice_op_id", how="left")
    jm = j.dropna(subset=["notice_method"])
    meth_agree = (jm["procurement_method"].map(norm) == jm["notice_method"].map(norm))
    jc = j.dropna(subset=["notice_category"])
    cat_agree = (jc["procurement_category"].map(norm) == jc["notice_category"].map(norm))
    mism = jm.loc[~meth_agree, ["procurement_method", "notice_method"]].value_counts().head(8)
    # independent recomputation of the as-of prior-tender count (strictly earlier publications)
    allt = pd.read_parquet(NOTICES)
    allt = allt[allt["notice_type"].isin(TENDER_TYPES)][["project_id", "publication_date"]]
    allt["publication_date"] = pd.to_datetime(allt["publication_date"])
    prior = m.apply(lambda r: int(((allt["project_id"] == r["project_id"]) & (allt["publication_date"] < r["first_tender_date"])).sum()), axis=1)
    feat = {
        "procurement_method": {"source": "DS00005 contract record", "check": "agreement with the first tender notice's DS00979 method",
                               "notices_with_method": int(len(jm)), "agreement_pct": round(100 * meth_agree.mean(), 2),
                               "top_disagreements": {f"{a} | notice: {b}": int(n) for (a, b), n in mism.items()},
                               "verdict": "available at publication (method is announced in the notice)"},
        "procurement_category": {"source": "DS00005 contract record", "notices_with_category": int(len(jc)),
                                 "agreement_pct": round(100 * cat_agree.mean(), 2), "verdict": "available at publication"},
        "first_tender_type": {"source": "the tender notice itself", "verdict": "available at publication"},
        "prediction_date": {"check": "first_tender_date equals the DS00979 publication date of first_tender_notice_op_id",
                            "agreement_pct": round(100 * (pd.to_datetime(j["notice_pub"]) == j["first_tender_date"]).mean(), 2)},
        "published_bid_window": {"source": "procnotices API submission deadline (current notice version)",
                                 "rows_with_more_than_one_tender_notice": int((m["n_tender_notices"] > 1).sum()),
                                 "verdict": "available at publication; residual risk: the API returns the current notice, so an in-place deadline extension cannot be ruled out (0 valid labels have a Revised/Cancelled tender notice)"},
        "project_age_and_approval_fy": {"source": "Silver projects board_approval_date", "verdict": "historical fact; available",
                                        "tenders_before_board_approval": int((m["project_age_days_at_tender"] < 0).sum())},
        "project_prior_tender_notices": {"check": "independent recomputation with strictly-earlier publication dates",
                                         "mismatches": int((prior != m["project_prior_tender_notices"]).sum()), "verdict": "as-of; available"},
        "income_group_at_tender_fy": {"source": "OGHIST (point-in-time)", "verdict": "available"},
        "review_type": {"source": "DS00005 contract record (current snapshot)", "verdict": "normally fixed in the procurement plan before publication; low risk, but not verifiable as-of (no history)"},
        "region_country_sector": {"source": "current project attributes", "verdict": "stable project attributes; region uses the current taxonomy (naming, not outcome information)"},
        "used_by_B0_B1": ["procurement_method", "procurement_category"],
    }
    feat_pass = meth_agree.mean() >= 0.95 and cat_agree.mean() >= 0.95 and feat["project_prior_tender_notices"]["mismatches"] == 0
    chk["8_feature_availability"] = {**feat, "missing_pct_by_split": {
        c: {k: round(100 * m.loc[m.split == k, c].isna().mean(), 2) for k in ORDER}
        for c in ["procurement_method", "procurement_category", "review_type", "api_window_days", "project_age_days_at_tender",
                  "income_group_at_tender_fy", "wdi_country_code", "primary_major_sector"]}, "pass": bool(feat_pass)}

    # 9. prediction date and follow-up cutoff
    fu = (pd.Timestamp(SNAPSHOT_DATE) - m["first_tender_date"]).dt.days
    tv = m[m["split"].isin(["train", "validation"])]
    chk["9_prediction_date_and_cutoff"] = {
        "prediction_date": "first_tender_date = earliest publication date among the package's tender notices (IFB/REOI/IFP)",
        "cutoff": str(WINDOW_END.date()), "min_follow_up_days_all_splits": int(fu.min()),
        "every_row_has_ge_730d_follow_up": bool((fu >= FOLLOW_UP_DAYS).all()),
        "train_val_durations_over_730d": int((tv["duration_days"] > FOLLOW_UP_DAYS).sum()),
        "train_val_signing_after_snapshot": int((tv["contract_signing_date"] > pd.Timestamp(SNAPSHOT_DATE)).sum()),
        "test_signing_after_snapshot": "not inspected here; Phase 1 found 0 among all 2,114 valid labels",
        "label_look_ahead": {
            "train_labels_signed_after_train_end": int((m.loc[m.split == "train", "contract_signing_date"] > SPLITS["train"][1]).sum()),
            "train_labels_signed_after_validation_end": int((m.loc[m.split == "train", "contract_signing_date"] > SPLITS["validation"][1]).sum()),
            "meaning": "A model deployed on 2022-07-01 could not yet have seen these train outcomes. Using them keeps train "
                       "durations complete (unbiased); a strictly as-of variant is run as a sensitivity in the baseline step.",
        },
        "pass": bool((fu >= FOLLOW_UP_DAYS).all()),
    }

    # 10. lots / shared packages
    lots = {}
    for k in ORDER:
        g = m[m.split == k].groupby("package_key").size()
        lots[k] = {"contracts": int(g.sum()), "packages": int(len(g)), "multi_contract_packages": int((g > 1).sum()),
                   "contracts_in_multi_contract_packages": int(g[g > 1].sum()), "largest_package": int(g.max())}
    same = tv.groupby("package_key").agg(n=("duration_days", "size"), nd=("duration_days", "nunique"))
    lots["train_validation_multi_packages_with_identical_durations"] = int(((same.n > 1) & (same.nd == 1)).sum())
    lots["train_validation_multi_packages"] = int((same.n > 1).sum())
    chk["10_lots"] = {**lots, "controls": [
        "Packages never straddle splits (check 5), so lots cannot leak a target across train/validation/test",
        "Within a split, lots are correlated: CIs use project- and package-cluster bootstrap; a package-weighted metric (each package weight 1) is reported",
        "B1 group medians are computed per contract; a package-deduplicated variant is reported as a sensitivity"], "pass": True}

    res["overall_pass"] = all(v.get("pass", True) for v in chk.values())
    m[["wb_contract_number", "project_id", "package_key", "first_tender_date", "split"]].assign(package_key=lambda d: d.package_key) \
        .to_csv(out / "split_assignment.csv", index=False)

    plt = style_mpl()
    t = m.pivot_table(index="tender_fiscal_year", columns="split", values="wb_contract_number", aggfunc="count", fill_value=0).reindex(columns=ORDER, fill_value=0)
    fig, a = plt.subplots(figsize=(9, 3.8))
    bottom = np.zeros(len(t))
    for i, k in enumerate(ORDER):
        a.bar(t.index.astype(int).astype(str), t[k], bottom=bottom, color=SERIES[i], label=f"{k} (n={counts[k]})", width=.7, edgecolor="white", linewidth=1)
        bottom += t[k].to_numpy()
    a.set(title="Model 1 modelling population by fiscal year of first tender notice", xlabel="WB fiscal year", ylabel="Contracts (labels)")
    a.legend(frameon=False, fontsize=8)
    caption(fig, "REAL OBSERVED DATA (counts only; test targets not read) · 1,615 valid labels, tenders 2019-07-01 to 2024-10-20, 2,000-project cohort", kind="COUNTS")
    fig.tight_layout(rect=(0, .08, 1, 1)); fig.savefig(out / "split_counts_by_fiscal_year.png"); plt.close(fig)

    write_json(out / "split_audit.json", res)
    print(out)
    print("overall_pass", res["overall_pass"])
    for k, v in chk.items():
        print(k, "PASS" if v.get("pass") else "FAIL")
    import json
    print(json.dumps({k: chk[k] for k in ("1_boundaries", "2_totals", "3_excluded_valid_labels", "5_package_overlap", "6_project_overlap", "9_prediction_date_and_cutoff", "10_lots")}, indent=1, default=str))
    print(json.dumps({k: v for k, v in chk["8_feature_availability"].items() if k in ("procurement_method", "procurement_category", "prediction_date", "project_prior_tender_notices", "missing_pct_by_split")}, indent=1, default=str))
    print(json.dumps({k: chk["7_counts"][k] for k in ("top5_project_share_pct", "largest_project_rows", "methods_absent_from_train_but_present_later", "countries_per_split")}, indent=1))


if __name__ == "__main__":
    main()
