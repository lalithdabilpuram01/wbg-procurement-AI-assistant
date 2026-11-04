"""Independent post-write verification of the contract-award Silver tables (read-only).

Re-reads the two Volume CSVs (bytes via binaryFile), parses them with pandas, and recomputes every expectation in
plain Python with exact Decimal arithmetic (no reuse of the Spark transform): line keys, supplier-ID types,
amounts (NULL vs 0), dates, row hashes, contract totals = sum of supplier lines, legacy rows. Also checks Delta
constraints / NOT NULL and that Bronze, existing Silver tables and the cohort manifest are unchanged.

Usage (from procurement-intelligence/):
  python -m silver.contracts.verify_contracts_silver --run-id contracts_<timestamp>
"""
from __future__ import annotations

from app_config.paths import COHORT_MANIFEST
import argparse
import hashlib
import io
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

S = "workspace.wbg_silver"
T = {"lines": f"{S}.contract_award_lines", "contracts": f"{S}.contract_awards", "legacy": f"{S}.contract_awards_legacy"}
BRONZE = {"ds5": "workspace.wbg_bronze.bronze_contract_awards_ds00005", "ds1": "workspace.wbg_bronze.bronze_contract_awards_legacy_ds01004"}
RUNS = Path(__file__).resolve().parents[1] / "runs"
COHORT = COHORT_MANIFEST
SEP = "\x1f"


def load_csv(spark, table: str) -> pd.DataFrame:
    """The Volume CSV behind a Bronze table, re-read from its bytes with pandas (every value as a string)."""
    path = spark.table(table).select("source_file").distinct().collect()[0][0]
    content = bytes(spark.read.format("binaryFile").load(path).select("content").collect()[0][0])
    return pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, encoding="utf-8-sig")


def d(s: str) -> Decimal | None:
    return Decimal(s) if s.strip() != "" else None


def date(s: str):
    return datetime.strptime(s, "%m/%d/%Y").date() if s.strip() else None


