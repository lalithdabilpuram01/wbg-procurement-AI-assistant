"""Stage 2A: the approved Model 1 population and chronological split (single source of truth).

Prediction point: first tender publication (`first_tender_date`). Target: days to contract signing.
Population: valid labels (documented p02 rule) whose first tender was published on/after 2019-07-01 (DS00005 window
start) and at least 730 days before the 2026-10-20 snapshot, so every tender had two years of follow-up.

`load()` never returns test-set targets: test rows come back with the target and every outcome-date column removed,
so no Stage 2A code can score or inspect the test target by accident.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase1"))
from common import ML_ROOT, SNAPSHOT_DATE  # noqa: E402

LABELS = ML_ROOT / "eda" / "tender_labels_20261025T070133Z" / "tender_duration_label_candidates_cohort.parquet"
FEATURES = ML_ROOT / "eda" / "eda_20261025T070501Z" / "tender_label_features_eda.parquet"
NOTICES = ML_ROOT / "eda" / "extract_20261025T065554Z" / "data" / "gold_procurement_notices.parquet"

WINDOW_START = pd.Timestamp("2019-07-01")
FOLLOW_UP_DAYS = 730
WINDOW_END = pd.Timestamp(SNAPSHOT_DATE) - pd.Timedelta(days=FOLLOW_UP_DAYS)  # 2024-10-20
SPLITS = {  # inclusive bounds on first_tender_date
    "train": (WINDOW_START, pd.Timestamp("2022-06-30")),
    "validation": (pd.Timestamp("2022-07-01"), pd.Timestamp("2023-06-30")),
    "test": (pd.Timestamp("2023-07-01"), WINDOW_END),
}
TARGET = "duration_days"
OUTCOME_COLUMNS = ["duration_days", "log_duration", "contract_signing_date", "contract_fiscal_year", "award_pub_bidref"]


def file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def assign_split(d: pd.Series) -> pd.Series:
    out = pd.Series(pd.NA, index=d.index, dtype="object")
    for name, (a, b) in SPLITS.items():
        out[(d >= a) & (d <= b)] = name
    out[d < WINDOW_START] = "excluded_left_truncated"
    out[d > WINDOW_END] = "excluded_insufficient_follow_up"
    return out


def load_all_valid() -> pd.DataFrame:
    """All 2,114 valid labels with features and package key (targets of test rows included: audit-internal only)."""
    lab = pd.read_parquet(LABELS)
    lab = lab[lab["is_valid"]]
    feat = pd.read_parquet(FEATURES)
    extra = [c for c in feat.columns if c not in lab.columns]
    df = lab.merge(feat[["wb_contract_number"] + extra], on="wb_contract_number", how="left", validate="1:1")
    df["first_tender_date"] = pd.to_datetime(df["first_tender_date"])
    df["contract_signing_date"] = pd.to_datetime(df["contract_signing_date"])
    df["split"] = assign_split(df["first_tender_date"])
    return df


def load() -> dict[str, pd.DataFrame]:
    df = load_all_valid()
    out = {k: df[df["split"] == k].copy() for k in ("train", "validation")}
    out["test_features_only"] = df[df["split"] == "test"].drop(columns=OUTCOME_COLUMNS)
    return out
