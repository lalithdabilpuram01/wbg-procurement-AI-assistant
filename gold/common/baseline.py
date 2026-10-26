"""Pre-write baseline and unchanged-state checks shared by Gold jobs: Delta version + row count of every Bronze, Silver and
existing Gold table, explicit grants (SHOW GRANTS), column masks and the cohort manifest SHA-256."""
from __future__ import annotations

import hashlib
from pathlib import Path

from silver.common.dq import DQReport

REPO = Path(__file__).resolve().parents[2]
COHORT = REPO / "cohort" / "poc_cohort_2000_manifest.csv"


def _tables(spark, schema: str) -> list[str]:
    if not spark.sql(f"SHOW SCHEMAS IN workspace LIKE '{schema}'").collect():
        return []
    return [f"workspace.{schema}.{r.tableName}" for r in spark.sql(f"SHOW TABLES IN workspace.{schema}").collect()]


def _state(spark, tbl: str) -> dict:
    h = spark.sql(f"DESCRIBE HISTORY {tbl} LIMIT 1").collect()[0]
    return {"version": h.version, "rows": spark.table(tbl).count()}


def _masks(spark) -> list:
    return sorted([r[0], r[1], r[2], r[3]] for r in spark.sql(
        "SELECT table_schema, table_name, column_name, mask_name FROM workspace.information_schema.column_masks").collect())


def _grants(spark, tbl: str) -> list:
    return sorted([str(x) for x in r] for r in spark.sql(f"SHOW GRANTS ON TABLE {tbl}").collect())


def capture(spark, exclude: set[str]) -> dict:
    """Baseline of all tables in wbg_bronze, wbg_ref_bronze, wbg_silver and wbg_gold except `exclude` (the targets)."""
    base: dict = {"cohort_manifest_sha256": hashlib.sha256(COHORT.read_bytes()).hexdigest(), "column_masks": _masks(spark), "grants": {}}
    for group, schemas in (("bronze", ("wbg_bronze", "wbg_ref_bronze")), ("silver", ("wbg_silver",)), ("gold", ("wbg_gold",))):
        base[group] = {t: _state(spark, t) for s in schemas for t in _tables(spark, s) if t not in exclude}
        for t in base[group]:
            base["grants"][t] = _grants(spark, t)
    return base


def check_unchanged(spark, base: dict, rep: DQReport, expected_counts: tuple[int, int, int]) -> None:
    rep.equals("baseline_covers (Bronze, Silver, Gold)", (len(base["bronze"]), len(base["silver"]), len(base["gold"])), expected_counts)
    for group in ("bronze", "silver", "gold"):
        changed = {t: (b, _state(spark, t)) for t, b in base[group].items() if _state(spark, t) != b}
        rep.equals(f"unchanged:{group}_tables_version_and_rows ({len(base[group])})", changed, {})
    rep.equals(f"unchanged:grants ({len(base['grants'])} tables)", {t: g for t, g in base["grants"].items() if _grants(spark, t) != g}, {})
    rep.equals("unchanged:column_masks", _masks(spark), base["column_masks"])
    rep.equals("unchanged:cohort_manifest_sha256", hashlib.sha256(COHORT.read_bytes()).hexdigest(), base["cohort_manifest_sha256"])
