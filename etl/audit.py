"""Last task of the ETL job: one place to see what each run did and what changed.

  <catalog>.etl.dq_results       every check of every step of this run (severity, passed, observed, expected)
  <catalog>.etl.table_snapshots  rows and Delta version of every Bronze, Silver and Gold table after this run

Both tables are appended to, so runs can be compared over time. The audit fails if any blocking check failed (which
would already have stopped its step) or if any expected table is missing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from etl.datasets import BRONZE_TABLES
from silver.common.session import get_spark

SCHEMAS = ("wbg_bronze", "wbg_ref_bronze", "wbg_silver", "wbg_gold")
EXPECTED_COUNTS = {"wbg_bronze": 9, "wbg_ref_bronze": 8, "wbg_silver": 18, "wbg_gold": 5}
RESULTS = "job_run_id string, step string, target string, check_name string, severity string, passed boolean, " \
          "observed string, expected string, detail string, recorded_at timestamp"
SNAPSHOTS = "job_run_id string, table_name string, table_type string, row_count bigint, delta_version bigint, recorded_at timestamp"


def reports(node, path: str = ""):
    """Every DQReport.to_dict() inside a run report (some steps write one per target)."""
    if isinstance(node, dict):
        if {"target", "passed", "results"} <= node.keys():
            yield path, node
        for k, v in node.items():
            yield from reports(v, f"{path}.{k}" if path else k)


def check_rows(run_id: str, runs: Path, now: datetime) -> list[tuple]:
    """One dq_results row per check found in the dq_report.json files under the run folder (long values truncated)."""
    rows = []
    for f in sorted(runs.rglob("dq_report.json")):
        step = str(f.parent.relative_to(runs))
        for _, rep in reports(json.loads(f.read_text())):
            for r in rep["results"]:
                rows.append((run_id, step, rep["target"], r["name"], r["severity"], r["passed"],
                             json.dumps(r["observed"], default=str)[:4000], json.dumps(r["expected"], default=str)[:4000], r["detail"][:2000], now))
    return rows


def table_rows(spark, catalog: str, run_id: str, now: datetime) -> list[tuple]:
    """One table_snapshots row per table or view in the four schemas: row count and Delta version (None for views)."""
    rows = []
    for schema in SCHEMAS:
        for t in spark.sql(f"SHOW TABLES IN {catalog}.{schema}").collect():
            full = f"{catalog}.{schema}.{t.tableName}"
            kind = spark.sql(f"DESCRIBE TABLE EXTENDED {full}").filter("col_name = 'Type'").collect()[0].data_type
            version = spark.sql(f"DESCRIBE HISTORY {full} LIMIT 1").collect()[0].version if kind != "VIEW" else None
            rows.append((run_id, full, kind, spark.table(full).count(), version, now))
    return rows


def append(spark, rows: list[tuple], schema: str, target: str) -> None:
    """Append this run's rows (the table is created on the first run)."""
    w = spark.createDataFrame(rows, schema).writeTo(target)
    w.append() if spark.catalog.tableExists(target) else w.using("delta").create()


def run(catalog: str, run_id: str, runs: Path) -> int:
    """Append this run's checks and table snapshots; return 1 if a blocking check failed or a table is missing."""
    spark = get_spark()
    now = datetime.now(timezone.utc)
    checks = check_rows(run_id, runs, now)
    tables = table_rows(spark, catalog, run_id, now)
    append(spark, checks, RESULTS, f"{catalog}.etl.dq_results")
    append(spark, tables, SNAPSHOTS, f"{catalog}.etl.table_snapshots")
    # Tuple positions follow RESULTS: 4 is the severity and 5 is passed.
    failed = [c for c in checks if c[4] == "error" and not c[5]]
    warned = [c for c in checks if c[4] == "warn" and not c[5]]
    counts = {s: sum(1 for t in tables if t[1].split(".")[1] == s and t[2] != "VIEW") for s in SCHEMAS}
    missing = {s: (counts[s], n) for s, n in EXPECTED_COUNTS.items() if counts[s] < n}
    bronze_missing = sorted({f"{catalog}.{t}" for t in BRONZE_TABLES} - {t[1] for t in tables})
    print(f"checks: {len(checks)} ({len(failed)} failed, {len(warned)} warnings); tables per schema: {counts}")
    for c in warned:
        print(f"  warn  {c[1]}: {c[3]}  observed={c[6][:120]}  {c[8][:80]}")
    for c in failed:
        print(f"  FAIL  {c[1]}: {c[3]}  observed={c[6][:160]}")
    if missing or bronze_missing:
        print(f"missing tables: {missing} {bronze_missing}")
    return 1 if failed or missing or bronze_missing else 0
