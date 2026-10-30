"""Independent post-write verification of the procurement-notice Silver tables (read-only).

Re-reads the DS00979 CSV, the procnotices JSONL and the DS00005 CSV from the Volume (bytes via binaryFile), and
recomputes every expectation in plain Python with the Phase 1 implementations (wbdisc normalize_ref,
parse_award_notice), without reusing the Spark transform: notice keys, URLs, dates, row hashes, duplicate-group sizes,
API fields, every candidate pair and every evidence-supported link with its cardinality and ambiguity status. Also checks
the D-14 column mask (placeholder-only reads, mask metadata, refused time travel, no e-mail addresses elsewhere), Delta
constraints / NOT NULL / run_id, and that Bronze, the existing Silver tables and the cohort manifest are unchanged.

Usage (from procurement-intelligence/):
  python -m silver.notices.verify_notices_silver --run-id notices_<timestamp>
"""
from __future__ import annotations

from app_config.paths import COHORT_MANIFEST
import argparse
import hashlib
import io
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from silver.common.dq import DQReport
from silver.common.session import DEFAULT_PROFILE, get_spark

from wbdisc.award_notice import parse_award_notice  # noqa: E402
from wbdisc.normalize import normalize_ref  # noqa: E402

from silver.notices.transform import EMAIL_RE, PHONE_RE, TEXT_MASK_FUNCTION, TEXT_MASK_GROUP, TEXT_PLACEHOLDER  # noqa: E402  (constants only)

S = "workspace.wbg_silver"
T = {"notices": f"{S}.procurement_notices", "details": f"{S}.procurement_notice_details",
     "candidates": f"{S}.notice_contract_link_candidates", "links": f"{S}.notice_contract_links"}
BRONZE = {"csv": "workspace.wbg_bronze.bronze_procurement_notices_ds00979", "api": "workspace.wbg_bronze.bronze_procurement_notices_api",
          "ds5": "workspace.wbg_bronze.bronze_contract_awards_ds00005"}
RUNS = Path(__file__).resolve().parents[1] / "runs"
COHORT = COHORT_MANIFEST
SEP = "\x1f"
URL = "https://projects.worldbank.org/en/projects-operations/procurement-detail/"


def volume_bytes(spark, table: str) -> bytes:
    path = spark.table(table).select("source_file").distinct().collect()[0][0]
    return bytes(spark.read.format("binaryFile").load(path).select("content").collect()[0][0])


def csv(spark, table: str) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(volume_bytes(spark, table)), dtype=str, keep_default_na=False, encoding="utf-8-sig")


def d_us(s: str):
    return datetime.strptime(s, "%m/%d/%Y").date() if s else None


