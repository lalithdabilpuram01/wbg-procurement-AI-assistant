"""Independent verification of wbg_silver.project_details_v3 and wbg_silver.project_loan_transactions (read-only).

Re-reads the Volume JSONL (bytes via binaryFile, split on LF only), parses every record with Python's json module and
recomputes every Silver column, nested arrays included, with its own copy of the rules (no reuse of the Spark transform
or checks): field selection, theme hierarchy with integer seqno/percent, indicator and location positions, exact
Decimal coordinates and amounts, dates with the 1900-01-01 placeholder -> NULL + flag, identical-row group sizes and all
lineage fields. It also checks the exact schemas (names, order, nested types), that every nested source key is mapped,
an independent personal-data scan (counts only), cohort / Projects Silver membership, and that all Bronze and existing
Silver tables, row counts, column masks and the cohort are unchanged against the run baseline.

--dry-run compares against the DataFrames built by the transform (nothing is read from or written to the targets);
without it, the written tables are verified, plus constraints, NOT NULL, run_id, history and the known anomalies.

Usage (from procurement-intelligence/):
  python -m silver.project_details.verify_project_details_silver --dry-run --run-id project_details_<ts>
  python -m silver.project_details.verify_project_details_silver --run-id project_details_<ts> --approved-dry-run project_details_<ts>
"""
from __future__ import annotations

from app_config.paths import BRONZE_INPUTS_MANIFEST, COHORT_MANIFEST, SOURCE_PULLS_SAMPLES_MANIFEST
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "silver" / "runs"
COHORT = COHORT_MANIFEST
MANIFEST = BRONZE_INPUTS_MANIFEST
SAMPLES_MANIFEST = SOURCE_PULLS_SAMPLES_MANIFEST
BRONZE = "workspace.wbg_bronze.bronze_projects_v3_allfields"
TABLES = {"details": "workspace.wbg_silver.project_details_v3", "transactions": "workspace.wbg_silver.project_loan_transactions"}
ROWS = {"details": 184, "transactions": 16643}
VERSION = "project_details_silver_v1.0.0"
CONSTRAINTS = {"details": ["counts_non_negative", "detail_coverage_value", "parent_project_format", "project_id_format", "risk_rating_valid",
                           "snapshot_semantics_value"],
               "transactions": ["financing_number_consistent", "identical_group_positive", "index_non_negative", "placeholder_consistent",
                                "project_id_format", "transaction_key_consistent", "transaction_type_valid"]}
NESTED_KEYS = {"theme1": {"code", "name", "seqno", "percent", "theme2"}, "theme2": {"code", "name", "seqno", "percent", "theme3"},
               "theme3": {"code", "name", "seqno", "percent"}, "indicator": {"Name", "Type", "Subtype", "Baseline", "Current", "Target"},
               "measure": {"Value", "Date", "Comment"},
               "geo": {"geo_loc_id", "place_id", "wbg_cntry_key", "geo_loc_name", "geo_latitude_nbr", "geo_longitude_nbr", "admin_unit1_name", "admin_unit2_name"},
               "transaction": {"amount", "period", "transactiontype", "financier_loan"}}
_TH3 = "struct<code:string,name:string,seqno:int,percent:int>"
_TH2 = f"struct<code:string,name:string,seqno:int,percent:int,level3:array<{_TH3}>>"
_M = "struct<value:string,date:date,comment:string>"
LINEAGE = [("source_api", "string"), ("raw_record_sha256", "string"), ("bronze_table", "string"), ("bronze_source_file", "string"),
           ("bronze_cohort_id", "string"), ("bronze_ingested_at", "timestamp"), ("bronze_input_file_sha256", "string"),
           ("source_population_sha256", "string"), ("source_retrieved_at", "timestamp"), ("silver_run_id", "string"),
           ("silver_processed_at", "timestamp"), ("transform_version", "string")]
