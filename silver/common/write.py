"""Create-only Delta writes shared by Silver jobs. Existing tables are never overwritten or replaced."""
from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession


def _sql_str(v: str) -> str:
    """Escape a value for a single-quoted Spark SQL string literal (backslash escapes are on by default)."""
    return v.replace("\\", "\\\\").replace("'", "\\'")


def create_table(spark: SparkSession, df: DataFrame, target: str, properties: dict[str, str], table_comment: str,
                 column_comments: dict[str, str] | None = None, not_null: list[str] | None = None,
                 constraints: dict[str, str] | None = None, column_masks: dict[str, str] | None = None) -> None:
    """column_masks: {column: mask function}. The masks are declared in CREATE TABLE and confirmed in
    information_schema before any row is inserted, so no row is ever stored in the table without its mask."""
    if spark.catalog.tableExists(target):
        raise SystemExit(f"{target} already exists: refusing to overwrite (create-only)")
    if column_masks:
        cols = ", ".join(f"`{f.name}` {f.dataType.simpleString()}" + (f" MASK {column_masks[f.name]}" if f.name in column_masks else "")
                         for f in df.schema.fields)
        props = ", ".join(f"'{k}' = '{_sql_str(v)}'" for k, v in properties.items())
        spark.sql(f"CREATE TABLE {target} ({cols}) USING DELTA TBLPROPERTIES ({props})")
        catalog, schema, table = target.split(".")
        got = {r[0]: r[1] for r in spark.sql(f"SELECT column_name, mask_name FROM {catalog}.information_schema.column_masks "
                                             f"WHERE table_schema = '{schema}' AND table_name = '{table}'").collect()}
        if got != column_masks:
            spark.sql(f"DROP TABLE {target}")  # empty table created by this call; nothing else is dropped
            raise SystemExit(f"{target}: column masks not confirmed ({got} != {column_masks}); empty table dropped, nothing written")
        df.writeTo(target).append()
    else:
        w = df.writeTo(target).using("delta")
        for k, v in properties.items():
            w = w.tableProperty(k, v)
        w.create()
    spark.sql(f"COMMENT ON TABLE {target} IS '{table_comment.replace(chr(39), '')}'")
    for col, text in (column_comments or {}).items():
        spark.sql(f"ALTER TABLE {target} ALTER COLUMN {col} COMMENT '{text.replace(chr(39), '')}'")
    for col in not_null or []:
        spark.sql(f"ALTER TABLE {target} ALTER COLUMN {col} SET NOT NULL")
    for name, expr in (constraints or {}).items():
        spark.sql(f"ALTER TABLE {target} ADD CONSTRAINT {name} CHECK ({expr})")


def complete_metadata(spark: SparkSession, target: str, table_comment: str, column_comments: dict[str, str] | None = None,
                      not_null: list[str] | None = None, constraints: dict[str, str] | None = None) -> list[str]:
    """Idempotently finish the metadata of a table created by an interrupted run (data untouched): (re)applies comments,
    sets NOT NULL only on columns still nullable, and adds only missing CHECK constraints. Returns the actions taken."""
    done: list[str] = []
    spark.sql(f"COMMENT ON TABLE {target} IS '{table_comment.replace(chr(39), '')}'")
    for col, text in (column_comments or {}).items():
        spark.sql(f"ALTER TABLE {target} ALTER COLUMN {col} COMMENT '{text.replace(chr(39), '')}'")
    nullable = {f.name for f in spark.table(target).schema.fields if f.nullable}
    for col in not_null or []:
        if col in nullable:
            spark.sql(f"ALTER TABLE {target} ALTER COLUMN {col} SET NOT NULL")
            done.append(f"not_null:{col}")
    props = {r[0] for r in spark.sql(f"SHOW TBLPROPERTIES {target}").collect()}
    for name, expr in (constraints or {}).items():
        if f"delta.constraints.{name.lower()}" not in props:
            spark.sql(f"ALTER TABLE {target} ADD CONSTRAINT {name} CHECK ({expr})")
            done.append(f"constraint:{name}")
    return done
