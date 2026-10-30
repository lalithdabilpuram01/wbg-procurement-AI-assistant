"""Independent post-write verification of workspace.wbg_silver.ieg_project_ratings (read-only).

Re-reads the Volume CSV (bytes via binaryFile), parses it with pandas, and recomputes ALL 44 Silver columns in plain
Python with its own copy of the documented mappings (no reuse of the Spark transform): published values, fiscal years,
lag/precedes flags, ordinal scores, satisfactory flag, as-of date, NULL availability date, and every lineage field
(against the Volume bytes, the Bronze rows and the extraction / Phase 1 manifests). Also checks the exact schema, Delta
constraints / NOT NULL / run_id, the known anomalies against the approved dry run, and that all Bronze and existing
Silver tables, their row counts, the column masks and the cohort manifest are unchanged.

Usage (from procurement-intelligence/):
  python -m silver.ieg.verify_ieg_silver --run-id ieg_<timestamp> [--approved-dry-run ieg_<timestamp>]
"""
from __future__ import annotations

from app_config.paths import BRONZE_INPUTS_MANIFEST, COHORT_MANIFEST, SOURCE_PULLS_CACHE_MANIFEST
import argparse
import hashlib
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

TARGET = "workspace.wbg_silver.ieg_project_ratings"
BRONZE = "workspace.wbg_bronze.bronze_ieg_ratings_ds00053"
REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "silver" / "runs"
COHORT = COHORT_MANIFEST
MANIFEST = BRONZE_INPUTS_MANIFEST
CACHE_MANIFEST = SOURCE_PULLS_CACHE_MANIFEST
SEP = "\x1f"
SIX = ["Highly Unsatisfactory", "Unsatisfactory", "Moderately Unsatisfactory", "Moderately Satisfactory", "Satisfactory", "Highly Satisfactory"]
FOUR = ["Negligible", "Modest", "Substantial", "High"]
FIELDS = {"Outcome": ("outcome", SIX), "Quality at Entry": ("quality_at_entry", SIX), "Quality of Supervision": ("quality_of_supervision", SIX),
          "Bank Performance": ("bank_performance", SIX), "M&E Quality": ("me_quality", FOUR)}
NAMES = {"ICRR": "Implementation Completion and Results Report Review", "PPAR": "Project Performance Assessment Report"}
PUBLISHED = {"project_name_source": "Project Name", "evaluation_type": "Evaluation Type", "wb_region_name": "WB Region",
             "country_name_source": "Country / Economy", "country_lending_group": "Country / Economy Lending Group",
             "fcs_status": "Country / Economy FCS Status", "fcs_lending_group": "Country / Economy FCS Lending Group",
             "practice_group": "Practice Group", "global_practice": "Global Practice", "agreement_type": "Agreement Type",
             "lending_instrument_type": "Lending Instrument Type"}
SCHEMA = [("project_id", "string"), ("project_name_source", "string"), ("evaluation_type", "string"), ("evaluation_type_name", "string"),
          ("evaluation_fiscal_year", "int"), ("approval_fiscal_year", "int"), ("final_closing_fiscal_year", "int"),
          ("evaluation_lag_fiscal_years", "int"), ("evaluation_precedes_final_closing", "boolean"), ("rating_available_from_date", "date"),
          ("outcome_rating", "string"), ("outcome_score", "int"), ("quality_at_entry_rating", "string"), ("quality_at_entry_score", "int"),
          ("quality_of_supervision_rating", "string"), ("quality_of_supervision_score", "int"), ("bank_performance_rating", "string"),
          ("bank_performance_score", "int"), ("me_quality_rating", "string"), ("me_quality_score", "int"), ("outcome_satisfactory", "boolean"),
          ("wb_region_name", "string"), ("country_name_source", "string"), ("country_lending_group", "string"), ("fcs_status", "string"),
          ("fcs_lending_group", "string"), ("practice_group", "string"), ("global_practice", "string"), ("agreement_type", "string"),
          ("lending_instrument_type", "string"), ("source_as_of_date", "date"), ("rating_snapshot_semantics", "string"),
          ("source_dataset", "string"), ("source_row_sha256", "string"), ("bronze_table", "string"), ("bronze_source_file", "string"),
          ("bronze_cohort_id", "string"), ("bronze_ingested_at", "timestamp"), ("bronze_input_file_sha256", "string"),
          ("source_population_sha256", "string"), ("source_retrieved_at", "timestamp"), ("silver_run_id", "string"),
          ("silver_processed_at", "timestamp"), ("transform_version", "string")]
