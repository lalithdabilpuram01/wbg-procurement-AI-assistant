"""Independent post-write verification of the WDI Silver tables (read-only).

Re-reads the written Delta tables and checks them against Bronze using calculations that do NOT reuse
silver.wdi.transform (Bronze values come from get_json_object; aggregates from the raw metadata JSON).
Also checks the Delta constraints/properties and that the Bronze source tables are unchanged since the
pre-write baseline.

Usage (from procurement-intelligence/):
  python -m silver.wdi.verify_wdi_silver --run-id wdi_20261023T142626Z
Writes silver/runs/<run_id>/post_write_verification.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

B = "workspace.wbg_ref_bronze"
OBS = "workspace.wbg_silver.wdi_indicator_observations"
CTY = "workspace.wbg_silver.wdi_country"
RUNS = Path(__file__).resolve().parents[1] / "runs"


def main() -> int:
    """Re-check both written WDI tables against Bronze and the run's baseline; return 0 when every check passed."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    baseline = json.loads((run_dir / "bronze_versions_before_write.json").read_text())
    spark = get_spark(args.profile)
    rep = DQReport(f"{OBS} + {CTY} (post-write, independent)")

    o, c = spark.table(OBS), spark.table(CTY)
    bo = spark.table(f"{B}.wdi_indicator_observations")
    raw_val = F.get_json_object("raw_record", "$.value")

    # ---- row counts
    rep.equals("obs_rows", o.count(), 20748)
    rep.equals("obs_rows_equal_bronze", o.count(), bo.count())
    rep.equals("obs_rows_per_indicator", {r.indicator_code: r["count"] for r in o.groupBy("indicator_code").count().collect()},
               {"NY.GDP.MKTP.KD.ZG": 6916, "FP.CPI.TOTL.ZG": 6916, "NY.GDP.PCAP.CD": 6916})
    rep.equals("country_rows", c.count(), 296)
    # ---- unique keys
    rep.equals("obs_duplicate_keys", o.groupBy("country_code", "year", "indicator_code").count().filter("count > 1").count(), 0)
    rep.equals("country_code_unique", c.select("country_code").distinct().count(), 296)
    rep.equals("country_wb_id_unique", c.select("country_wb_id").distinct().count(), 296)
    # ---- null values
    bronze_nulls = {r.indicator_code: r["count"] for r in bo.filter(raw_val.isNull()).groupBy("indicator_code").count().collect()}
    silver_nulls = {r.indicator_code: r["count"] for r in o.filter(F.col("value").isNull()).groupBy("indicator_code").count().collect()}
    rep.equals("value_nulls_match_bronze_raw_nulls", silver_nulls, bronze_nulls)
    rep.equals("value_nulls_total", sum(silver_nulls.values()), 1526)
    must = ["country_code", "country_wb_id", "entity_type", "indicator_code", "year", "is_value_missing", "source_lastupdated",
            "bronze_table", "bronze_source_file", "bronze_ingested_at", "raw_record_sha256", "silver_run_id", "transform_version"]
    rep.equals("obs_required_column_nulls", {k: o.filter(F.col(k).isNull()).count() for k in must}, {k: 0 for k in must})
    iso_null = o.filter(F.col("country_iso3_code").isNull())
    rep.equals("iso3_null_rows_and_entities", (iso_null.count(), sorted(r[0] for r in iso_null.select("country_code").distinct().collect())),
               (390, ["XD", "XM", "XN", "XT", "XY"]))
    rep.equals("iso3_null_only_aggregates", iso_null.filter("entity_type != 'aggregate'").count(), 0)
    # ---- value fidelity: join to Bronze by raw-record hash, compare with independently extracted value
    bj = bo.select(F.sha2("raw_record", 256).alias("raw_record_sha256"), raw_val.cast("double").alias("bronze_value"),
                   F.get_json_object("raw_record", "$.country.id").alias("b_cc"), F.get_json_object("raw_record", "$.date").cast("int").alias("b_year"),
                   F.col("source_file").alias("b_file"), F.col("ingested_at").alias("b_ing"))
    j = o.join(bj, "raw_record_sha256", "full_outer")
    rep.equals("rows_without_bronze_match (either side)", j.filter(F.col("country_code").isNull() | F.col("b_cc").isNull()).count(), 0)
    rep.equals("value_mismatch_vs_bronze", j.filter(~F.col("value").eqNullSafe(F.col("bronze_value"))).count(), 0)
    rep.equals("key_mismatch_vs_bronze (country_code, year)",
               j.filter((F.col("country_code") != F.col("b_cc")) | (F.col("year") != F.col("b_year"))).count(), 0)
    # ---- entity classification, independently from the raw metadata JSON
    raw_meta = spark.table(f"{B}.wdi_country_metadata").select(
        F.explode(F.from_json(F.get_json_object("raw_response", "$[1]"),
                              "array<struct<iso2Code:string,region:struct<id:string>>>")).alias("e"))
    agg_codes = {r[0] for r in raw_meta.filter("e.region.id = 'NA'").select("e.iso2Code").collect()}
    silver_agg = {r[0] for r in c.filter("entity_type = 'aggregate'").select("country_code").collect()}
    rep.equals("aggregate_set_matches_raw_metadata", (len(silver_agg), silver_agg == agg_codes), (len(agg_codes), True))
    rep.equals("country_entity_type_counts", {r.entity_type: r["count"] for r in c.groupBy("entity_type").count().collect()},
               {"economy": 217, "aggregate": 79})
    rep.equals("obs_entity_counts", {r.entity_type: r.n for r in o.groupBy("entity_type").agg(F.countDistinct("country_code").alias("n")).collect()},
               {"economy": 217, "aggregate": 49})
    oc = o.select("country_code", F.col("entity_type").alias("oe"), F.col("country_wb_id").alias("ow")).distinct()
    rep.equals("obs_vs_country_table_consistency",
               oc.join(c.select("country_code", "entity_type", "country_wb_id"), "country_code", "left")
               .filter(~F.col("oe").eqNullSafe(F.col("entity_type")) | ~F.col("ow").eqNullSafe(F.col("country_wb_id"))).count(), 0)
    rep.equals("aggregates_without_region", c.filter("entity_type = 'aggregate' AND region_code_current IS NOT NULL").count(), 0)
    # ---- source lineage
    rep.equals("lineage_source_file_and_ingestion_match_bronze",
               j.filter((F.col("bronze_source_file") != F.col("b_file")) | (F.col("bronze_ingested_at") != F.col("b_ing"))).count(), 0)
    hdr = {r.indicator_code: r.lu for r in spark.table(f"{B}.wdi_api_responses").select(
        "indicator_code", F.get_json_object("raw_response", "$[0].lastupdated").alias("lu")).collect()}
    sv = {r.indicator_code: str(r.source_lastupdated) for r in o.select("indicator_code", "source_lastupdated").distinct().collect()}
    rep.equals("source_lastupdated_equals_api_header", sv, hdr)
    rep.equals("single_run_id_and_version", [tuple(r) for r in o.select("silver_run_id", "transform_version").distinct().collect()],
               [(args.run_id, "wdi_silver_v1.0.0")])
    rep.equals("bronze_table_lineage_values", sorted(r[0] for r in o.select("bronze_table").distinct().collect()),
               [f"{B}.wdi_indicator_observations"])
    # ---- Delta metadata: constraints, NOT NULL, properties
    props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {OBS}").collect()}
    rep.equals("obs_check_constraint", props.get("delta.constraints.entity_type_valid", "").lower().replace(" ", ""),
               "entity_typein('economy','aggregate')")
    rep.equals("obs_run_id_property", props.get("silver.run_id"), args.run_id)
    nn = {f.name for f in o.schema.fields if not f.nullable}
    rep.equals("obs_not_null_columns_enforced", sorted({"country_code", "country_wb_id", "entity_type", "indicator_code", "year",
                                                        "is_value_missing", "source_lastupdated", "bronze_source_file",
                                                        "bronze_ingested_at", "raw_record_sha256"} - nn), [])
    cprops = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {CTY}").collect()}
    rep.equals("country_check_constraint_present", "delta.constraints.entity_type_valid" in cprops, True)
    # ---- Bronze untouched
    after = {}
    for t, before in baseline.items():
        if t in ("targets_exist", "silver_tables"):
            continue
        h = spark.sql(f"DESCRIBE HISTORY {B}.{t} LIMIT 1").collect()[0]
        after[t] = {"version": h.version, "rows": spark.table(f"{B}.{t}").count()}
        rep.equals(f"bronze_unchanged:{t}", after[t], {"version": before["version"], "rows": before["rows"]})
    rep.equals("silver_schema_tables", sorted(r.tableName for r in spark.sql("SHOW TABLES IN workspace.wbg_silver").collect()),
               ["wdi_country", "wdi_indicator_observations"])
    hist = {t: [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {t}").orderBy("version").collect()] for t in (OBS, CTY)}

    print(rep.render())
    out = run_dir / "post_write_verification.json"
    out.write_text(json.dumps({"run_id": args.run_id, "verification": rep.to_dict(), "bronze_after": after,
                               "silver_table_history": hist}, indent=1, default=str))
    print(f"\nreport: {out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
