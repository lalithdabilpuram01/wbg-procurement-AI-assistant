"""The frozen Models 1–3, loaded read-only after SHA-256 checks against their FROZEN.json records.

Model 1 (real data): Ridge on log(days) -> point estimate, P10–P90 range, P(duration > 365 days) and the frozen
alert threshold (ml/final/final_refit.py). Models 2 and 3 (SYNTHETIC outcomes): L2 logistic -> simulated probability,
with the frozen review threshold (Model 2) or risk bands (Model 3) (ml/final23/final_refit23.py).
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ML = Path(__file__).resolve().parents[1]
M1_DIR, M23_DIR = ML / "final" / "frozen", ML / "final23" / "frozen"
LONG_DAYS = 365
SYNTHETIC_DISCLAIMER = "Trained on synthetic data – not validated World Bank risk accuracy"
READABLE = {"procurement_method": "Procurement method", "procurement_category": "Procurement category",
            "review_type": "Review type", "region_name": "Region", "country_grp": "Country", "sector": "Project sector",
            "api_window_days": "Bid window", "window_days": "Bid window", "deadline_status": "Bid window",
            "has_positive_window": "Bid window", "project_prior_tender_notices": "Earlier tenders in the project",
            "project_age_days_at_tender": "Project age at tender", "project_age_days": "Project age at tender",
            "approval_fiscal_year": "Project approval year", "income_ordinal": "Country income group",
            "days_from_approval": "Time since project approval", "signed_before_approval": "Signed before approval"}
HIDDEN = {"country_grp"}          # country dummies are unstable (few contracts per country): not shown as drivers


class FrozenModelError(RuntimeError):
    """A frozen artefact is missing or differs from its recorded hash."""


@dataclass(frozen=True)
class Driver:
    label: str
    effect: str                   # e.g. "+35% longer" or "raises the simulated risk"


@dataclass(frozen=True)
class DurationPrediction:
    """Model 1 output: estimated days, P10-P90 range, probability of over 365 days, alert flag and top drivers."""
    days: int
    p10_days: int
    p90_days: int
    probability_over_365: float
    long_tender_alert: bool
    drivers: tuple[Driver, ...]
    model: str = "Model 1 – tender duration (Ridge, real data; test MAE ≈ 77 days)"


@dataclass(frozen=True)
class RiskPrediction:
    """Model 2 or 3 output: simulated probability, flag or band, the drivers that raise it, and the disclaimer."""
    model: str
    simulated_probability_pct: float
    flag: str                     # Model 2: "on review list" / "not on review list"; Model 3: Low / Medium / High
    drivers: tuple[Driver, ...]
    disclaimer: str = SYNTHETIC_DISCLAIMER
    notes: tuple[str, ...] = field(default_factory=tuple)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _checked(path: Path, expected: str) -> Path:
    """Return the path when the file exists and matches its frozen SHA-256; raise FrozenModelError otherwise."""
    if not path.is_file() or _sha256(path) != expected:
        raise FrozenModelError(f"{path.parent.name}/{path.name} is missing or differs from its frozen hash")
    return path


def unknown_values(pipeline, row: pd.DataFrame) -> list[str]:
    """Categorical inputs the model never saw (scored as 'infrequent'): reported, not silently absorbed."""
    encoder = pipeline.named_steps["pre"].named_transformers_["cat"]
    return [f"{READABLE.get(c, c)} '{row.iloc[0][c]}' was not in the training data; treated as a rare value"
            for c, cats in zip(encoder.feature_names_in_, encoder.categories_) if row.iloc[0][c] not in set(cats)]


def contributions(pipeline, row: pd.DataFrame) -> dict[str, float]:
    """Linear contribution of each original feature (design columns summed), on the model's own scale."""
    pre, coef = pipeline.named_steps["pre"], np.ravel(pipeline.named_steps["model"].coef_)
    values = pre.transform(row)[0] * coef
    names = list(pre.get_feature_names_out())
    originals = list(pre.feature_names_in_)
    totals: dict[str, float] = {}
    for name, value in zip(names, values):
        original = next((o for o in originals if name == o or name.startswith(o + "_")), name)
        label = READABLE.get(original, original)
        if original not in HIDDEN:
            totals[label] = totals.get(label, 0.0) + float(value)
    return totals