SCHEMA = {
    "details": [("project_id", "string"), ("product_line_code", "string"), ("environmental_social_risk_rating", "string"), ("parent_project_id", "string"),
                ("p2a_flag", "string"), ("p2a_updated_date", "date"),
                ("themes", f"array<struct<code:string,name:string,seqno:int,percent:int,level2:array<{_TH2}>>>"),
                ("themes_level2", "array<string>"), ("themes_level3", "array<string>"),
                ("results_indicators", f"array<struct<indicator_index:int,name:string,indicator_type:string,subtype:string,baseline:{_M},current:{_M},target:{_M}>>"),
                ("geo_locations", "array<struct<location_index:int,geo_loc_id:string,place_id:string,country_key:string,name:string,latitude:decimal(10,7),"
                                  "longitude:decimal(10,7),admin_unit1_name:string,admin_unit2_name:string>>"),
                ("theme_level1_count", "int"), ("indicator_count", "int"), ("geo_location_count", "int"), ("loan_transaction_count", "int"),
                ("detail_coverage", "string"), ("record_snapshot_semantics", "string"), *LINEAGE],
    "transactions": [("transaction_key", "string"), ("project_id", "string"), ("transaction_index", "int"), ("financier_loan", "string"),
                     ("financing_number", "string"), ("financing_number_prefix", "string"), ("transaction_type", "string"),
                     ("transaction_period_published", "string"), ("transaction_date", "date"), ("transaction_date_is_placeholder", "boolean"),
                     ("amount_published", "string"), ("amount", "decimal(22,2)"), ("identical_transaction_group_size", "int"),
                     ("transaction_snapshot_semantics", "string"), *LINEAGE],
}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ID11 = re.compile(r"(?<![0-9.])[0-9]{11}(?![0-9]|\.[0-9])")  # an 11-digit integer token (staff-ID shape)
ID11_DECIMAL = re.compile(r"(?<![0-9.])[0-9]{11}\.[0-9]")  # integer part of a decimal number (e.g. an indicator target), reported only


def epoch(v) -> int | None:
    """Timezone-free instant (Databricks Connect collect() returns naive local-time datetimes)."""
    if v is None:
        return None
    return int(datetime.fromisoformat(v).timestamp()) if isinstance(v, str) else int(v.timestamp())


def iso_date(s: str | None) -> date | None:
    """A yyyy-mm-dd prefix as a date; None when absent or not a valid date."""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date() if s else None
    except ValueError:
        return None


def integer(s: str | None) -> int | None:
    return int(s) if s is not None and re.fullmatch(r"-?[0-9]+", s) else None


def dec(s: str | None, places: str) -> Decimal | None:
    """A numeric string rounded half-up to `places`; None when absent or not a number."""
    try:
        return Decimal(s.strip()).quantize(Decimal(places), rounding=ROUND_HALF_UP) if s is not None else None
    except InvalidOperation:
        return None


def theme(x: dict, level: int) -> dict:
    """One published theme as the expected nested dict (child levels down to level 3)."""
    out = {"code": x.get("code"), "name": x.get("name"), "seqno": integer(x.get("seqno")), "percent": integer(x.get("percent"))}
    if level < 3:
        kids = x.get(f"theme{level + 1}")
        out[f"level{level + 1}"] = None if kids is None else [theme(k, level + 1) for k in kids]
    return out


def measure(m: dict | None) -> dict | None:
    return None if m is None else {"value": m.get("Value"), "date": iso_date(m.get("Date")), "comment": m.get("Comment")}


