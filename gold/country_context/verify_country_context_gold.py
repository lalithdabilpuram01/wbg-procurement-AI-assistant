"""Independent verification of gold_country_context (read-only).

Collects the Silver rows and recomputes every Gold column per (country_code, year) in plain Python with its own copy of
the rules (no reuse of the Spark transform or checks): the economy x year grid, every WDI/WGI/CPIA value (exact float
equality with the published value; NULL where missing), coverage counts and statuses, both fiscal-year income
classifications and lineage. Also checks the exact schema and the unchanged Bronze/Silver/Gold/grants/mask/cohort state.

Usage (from procurement-intelligence/):
  python -m gold.country_context.verify_country_context_gold --dry-run --run-id gold_country_context_<ts>
  python -m gold.country_context.verify_country_context_gold --run-id gold_country_context_<ts> --approved-dry-run <dry_run_id>
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from gold.common import baseline as B
from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "gold" / "runs"
TARGET = "workspace.wbg_gold.gold_country_context"
S = "workspace.wbg_silver."
SOURCES = sorted(["wdi_country", "wdi_indicator_observations", "wgi_estimates", "cpia_ratings", "income_classification_history"])
WDI = {"NY.GDP.MKTP.KD.ZG": "gdp_growth_annual_pct", "FP.CPI.TOTL.ZG": "inflation_consumer_prices_annual_pct", "NY.GDP.PCAP.CD": "gdp_per_capita_current_usd"}
WGI = {"CC.EST": "wgi_control_of_corruption_est", "GE.EST": "wgi_government_effectiveness_est", "PV.EST": "wgi_political_stability_est",
       "RL.EST": "wgi_rule_of_law_est", "RQ.EST": "wgi_regulatory_quality_est", "VA.EST": "wgi_voice_accountability_est"}
CPIA = {"IQ.CPA.IRAI.XQ": "cpia_irai_overall", "IQ.CPA.PROP.XQ": "cpia_property_rights", "IQ.CPA.PUBS.XQ": "cpia_public_sector_mgmt_cluster",
        "IQ.CPA.FINQ.XQ": "cpia_budget_financial_mgmt", "IQ.CPA.PADM.XQ": "cpia_public_administration", "IQ.CPA.TRAN.XQ": "cpia_transparency_corruption"}
SCHEMA = ([("country_code", "string"), ("country_iso3", "string"), ("country_name", "string"), ("year", "int"), ("indicator_year_basis", "string")]
          + [(c, "double") for c in WDI.values()] + [("wdi_values_present", "int"), ("wdi_source_lastupdated", "date")]
          + [(c, "double") for c in WGI.values()] + [("wgi_values_present", "int"), ("wgi_coverage_status", "string"), ("wgi_source_lastupdated", "date")]
          + [(c, "double") for c in CPIA.values()] + [("cpia_values_present", "int"), ("cpia_coverage_status", "string"), ("cpia_source_lastupdated", "date")]
          + [("income_fy_ending_in_year", "int"), ("income_group_fy_ending_in_year", "string"), ("income_status_fy_ending_in_year", "string"),
             ("income_fy_starting_in_year", "int"), ("income_group_fy_starting_in_year", "string"), ("income_status_fy_starting_in_year", "string"),
             ("income_source_workbook", "string"), ("income_source_file_sha256", "string"), ("context_snapshot_semantics", "string"),
             ("silver_source_versions", "string"), ("gold_run_id", "string"), ("gold_processed_at", "timestamp"), ("transform_version", "string")])


def rows(spark, table: str, *cols: str) -> list[dict]:
    return [r.asDict() for r in spark.table(S + table).select(*cols).collect()]


def expected(spark, run_id: str) -> dict:
    eco = [r for r in rows(spark, "wdi_country", "country_code", "country_wb_id", "country_name", "entity_type") if r["entity_type"] == "economy"]
    vals: dict = defaultdict(dict)
    meta: dict = {}
    for src, table, code, mapping, cond in (("wdi", "wdi_indicator_observations", "indicator_code", WDI, lambda r: r["entity_type"] == "economy"),
                                            ("wgi", "wgi_estimates", "indicator_short_code", WGI, lambda r: r["wdi_match_status"] == "matched"),
                                            ("cpia", "cpia_ratings", "indicator_code", CPIA, lambda r: r["wdi_entity_type"] == "economy")):
        extra = {"wdi": "entity_type", "wgi": "wdi_match_status", "cpia": "wdi_entity_type"}[src]
        for r in rows(spark, table, "country_code", "year", code, "value", "is_value_missing", "source_lastupdated", extra):
            if cond(r) and 2000 <= r["year"] <= 2025:
                k = (r["country_code"], r["year"])
                vals[k][mapping[r[code]]] = None if r["is_value_missing"] else r["value"]
                m = meta.setdefault((src, k), {"rows": 0, "upd": None})
                m["rows"] += 1
                m["upd"] = max(filter(None, [m["upd"], r["source_lastupdated"]]), default=None)
    wgi_all = rows(spark, "wgi_estimates", "country_code", "year", "wdi_match_status")
    wgi_eco = {r["country_code"] for r in wgi_all if r["wdi_match_status"] == "matched"}
    wgi_years = {r["year"] for r in wgi_all}
    cpia_rated = {r["country_code"] for r in rows(spark, "cpia_ratings", "country_code", "is_value_missing", "wdi_entity_type")
                  if not r["is_value_missing"] and r["wdi_entity_type"] == "economy"}
    inc_rows = rows(spark, "income_classification_history", "iso3_code", "fiscal_year", "income_group_code", "classification_status", "workbook_file_name",
                    "bronze_file_sha256")
    inc = {(r["iso3_code"], r["fiscal_year"]): r for r in inc_rows}
    (wb,), (sha,) = {r["workbook_file_name"] for r in inc_rows}, {r["bronze_file_sha256"] for r in inc_rows}  # exactly one source-wide value each
    versions = json.dumps({t: spark.sql(f"DESCRIBE HISTORY {S}{t} LIMIT 1").collect()[0].version for t in SOURCES}, separators=(",", ":"))
    out = {}
    for e in eco:
        for y in range(2000, 2026):
            k, v = (e["country_code"], y), vals.get((e["country_code"], y), {})
            n = {src: sum(v.get(c) is not None for c in m.values()) for src, m in (("wdi", WDI), ("wgi", WGI), ("cpia", CPIA))}
            wgi_status = ("economy_not_in_source" if e["country_code"] not in wgi_eco else "year_not_published" if y not in wgi_years
                          else "published" if n["wgi"] else "all_values_missing")
            cpia_status = "economy_never_rated" if e["country_code"] not in cpia_rated else "published" if n["cpia"] else "not_rated_in_year"
            ie, is_ = inc.get((e["country_wb_id"], y)), inc.get((e["country_wb_id"], y + 1))
            out[k] = {"country_code": e["country_code"], "country_iso3": e["country_wb_id"], "country_name": e["country_name"], "year": y,
                      "indicator_year_basis": "calendar_year",
                      **{c: v.get(c) for c in WDI.values()}, "wdi_values_present": n["wdi"], "wdi_source_lastupdated": meta.get(("wdi", k), {}).get("upd"),
                      **{c: v.get(c) for c in WGI.values()}, "wgi_values_present": n["wgi"], "wgi_coverage_status": wgi_status,
                      "wgi_source_lastupdated": meta.get(("wgi", k), {}).get("upd"),
                      **{c: v.get(c) for c in CPIA.values()}, "cpia_values_present": n["cpia"], "cpia_coverage_status": cpia_status,
                      "cpia_source_lastupdated": meta.get(("cpia", k), {}).get("upd"),
                      "income_fy_ending_in_year": y, "income_group_fy_ending_in_year": ie["income_group_code"] if ie else None,
                      "income_status_fy_ending_in_year": ie["classification_status"] if ie else "no_row_in_source",
                      "income_fy_starting_in_year": y + 1, "income_group_fy_starting_in_year": is_["income_group_code"] if is_ else None,
                      "income_status_fy_starting_in_year": is_["classification_status"] if is_ else "no_row_in_source",
                      "income_source_workbook": wb, "income_source_file_sha256": sha, "context_snapshot_semantics": "current_vintage_values_not_point_in_time",
                      "silver_source_versions": versions, "gold_run_id": run_id, "transform_version": "gold_country_context_v1.0.0"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--approved-dry-run")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    base = json.loads((run_dir / "baseline_before_write.json").read_text())
    spark = get_spark(args.profile)
    rep = DQReport(TARGET + (" (dry run, independent)" if args.dry_run else " (post-write, independent)"))
    if args.dry_run:
        from gold.country_context import transform as T
        dfs, versions = T.load_sources(spark)
        g = T.build(dfs, versions, args.run_id)
    else:
        g = spark.table(TARGET)
    rep.equals(f"schema_exact ({len(SCHEMA)} columns, names, order, types)", [(f.name, f.dataType.simpleString()) for f in g.schema.fields], SCHEMA)
    exp = expected(spark, args.run_id)
    cols = [n for n, _ in SCHEMA if n != "gold_processed_at"]
    assert set(cols) == set(next(iter(exp.values()))), "verifier must recompute every column except gold_processed_at"
    got = {(r["country_code"], r["year"]): r.asDict() for r in g.select(*cols).collect()}
    rep.equals("rows_and_unique_country_years", (g.count(), len(got)), (5642, 5642))
    rep.equals("country_years_equal_expected_grid", sorted(set(got) ^ set(exp))[:10], [])
    mism: dict = {}
    for k, e in exp.items():
        for c in cols:
            if got.get(k, {}).get(c) != e[c]:
                mism.setdefault(c, []).append(k)
    rep.equals(f"field_mismatches ({len(cols)} columns recomputed in Python; values exact)", {c: v[:5] for c, v in mism.items()}, {})
    value_cols = [*WDI.values(), *WGI.values(), *CPIA.values()]
    rep.equals("no_zero_filled_missing_values", sum(1 for k, e in exp.items() for c in value_cols if e[c] is None and got[k][c] is not None), 0)
    rep.equals("gold_processed_at_not_null", g.filter("gold_processed_at IS NULL").count(), 0)
    if not args.dry_run:
        props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {TARGET}").collect()}
        rep.equals("constraints_present_count", len([k for k in props if k.startswith("delta.constraints.")]), 13)
        rep.equals("run_id_property", props.get("gold.run_id"), args.run_id)
        rep.equals("not_null_columns_enforced", sum(1 for f in g.schema.fields if not f.nullable), 20)
        hist = [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {TARGET}").orderBy("version").collect()]
        rep.equals("history_first_operation_is_create", hist[0][1] if hist else None, "CREATE TABLE AS SELECT")
        rep.add("delta_version_after_write", hist[-1][0] if hist else None, "reported", True, "warn")
    else:
        rep.equals("target_still_absent", spark.catalog.tableExists(TARGET), False)
    if args.approved_dry_run:
        appr = json.loads((RUNS / args.approved_dry_run / "baseline_before_write.json").read_text())
        keys = ("bronze", "silver", "gold", "grants", "column_masks", "cohort_manifest_sha256")
        rep.equals("baseline_equals_approved_dry_run_baseline", {k: base[k] == appr[k] for k in keys}, {k: True for k in keys})
    B.check_unchanged(spark, base, rep, (17, 18, 3))
    print(rep.render())
    out = run_dir / ("dry_run_independent_verification.json" if args.dry_run else "post_write_verification.json")
    out.write_text(json.dumps({"run_id": args.run_id, "dry_run": args.dry_run, "verification": rep.to_dict(),
                               "verified_at": datetime.now(timezone.utc).isoformat()}, indent=1, default=str))
    print(f"\nreport: {out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
