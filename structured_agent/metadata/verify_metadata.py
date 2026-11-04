"""Live, read-only verification of documented grains and relationships against Databricks data.

For every object: row count, distinct grain-key combinations, and NULL grain keys.
For every curated relationship: from-side NULL keys, orphans (from-keys missing on the to side), the largest number of
to-rows per key (must be 1), and for 1:1 relationships the largest number of from-rows per key.

Only SELECT statements are issued. Usage (from procurement-intelligence/):
    /opt/anaconda3/bin/python3 -m structured_agent.metadata.verify_metadata
Output: structured_agent/metadata/verification/metadata_verification_<UTC>.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from silver.common.session import get_spark
from structured_agent.metadata.sources import SNAPSHOT_DIR, VERIFICATION_DIR, curated, latest, object_grains, read_json


def _one(spark, sql: str) -> dict:
    return spark.sql(sql).collect()[0].asDict()


def verify_grain(spark, fqn: str, columns: list[str]) -> dict:
    """Row count, distinct key count and NULL-key rows for one object; 'unique' when rows equal distinct keys."""
    if not columns:  # single-row view
        rows = _one(spark, f"SELECT count(*) AS n FROM {fqn}")["n"]
        return {"rows": rows, "distinct_keys": min(rows, 1), "null_key_rows": 0, "unique": rows == 1}
    cols = ", ".join(columns)
    any_null = " OR ".join(f"{c} IS NULL" for c in columns)
    result = _one(spark, f"""
        SELECT (SELECT count(*) FROM {fqn}) AS rows,
               (SELECT count(*) FROM (SELECT DISTINCT {cols} FROM {fqn})) AS distinct_keys,
               (SELECT count(*) FROM {fqn} WHERE {any_null}) AS null_key_rows""")
    result["unique"] = result["rows"] == result["distinct_keys"]
    return result


def verify_relationship(spark, rel: dict) -> dict:
    """Live check of one relationship (NULL keys, orphans, rows per key); 'passed' says whether it holds."""
    frm, to = rel["from"], rel["to"]
    on = " AND ".join(f"f.{a} = t.{b}" for a, b in zip(rel["from_columns"], rel["to_columns"]))
    from_cols = ", ".join(rel["from_columns"])
    from_not_null = " AND ".join(f"{c} IS NOT NULL" for c in rel["from_columns"])
    # Required conditions (e.g. a fixed context year) are part of the to-side key.
    to_key = rel["to_columns"] + [c["column"] for c in rel.get("required_conditions", [])]
    to_cols = ", ".join(to_key)

    result = _one(spark, f"""
        SELECT (SELECT count(*) FROM {frm} WHERE NOT ({from_not_null})) AS from_null_keys,
               (SELECT count(*) FROM {frm} f LEFT ANTI JOIN {to} t ON {on} WHERE {" AND ".join(f"f.{c} IS NOT NULL" for c in rel["from_columns"])}) AS orphans,
               (SELECT coalesce(max(n), 0) FROM (SELECT count(*) AS n FROM {to} GROUP BY {to_cols})) AS max_to_rows_per_key""")
    if rel["cardinality"] in ("1:1", "1:0..1"):
        result["max_from_rows_per_key"] = _one(spark, f"""
            SELECT coalesce(max(n), 0) AS m FROM (SELECT count(*) AS n FROM {frm} WHERE {from_not_null} GROUP BY {from_cols})""")["m"]
    to_unique = result["max_to_rows_per_key"] <= 1
    from_unique = result.get("max_from_rows_per_key", 1) <= 1
    result["cardinality_holds"] = to_unique and from_unique

    if rel["cardinality"] == "1:0..1":
        # Optional side: unmatched from-rows are partial coverage, not orphans. Integrity = every to-row has a from-row.
        result["unmatched_from_rows_coverage"] = result.pop("orphans")
        result["reverse_orphans"] = _one(spark, f"SELECT count(*) AS n FROM {to} t LEFT ANTI JOIN {frm} f ON {on}")["n"]
        expected = rel.get("documented_exceptions", {}).get("reverse_orphans", 0)  # a documented, counted exception
        result["passed"] = result["cardinality_holds"] and result["reverse_orphans"] == expected
    else:
        result["passed"] = result["cardinality_holds"] and result["orphans"] == 0
    return result


def main() -> None:
    """Verify the grains and relationships for the latest snapshot and save a timestamped result file."""
    spark = get_spark()
    snapshot_path = latest(SNAPSHOT_DIR, "databricks_metadata")
    snapshot = read_json(snapshot_path)

    grains = {}
    for fqn, grain in object_grains(snapshot).items():
        print(f"  grain {fqn}", flush=True)
        grains[fqn] = {"columns": grain.columns, **verify_grain(spark, fqn, grain.columns)}

    relationships = {}
    for rel in curated("relationships.json")["relationships"]:
        print(f"  relationship {rel['id']}", flush=True)
        relationships[rel["id"]] = verify_relationship(spark, rel)

    verified_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = {"verified_at": verified_at, "snapshot": snapshot_path.name, "grains": grains, "relationships": relationships}
    VERIFICATION_DIR.mkdir(parents=True, exist_ok=True)
    path = VERIFICATION_DIR / f"metadata_verification_{verified_at}.json"
    path.write_text(json.dumps(out, indent=1, default=str))

    failed_grains = [f for f, g in grains.items() if not g["unique"]]
    failed_rels = [r for r, v in relationships.items() if not v["passed"]]
    print(f"grains unique: {len(grains) - len(failed_grains)}/{len(grains)} {failed_grains}")
    print(f"relationships passed: {len(relationships) - len(failed_rels)}/{len(relationships)} {failed_rels}")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
