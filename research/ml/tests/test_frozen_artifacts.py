"""The three frozen Module 2 models are unchanged: every artefact matches the SHA-256 recorded when it was frozen.

Read-only: no model is loaded, refit or scored, and no test set is read.
Model 1 (tender time) lives in ml/final/frozen; Models 2 and 3 (amendment, re-bid) in ml/final23/frozen.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ML = Path(__file__).resolve().parents[1]
MODEL_1 = ML / "final"
MODELS_2_3 = ML / "final23"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def frozen_record(folder: Path) -> dict:
    return json.loads((folder / "frozen" / "FROZEN.json").read_text())


def model_1_expectations() -> list[tuple[str, str]]:
    record = frozen_record(MODEL_1)
    expected = [(f"final/frozen/models/{name}", digest) for name, digest in record["models_sha256"].items()]
    return expected + [("final/frozen/test_predictions_frozen.csv", record["test_predictions_sha256"]),
                       ("final/frozen/refit_details.json", record["refit_details_sha256"]),
                       ("final/FINAL_EVAL_CONFIG.json", record["config_sha256"]),
                       ("final/final_refit.py", record["refit_py_sha256"])]


def models_2_3_expectations() -> list[tuple[str, str]]:
    record = frozen_record(MODELS_2_3)
    expected = [(f"final23/frozen/{name}", digest) for name, digest in record["artefacts_sha256"].items()]
    return expected + [("final23/FINAL_EVAL_CONFIG_M23.json", record["config_sha256"]),
                       ("final23/common23.py", record["common23_sha256"]),
                       ("final23/final_refit23.py", record["refit_script_sha256"])]


@pytest.mark.parametrize("relative, digest", model_1_expectations() + models_2_3_expectations(), ids=lambda v: v[:60])
def test_frozen_artefact_matches_its_recorded_checksum(relative, digest):
    assert sha256(ML / relative) == digest, f"frozen artefact changed: ml/{relative}"


def test_model_1_freeze_record_is_the_one_that_was_scored():
    scored = json.loads((MODEL_1 / "frozen" / "TEST_SCORED.json").read_text())
    assert sha256(MODEL_1 / "frozen" / "FROZEN.json") == scored["frozen_sha256"]


def test_all_three_models_have_frozen_estimators():
    assert len(frozen_record(MODEL_1)["models_sha256"]) == 3                      # Model 1 candidates (b1, rf, ridge)
    names = [n for n in frozen_record(MODELS_2_3)["artefacts_sha256"] if n.endswith(".joblib")]
    assert {n.split("/")[1][:2] for n in names} == {"m2", "m3"}
