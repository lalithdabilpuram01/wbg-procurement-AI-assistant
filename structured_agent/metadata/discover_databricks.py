"""Read-only discovery of the actual Databricks metadata for the Structured Data Agent.

Collects, for every object in `workspace.wbg_silver` and `workspace.wbg_gold`:
  * object type and comment,
  * columns (name, type, nullability, comment, position),
  * view definitions,
  * Delta CHECK constraints and documentation table properties,
  * column masks and row filters,
  * row counts.

Nothing is written to Databricks. Owners, grantees and the current user are not recorded (they are personal identifiers).

Usage (from procurement-intelligence/):
    /opt/anaconda3/bin/python3 -m structured_agent.metadata.discover_databricks
Output: structured_agent/metadata/snapshots/databricks_metadata_<UTC>.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from silver.common.session import get_spark

CATALOG = "workspace"
SCHEMAS = ("wbg_silver", "wbg_gold")
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"

# Table properties worth keeping as documentation (everything else is Delta internals).
DOC_PROPERTY_PREFIXES = ("delta.constraints.", "silver.", "gold.", "comment")


def _rows(spark, sql: str) -> list[dict]:
    return [row.asDict() for row in spark.sql(sql).collect()]


def list_objects(spark) -> list[dict]:
    """Every table and view in the two schemas, with its type and comment."""
    schemas = ", ".join(f"'{s}'" for s in SCHEMAS)
    return _rows(spark, f"""
        SELECT table_schema, table_name, table_type, comment
        FROM {CATALOG}.information_schema.tables
        WHERE table_schema IN ({schemas})
        ORDER BY table_schema, table_name""")


def list_columns(spark) -> dict[str, list[dict]]:
    """Columns of every object, keyed by fully qualified name and ordered by position."""
    schemas = ", ".join(f"'{s}'" for s in SCHEMAS)
    rows = _rows(spark, f"""
        SELECT table_schema, table_name, column_name, ordinal_position, full_data_type, is_nullable, comment
        FROM {CATALOG}.information_schema.columns
        WHERE table_schema IN ({schemas})
        ORDER BY table_schema, table_name, ordinal_position""")
    by_table: dict[str, list[dict]] = {}
    for r in rows:
        fqn = f"{CATALOG}.{r['table_schema']}.{r['table_name']}"
        by_table.setdefault(fqn, []).append({
            "name": r["column_name"],
            "position": r["ordinal_position"],
            "data_type": r["full_data_type"],
            "nullable": r["is_nullable"] == "YES",
            "comment": r["comment"],
        })
    return by_table


def view_definitions(spark) -> dict[str, str]:
    """SQL text of every view, keyed by fully qualified name."""
    schemas = ", ".join(f"'{s}'" for s in SCHEMAS)
    rows = _rows(spark, f"""
        SELECT table_schema, table_name, view_definition
        FROM {CATALOG}.information_schema.views
        WHERE table_schema IN ({schemas})""")
    return {f"{CATALOG}.{r['table_schema']}.{r['table_name']}": r["view_definition"] for r in rows}


def column_masks(spark) -> list[dict]:
    """Column masks defined on objects in the two schemas."""
    schemas = ", ".join(f"'{s}'" for s in SCHEMAS)
    return _rows(spark, f"""
        SELECT table_schema, table_name, column_name, mask_name
        FROM {CATALOG}.information_schema.column_masks
        WHERE table_schema IN ({schemas})""")


def row_filter_count(spark) -> int:
    """Number of row filters defined in the two schemas (only the count is recorded)."""
    schemas = ", ".join(f"'{s}'" for s in SCHEMAS)
    return _rows(spark, f"""
        SELECT count(*) AS n FROM {CATALOG}.information_schema.row_filters
        WHERE table_schema IN ({schemas})""")[0]["n"]


def documentation_properties(spark, fqn: str) -> dict[str, str]:
    props = _rows(spark, f"SHOW TBLPROPERTIES {fqn}")
    return {p["key"]: p["value"] for p in props if p["key"].startswith(DOC_PROPERTY_PREFIXES)}


def row_count(spark, fqn: str) -> int:
    return _rows(spark, f"SELECT count(*) AS n FROM {fqn}")[0]["n"]


def discover() -> dict:
    """Collect the full metadata snapshot (one dict, ready to save as JSON)."""
    spark = get_spark()
    columns = list_columns(spark)
    views = view_definitions(spark)
    masks = column_masks(spark)

    objects = []
    for obj in list_objects(spark):
        fqn = f"{CATALOG}.{obj['table_schema']}.{obj['table_name']}"
        print(f"  {fqn}", flush=True)
        objects.append({
            "fqn": fqn,
            "schema": obj["table_schema"],
            "name": obj["table_name"],
            "object_type": "view" if obj["table_type"] == "VIEW" else "table",
            "comment": obj["comment"],
            "row_count": row_count(spark, fqn),
            "columns": columns.get(fqn, []),
            "view_definition": views.get(fqn),
            # Views have no Delta table properties worth reading.
            "properties": {} if fqn in views else documentation_properties(spark, fqn),
            "column_masks": [m["column_name"] for m in masks if m["table_name"] == obj["table_name"]
                             and m["table_schema"] == obj["table_schema"]],
        })

    return {
        "discovered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "catalog": CATALOG,
        "schemas": list(SCHEMAS),
        "row_filters": row_filter_count(spark),
        "column_masks": [{"table": f"{CATALOG}.{m['table_schema']}.{m['table_name']}", "column": m["column_name"],
                          "mask_function": m["mask_name"]} for m in masks],
        "objects": objects,
    }


def main() -> None:
    """Discover the metadata and save it as a timestamped snapshot file."""
    snapshot = discover()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = snapshot["discovered_at"].replace("-", "").replace(":", "").replace("+0000", "Z")
    path = SNAPSHOT_DIR / f"databricks_metadata_{stamp}.json"
    path.write_text(json.dumps(snapshot, indent=1, default=str))
    tables = sum(o["object_type"] == "table" for o in snapshot["objects"])
    print(f"{len(snapshot['objects'])} objects ({tables} tables) -> {path}")


if __name__ == "__main__":
    main()