class FrozenModels:
    """Loads lazily; every artefact is hash-checked before use."""

    def __init__(self, m1_dir: Path = M1_DIR, m23_dir: Path = M23_DIR):
        self._m1_dir, self._m23_dir = m1_dir, m23_dir

    @cached_property
    def _m1(self) -> tuple:
        """Model 1 pipeline and its refit details, hash-checked on first use."""
        frozen = json.loads((self._m1_dir / "FROZEN.json").read_text())
        model = joblib.load(_checked(self._m1_dir / "models" / "ridge_final.joblib", frozen["models_sha256"]["ridge_final.joblib"]))
        details = json.loads(_checked(self._m1_dir / "refit_details.json", frozen["refit_details_sha256"]).read_text())["ridge"]
        return model, details

    @cached_property
    def _m23(self) -> dict:
        """Models 2 and 3 (scenario S2) with their refit details, hash-checked on first use."""
        frozen = json.loads((self._m23_dir / "FROZEN.json").read_text())["artefacts_sha256"]
        final23 = str(ML / "final23")
        if final23 not in sys.path:                     # the pickles reference common23 (Winsorizer)
            sys.path.insert(0, final23)
        details = json.loads(_checked(self._m23_dir / "refit_details.json", frozen["refit_details.json"]).read_text())
        out = {}
        for m in ("m2", "m3"):
            name = f"models/{m}_S2_l2_final.joblib"
            out[m] = (joblib.load(_checked(self._m23_dir / name, frozen[name])), details[m]["scenarios"]["S2"])
        return out

    def duration(self, features: dict) -> tuple[DurationPrediction, list[str]]:
        """Score Model 1 for one feature row; returns the prediction and notes about categories never seen in training.
        P(duration > 365 days) assumes normal residuals on the log scale, with the out-of-fold residual SD."""
        model, details = self._m1
        row = pd.DataFrame([features])[list(model.named_steps["pre"].feature_names_in_)]
        log_days = float(model.predict(row)[0])
        q10, q90 = details["interval_log_quantiles"]
        sigma = details["oof_log_residual_sd"]
        probability = 1 - 0.5 * (1 + math.erf((math.log(LONG_DAYS) - log_days) / (sigma * math.sqrt(2))))
        drivers = sorted(contributions(model, row).items(), key=lambda kv: -abs(kv[1]))[:3]
        prediction = DurationPrediction(
            days=round(math.exp(log_days)), p10_days=round(math.exp(log_days + q10)), p90_days=round(math.exp(log_days + q90)),
            probability_over_365=round(probability, 3), long_tender_alert=probability >= details["alert_threshold"]["t"],
            drivers=tuple(Driver(label, f"{math.exp(v) - 1:+.0%} {'longer' if v > 0 else 'shorter'} than average")
                          for label, v in drivers))
        return prediction, unknown_values(model, row)

    def _risk(self, m: str, features: dict) -> tuple[float, list[tuple[str, float]], list[str], dict]:
        """Score Model 2 or 3: (probability, top three risk-raising features, unseen-category notes, refit details)."""
        artefact, details = self._m23[m]
        pipeline = artefact["pipeline"]
        row = pd.DataFrame([features])[list(pipeline.named_steps["pre"].feature_names_in_)]
        probability = float(pipeline.predict_proba(row)[0, 1])
        raising = sorted(((k, v) for k, v in contributions(pipeline, row).items() if v > 0), key=lambda kv: -kv[1])[:3]
        return probability, raising, unknown_values(pipeline, row), details

    def amendment(self, features: dict) -> tuple[RiskPrediction, list[str]]:
        """Score Model 2 and put the package on the review list when the probability reaches the frozen threshold."""
        probability, raising, unknown, details = self._risk("m2", features)
        flag = "on the demonstration review list" if probability >= details["threshold"]["t"] else "not on the review list"
        return RiskPrediction("Model 2 – contract amendment risk (L2 logistic, SYNTHETIC outcomes)", round(100 * probability, 1),
                              flag, tuple(Driver(k, "raises the simulated risk") for k, _ in raising)), unknown

    def rebid(self, features: dict) -> tuple[RiskPrediction, list[str]]:
        """Score Model 3 and place the probability in the frozen Low / Medium / High bands."""
        probability, raising, unknown, details = self._risk("m3", features)
        bands = details["risk_bands"]
        band = "High" if probability >= bands["high_cutoff_q90"] else "Medium" if probability >= bands["medium_cutoff_q70"] else "Low"
        return RiskPrediction("Model 3 – re-bidding risk (L2 logistic, SYNTHETIC outcomes)", round(100 * probability, 1),
                              f"{band} (relative to the simulated population)",
                              tuple(Driver(k, "raises the simulated risk") for k, _ in raising)), unknown
