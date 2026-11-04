"""Create or replace the seven vw_kpi_* views over the rebuilt Gold tables (the reviewed SQL in kpi_queries, unchanged).

Before each view is (re)created its query runs once and must keep the reviewed guarantees: a unique grain, no sensitive
columns and no e-mail or phone values. The snapshot comparisons of the original create-only run (fixed totals and row
counts) are not repeated: the data is new on every run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from gold.procurement_kpis import kpi_queries as Q
from gold.procurement_kpis.create_kpi_views import PROHIBITED
from silver.common.dq import DQReport
from silver.common.session import get_spark
from silver.notices.transform import EMAIL_RE, PHONE_RE


def preflight(spark, name: str, rep: DQReport) -> None:
    """Run one KPI query once and record its checks: unique grain, no sensitive columns, no e-mail or phone values."""
    df = spark.sql(Q.bound(name))
    schema = [(f.name, f.dataType.simpleString()) for f in df.schema.fields]
    rows = [r.asDict() for r in df.collect()]
    rep.equals(f"{name}:grain_unique", len({tuple(r[c] for c in Q.GRAINS[name]) for r in rows}), len(rows))
    rep.equals(f"{name}:no_sensitive_columns", [c for c, _ in schema if PROHIBITED.search(c)], [])
    strings = [r[c] for r in rows for c, t in schema if t == "string" and r[c] is not None]
    rep.equals(f"{name}:no_email_or_phone_values",
               (sum(bool(re.search(EMAIL_RE, s)) for s in strings), sum(bool(re.search("(?i)" + PHONE_RE, s)) for s in strings)), (0, 0))
    rep.add(f"{name}:rows", len(rows), "reported", True, "warn")


def run(catalog: str, runs: Path) -> int:
    """Check every KPI query, then create or replace the views; return 0 when every check passed, else 1."""
    spark = get_spark()
    rep = DQReport(f"{Q.VIEW_SCHEMA}.vw_kpi_*")
    for name in Q.QUERIES:
        preflight(spark, name, rep)
    created = []
    # All or nothing: no view is replaced unless every query passed its checks.
    if rep.passed:
        for name in Q.QUERIES:
            # The reviewed DDL says CREATE VIEW; it becomes CREATE OR REPLACE so that every run can refresh the views.
            spark.sql(Q.view_ddl(name).replace("CREATE VIEW", "CREATE OR REPLACE VIEW", 1))
            created.append(name)
    folder = runs / "gold" / "kpi_views"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dq_report.json").write_text(json.dumps({"catalog": catalog, "created": created, "dq": rep.to_dict()}, indent=1, default=str))
    print(rep.render())
    print(f"views created or replaced: {created}")
    return 0 if rep.passed else 1
