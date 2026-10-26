"""Independent post-write verification of workspace.wbg_silver.cpia_ratings (read-only).

Recomputes expectations from Bronze raw JSON with get_json_object (no reuse of the transform), joins on the
raw-record SHA-256 and compares values, keys, nulls, WDI matches and lineage; checks Delta constraints and
NOT NULL; confirms Bronze and all existing Silver tables are unchanged since the run's pre-write baseline.

Usage (from procurement-intelligence/):
  python -m silver.cpia.verify_cpia_silver --run-id cpia_<timestamp>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

BRONZE = "workspace.wbg_ref_bronze.cpia_ratings"
TARGET = "workspace.wbg_silver.cpia_ratings"
WDI_COUNTRY = "workspace.wbg_silver.wdi_country"
RUNS = Path(__file__).resolve().parents[1] / "runs"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    baseline = json.loads((run_dir / "baseline_before_write.json").read_text())
    spark = get_spark(args.profile)
    rep = DQReport(f"{TARGET} (post-write, independent)")
    t, b = spark.table(TARGET), spark.table(BRONZE)
    j = lambda p: F.get_json_object("raw_record", p)  # noqa: E731
    be = b.select(F.sha2("raw_record", 256).alias("raw_record_sha256"), F.col("raw_record").alias("b_raw"),
                  j("$.country.id").alias("b_cid"), j("$.date").cast("int").alias("b_year"), j("$.indicator.id").alias("b_ind"),
                  j("$.value").cast("double").alias("b_value"), F.col("source_file").alias("b_file"), F.col("ingested_at").alias("b_ing"))

    n = t.count()
    rep.equals("row_count", n, b.count())
    rep.equals("rows_per_indicator", {r[0]: r[1] for r in t.groupBy("indicator_code").count().collect()},
               {r[0]: r[1] for r in be.groupBy("b_ind").count().collect()})
    rep.equals("unique_key (country_code, year, indicator_code)", t.select("country_code", "year", "indicator_code").distinct().count(), n)
    x = t.join(be, "raw_record_sha256", "full_outer")
    rep.equals("rows_without_bronze_match (either side)", x.filter(F.col("country_code").isNull() | F.col("b_ind").isNull()).count(), 0)
    rep.equals("value_mismatch", x.filter(~F.col("value").eqNullSafe(F.col("b_value"))).count(), 0)
    rep.equals("key_mismatch (country, year, indicator)",
               x.filter((F.col("country_code") != F.col("b_cid")) | (F.col("year") != F.col("b_year")) | (F.col("indicator_code") != F.col("b_ind"))).count(), 0)
    rep.equals("nulls_per_indicator_match_bronze", {r[0]: r[1] for r in t.filter("value IS NULL").groupBy("indicator_code").count().collect()},
               {r[0]: r[1] for r in be.filter("b_value IS NULL").groupBy("b_ind").count().collect()})
    rep.equals("non_null_total", t.filter("value IS NOT NULL").count(), be.filter("b_value IS NOT NULL").count())
    rep.equals("zero_values_match_bronze", t.filter("value = 0").count(), be.filter("b_value = 0").count())
    rep.equals("is_value_missing_consistent", t.filter(F.col("is_value_missing") != F.col("value").isNull()).count(), 0)
    wdi = spark.table(WDI_COUNTRY).select(F.col("country_code").alias("w_cc"), F.col("country_wb_id").alias("w_id"), F.col("entity_type").alias("w_type"))
    m = t.join(wdi, t.country_code == wdi.w_cc, "left")
    rep.equals("wdi_match_recomputed",
               m.filter((F.col("wdi_match_status") == "matched") != F.col("w_cc").isNotNull()).count() +
               m.filter(~F.col("wdi_country_wb_id").eqNullSafe(F.col("w_id")) | ~F.col("wdi_entity_type").eqNullSafe(F.col("w_type"))).count(), 0)
    rep.equals("entity_counts_by_type", {r[0]: r[1] for r in t.groupBy("wdi_entity_type").agg(F.countDistinct("country_code")).collect()},
               {"economy": 217, "aggregate": 49})
    rep.equals("years", sorted(r[0] for r in t.select("year").distinct().collect()), sorted(r[0] for r in be.select("b_year").distinct().collect()))
    rep.equals("lineage_matches_bronze (raw record, file, ingestion time)",
               x.filter((F.col("bronze_raw_record") != F.col("b_raw")) | (F.col("bronze_source_file") != F.col("b_file")) | (F.col("bronze_ingested_at") != F.col("b_ing"))).count(), 0)
    rep.equals("source_vintage", sorted(str(r[0]) for r in t.select("source_lastupdated").distinct().collect()), ["2026-10-08"])
    rep.equals("source_file_sha256_per_file", t.select("bronze_source_file", "source_file_sha256").distinct().count(), 6)
    props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {TARGET}").collect()}
    rep.equals("constraints_present", sorted(k for k in props if k.startswith("delta.constraints.")),
               ["delta.constraints.missing_flag_consistent", "delta.constraints.wdi_match_status_valid"])
    rep.equals("run_id_property", props.get("silver.run_id"), args.run_id)
    nn = {f.name for f in t.schema.fields if not f.nullable}
    rep.equals("not_null_enforced", sorted({"country_code", "indicator_code", "year", "is_value_missing", "bronze_raw_record",
                                            "raw_record_sha256", "source_lastupdated", "source_url"} - nn), [])
    for tbl, before in {**baseline["bronze"], **baseline["silver"]}.items():
        h = spark.sql(f"DESCRIBE HISTORY {tbl} LIMIT 1").collect()[0]
        rep.equals(f"unchanged:{tbl}", {"version": h.version, "rows": spark.table(tbl).count()}, before)
    hist = [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {TARGET}").orderBy("version").collect()]
    print(rep.render())
    out = run_dir / "post_write_verification.json"
    out.write_text(json.dumps({"run_id": args.run_id, "verification": rep.to_dict(), "table_history": hist}, indent=1, default=str))
    print(f"\nreport: {out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
