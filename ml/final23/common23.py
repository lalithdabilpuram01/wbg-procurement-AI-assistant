"""Shared pieces for the Model 2 / 3 finalisation (feature policies, pipelines, explanation helpers)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

ML = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML / "stage3a"))
sys.path.insert(0, str(ML / "phase1"))

DISCLAIMER = "SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY"
FEATURES = {"m2": ML / "features" / "m2_contract_features_v1.parquet", "m3": ML / "features" / "m3_tender_features_v1.parquet"}
LABELS = {"m2": ML / "synthetic" / "m2_sim_v1" / "labels" / "labels.parquet", "m3": ML / "synthetic" / "m3_sim_v1" / "labels" / "labels.parquet"}

# feature policies
POLICY = {
    "m2_v1": {"cat": ["procurement_method", "procurement_category", "review_type", "region_name", "country_grp", "sector"],
              "log": [], "num": ["days_from_approval", "signed_before_approval", "approval_fiscal_year", "income_ordinal"],
              "winsor": [], "min_frequency": 50},
    "m3_v1_stage3a": {"cat": ["notice_type", "procurement_method", "procurement_category", "region_name", "country_grp", "sector", "deadline_status"],
                      "log": ["window_days"], "num": ["has_positive_window", "project_age_days", "income_ordinal"], "winsor": [], "min_frequency": 50},
    # cleaned: no notice_type; rare levels grouped by a training-only support rule; extreme windows / ages winsorised
    "m3_v2_clean": {"cat": ["procurement_method", "procurement_category", "region_name", "country_grp", "sector", "deadline_status"],
                    "log": ["window_days"], "num": ["has_positive_window", "project_age_days", "income_ordinal"],
                    "winsor": ["window_days", "project_age_days"], "min_frequency": "ceil(20 / training prevalence)"},
}
READABLE = {"procurement_method": "Procurement method", "procurement_category": "Procurement category", "review_type": "Review type",
            "region_name": "Region", "country_grp": "Country", "sector": "Project sector", "deadline_status": "Bid-window status",
            "window_days": "Published bid window", "has_positive_window": "Bid window published", "project_age_days": "Project age at publication",
            "income_ordinal": "Country income group", "days_from_approval": "Time since project approval", "signed_before_approval": "Signed before approval",
            "approval_fiscal_year": "Project approval year", "notice_type": "Notice type"}


def min_freq(policy: dict, y_train: np.ndarray) -> int:
    v = policy["min_frequency"]
    return int(v) if isinstance(v, int) else int(math.ceil(20 / max(float(np.mean(y_train)), 1e-6)))


class Winsorizer(BaseEstimator, TransformerMixin):
    """Clip each column to its training 1st / 99th percentile (fitted on training data only)."""

    def fit(self, X, y=None):
        a = np.asarray(X, dtype=float)
        self.lo_, self.hi_ = np.nanquantile(a, 0.01, axis=0), np.nanquantile(a, 0.99, axis=0)
        return self

    def transform(self, X):
        return np.clip(np.asarray(X, dtype=float), self.lo_, self.hi_)

    def get_feature_names_out(self, input_features=None):
        return np.asarray(input_features)


def pipeline(policy: dict, mf: int, C: float | None = 1.0, drop_first: bool = False) -> Pipeline:
    def numeric(cols, log):
        steps = [("imp", SimpleImputer(strategy="median"))]
        if any(c in policy["winsor"] for c in cols):
            steps.append(("win", Winsorizer()))
        if log:
            steps.append(("log1p", FunctionTransformer(np.log1p, feature_names_out="one-to-one")))
        steps.append(("sc", StandardScaler()))
        return Pipeline(steps)
    tr = [("cat", OneHotEncoder(min_frequency=mf, handle_unknown="infrequent_if_exist", drop="first" if drop_first else None, sparse_output=False), policy["cat"])]
    if policy["log"]:
        tr.append(("log", numeric(policy["log"], True), policy["log"]))
    w = [c for c in policy["num"] if c in policy["winsor"]]
    nw = [c for c in policy["num"] if c not in policy["winsor"]]
    if w:
        tr.append(("numw", numeric(w, False), w))
    if nw:
        tr.append(("num", Pipeline([("imp", SimpleImputer(strategy="median")), ("sc", StandardScaler())]), nw))
    clf = LogisticRegression(penalty=None, max_iter=5000) if C is None else LogisticRegression(C=C, max_iter=5000)
    return Pipeline([("pre", ColumnTransformer(tr, verbose_feature_names_out=False)), ("model", clf)])


def original_feature(design_col: str, policy: dict) -> str:
    for c in policy["cat"] + policy["log"] + policy["num"]:
        if design_col == c or design_col.startswith(c + "_"):
            return c
    return design_col


def contributions(pipe: Pipeline, X: pd.DataFrame, policy: dict, unstable: set[str] | None = None) -> pd.DataFrame:
    """Per-unit log-odds contributions aggregated to original features; unstable design columns are pooled as 'other'."""
    pre, coef = pipe.named_steps["pre"], pipe.named_steps["model"].coef_[0]
    names = pre.get_feature_names_out()
    Z = pre.transform(X) * coef
    keys = [("other (low-stability levels)" if unstable and n in unstable else original_feature(n, policy)) for n in names]
    return pd.DataFrame(Z, columns=names, index=X.index).T.groupby(keys).sum().T
