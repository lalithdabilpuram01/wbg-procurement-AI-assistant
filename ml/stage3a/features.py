"""Stage 3A: real-data feature tables and chronological splits for Models 2 and 3 (offline; no outcome columns).

Usage (from procurement-intelligence/):  /opt/anaconda3/bin/python3 ml/stage3a/features.py

Writes ml/features/m2_contract_features_v1.parquet, ml/features/m3_tender_features_v1.parquet and
ml/features/FEATURES_MANIFEST.json (row counts, windows, de-duplication, overlaps, input hashes).
Only inventory `include` features known at the prediction point; no amounts, supplier data, free text, re-bid markers.
Synthetic labels never live here (see ml/synthetic/).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase1"))
from common import ML_ROOT, SNAPSHOT_DATE, TENDER_TYPES  # noqa: E402

EX = ML_ROOT / "eda" / "extract_20261025T065554Z" / "data"
OUT = ML_ROOT / "features"
INCOME_ORD = {"L": 1.0, "LM": 2.0, "UM": 3.0, "H": 4.0}
M2_SPLITS = {"train": (2020, 2022), "validation": (2023, 2023), "test": (2024, 2024)}   # contract fiscal year (24-month window complete)
M3_SPLITS = {"train": (2016, 2021), "validation": (2022, 2022), "test": (2023, 2025)}   # publication fiscal year (12-month window complete)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fy_split(fy: pd.Series, splits: dict) -> pd.Series:
    out = pd.Series("out_of_window", index=fy.index, dtype=object)
    for k, (a, b) in splits.items():
        out[(fy >= a) & (fy <= b)] = k
    return out


def sector(pj: pd.DataFrame) -> pd.Series:
    return pj.set_index("project_id")["primary_major_sector"].fillna("Unknown").str.replace(r"^FY17\s*-\s*", "", regex=True)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    pj = pd.read_parquet(EX / "silver_projects.parquet")
    sec = sector(pj)
    appr = pj.set_index("project_id")["board_approval_date"].pipe(pd.to_datetime)
    man: dict = {"snapshot": str(SNAPSHOT_DATE), "inputs_sha256": {}}

    # ---------------- Model 2: contracts at signing ----------------
    ca = pd.read_parquet(EX / "gold_contract_analytics.parquet")
    ck = pd.read_parquet(EX / "contract_keys.parquet")[["wb_contract_number", "package_key"]]
    m2 = ca.merge(ck, on="wb_contract_number", how="left", validate="1:1")
    f2 = pd.DataFrame({
        "unit_id": m2["wb_contract_number"], "project_id": m2["project_id"], "package_key": m2["package_key"],
        "prediction_date": pd.to_datetime(m2["contract_signing_date"]), "fiscal_year": m2["contract_fiscal_year"].astype(int),
        "procurement_method": m2["procurement_method"].str.replace(r"\s+", " ", regex=True),
        "procurement_category": m2["procurement_category"], "review_type": m2["review_type"].fillna("Unknown"),
        "region_name": m2["region_name"], "country_grp": m2["wdi_country_code"].fillna("REGIONAL"),
        "sector": m2["project_id"].map(sec).fillna("Unknown"),
        "income_ordinal": m2["income_group_code_at_signing_fy"].map(INCOME_ORD),
        "days_from_approval": m2["days_from_board_approval_to_signing"].astype(float),
        "signed_before_approval": m2["signed_before_board_approval"].astype(float),
        "approval_fiscal_year": m2["project_approval_fiscal_year"].astype(float),
    })
    f2["split"] = fy_split(f2["fiscal_year"], M2_SPLITS)
    f2.to_parquet(OUT / "m2_contract_features_v1.parquet", index=False)

    # ---------------- Model 3: tenders at first publication ----------------
    gn = pd.read_parquet(EX / "gold_procurement_notices.parquet")
    nd = pd.read_parquet(EX / "notice_details_keys.parquet")[["notice_op_id", "package_key"]]
    t = gn[gn["notice_type"].isin(TENDER_TYPES)].merge(nd, on="notice_op_id", how="left")
    n0 = len(t)
    t["publication_date"] = pd.to_datetime(t["publication_date"])
    t = t.sort_values(["publication_date", "notice_op_id"])
    # (a) rows identical except ID (DS00979 duplicates): keep the earliest-ID row of each metadata group
    key = ["project_id", "notice_type", "publication_date", "deadline_date_published", "procurement_method", "procurement_category"]
    dup_mask = t["identical_metadata_group_size"] > 1
    before = len(t)
    t = pd.concat([t[~dup_mask], t[dup_mask].drop_duplicates(subset=key, keep="first")])
    dropped_identical = before - len(t)
    # (b) where a reference exists (184 API projects), keep only the first tender notice of each package
    with_pk = t["package_key"].notna()
    before = len(t)
    t = pd.concat([t[~with_pk], t[with_pk].sort_values(["publication_date", "notice_op_id"]).drop_duplicates("package_key", keep="first")])
    dropped_package = before - len(t)
    win = t["published_response_window_days"].astype(float)
    f3 = pd.DataFrame({
        "unit_id": t["notice_op_id"], "project_id": t["project_id"], "package_key": t["package_key"],
        "prediction_date": t["publication_date"], "fiscal_year": t["publication_fiscal_year"].astype(int),
        "notice_type": t["notice_type"], "procurement_method": t["procurement_method"].fillna("Unknown").str.replace(r"\s+", " ", regex=True),
        "procurement_category": t["procurement_category"].fillna("Unknown"), "region_name": t["region_name"],
        "country_grp": t["wdi_country_code"].fillna("REGIONAL"), "sector": t["project_id"].map(sec).fillna("Unknown"),
        "income_ordinal": t["income_group_code_at_publication_fy"].map(INCOME_ORD),
        "deadline_status": t["deadline_status"], "window_days": win,
        "has_positive_window": win.notna().astype(float),
        "project_age_days": (t["publication_date"] - t["project_id"].map(appr)).dt.days.astype(float),
    })
    f3["split"] = fy_split(f3["fiscal_year"], M3_SPLITS)
    f3 = f3.sort_values(["prediction_date", "unit_id"]).reset_index(drop=True)
    f3.to_parquet(OUT / "m3_tender_features_v1.parquet", index=False)

    for name, f, sp in (("m2", f2, M2_SPLITS), ("m3", f3, M3_SPLITS)):
        inw = f[f["split"] != "out_of_window"]
        P = {k: set(inw.loc[inw.split == k, "project_id"]) for k in ("train", "validation", "test")}
        pk = inw.dropna(subset=["package_key"]).groupby("package_key")["split"].nunique()
        man[name] = {
            "rows_all": int(len(f)), "rows_by_split": f["split"].value_counts().to_dict(),
            "fiscal_year_windows": {k: list(v) for k, v in sp.items()},
            "unit_id_unique": bool(f["unit_id"].is_unique),
            "projects_by_split": {k: len(v) for k, v in P.items()},
            "project_overlap": {"train_validation": len(P["train"] & P["validation"]), "train_test": len(P["train"] & P["test"]),
                                "validation_rows_in_train_projects": int(inw[(inw.split == "validation") & inw.project_id.isin(P["train"])].shape[0])},
            "packages_crossing_splits": int((pk > 1).sum()), "packages_with_key": int(len(pk)),
            "missing_pct_in_window": (inw.drop(columns=["split"]).isna().mean() * 100).round(2).to_dict(),
        }
    man["m3"]["deduplication"] = {"tender_notices_input": n0, "dropped_identical_metadata_rows": int(dropped_identical),
                                  "dropped_later_notices_of_same_package": int(dropped_package), "units_after": int(len(f3)),
                                  "note": "DS00979 has no reference number outside the 184 API projects, so later notices of the same tender "
                                          "elsewhere cannot be collapsed; residual dependence is handled by project-grouped CV."}
    for p in sorted(EX.glob("*.parquet")):
        if p.stem in ("gold_contract_analytics", "contract_keys", "gold_procurement_notices", "notice_details_keys", "silver_projects"):
            man["inputs_sha256"][p.name] = sha(p)
    man["outputs_sha256"] = {p.name: sha(p) for p in (OUT / "m2_contract_features_v1.parquet", OUT / "m3_tender_features_v1.parquet")}
    (OUT / "FEATURES_MANIFEST.json").write_text(json.dumps(man, indent=1, default=str))
    print(json.dumps({k: man[k] for k in ("m2", "m3")}, indent=1, default=str))


if __name__ == "__main__":
    main()
