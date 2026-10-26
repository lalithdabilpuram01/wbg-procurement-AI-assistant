"""Independent post-write verification of workspace.wbg_silver.projects (read-only).

Collects the 2,000 Bronze raw records and the written Silver rows locally and recomputes every expectation in
plain Python with json.loads (no reuse of the Spark transform): IDs vs the cohort manifest, country / geography
(wbdisc.countries.is_regional), WDI matching (exact + approved curated aliases), dates, amounts (NULL vs 0),
nested element counts and lineage. Also checks Delta constraints / NOT NULL and that Bronze, the existing Silver
tables and the cohort manifest are unchanged since the run's pre-write baseline.

Usage (from procurement-intelligence/):
  python -m silver.projects.verify_projects_silver --run-id projects_<timestamp>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd

from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.projects.transform import AMOUNTS, COHORT_MANIFEST, MILESTONES, WB_TO_ISO2

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "discovery"))
from wbdisc.countries import is_regional  # noqa: E402

BRONZE = "workspace.wbg_bronze.bronze_projects_v3"
TARGET = "workspace.wbg_silver.projects"
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

    bronze = {json.loads(r.raw_record)["id"]: (json.loads(r.raw_record), r) for r in spark.table(BRONZE).collect()}
    silver = {r.project_id: r for r in spark.table(TARGET).collect()}
    cohort = pd.read_csv(COHORT_MANIFEST, dtype=str, keep_default_na=False).set_index("project_id")
    wdi = {r.country_code: r.entity_type for r in spark.table("workspace.wbg_silver.wdi_country").select("country_code", "entity_type").collect()}

    rep.equals("row_count", len(silver), 2000)
    rep.equals("project_ids_equal_cohort_manifest", sorted(set(silver) ^ set(cohort.index)), [])
    rep.equals("project_ids_equal_bronze", sorted(set(silver) ^ set(bronze)), [])
    rep.equals("cohort_manifest_unchanged", hashlib.sha256(COHORT_MANIFEST.read_bytes()).hexdigest(), baseline["cohort_manifest_sha256"])
    bad: dict[str, list] = {k: [] for k in ["country", "geography", "wdi", "dates", "fiscal_year", "amounts", "nested", "lineage", "manifest"]}
    geo = {"single_country": 0, "regional_multicountry": 0}
    for pid, s in silver.items():
        raw, b = bronze[pid]
        cc = raw["countrycode"][0]
        reg = is_regional(cc)
        geo[s.geography_type] += 1
        if s.country_code != cc or len(raw["countrycode"]) != 1:
            bad["country"].append(pid)
        if (s.geography_type == "regional_multicountry") != reg or reg != (cohort.loc[pid, "country_is_regional"] == "True"):
            bad["geography"].append(pid)
        exp_wdi = None if reg else (cc if cc in wdi else (WB_TO_ISO2.get(cc) if WB_TO_ISO2.get(cc) in wdi else None))
        exp_method = "not_applicable_regional" if reg else ("exact" if cc in wdi else ("curated_alias" if exp_wdi else "unmatched"))
        if s.wdi_country_code != exp_wdi or s.wdi_match_method != exp_method or (exp_wdi and wdi[exp_wdi] != "economy"):
            bad["wdi"].append(pid)
        for src, col in (("boardapprovaldate", "board_approval_date"), ("closingdate", "closing_date"),
                         ("loan_effective_date", "loan_effective_date"), ("public_disclosure_date", "public_disclosure_date")):
            got = s[col].isoformat() if s[col] else None
            if got != (raw[src][:10] if src in raw else None):
                bad["dates"].append((pid, col))
        ms = raw["milestones"][0]
        for src, col in MILESTONES.items():
            if (s[col].isoformat() if s[col] else None) != (ms.get(src)[:10] if ms.get(src) else None):
                bad["dates"].append((pid, col))
        if s.approval_fiscal_year_raw != raw["fiscalyear"] or s.approval_fiscal_year != (None if raw["fiscalyear"] == "0" else int(raw["fiscalyear"])):
            bad["fiscal_year"].append(pid)
        for src, col in AMOUNTS.items():
            exp = Decimal(raw[src]) if src in raw else None
            if s[col] != exp:
                bad["amounts"].append((pid, col))
        if (len(s.financers or []) != len(raw.get("financers") or []) or len(s.isr_ratings_snapshot or []) != len(raw.get("isr_ratings") or [])
                or len(s.icr_ratings_snapshot or []) != len(raw.get("icr_ratings") or []) or len(s.ieg_ratings_snapshot or []) != len(raw.get("ieg_ratings") or [])
                or len(s.major_sectors or []) != len(raw.get("major_sectors") or [])
                or [f.amount_usd for f in s.financers or []] != [Decimal(f["fincr_usd_amt"]) for f in raw.get("financers") or []]):
            bad["nested"].append(pid)
        if (s.bronze_raw_record != b.raw_record or s.raw_record_sha256 != hashlib.sha256(b.raw_record.encode()).hexdigest()
                or s.bronze_source_file != b.source_file or s.bronze_ingested_at != b.ingested_at or s.bronze_cohort_id != b.cohort_id):
            bad["lineage"].append(pid)
        c = cohort.loc[pid]
        if (s.project_name != c.project_name or s.region_name != c.region or s.project_status != c.status):
            bad["manifest"].append(pid)
    for k, v in bad.items():
        rep.equals(f"mismatches:{k}", v[:10], [])
    rep.equals("geography_counts", geo, {"single_country": 1861, "regional_multicountry": 139})
    rep.equals("zero_vs_null_amounts", {col: (sum(1 for s in silver.values() if s[col] is None), sum(1 for s in silver.values() if s[col] == 0)) for col in AMOUNTS.values()},
               {col: (sum(1 for raw, _ in bronze.values() if src not in raw), sum(1 for raw, _ in bronze.values() if src in raw and Decimal(raw[src]) == 0))
                for src, col in AMOUNTS.items()})
    props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {TARGET}").collect()}
    rep.equals("constraints_present", sorted(k for k in props if k.startswith("delta.constraints.")),
               ["delta.constraints.geography_type_valid", "delta.constraints.project_id_format", "delta.constraints.regional_not_mapped",
                "delta.constraints.wdi_match_method_valid"])
    rep.equals("run_id_property", props.get("silver.run_id"), args.run_id)
    nn = {f.name for f in spark.table(TARGET).schema.fields if not f.nullable}
    rep.equals("not_null_enforced", sorted({"project_id", "country_code", "geography_type", "wdi_match_method", "bronze_raw_record", "raw_record_sha256"} - nn), [])
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