def expected(lines: list[str], lineage: dict) -> tuple[dict, dict, dict]:
    """Python recomputation of both tables keyed by project_id / transaction_key, plus unmapped nested keys."""
    det: dict = {}
    tx: dict = {}
    unmapped: Counter[str] = Counter()
    for line in lines:
        r = json.loads(line)
        pid = r["id"]
        lin = {**lineage, "raw_record_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest()}
        for t1 in r.get("theme_list") or []:
            unmapped.update(f"theme1.{k}" for k in set(t1) - NESTED_KEYS["theme1"])
            for t2 in t1.get("theme2") or []:
                unmapped.update(f"theme2.{k}" for k in set(t2) - NESTED_KEYS["theme2"])
                for t3 in t2.get("theme3") or []:
                    unmapped.update(f"theme3.{k}" for k in set(t3) - NESTED_KEYS["theme3"])
        for x in r.get("indicators") or []:
            unmapped.update(f"indicator.{k}" for k in set(x) - NESTED_KEYS["indicator"])
            for m in ("Baseline", "Current", "Target"):
                unmapped.update(f"measure.{k}" for k in set(x.get(m) or {}) - NESTED_KEYS["measure"])
        for g in r.get("geo_locations") or []:
            unmapped.update(f"geo.{k}" for k in set(g) - NESTED_KEYS["geo"])
        txs = r.get("loan_transactions")
        det[pid] = {
            "project_id": pid, "product_line_code": r.get("prodline_exact"), "environmental_social_risk_rating": r.get("esrc_ovrl_risk_rate"),
            "parent_project_id": r.get("parentprojid"), "p2a_flag": r.get("p2a_flag"), "p2a_updated_date": iso_date(r.get("p2a_updated_date")),
            "themes": None if r.get("theme_list") is None else [theme(t, 1) for t in r["theme_list"]],
            "themes_level2": r.get("themev2_level2_exact"), "themes_level3": r.get("themev2_level3_exact"),
            "results_indicators": None if r.get("indicators") is None else [
                {"indicator_index": i, "name": x.get("Name"), "indicator_type": x.get("Type"), "subtype": x.get("Subtype"),
                 "baseline": measure(x.get("Baseline")), "current": measure(x.get("Current")), "target": measure(x.get("Target"))}
                for i, x in enumerate(r["indicators"])],
            "geo_locations": None if r.get("geo_locations") is None else [
                {"location_index": i, "geo_loc_id": g.get("geo_loc_id"), "place_id": g.get("place_id"), "country_key": g.get("wbg_cntry_key"),
                 "name": g.get("geo_loc_name"), "latitude": dec(g.get("geo_latitude_nbr"), "1e-7"), "longitude": dec(g.get("geo_longitude_nbr"), "1e-7"),
                 "admin_unit1_name": g.get("admin_unit1_name"), "admin_unit2_name": g.get("admin_unit2_name")}
                for i, g in enumerate(r["geo_locations"])],
            "theme_level1_count": len(r.get("theme_list") or []), "indicator_count": len(r.get("indicators") or []),
            "geo_location_count": len(r.get("geo_locations") or []), "loan_transaction_count": len(txs or []),
            "detail_coverage": "phase1_cluster_detail_pull", "record_snapshot_semantics": "current_values_as_of_source_retrieval", **lin}
        ident = Counter((t.get("financier_loan"), t.get("period"), t.get("transactiontype"), t.get("amount")) for t in txs or [])
        for i, t in enumerate(txs or []):
            unmapped.update(f"transaction.{k}" for k in set(t) - NESTED_KEYS["transaction"])
            loan, period, amt = t.get("financier_loan"), t.get("period"), t.get("amount")
            placeholder = period is not None and period.startswith("1900-01-01")
            unambiguous = amt is not None and re.fullmatch(r"-?[0-9]+(\.[0-9]+)?([Ee][+-]?[0-9]+)?", amt) is not None
            pre = re.match(r"[A-Z]+", loan) if loan is not None else None
            tx[f"{pid}:{i}"] = {
                "transaction_key": f"{pid}:{i}", "project_id": pid, "transaction_index": i, "financier_loan": loan,
                "financing_number": None if loan is None else loan.replace("-", ""), "financing_number_prefix": None if loan is None else (pre.group(0) if pre else ""),
                "transaction_type": t.get("transactiontype"), "transaction_period_published": period,
                "transaction_date": None if placeholder else iso_date(period), "transaction_date_is_placeholder": placeholder,
                "amount_published": amt, "amount": dec(amt, "0.01") if unambiguous else None,
                "identical_transaction_group_size": ident[(loan, period, t.get("transactiontype"), amt)],
                "transaction_snapshot_semantics": "dated_records_from_snapshot_retrieved_2026_10_21", **lin}
    return det, tx, dict(unmapped)


def personal_scan(rows: list[dict], names: set[str], upis: set[str]) -> dict:
    """Counts of rows whose serialised values contain a team-lead name / UPI / e-mail / 11-digit id (values never printed).
    SHA-256 lineage columns are hex digests and are left out of the pattern scans (they can contain 11-digit runs)."""
    hits: Counter[str] = Counter()
    for row in rows:
        text = json.dumps({k: v for k, v in row.items() if not k.endswith("_sha256")}, default=str, ensure_ascii=False)
        low = text.lower()
        hits["team_lead_name"] += any(n in low for n in names)
        hits["team_lead_upi"] += any(u in text for u in upis)
        hits["email"] += bool(EMAIL.search(text))
        hits["eleven_digit_id"] += bool(ID11.search(text))
    return dict(hits)


def decimal_11_digit_rows(rows: list[dict]) -> int:
    return sum(bool(ID11_DECIMAL.search(json.dumps({k: v for k, v in r.items() if not k.endswith("_sha256")}, default=str))) for r in rows)


def main() -> int:
    """Recompute both tables in plain Python and compare (written tables, or the transform's frames with --dry-run)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--dry-run", action="store_true", help="verify the transform's DataFrames instead of the written tables")
    ap.add_argument("--approved-dry-run")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    if not args.dry_run and not args.approved_dry_run:
        ap.error("post-write verification requires --approved-dry-run")
    run_dir = RUNS / args.run_id
    baseline = json.loads((run_dir / "baseline_before_write.json").read_text())
    spark = get_spark(args.profile)
    rep = DQReport(", ".join(TABLES.values()) + (" (dry run, independent)" if args.dry_run else " (post-write, independent)"))

    bronze = spark.table(BRONZE)
    files = [r[0] for r in bronze.select("source_file").distinct().collect()]
    ingested = {epoch(r[0]) for r in bronze.select("ingested_at").distinct().collect()}
    cohort_ids = {r[0] for r in bronze.select("cohort_id").distinct().collect()}
    rep.equals("bronze_single_source_file_ingestion_cohort", (len(files), len(ingested), sorted(cohort_ids)), (1, 1, ["wbg_poc_2000"]))
    raw = bytes(spark.read.format("binaryFile").load(files[0]).select("content").collect()[0][0])
    entry = next(f for f in json.loads(MANIFEST.read_text())["files"] if f["bronze_table"] == "bronze_projects_v3_allfields")
    pull = next(json.loads(ln) for ln in SAMPLES_MANIFEST.read_text().splitlines() if Path(entry["source_file"]).name in ln)
    rep.equals("volume_file_sha256_equals_manifest", hashlib.sha256(raw).hexdigest(), entry["output_sha256"])
    text = raw.decode("utf-8")
    rep.equals("volume_file_ends_with_single_lf", text.endswith("\n") and not text.endswith("\n\n"), True)
    lines = text[:-1].split("\n")
    rep.equals("volume_file_records", len(lines), ROWS["details"])
    lineage = {"source_api": "search.worldbank.org/api/v3/projects (id=<P>, fl=*)", "bronze_table": BRONZE, "bronze_source_file": files[0],
               "bronze_cohort_id": "wbg_poc_2000", "bronze_ingested_at": next(iter(ingested)), "bronze_input_file_sha256": entry["output_sha256"],
               "source_population_sha256": entry["source_sha256"], "source_retrieved_at": epoch(pull["extracted_at_utc"]),
               "silver_run_id": args.run_id, "transform_version": VERSION}
    exp_details, exp_tx, unmapped = expected(lines, lineage)
    exp = {"details": exp_details, "transactions": exp_tx}
    rep.equals("nested_source_keys_all_mapped", unmapped, {})

    if args.dry_run:
        from silver.project_details import transform as T
        prov = T.load_provenance()
        frames = {"details": T.drop_validation(T.build_details(bronze, prov, args.run_id)),
                  "transactions": T.drop_validation(T.build_transactions(bronze, prov, args.run_id))}
    else:
        frames = {k: spark.table(v) for k, v in TABLES.items()}
    key = {"details": "project_id", "transactions": "transaction_key"}
    got_rows: dict[str, list[dict]] = {}
    for k, df in frames.items():
        rep.equals(f"{k}:schema_exact ({len(SCHEMA[k])} columns, names, order, nested types)",
                   [(f.name, f.dataType.simpleString()) for f in df.schema.fields], SCHEMA[k])
        cols = [n for n, _ in SCHEMA[k] if n != "silver_processed_at"]
        assert set(cols) == set(next(iter(exp[k].values()))), f"{k}: verifier must recompute every column except silver_processed_at"
        rows = [r.asDict(recursive=True) for r in df.select(*cols).collect()]
        got_rows[k] = rows
        got = {x[key[k]]: {**x, "bronze_ingested_at": epoch(x["bronze_ingested_at"]), "source_retrieved_at": epoch(x["source_retrieved_at"])} for x in rows}
        rep.equals(f"{k}:row_count", len(rows), ROWS[k])
        rep.equals(f"{k}:distinct_keys", len(got), ROWS[k])
        rep.equals(f"{k}:keys_equal_source", sorted(set(got) ^ set(exp[k]))[:10], [])
        mism: dict = {}
        for kk, e in exp[k].items():
            for col in cols:
                if got.get(kk, {}).get(col) != e[col]:
                    mism.setdefault(col, []).append(kk)
        rep.equals(f"{k}:field_mismatches ({len(cols)} columns incl. nested arrays recomputed in Python)", {c: v[:5] for c, v in mism.items()}, {})
        rep.equals(f"{k}:silver_processed_at_not_null", df.filter("silver_processed_at IS NULL").count(), 0)

    rep.equals("transactions_equal_sum_of_detail_counts", sum(e["loan_transaction_count"] for e in exp["details"].values()), len(exp["transactions"]))
    cohort = set(pd.read_csv(COHORT, dtype=str, keep_default_na=False)["project_id"])
    projects = {r[0] for r in spark.table("workspace.wbg_silver.projects").select("project_id").collect()}
    rep.equals("project_ids_in_cohort", sorted(set(exp["details"]) - cohort)[:10], [])
    rep.equals("project_ids_in_silver_projects", sorted(set(exp["details"]) - projects)[:10], [])
    tx_projects = {e["project_id"] for e in exp["transactions"].values()}
    rep.equals("transaction_projects_count_and_in_cohort", (len(tx_projects), sorted(tx_projects - cohort)[:10]), (134, []))
    recs = [json.loads(ln) for ln in lines]
    names = {n.strip().lower() for r in recs for n in (r.get("teamleadname") or []) if isinstance(n, str) and len(n.strip()) >= 4}
    upis = {u for r in recs for u in re.findall(r"[0-9]{6,}", str(r.get("teamleaderupi") or ""))}
    rep.equals("personal_reference_sets_loaded (counts)", (len(names) > 0, len(upis) > 0), (True, True))
    for k, rows in got_rows.items():
        rep.equals(f"{k}:personal_data_scan_row_counts (values not printed)", personal_scan(rows, names, upis),
                   {"team_lead_name": 0, "team_lead_upi": 0, "email": 0, "eleven_digit_id": 0})
        rep.add(f"{k}:rows_with_11_digit_decimal_numbers (numeric values, reported)", decimal_11_digit_rows(rows), "reported", True, "warn")

    if not args.dry_run:
        for k, tbl in TABLES.items():
            props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {tbl}").collect()}
            rep.equals(f"{k}:constraints_present", sorted(x.split(".")[-1] for x in props if x.startswith("delta.constraints.")), CONSTRAINTS[k])
            rep.equals(f"{k}:run_id_property", props.get("silver.run_id"), args.run_id)
            nn = {f.name for f in spark.table(tbl).schema.fields if not f.nullable}
            rep.add(f"{k}:not_null_columns", sorted(nn), "reported", len(nn) >= 12, "error")
            comments = {r[0]: r[2] for r in spark.sql(f"DESCRIBE TABLE {tbl}").collect() if r[0] and not r[0].startswith("#")}
            rep.equals(f"{k}:pii_free_column_names", sorted(c for c in comments if re.search(r"teamlead|upi|staff|email|phone|raw_record$", c, re.I)), [])
            rep.add(f"{k}:columns_with_comments", sum(1 for v in comments.values() if v), "reported", True, "warn")
            rep.equals(f"{k}:key_not_null_enforced", [f.nullable for f in spark.table(tbl).schema.fields if f.name == key[k]], [False])
            hist = [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {tbl}").orderBy("version").collect()]
            rep.equals(f"{k}:history_first_operation_is_create", hist[0][1] if hist else None, "CREATE TABLE AS SELECT")
            rep.add(f"{k}:delta_version_after_write", hist[-1][0] if hist else None, "reported", True, "warn")
        approved = json.loads((RUNS / args.approved_dry_run / "known_anomalies.json").read_text())
        rep.equals("known_anomalies_equal_approved_dry_run", json.loads((run_dir / "known_anomalies.json").read_text()), approved)
    else:
        rep.equals("targets_still_absent", {k: spark.catalog.tableExists(v) for k, v in TABLES.items()}, {k: False for k in TABLES})
    rep.equals("cohort_manifest_unchanged", hashlib.sha256(COHORT.read_bytes()).hexdigest(), baseline["cohort_manifest_sha256"])
    masks = sorted([r[0], r[1], r[2], r[3]] for r in spark.sql(
        "SELECT table_schema, table_name, column_name, mask_name FROM workspace.information_schema.column_masks").collect())
    rep.equals("column_masks_unchanged", masks, baseline["column_masks"])
    rep.equals("baseline_covers_17_bronze_16_silver", (len(baseline["bronze"]), len(baseline["silver"])), (17, 16))
    for tbl, before in {**baseline["bronze"], **baseline["silver"]}.items():
        h = spark.sql(f"DESCRIBE HISTORY {tbl} LIMIT 1").collect()[0]
        rep.equals(f"unchanged:{tbl}", {"version": h.version, "rows": spark.table(tbl).count()}, before)
    print(rep.render())
    out = run_dir / ("dry_run_independent_verification.json" if args.dry_run else "post_write_verification.json")
    out.write_text(json.dumps({"run_id": args.run_id, "dry_run": args.dry_run, "verification": rep.to_dict()}, indent=1, default=str))
    print(f"\nreport: {out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
