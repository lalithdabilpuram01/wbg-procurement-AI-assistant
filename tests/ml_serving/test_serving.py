"""Serving reproduces the frozen models exactly; features, lookups and refusals behave (offline, no Databricks).

    cd orchestrator && env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m pytest ../ml/serving/tests -q
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from ml.serving import features
from ml.serving.features import InvalidRequest, PackageRequest, ProjectContext
from ml.serving.gold import INCOME_SQL, PRIOR_TENDERS_SQL, PROJECT_SQL, project_context
from ml.serving.models import M1_DIR, M23_DIR, FrozenModelError, FrozenModels
from ml.serving.service import predict_package

HERE = Path(__file__).resolve().parent
MODELS = FrozenModels()
REQUEST = PackageRequest("P148850", "Request for Bids", "Works", "Prior", 60, dt.date(2026, 12, 1))


def fake_sql(project=True):
    def run(sql, parameters):
        if sql == PROJECT_SQL:
            return [{"project_id": "P148850", "region_name": "Eastern and Southern Africa", "wdi_country_code": "ET",
                     "board_approval_date": "2016-06-28", "approval_fiscal_year": 2016}] if project else []
        if sql == INCOME_SQL:
            return [{"year": 2024, "income_group_fy_starting_in_year": "L"}]
        if sql == PRIOR_TENDERS_SQL:
            return [{"prior_tender_notices": 34}]
        raise AssertionError(sql)
    return run


# ---------------------------------------------------------------------------------------- frozen reproduction
@pytest.mark.parametrize("case", json.loads((HERE / "m1_frozen_test_rows.json").read_text()))
def test_model1_reproduces_the_frozen_test_predictions(case):
    prediction, _ = MODELS.duration(case["features"])
    assert prediction.days == round(case["pred_ridge"])
    assert (prediction.p10_days, prediction.p90_days) == (round(case["lo_ridge"]), round(case["hi_ridge"]))
    assert prediction.probability_over_365 == pytest.approx(case["score_ridge"], abs=5e-4)
    assert prediction.long_tender_alert == case["alert_ridge"]


@pytest.mark.parametrize("m, predict", [("m2", MODELS.amendment), ("m3", MODELS.rebid)])
def test_models_2_and_3_reproduce_the_frozen_example_outputs(m, predict):
    for example in json.loads((M23_DIR / f"{m}_example_api_outputs.json").read_text()):
        prediction, _ = predict(example["inputs"])
        assert prediction.simulated_probability_pct == example["simulated_probability_pct"]
        if m == "m3":
            assert prediction.flag.startswith(example["risk_band"])
        assert "synthetic" in prediction.disclaimer.lower()


def test_a_changed_model_file_is_refused(tmp_path):
    copy = tmp_path / "frozen"
    shutil.copytree(M1_DIR, copy)
    (copy / "models" / "ridge_final.joblib").write_bytes(b"tampered")
    with pytest.raises(FrozenModelError, match="frozen hash"):
        FrozenModels(m1_dir=copy).duration({})


# ---------------------------------------------------------------------------------------------------- features
def test_fiscal_year_and_income_lookup_follow_the_training_rules():
    context = ProjectContext("P1", "R", "ET", "S", dt.date(2016, 1, 1), 2016, {2024: "L", 2025: "LM"})
    assert features.fiscal_year(dt.date(2026, 7, 1)) == 2027 and features.fiscal_year(dt.date(2026, 6, 30)) == 2026
    assert features.income_at(context, 2026) == (2.0, None)                        # FY2026 <- row year 2025
    value, note = features.income_at(context, 2028)
    assert value == 2.0 and "FY2026 used" in note


def test_deadline_status_matches_the_training_categories():
    assert features.deadline_status(0) == "equals_publication_date_no_window"
    assert features.deadline_status(60) == "positive_window"
    assert features.deadline_status(200) == "positive_window_over_180_days_review"


@pytest.mark.parametrize("bad", [PackageRequest("148850", "RFB", "Works", "Prior", 60, dt.date(2026, 1, 1)),
                                 PackageRequest("P148850", "RFB", "Works", "Prior", -1, dt.date(2026, 1, 1)),
                                 PackageRequest("P148850", " ", "Works", "Prior", 60, dt.date(2026, 1, 1))])
def test_invalid_requests_are_refused(bad):
    with pytest.raises(InvalidRequest):
        bad.validate()


# ----------------------------------------------------------------------------------------------------- service
def test_a_package_is_scored_by_all_three_models_with_its_looked_up_context():
    result = predict_package(REQUEST, fake_sql(), MODELS)
    assert result.context["sector"] == "Transportation" and result.context["prior_tender_notices"] == 34
    assert result.duration.p10_days < result.duration.days < result.duration.p90_days
    assert result.estimated_signing_date == REQUEST.tender_date + dt.timedelta(days=result.duration.days)
    assert "SYNTHETIC" in result.amendment.model and "SYNTHETIC" in result.rebid.model
    assert any("FY2025 used" in note for note in result.notes)                      # income published only to FY2025


def test_an_unknown_method_is_scored_but_reported():
    request = PackageRequest("P148850", "Brand New Method", "Works", "Prior", 60, dt.date(2026, 12, 1))
    result = predict_package(request, fake_sql(), MODELS)
    assert any("Brand New Method" in note for note in result.notes)


def test_a_project_outside_the_portfolio_is_refused():
    with pytest.raises(InvalidRequest, match="not in the project portfolio"):
        project_context("P999999", dt.date(2026, 1, 1), fake_sql(project=False))
