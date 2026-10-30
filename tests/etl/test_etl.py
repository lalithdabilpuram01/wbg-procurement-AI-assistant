"""The ETL job offline (no Spark, no Databricks): staging and manifests, pipeline mode, and that the job, the Bronze
registry and the Silver/Gold code agree with each other."""
from __future__ import annotations

import csv
import gzip
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from etl import run_step
from etl.datasets import BRONZE_TABLES, DATASETS, REFERENCE, series_code
from etl.stage import MissingUpload, Originals, stage_all

REPO = Path(__file__).resolve().parents[2]
COHORT_IDS = [f"P{i:06d}" for i in range(1, 2001)]


# ------------------------------------------------------------------------------------------------ fixtures
@pytest.fixture
def cohort(tmp_path) -> Path:
    path = tmp_path / "cohort.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["project_id", "project_name"])
        w.writerows([pid, f"Project {pid}"] for pid in COHORT_IDS)
    return path


def write_landing(root: Path) -> Path:
    """One small file per dataset folder, shaped like the World Bank downloads."""
    land = root / "landing"
    csv_body = '﻿Project ID,Contract Description,Amount\nP000001,"Road works\nlot 1",10\nP999999,Other,5\nP000002,"Say ""hi""",\n'
    for d in DATASETS:
        (land / d.folder).mkdir(parents=True)
    for d in DATASETS:
        folder = land / d.folder
        if d.kind == "csv":
            (folder / f"{d.folder}.csv").write_text(csv_body.replace("Project ID", d.key), encoding="utf-8", newline="")
        elif d.kind == "jsonl":
            lines = [json.dumps({d.key: "P000001", "x": "a\u2028b"}), json.dumps({d.key: "P999999"}), json.dumps({d.key: " P000002"})]
            (folder / f"{d.folder}.jsonl.gz").write_bytes(gzip.compress(("\n".join(lines) + "\n").encode()))
        elif d.kind == "series":
            name = "GOV_WGI_CC.EST.jsonl.gz" if d.folder == "wgi_estimates" else "IQ.CPA.IRAI.XQ.jsonl.gz"
            (folder / name).write_bytes(gzip.compress(b'{"value": 1}\n\n{"value": null}\n'))
        elif d.kind == "wdi_observations":
            (folder / "observations_NY.GDP.PCAP.CD_2000-2025.json").write_text(json.dumps([{"lastupdated": "2026-10-08"}, [{"value": 1}] * 3]))
        elif d.kind == "wdi_indicator":
            (folder / "indicator_NY.GDP.PCAP.CD.json").write_text(json.dumps([{"page": 1}, [{"id": "NY.GDP.PCAP.CD"}]]))
        elif d.kind == "json_response":
            (folder / f"{d.folder}.json").write_text(json.dumps([{"page": 1}, [{"id": "a"}, {"id": "b"}]]))
        else:
            (folder / "OGHIST.xlsx").write_bytes(b"PK\x03\x04 not really a workbook")
    return land


NO_ORIGINALS = Originals(phase1_names={"bronze_projects_v3": "projects_v3_population.jsonl.gz"}, pulls={}, ref={})


# ------------------------------------------------------------------------------------------------- staging
def test_staging_cuts_procurement_files_to_the_cohort_byte_for_byte(tmp_path, cohort):
    staged = stage_all(write_landing(tmp_path), tmp_path / "staged", cohort, NO_ORIGINALS)
    contracts = staged.files["contract_awards_ds00005"][0]
    assert contracts.name == "contract_awards_ds00005__cohort2000.csv"
    text = contracts.read_text(encoding="utf-8")
    assert text.startswith("﻿Project ID") and '"Road works\nlot 1"' in text and "P999999" not in text and '"Say ""hi"""' in text
    lines = staged.files["projects_v3"][0].read_text(encoding="utf-8").splitlines()
    assert lines == [json.dumps({"id": "P000001", "x": "a\u2028b"})]                     # " P000002" is not trimmed into the cohort
    entry = next(f for f in staged.bronze["files"] if f["bronze_table"] == "bronze_contract_awards_ds00005")
    assert (entry["records_selected"], entry["source_records"], entry["coverage"]) == (2, 3, "complete")