def main() -> int:
    """Re-check the written contract tables against the Volume CSVs and the baseline; return 0 when all passed."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    baseline = json.loads((run_dir / "baseline_before_write.json").read_text())
    spark = get_spark(args.profile)
    rep = DQReport("contract award Silver tables (post-write, independent)")

    # ---------- DS00005 lines ----------
    src5 = load_csv(spark, BRONZE["ds5"])
    exp_lines = {}
    for row in src5.itertuples(index=False, name=None):
        r = dict(zip(src5.columns, row))
        sid = r["Supplier ID"]
        key = f"{r['WB Contract Number']}:" + (sid if sid else "NO_SUPPLIER_ID-" + hashlib.sha256(r["Supplier"].encode()).hexdigest()[:12])
        typ = "missing" if not sid else ("anonymized_individual_consultant" if re.fullmatch(r"[0-9a-f]{16}", sid) else "numeric")
        exp_lines[key] = (r["WB Contract Number"], sid or None, typ, d(r["Supplier Contract Amount (USD)"]), date(r["Contract Signing Date"]),
                          r["Project ID"], hashlib.sha256(SEP.join(row).encode("utf-8")).hexdigest())
    got = {r.line_key: (r.wb_contract_number, r.supplier_id, r.supplier_id_type, r.supplier_amount_usd, r.contract_signing_date, r.project_id,
                        r.source_row_sha256) for r in spark.table(T["lines"]).select("line_key", "wb_contract_number", "supplier_id", "supplier_id_type",
                                                                                     "supplier_amount_usd", "contract_signing_date", "project_id",
                                                                                     "source_row_sha256").collect()}
    rep.equals("lines:row_count", len(got), len(src5))
    rep.equals("lines:line_keys_equal_source", sorted(set(got) ^ set(exp_lines))[:10], [])
    rep.equals("lines:field_mismatches (contract, supplier id, type, amount, date, project, row hash)",
               sorted(k for k in exp_lines if got.get(k) != exp_lines[k])[:10], [])
    rep.equals("lines:null_vs_zero_amounts", (sum(v[3] is None for v in got.values()), sum(v[3] == 0 for v in got.values())),
               (sum(v[3] is None for v in exp_lines.values()), sum(v[3] == 0 for v in exp_lines.values())))
    # ---------- DS00005 contracts ----------
    tot: dict = defaultdict(lambda: Decimal(0))
    cnt: dict = defaultdict(int)
    for v in exp_lines.values():
        cnt[v[0]] += 1
        if v[3] is not None:
            tot[v[0]] += v[3]
    gc = {r.wb_contract_number: (r.contract_amount_usd, r.supplier_line_count, r.is_multi_supplier)
          for r in spark.table(T["contracts"]).select("wb_contract_number", "contract_amount_usd", "supplier_line_count", "is_multi_supplier").collect()}
    rep.equals("contracts:row_count_equals_distinct_contract_numbers", len(gc), len(cnt))
    rep.equals("contracts:keys_equal_source", sorted(set(gc) ^ set(cnt))[:10], [])
    rep.equals("contracts:amount_equals_sum_of_source_lines", sorted(k for k in cnt if gc.get(k, (None,))[0] != tot[k])[:10], [])
    rep.equals("contracts:line_counts", sorted(k for k in cnt if gc[k][1] != cnt[k] or gc[k][2] != (cnt[k] > 1))[:10], [])
    rep.equals("contracts:grand_total_equals_lines_total", str(sum(v[0] for v in gc.values())), str(sum(v[3] for v in exp_lines.values() if v[3] is not None)))
    # ---------- DS01004 legacy ----------
    src1 = load_csv(spark, BRONZE["ds1"])
    exp_leg = {r["Contract Number"]: (d(r["Supplier Contract Amount (US$)"]), date(r["Contract Signing Date"]), r["Project ID"],
                                      r["Borrower Contract Reference Number"] or None, r["Supplier"],
                                      hashlib.sha256(SEP.join(row).encode("utf-8")).hexdigest())
               for row in src1.itertuples(index=False, name=None) for r in [dict(zip(src1.columns, row))]}
    gl = {r.contract_number: (r.contract_amount_usd, r.contract_signing_date, r.project_id, r.borrower_contract_reference, r.supplier_text, r.source_row_sha256)
          for r in spark.table(T["legacy"]).select("contract_number", "contract_amount_usd", "contract_signing_date", "project_id",
                                                   "borrower_contract_reference", "supplier_text", "source_row_sha256").collect()}
    rep.equals("legacy:row_count", len(gl), len(src1))
    rep.equals("legacy:field_mismatches (amount, date, project, reference, supplier text as published, row hash)",
               sorted(k for k in exp_leg if gl.get(k) != exp_leg[k])[:10], [])
    rep.equals("legacy:no_contract_numbers_shared_with_ds00005", len(set(gl) & set(gc)), 0)
    # ---------- cohort / projects ----------
    projects = {r[0] for r in spark.table(f"{S}.projects").select("project_id").collect()}
    rep.equals("project_ids_subset_of_silver_projects", sorted({v[5] for v in exp_lines.values()} - projects) + sorted({v[2] for v in exp_leg.values()} - projects), [])
    rep.equals("all_2000_projects_have_ds00005_lines", len(projects - {v[5] for v in exp_lines.values()}), 0)
    rep.equals("cohort_manifest_unchanged", hashlib.sha256(COHORT.read_bytes()).hexdigest(), baseline["cohort_manifest_sha256"])
    # ---------- Delta metadata ----------
    expected_constraints = {"lines": ["amount_non_negative", "missing_flag_consistent", "project_id_format", "supplier_id_type_valid"],
                            "contracts": ["amount_non_negative", "line_count_positive", "project_id_format"],
                            "legacy": ["amount_non_negative", "project_id_format"]}
    hist = {}
    for k, t in T.items():
        props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {t}").collect()}
        rep.equals(f"{k}:constraints_present", sorted(p.split(".")[-1] for p in props if p.startswith("delta.constraints.")), expected_constraints[k])
        rep.equals(f"{k}:run_id_property", props.get("silver.run_id"), args.run_id)
        hist[t] = [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {t}").orderBy("version").collect()]
    nn = {f.name for f in spark.table(T["lines"]).schema.fields if not f.nullable}
    rep.equals("lines:not_null_enforced", sorted({"line_key", "wb_contract_number", "project_id", "source_row_sha256"} - nn), [])
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
