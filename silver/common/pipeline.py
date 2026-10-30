"""Pipeline mode: the same Silver and Gold jobs, run by the Lakeflow ETL job (etl/) on newly uploaded files.

Nothing changes unless WBG_PIPELINE=1. Run from a laptop, every job keeps its original create-only behaviour, its
`workspace` catalog, its local run folders and its local manifests. In pipeline mode:

  * tables live in the catalog named by WBG_CATALOG (the demo's `workspace` tables are never touched);
  * targets are replaced (CREATE OR REPLACE keeps the Delta history) instead of refused;
  * run reports go to WBG_RUNS_DIR (a Volume folder per job run);
  * file provenance comes from the manifests the Bronze step writes for the uploaded files;
  * checks that pin today's snapshot (row counts, totals, source dates, "target absent") are reported as warnings,
    so new data can flow; every structural rule (keys, nulls, links, masks, parity) still blocks the write.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ENABLED = os.environ.get("WBG_PIPELINE") == "1"
CATALOG = os.environ.get("WBG_CATALOG", "workspace")

# Checks whose expected value is the October 2026 snapshot (or the first-run state), matched on the check name.
SNAPSHOT_CHECKS = re.compile("|".join([
    r"as_of_date_values", r"end_of_period_values", r"source_lastupdated_values", r"rows_equal_expected", r"^bronze_rows$",
    r"bronze_ieg_rows", r"geography_counts", r"details:in_ds00979", r"theme_elements_per_level",
    r"references_matching_financing_tables", r"unmatched_references_preserved", r"code_name_mapping_consistent",
    r"rows_equal_silver_\d+", r"amount:total_equals", r"all_contract_projects_in_cohort", r"rows_\d+_economies",
    r"years_2000_2025", r"economies_not_in_source", r"projects:in_cohort_and_count", r"projects:all_in_cohort",
    r"indicator_geo_transaction_sums", r"null_counts_where_array_unpublished", r"regional_projects_no_country_context",
    r"baseline_table_counts", r"baseline_covers", r"target_absent_before_write", r"mask_function_absent_before_write",
    r"access_control_probe",
]))


def relaxed(check_name: str) -> bool:
    """True when a failing "error" check only pins the snapshot and pipeline mode reports it instead of blocking."""
    return ENABLED and bool(SNAPSHOT_CHECKS.search(check_name))


def runs_dir(default: Path) -> Path:
    """Where a job writes its run folders: the job run's Volume folder in pipeline mode, else the local default."""
    override = os.environ.get("WBG_RUNS_DIR")
    return Path(override) / default.parent.name if override else default


def manifest(env_var: str, default: Path) -> Path:
    """A provenance manifest: the one the Bronze step wrote for the uploaded files, else the original local one."""
    override = os.environ.get(env_var)
    return Path(override) if override else default