def test_manifests_are_in_the_formats_the_silver_jobs_read(tmp_path, cohort):
    staged = stage_all(write_landing(tmp_path), tmp_path / "staged", cohort, NO_ORIGINALS)
    paths = staged.write_manifests(tmp_path / "manifests")
    bronze = json.loads(paths["bronze"].read_text())
    assert {f["bronze_table"] for f in bronze["files"]} == {t.split(".")[1] for t in BRONZE_TABLES if t.startswith("wbg_bronze.")}
    assert all({"output_file", "output_sha256", "records_selected", "source_sha256", "source_file", "extracted_at_utc"} <= f.keys()
               for f in bronze["files"])
    pulls = paths["pulls"].read_text()
    for f in bronze["files"]:                                                           # each Silver job finds its line by file name
        assert Path(f["source_file"]).name in pulls
    assert "projects_v3_population" in pulls.splitlines()[0]                            # the projects job looks up the Phase 1 name
    ref = {f["bronze_table"]: f for f in json.loads(paths["ref"].read_text())["files"]}
    assert ref["wgi_estimates"]["records"] == "2" and ref["wgi_estimates"]["format"] == "jsonl.gz"
    assert ref["wdi_indicator_observations"]["source_lastupdated"] == "2026-10-08" and ref["wdi_indicator_observations"]["records"] == "3"
    assert ref["wgi_estimates"]["source_url"].endswith("GOV_WGI_CC.EST?source=3&format=json&per_page=20000")


def test_a_byte_identical_phase1_file_keeps_its_original_provenance(tmp_path, cohort):
    land = write_landing(tmp_path)
    wgi = next((land / "wgi_estimates").glob("*"))
    from etl.stage import sha256
    original = {"source_url": "https://api.worldbank.org/original", "retrieved_utc": "2026-10-21T01:20:09+00:00",
                "source_lastupdated": "2026-09-25", "acquisition": "R3 reuse (Phase 1, unchanged)"}
    staged = stage_all(land, tmp_path / "staged", cohort, Originals(NO_ORIGINALS.phase1_names, {}, {sha256(wgi): original}))
    entry = next(f for f in staged.ref["files"] if f["bronze_table"] == "wgi_estimates")
    assert {k: entry[k] for k in original} == original


def test_every_empty_folder_is_named(tmp_path, cohort):
    land = write_landing(tmp_path)
    for f in (land / "cpia_ratings").glob("*"):
        f.unlink()
    with pytest.raises(MissingUpload, match="cpia_ratings"):
        stage_all(land, tmp_path / "staged", cohort, NO_ORIGINALS)


def add_file(folder: Path, name: str, text: str, when: int = 2_000_000_000) -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8", newline="")
    os.utime(path, (when, when))
    return path


def test_a_new_file_adds_its_rows_to_the_dataset_in_one_staged_file(tmp_path, cohort):
    land = write_landing(tmp_path)
    add_file(land / "ieg_ratings_ds00053", "ieg_new_rows.csv", "Project ID,Contract Description,Amount\nP000003,New,7\nP999998,Out,1\n")
    staged = stage_all(land, tmp_path / "staged", cohort, NO_ORIGINALS)
    [extract] = staged.files["ieg_ratings_ds00053"]
    assert extract.name == "ieg_ratings_ds00053__cohort2000.csv"                        # the first upload names the extract
    text = extract.read_text(encoding="utf-8")
    assert text.count("Project ID") == 1 and "P000003,New,7" in text and "P999998" not in text
    entry = next(f for f in staged.bronze["files"] if f["bronze_table"] == "bronze_ieg_ratings_ds00053")
    assert entry["records_selected"] == 3 and [Path(f["file"]).name for f in entry["source_files"]][-1] == "ieg_new_rows.csv"
    assert Path(entry["source_file"]).name == "ieg_new_rows.csv" and "ieg_new_rows.csv" in json.dumps(staged.pulls)


def test_a_file_with_other_columns_is_refused(tmp_path, cohort):
    land = write_landing(tmp_path)
    add_file(land / "ieg_ratings_ds00053", "ieg_other.csv", "Project ID,Rating\nP000003,S\n")
    with pytest.raises(MissingUpload, match="different columns"):
        stage_all(land, tmp_path / "staged", cohort, NO_ORIGINALS)


def test_a_newer_single_file_reference_source_replaces_the_old_one(tmp_path, cohort):
    land = write_landing(tmp_path)
    add_file(land / "wdi_country_metadata", "countries_new.json", json.dumps([{"page": 1}, [{"id": "a"}]]))
    staged = stage_all(land, tmp_path / "staged", cohort, NO_ORIGINALS)
    assert [p.name for p in staged.files["wdi_country_metadata"]] == ["countries_new.json"]


def test_series_codes_follow_the_phase1_file_names():
    assert series_code("wgi_estimates", "GOV_WGI_GE.EST.jsonl.gz") == "GE.EST"
    assert series_code("cpia_ratings", "IQ.CPA.FINQ.XQ.jsonl.gz") == "IQ.CPA.FINQ.XQ"
    assert series_code("wdi_indicator_observations", "observations_FP.CPI.TOTL.ZG_2000-2025.json") == "FP.CPI.TOTL.ZG"
    assert series_code("wdi_indicator_metadata", "indicator_NY.GDP.MKTP.KD.ZG.json") == "NY.GDP.MKTP.KD.ZG"


