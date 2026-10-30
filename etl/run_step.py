"""One task of the Lakeflow ETL job: `python etl/run_step.py <step> --catalog <catalog> --run-id <job run id>`.

Steps, in job order:
  bronze             stage the uploaded files (cohort cut, manifests) and rebuild the 17 Bronze tables
  silver_<name>      the existing Silver job (silver/<name>/run_<name>_silver.py) in pipeline mode
  gold_<name>        the existing Gold job (gold/<name>/run_<name>_gold.py) in pipeline mode
  kpi_views          create or replace the seven vw_kpi_* views
  audit              collect every check of this run into <catalog>.etl.dq_results and snapshot the tables

Pipeline mode (silver/common/pipeline.py) is switched on here, before any Silver or Gold module is imported.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import sys
from pathlib import Path

# Databricks runs this file through exec() without __file__; the compiled code still knows its path.
ROOT = Path(inspect.currentframe().f_code.co_filename).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SILVER = ("wdi", "income_class", "wgi", "cpia", "projects", "contracts", "notices", "ieg", "financing", "project_details")
GOLD = ("project_portfolio", "contract_analytics", "procurement_notices", "country_context", "financing_analytics")
STEPS = ("bronze", *(f"silver_{s}" for s in SILVER), *(f"gold_{g}" for g in GOLD), "kpi_views", "audit")


def volume(catalog: str) -> Path:
    return Path(f"/Volumes/{catalog}/etl")


def runs_folder(catalog: str, run_id: str) -> Path:
    return volume(catalog) / "runs" / run_id


def configure(catalog: str, run_id: str) -> None:
    """Pipeline mode for this process: catalog, run folder and the manifests the bronze step wrote."""
    runs = runs_folder(catalog, run_id)
    manifests = runs / "manifests"
    os.environ.update({
        "WBG_PIPELINE": "1", "WBG_CATALOG": catalog, "WBG_RUNS_DIR": str(runs),
        "WBG_BRONZE_INPUTS_MANIFEST": str(manifests / "bronze_inputs_manifest.json"),
        "WBG_SOURCE_PULLS_MANIFEST": str(manifests / "source_pulls.jsonl"),
        "WBG_REF_BRONZE_MANIFEST": str(manifests / "ref_bronze_inputs_manifest.json"),
    })


def run_bronze(catalog: str, run_id: str) -> int:
    from etl import bronze
    from etl.stage import stage_all
    from silver.common.session import get_spark

    staged = stage_all(volume(catalog) / "landing", volume(catalog) / "staged")
    staged.write_manifests(runs_folder(catalog, run_id) / "manifests")
    records = {f"wbg_bronze.{f['bronze_table']}": f["records_selected"] for f in staged.bronze["files"]}
    for f in staged.ref["files"]:
        if f["records"] and f["bronze_table"] in ("wgi_estimates", "cpia_ratings", "wdi_indicator_observations"):
            key = f"wbg_ref_bronze.{f['bronze_table']}"
            records[key] = records.get(key, 0) + int(f["records"])
    rep = bronze.load(get_spark(), catalog, staged.files, records, run_id)
    return report(rep, runs_folder(catalog, run_id) / "etl" / "bronze")


def run_job(module: str) -> int:
    """An existing Silver/Gold job, unchanged, in write mode (pipeline mode makes the write a replace)."""
    sys.argv = [module, "--mode", "write", "--confirm-create"]
    return importlib.import_module(module).main()


def report(rep, folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dq_report.json").write_text(json.dumps({"target": rep.target, "dq": rep.to_dict()}, indent=1, default=str))
    print(rep.render())
    return 0 if rep.passed else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=STEPS)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--run-id", required=True, help="the job run id ({{job.run_id}}): one run folder for all tasks")
    args = ap.parse_args(argv)
    configure(args.catalog, args.run_id)
    if args.step == "bronze":
        return run_bronze(args.catalog, args.run_id)
    if args.step.startswith("silver_"):
        name = args.step.removeprefix("silver_")
        return run_job(f"silver.{name}.run_{name}_silver")
    if args.step.startswith("gold_"):
        name = args.step.removeprefix("gold_")
        return run_job(f"gold.{name}.run_{name}_gold")
    from etl import audit, kpi_views
    folder = runs_folder(args.catalog, args.run_id)
    return kpi_views.run(args.catalog, folder) if args.step == "kpi_views" else audit.run(args.catalog, args.run_id, folder)


if __name__ == "__main__":
    code = main(sys.argv[1:])
    if code:
        raise SystemExit(code)