def sha(s: str | None) -> str | None:
    return hashlib.sha256(s.encode("utf-8")).hexdigest() if s is not None else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    baseline = json.loads((run_dir / "baseline_before_write.json").read_text())
    spark = get_spark(args.profile)
    rep = DQReport("procurement notice Silver tables (post-write, independent)")

    # ---------- DS00979 notices ----------
    src = csv(spark, BRONZE["csv"])
    content = [c for c in src.columns if c not in ("ID", "URL")]
    grp = Counter(tuple(r) for r in src[content].itertuples(index=False, name=None))
    raw_lines = [ln for ln in volume_bytes(spark, BRONZE["api"]).decode("utf-8").split("\n") if ln.strip()]  # LF only (U+2028 occurs in text)
    api = [json.loads(ln) for ln in raw_lines]
    api_nids = {str(int(r["id"][2:])) for r in api}
    exp_n = {}
    for row in src.itertuples(index=False, name=None):
        r = dict(zip(src.columns, row))
        exp_n[r["ID"]] = ("OP" + r["ID"].zfill(8), r["URL"], r["Notice Type"], d_us(r["Publication Date"]), d_us(r["Deadline Date"]), r["Project ID"],
                          r["Bid Description"] or None, r["Country Code"] or None, sha(SEP.join(row)), grp[tuple(r[c] for c in content)], r["ID"] in api_nids)
    got_n = {r[0]: tuple(r[1:]) for r in spark.table(T["notices"]).select(
        "notice_id", "notice_op_id", "notice_url", "notice_type", "publication_date", "deadline_date", "project_id", "bid_description", "country_code",
        "source_row_sha256", "identical_metadata_group_size", "has_api_detail").collect()}
    rep.equals("notices:row_count", len(got_n), len(src))
    rep.equals("notices:keys_equal_source", sorted(set(got_n) ^ set(exp_n))[:10], [])
    rep.equals("notices:field_mismatches (op id, url, type, dates, project, description, country, row hash, dup group, api flag)",
               sorted(k for k in exp_n if got_n.get(k) != exp_n[k])[:10], [])
    rep.equals("notices:url_is_prefix_plus_op_id", sum(v[1] != URL + v[0] for v in exp_n.values()), 0)

    # ---------- API details ----------
    exp_d = {}
    for ln, r in zip(raw_lines, api):
        p = parse_award_notice(r.get("notice_text")) if r["notice_type"] == "Contract Award" else {}
        nd = r.get("noticedate")
        exp_d[r["id"]] = (str(int(r["id"][2:])), r["notice_type"], r["notice_status"], datetime.strptime(nd, "%d-%b-%Y").date() if nd else None,
                          r["project_id"], r.get("bid_reference_no"), normalize_ref(r.get("bid_reference_no")), sha(r.get("notice_text")),
                          None if r.get("notice_text") is None else bool(re.search(EMAIL_RE, r["notice_text"])),
                          None if r.get("notice_text") is None else bool(re.search(PHONE_RE, r["notice_text"], re.I)),
                          p.get("notice_version_no"), p.get("reference_in_text"), normalize_ref(p.get("reference_in_text")),
                          p.get("awarded_supplier_ids"), sha(ln))
    got_d = {r[0]: tuple(r[1:]) for r in spark.table(T["details"]).selectExpr(
        "notice_op_id", "notice_id", "notice_type", "notice_status", "notice_date", "project_id", "bid_reference_no", "bid_reference_normalized",
        "notice_text_sha256", "notice_text_contains_email", "notice_text_contains_phone", "award_notice_version_no", "award_reference_in_text", "award_reference_in_text_normalized",
        "awarded_supplier_ids", "raw_record_sha256").collect()}
    rep.equals("details:row_count", len(got_d), len(api))
    rep.equals("details:keys_equal_source", sorted(set(got_d) ^ set(exp_d))[:10], [])
    rep.equals("details:field_mismatches (id, type, status, date, project, reference raw+normalised, text hash, contact flags, award fields, raw hash)",
               sorted(k for k in exp_d if got_d.get(k) != exp_d[k])[:10], [])
    rep.equals("details:in_ds00979", sum(v[0] in exp_n for v in exp_d.values()), 20296)

    # ---------- candidate pairs and evidence-supported links ----------
    c5 = csv(spark, BRONZE["ds5"])
    con: dict = {}
    for r in c5.itertuples(index=False):
        rec = con.setdefault(r[c5.columns.get_loc("WB Contract Number")], {"p": r[c5.columns.get_loc("Project ID")], "ref": r[c5.columns.get_loc("Borrower Contract Reference Number")], "sids": set()})
        sid = r[c5.columns.get_loc("Supplier ID")]
        if sid:
            rec["sids"].add(sid)
    by_ref = defaultdict(list)
    for k, v in con.items():
        nr = normalize_ref(v["ref"] or None)
        if nr:
            by_ref[(v["p"], nr)].append(k)
    exp_c = {}
    for r in api:
        p = parse_award_notice(r.get("notice_text")) if r["notice_type"] == "Contract Award" else {}
        keys = {("bid", normalize_ref(r.get("bid_reference_no"))), ("text", normalize_ref(p.get("reference_in_text")))}
        hits: dict = defaultdict(set)
        for basis, k in keys:
            if k:
                for cn in by_ref.get((r["project_id"], k), []):
                    hits[cn].add(basis)
        for cn, bases in hits.items():
            aw = p.get("awarded_supplier_ids")
            conf = None if r["notice_type"] != "Contract Award" else bool(aw is not None and con[cn]["sids"] & set(aw))
            exp_c[f"{r['id']}:{cn}"] = ("bid" in bases, "text" in bases, conf)
    got_c = {r[0]: (r[1], r[2], r[3]) for r in spark.table(T["candidates"]).select(
        "candidate_key", "matched_on_bid_reference_no", "matched_on_award_reference_in_text", "supplier_id_confirmed").collect()}
    rep.equals("candidates:row_count", len(got_c), len(exp_c))
    rep.equals("candidates:pairs_equal_recomputation", sorted(set(got_c) ^ set(exp_c))[:10], [])
    rep.equals("candidates:basis_and_supplier_confirmation_equal", sorted(k for k in exp_c if got_c.get(k) != exp_c[k])[:10], [])
    ver = [k for k, v in exp_c.items() if v[2]]
    nper, cper = Counter(k.split(":")[0] for k in ver), Counter(k.split(":")[1] for k in ver)
    exp_l = {}
    for k in ver:
        one_n, one_c = nper[k.split(":")[0]] == 1, cper[k.split(":")[1]] == 1
        card = "one_to_one" if one_n and one_c else "many_notices_one_contract" if one_n else "one_notice_many_contracts" if one_c else "many_to_many"
        amb = "unambiguous" if card == "one_to_one" else "ambiguous"
        exp_l[k] = (card, amb, "evidence_supported_one_to_one" if amb == "unambiguous" else "evidence_supported_ambiguous",
                    cper[k.split(":")[1]], nper[k.split(":")[0]])
    got_l = {r[0]: tuple(r[1:]) for r in spark.table(T["links"]).select("link_key", "link_cardinality", "ambiguity_status", "verification_status",
                                                                          "supported_notices_for_contract", "supported_contracts_for_notice").collect()}
    rep.equals("links:row_count", len(got_l), len(exp_l))
    rep.equals("links:keys_cardinality_ambiguity_status_equal_recomputation", sorted(k for k in set(exp_l) | set(got_l) if got_l.get(k) != exp_l.get(k))[:10], [])
    rep.add("links:cardinality_and_ambiguity", dict(Counter(f"{v[0]}|{v[1]}" for v in got_l.values())), "reported", True, "warn")
    rep.add("links:contracts_with_exactly_one_supported_notice", sum(1 for v in cper.values() if v == 1), "reported", True, "warn")

    # ---------- cohort / projects ----------
    projects = {r[0] for r in spark.table(f"{S}.projects").select("project_id").collect()}
    rep.equals("project_ids_subset_of_silver_projects", sorted({v[5] for v in exp_n.values()} - projects) + sorted({v[4] for v in exp_d.values()} - projects), [])
    rep.equals("all_2000_projects_have_notices", len(projects - {v[5] for v in exp_n.values()}), 0)
    rep.equals("cohort_manifest_unchanged", hashlib.sha256(COHORT.read_bytes()).hexdigest(), baseline["cohort_manifest_sha256"])

    # ---------- Delta metadata ----------
    expected_constraints = {"notices": ["notice_id_numeric", "notice_type_valid", "project_id_format", "url_matches_op_id"],
                            "details": [],  # CHECK constraints unsupported with column masks: rules validated below
                            "candidates": ["link_evidence_valid", "project_id_format", "supplier_check_awards_only"],
                            "links": ["ambiguity_consistent", "award_notice_only", "cardinality_valid", "project_id_format", "verification_status_consistent"]}
    key_cols = {"notices": "notice_id", "details": "notice_op_id", "candidates": "candidate_key", "links": "link_key"}
    hist = {}
    for k, t in T.items():
        props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {t}").collect()}
        rep.equals(f"{k}:constraints_present", sorted(p.split(".")[-1] for p in props if p.startswith("delta.constraints.")), expected_constraints[k])
        rep.equals(f"{k}:run_id_property", props.get("silver.run_id"), args.run_id)
        rep.equals(f"{k}:key_not_null_enforced", [f.nullable for f in spark.table(t).schema.fields if f.name == key_cols[k]], [False])
        hist[t] = [(r.version, r.operation) for r in spark.sql(f"DESCRIBE HISTORY {t}").orderBy("version").collect()]
    # ---------- details rules (recorded in silver.validated_rules; enforced here) ----------
    dprops = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {T['details']}").collect()}
    rules = json.loads(dprops.get("silver.validated_rules", "{}"))
    rep.equals("details:validated_rules_recorded", sorted(rules), ["award_fields_only_on_awards", "extract_filter_matches", "op_id_format",
                                                                   "project_id_format", "text_access_class_valid"])
    rep.equals("details:validated_rules_hold", {n: spark.table(T["details"]).filter(f"NOT ({e}) OR ({e}) IS NULL").count() for n, e in rules.items()},
               {n: 0 for n in rules})
    # ---------- D-14 access control on the written table ----------
    dt = spark.table(T["details"])
    vis = dt.selectExpr(f"count_if(notice_text IS NOT NULL AND notice_text != '{TEXT_PLACEHOLDER}') AS exposed",
                        f"count_if(notice_text = '{TEXT_PLACEHOLDER}') AS masked", "count_if(notice_text IS NULL) AS nulls").collect()[0]
    src_text = sum(r.get("notice_text") is not None for r in api)
    rep.equals("d14:notice_text_masked_for_this_identity (exposed, masked, null)", (vis.exposed, vis.masked, vis.nulls), (0, src_text, len(api) - src_text))
    g = spark.sql(f"SELECT is_account_group_member('{TEXT_MASK_GROUP}') AS a, is_member('{TEXT_MASK_GROUP}') AS w").collect()[0]
    rep.equals("d14:current_identity_not_in_restricted_group", (g.a, g.w), (False, False))
    cm = {f"{r[0]}.{r[1]}": r[2] for r in spark.sql("SELECT table_name, column_name, mask_name FROM workspace.information_schema.column_masks "
                                                "WHERE table_schema = 'wbg_silver'").collect()}
    rep.equals("d14:column_masks_in_wbg_silver", cm, {"procurement_notice_details.notice_text": TEXT_MASK_FUNCTION})
    body = " ".join(str(x) for r in spark.sql(f"DESCRIBE FUNCTION EXTENDED {TEXT_MASK_FUNCTION}").collect() for x in r)
    rep.equals("d14:mask_function_checks_restricted_group", f"is_account_group_member('{TEXT_MASK_GROUP}')" in body, True)
    try:
        spark.sql(f"SELECT count(*) FROM {T['details']} VERSION AS OF 0").collect()
        tt = "time travel allowed"
    except Exception as e:  # noqa: BLE001
        tt = "refused" if "COLUMN_MASKS_FEATURE_NOT_SUPPORTED" in str(e) else f"error: {str(e)[:120]}"
    rep.equals("d14:time_travel_on_masked_table_refused", tt, "refused")
    rep.equals("d14:notice_text_only_in_details", sorted(k for k, t in T.items() if "notice_text" in spark.table(t).columns), ["details"])
    email_cols = {}
    for k, t in T.items():
        df = spark.table(t)
        for f in df.schema.fields:
            if f.dataType.simpleString() == "string" and f.name != "notice_text":
                n_e = df.filter(df[f.name].rlike(EMAIL_RE)).count()
                if n_e:
                    email_cols[f"{k}.{f.name}"] = n_e
    rep.equals("d14:no_email_addresses_outside_masked_notice_text", email_cols, {})
    for tbl, before in {**baseline["bronze"], **baseline["silver"]}.items():
        h = spark.sql(f"DESCRIBE HISTORY {tbl} LIMIT 1").collect()[0]
        rep.equals(f"unchanged:{tbl}", {"version": h.version, "rows": spark.table(tbl).count()}, before)
    rep.equals("no_label_or_plain_text_columns", sorted(c for t in T.values() for c in spark.table(t).columns if re.search(r"label|rebid|re_bid|cancel|outcome|amendment|plain", c)), [])
    print(rep.render())
    out = run_dir / "post_write_verification.json"
    out.write_text(json.dumps({"run_id": args.run_id, "verification": rep.to_dict(), "table_history": hist}, indent=1, default=str))
    print(f"\nreport: {out}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