# ------------------------------------------------------------------------------------------- pipeline mode
PIPELINE_VARS = ("WBG_PIPELINE", "WBG_CATALOG", "WBG_RUNS_DIR", "WBG_BRONZE_INPUTS_MANIFEST", "WBG_SOURCE_PULLS_MANIFEST",
                 "WBG_REF_BRONZE_MANIFEST")


def _python(code: str, **env) -> str:
    """Run code in a fresh interpreter: pipeline mode is read once, at import."""
    base = {k: v for k, v in os.environ.items() if k not in PIPELINE_VARS}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=REPO, env={**base, "PYTHONPATH": str(REPO), **env},
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_outside_the_job_nothing_changes():
    code = ("from silver.common import pipeline as p; from silver.projects import transform as T; from gold.procurement_kpis import kpi_queries as Q;"
            "print(p.ENABLED, p.relaxed('bronze_rows'), T.BRONZE_TABLE, Q.VIEW_SCHEMA, T.BRONZE_INPUTS_MANIFEST.name)")
    assert _python(code).split() == [
        "False", "False", "workspace.wbg_bronze.bronze_projects_v3", "workspace.wbg_gold", "bronze_inputs_manifest.json"]


def test_in_the_job_only_snapshot_checks_are_relaxed_and_names_follow_the_catalog():
    code = ("from silver.common import pipeline as p; from silver.common.dq import DQReport; from silver.projects import transform as T;"
            "r = DQReport('t'); r.equals('bronze_rows', 1999, 2000); r.equals('project_id_unique', 1, 2);"
            "print(T.BRONZE_TABLE, [x.severity for x in r.results], r.passed, p.runs_dir(T.REPO / 'silver' / 'runs'))")
    out = _python(code, WBG_PIPELINE="1", WBG_CATALOG="wbg_etl", WBG_RUNS_DIR="/Volumes/wbg_etl/etl/runs/42")
    assert out == "wbg_etl.wbg_bronze.bronze_projects_v3 ['warn', 'error'] False /Volumes/wbg_etl/etl/runs/42/silver"


@pytest.mark.parametrize("name", ["target_absent_before_write", "rows_equal_silver_243533", "lines:source_as_of_date_values",
                                  "baseline_table_counts (Bronze, Silver, Gold)", "geography_counts"])
def test_snapshot_checks_match_the_relaxed_list(name):
    from silver.common.pipeline import SNAPSHOT_CHECKS
    assert SNAPSHOT_CHECKS.search(name)


@pytest.mark.parametrize("name", ["project_id_unique", "lineage_nulls", "silver_rows_equal_bronze", "rows_equal_manifest",
                                  "source_file_sha256_matches_manifest", "d14:no_unmasked_text", "unchanged:column_masks"])
def test_structural_checks_still_block(name):
    from silver.common.pipeline import SNAPSHOT_CHECKS
    assert not SNAPSHOT_CHECKS.search(name)


# ------------------------------------------------------------------------------------------------ wiring
def test_the_job_runs_every_step_in_order():
    job = yaml.safe_load((REPO / "resources" / "etl_job.yml").read_text())["resources"]["jobs"]["wbg_procurement_etl"]
    tasks = job["tasks"]
    assert [t["task_key"] for t in tasks] == list(run_step.STEPS)
    assert all(t["spark_python_task"]["parameters"][0] == t["task_key"] for t in tasks)
    assert all(t["depends_on"] == [{"task_key": prev["task_key"]}] for prev, t in zip(tasks, tasks[1:]))
    assert "trigger" not in job and job["queue"]["enabled"]                       # started by etl/watch.py


def test_the_bronze_registry_covers_every_bronze_table_the_silver_and_gold_code_reads():
    read = set()
    for f in list((REPO / "silver").glob("*/*.py")) + list((REPO / "gold").glob("*/*.py")):
        read |= set(re.findall(r"\{CATALOG\}\.(wbg_(?:ref_)?bronze\.[a-z0-9_]+)", f.read_text()))
        runner = f.parent / f"run_{f.parent.name}_silver.py"
        if f.name == "transform.py" and "wbg_ref_bronze" in f.read_text() and runner.exists():
            read |= {f"wbg_ref_bronze.{t}" for t in re.findall(r"\{b\}\.([a-z_]+)", runner.read_text())}
    assert read <= set(BRONZE_TABLES)
    assert len(BRONZE_TABLES) == len(set(BRONZE_TABLES)) == 17


def test_every_runner_the_job_calls_exists():
    for step in run_step.STEPS:
        layer, _, name = step.partition("_")
        if layer in ("silver", "gold"):
            assert (REPO / layer / name / f"run_{name}_{layer}.py").exists(), step
    assert {d.folder for d in REFERENCE} >= {"wgi_estimates", "cpia_ratings", "income_classification_history"}