CONSTRAINTS = ["lag_consistent", "me_score_range", "precedes_consistent", "project_id_format", "satisfactory_consistent", "six_point_score_range",
               "unrated_has_null_score"]


def fy(s: str):
    return int(s) if len(s) == 4 and s.isdigit() else None


def epoch(v) -> int | None:
    """Timezone-free instant. Databricks Connect collect() returns naive datetimes in the local (process) timezone,
    so naive values are interpreted as local time; ISO strings with an offset are exact."""
    if v is None:
        return None
    if isinstance(v, str):
        return int(datetime.fromisoformat(v).timestamp())
    return int(v.timestamp())


TIMESTAMP_COLS = ["bronze_ingested_at", "source_retrieved_at"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--approved-dry-run", default="ieg_20261023T224233Z")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    baseline = json.loads((run_dir / "baseline_before_write.json").read_text())
    spark = get_spark(args.profile)
    rep = DQReport(f"{TARGET} (post-write, independent)")

    # ---------- schema ----------
    got_schema = [(f.name, f.dataType.simpleString()) for f in spark.table(TARGET).schema.fields]
    rep.equals("schema_exact (44 columns, names, order, types)", got_schema, SCHEMA)

    # ---------- expected values from the Volume file + manifests ----------
    bronze = spark.table(BRONZE)
    path = bronze.select("source_file").distinct().collect()[0][0]
    raw = bytes(spark.read.format("binaryFile").load(path).select("content").collect()[0][0])
    entry = next(f for f in json.loads(MANIFEST.read_text())["files"] if f["bronze_table"] == "bronze_ieg_ratings_ds00053")
    pull = next(json.loads(ln) for ln in CACHE_MANIFEST.read_text().splitlines() if Path(entry["source_file"]).name in ln)
    rep.equals("volume_file_sha256_equals_manifest", hashlib.sha256(raw).hexdigest(), entry["output_sha256"])
    ingested = {r[0] for r in bronze.select("ingested_at").distinct().collect()}
    rep.equals("bronze_single_ingestion_timestamp", len(ingested), 1)
    src = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    exp = {}
    for row in src.itertuples(index=False, name=None):
        r = dict(zip(src.columns, row))
        e_fy, c_fy = fy(r["Evaluation FY"]), fy(r["Final Closing FY"])
        rec = {k: (r[v] or None) for k, v in PUBLISHED.items()}
        rec.update({"project_id": r["Project ID"], "evaluation_type_name": NAMES.get(r["Evaluation Type"]),
                    "evaluation_fiscal_year": e_fy, "approval_fiscal_year": fy(r["Approval FY"]), "final_closing_fiscal_year": c_fy,
                    "evaluation_lag_fiscal_years": (e_fy - c_fy) if e_fy is not None and c_fy is not None else None,
                    "evaluation_precedes_final_closing": (e_fy < c_fy) if e_fy is not None and c_fy is not None else None,
                    "rating_available_from_date": None, "source_as_of_date": datetime.strptime(r["As of Date"], "%m/%d/%Y").date(),
                    "rating_snapshot_semantics": "latest_evaluation_as_of_source_date", "source_dataset": "DS00053",
                    "source_row_sha256": hashlib.sha256(SEP.join(row).encode("utf-8")).hexdigest(),
                    "bronze_table": BRONZE, "bronze_source_file": path, "bronze_cohort_id": "wbg_poc_2000",
                    "bronze_ingested_at": epoch(next(iter(ingested))), "bronze_input_file_sha256": entry["output_sha256"],
                    "source_population_sha256": entry["source_sha256"], "source_retrieved_at": epoch(pull["extracted_at_utc"]),
                    "silver_run_id": args.run_id, "transform_version": "ieg_silver_v1.0.0"})
        for col, (stem, scale) in FIELDS.items():
            text = r[col] or None
            rec[f"{stem}_rating"] = text
            rec[f"{stem}_score"] = scale.index(text) + 1 if text in scale else None
        rec["outcome_satisfactory"] = None if rec["outcome_score"] is None else rec["outcome_score"] >= 4
        exp[r["Project ID"]] = rec
    cols = [c for c, _ in SCHEMA if c != "silver_processed_at"]
    assert set(cols) == set(next(iter(exp.values()))), "verifier must recompute every column except silver_processed_at"
    got = {x["project_id"]: {**x, **{c: epoch(x[c]) for c in TIMESTAMP_COLS}} for x in (r.asDict() for r in spark.table(TARGET).select(*cols).collect())}

    # ---------- counts, keys, cohort ----------
    rep.equals("row_count", len(got), 750)
    rep.equals("distinct_project_ids", len(set(got)), 750)
    rep.equals("bronze_rows", bronze.count(), 750)
    rep.equals("keys_equal_source", sorted(set(got) ^ set(exp))[:10], [])
    cohort = set(pd.read_csv(COHORT, dtype=str, keep_default_na=False)["project_id"])
    rep.equals("cohort_size", len(cohort), 2000)
    rep.equals("cohort_membership_100pct", sorted(set(got) - cohort)[:10], [])
    rep.equals("cohort_manifest_unchanged", hashlib.sha256(COHORT.read_bytes()).hexdigest(), baseline["cohort_manifest_sha256"])
    projects = {r[0] for r in spark.table("workspace.wbg_silver.projects").select("project_id").collect()}
    rep.equals("project_ids_in_silver_projects", sorted(set(got) - projects)[:10], [])

    # ---------- every column recomputed ----------
    mism: dict = {}
    for k, e in exp.items():
        for c in cols:
            if got.get(k, {}).get(c) != e[c]:
                mism.setdefault(c, []).append(k)
    rep.equals(f"field_mismatches ({len(cols)} columns recomputed in Python incl. lineage)", {c: v[:5] for c, v in mism.items()}, {})
    rep.equals("rating_available_from_date_null_rows", sum(1 for x in got.values() if x["rating_available_from_date"] is None), 750)
    rep.equals("silver_processed_at_not_null", spark.table(TARGET).filter("silver_processed_at IS NULL").count(), 0)
    rep.add("score_distribution", {stem: dict(sorted(pd.Series([x[f"{stem}_score"] for x in got.values()]).value_counts(dropna=False).items(), key=lambda t: str(t[0])))
                                   for _, (stem, _) in FIELDS.items()}, "reported", True, "warn")

    # ---------- known anomalies (preserved, equal to the approved dry run) ----------
    approved = json.loads((RUNS / args.approved_dry_run / "known_anomalies.json").read_text())
    now = json.loads((run_dir / "known_anomalies.json").read_text())
    rep.equals("known_anomalies_equal_approved_dry_run", now, approved)
    precedes = sorted(k for k, x in got.items() if x["evaluation_precedes_final_closing"])
    rep.equals("evaluation_precedes_final_closing_in_table", precedes, approved["evaluation_precedes_final_closing"])
    rep.equals("anomalous_records_unchanged (fields equal source)", sorted(k for k in precedes if k in mism), [])

    # ---------- Delta metadata ----------
    props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {TARGET}").collect()}
    rep.equals("constraints_present", sorted(p.split(".")[-1] for p in props if p.startswith("delta.constraints.")), CONSTRAINTS)
    rep.equals("run_id_property", props.get("silver.run_id"), args.run_id)
    nn = {f.name for f in spark.table(TARGET).schema.fields if not f.nullable}
    rep.equals("not_null_enforced", sorted({"project_id", "evaluation_type", "evaluation_fiscal_year", "approval_fiscal_year", "source_as_of_date",
                                            "rating_snapshot_semantics", "source_dataset", "source_row_sha256", "bronze_source_file",
                                            "bronze_ingested_at", "bronze_input_file_sha256"} - nn), [])
    hist = [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {TARGET}").orderBy("version").collect()]
    rep.equals("history_first_operation_is_create", hist[0][1] if hist else None, "CREATE TABLE AS SELECT")
    rep.add("delta_version_after_write", hist[-1][0] if hist else None, "reported", True, "warn")

    # ---------- everything else unchanged ----------
    masks = sorted([r[0], r[1], r[2], r[3]] for r in spark.sql(
        "SELECT table_schema, table_name, column_name, mask_name FROM workspace.information_schema.column_masks").collect())
    rep.equals("column_masks_unchanged", masks, baseline["column_masks"])
    rep.equals("baseline_covers_17_bronze_13_silver", (len(baseline["bronze"]), len(baseline["silver"])), (17, 13))
    for tbl, before in {**baseline["bronze"], **baseline["silver"]}.items():
        h = spark.sql(f"DESCRIBE HISTORY {tbl} LIMIT 1").collect()[0]
        rep.equals(f"unchanged:{tbl}", {"version": h.version, "rows": spark.table(tbl).count()}, before)
    print(rep.render())
    out = run_dir / "post_write_verification.json"
    out.write_text(json.dumps({"run_id": args.run_id, "verification": rep.to_dict(), "table_history": hist}, indent=1, default=str))
    print(f"\nreport: {out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
