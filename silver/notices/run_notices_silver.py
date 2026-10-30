"""Run the procurement-notice Silver transformations (Databricks Connect, Serverless).

Default is a DRY RUN: records the pre-write baseline, verifies both Volume files against the extraction manifest
(SHA-256 + every record equal to a Bronze row), builds the four Silver DataFrames, runs all checks (including
Spark/Python parity), prints schemas and summaries, and saves reports. Nothing is written.

Writing requires --mode write --confirm-create, all "error" checks passing, and none of the four targets existing.

Usage (from procurement-intelligence/):
  python -m silver.notices.run_notices_silver
  python -m silver.notices.run_notices_silver --mode write --confirm-create
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyspark.sql import functions as F

from silver.common import pipeline
from silver.common.dq import DQReport
from silver.common.pipeline import CATALOG, runs_dir
from silver.common.session import DEFAULT_PROFILE, get_spark
from silver.common.sourcefiles import verify_csv_source_file, verify_jsonl_source_file
from silver.common.write import _sql_str, complete_metadata, create_table
from silver.notices import transform as T
from silver.notices.checks import check_contact_data_outside_text, check_details, check_links, check_notices

sys.path.insert(0, str(T.REPO / "discovery" / "scripts"))
from p09_ml_label_feasibility import BID_DOC_AMENDMENT, CONTRACT_AMENDMENT, NOTICE_CANCELLED, REBID_STRONG, REF_REBID_STRONG, clean  # noqa: E402

S = f"{CATALOG}.wbg_silver"
TARGETS = {"notices": f"{S}.procurement_notices", "details": f"{S}.procurement_notice_details",
           "candidates": f"{S}.notice_contract_link_candidates", "links": f"{S}.notice_contract_links"}
RUNS = runs_dir(Path(__file__).resolve().parents[1] / "runs")
EXISTING_SILVER = ["wdi_indicator_observations", "wdi_country", "income_classification_history", "wgi_estimates", "cpia_ratings", "projects",
                   "contract_award_lines", "contract_awards", "contract_awards_legacy"]
COHORT = T.REPO / "cohort" / "poc_cohort_2000_manifest.csv"
PID = "project_id RLIKE '^P[0-9]{6}$'"
LINEAGE_NN = ["source_row_sha256", "bronze_source_file", "bronze_ingested_at", "bronze_input_file_sha256"]
SPEC: dict[str, dict[str, Any]] = {
    "notices": {
        "not_null": ["notice_id", "notice_op_id", "notice_url", "notice_type", "publication_date", "project_id", "has_api_detail",
                     "identical_metadata_group_size", *LINEAGE_NN],
        "constraints": {"project_id_format": PID, "notice_id_numeric": "notice_id RLIKE '^[0-9]+$'",
                        "url_matches_op_id": f"notice_url = concat('{T.URL_PREFIX}', notice_op_id)",
                        "notice_type_valid": "notice_type IN (" + ", ".join(f"'{t}'" for t in T.NOTICE_TYPES) + ")"},
        "comment": "DS00979 procurement notices, one row per published notice (ID); all 2,000 cohort projects; metadata only (no reference number or text)",
        "columns": {"notice_id": "DS00979 ID as published (numeric string)", "notice_op_id": "OP + ID zero-padded to 8; key of procurement_notice_details",
                    "deadline_date": "Deadline Date as published; equals publication_date on every award notice (not a real deadline there)",
                    "country_code": "Country Code as published; blank for regional projects and some economies (never filled)",
                    "identical_metadata_group_size": "Rows sharing every published field except ID/URL (separate notices; not de-duplicated)",
                    "has_api_detail": "A procnotices API record (text + reference) exists in procurement_notice_details"},
    },
    "details": {
        "not_null": ["notice_op_id", "notice_id", "notice_type", "notice_status", "project_id", "extract_filter_project_id", "raw_record_sha256", *LINEAGE_NN],
        # Databricks rejects CHECK constraints on tables with column masks (COLUMN_MASKS_CHECK_CONSTRAINT_UNSUPPORTED):
        # these rules are recorded as a table property and enforced by checks before and after the write.
        "constraints": {},
        "validated_rules": {"project_id_format": PID, "op_id_format": "notice_op_id RLIKE '^OP[0-9]{8}$'",
                        "extract_filter_matches": "extract_filter_project_id = project_id",
                        "award_fields_only_on_awards": "notice_type = 'Contract Award' OR (award_notice_version_no IS NULL AND awarded_supplier_ids IS NULL)",
                        "text_access_class_valid": "(notice_text_sha256 IS NULL AND notice_text_access_class IS NULL) OR notice_text_access_class = 'restricted'"},
        "comment": "procnotices v2 API notice records (bid reference, status, restricted full text) for the 184 cohort projects of the Phase 1 cluster sample; partial coverage. "
                   "notice_text is RESTRICTED (D-14): column mask, never to frontend, APIs, RAG indexes, embeddings or logs",
        "columns": {"notice_text": f"RESTRICTED (D-14). Notice HTML exactly as published; may contain personal contact data. Masked to a placeholder unless the reader is in account group {T.TEXT_MASK_GROUP}",
                    "notice_text_sha256": "SHA-256 of the unmasked published notice_text (fidelity check without reading the text)",
                    "notice_text_access_class": "restricted for every non-NULL notice_text (contact-data detection is not exhaustive)",
                    "notice_text_contains_email": "E-mail address pattern found in notice_text (detection flag only)",
                    "notice_text_contains_phone": "Labelled phone/fax number pattern found in notice_text (detection flag only)",
                    "bid_reference_normalized": "wbdisc normalize_ref rule (upper, separators to '-', whitespace removed)",
                    "award_reference_in_text": "'Bid/Contract Reference No' quoted in award-notice text (can differ from bid_reference_no for lots)",
                    "award_notice_version_no": "Notice Version No in award text: a revision of the notice, NOT a contract amendment",
                    "awarded_supplier_ids": "STEP supplier IDs in the Awarded Bidder/Firm section; NULL when the section is absent",
                    "identical_text_group_size": "Notices in the same project with identical type and full text (kept)"},
    },
    "candidates": {
        "not_null": ["candidate_key", "notice_op_id", "wb_contract_number", "project_id", "notice_type", "matched_reference_normalized", "match_method", "link_evidence"],
        "constraints": {"project_id_format": PID, "link_evidence_valid": "link_evidence IN ('reference_and_awarded_supplier_id', 'reference_only')",
                        "supplier_check_awards_only": "notice_type = 'Contract Award' OR supplier_id_confirmed IS NULL"},
        "comment": "Notice x contract pairs in the same project with equal normalised references (bid reference or award-text reference). Candidates, not confirmed links",
        "columns": {"link_evidence": "reference_and_awarded_supplier_id (also in notice_contract_links) or reference_only (not supported by a supplier ID)",
                    "match_method": "Rule that produced the candidate pair",
                    "supplier_id_confirmed": "Award notices only: a contract supplier ID is among the notice's awarded supplier IDs"},
    },
    "links": {
        "not_null": ["link_key", "notice_op_id", "wb_contract_number", "project_id", "matched_supplier_ids", "match_method", "link_cardinality",
                     "ambiguity_status", "verification_status", "link_evidence"],
        "constraints": {"project_id_format": PID, "award_notice_only": "notice_type = 'Contract Award'",
                        "cardinality_valid": "link_cardinality IN ('one_to_one', 'many_notices_one_contract', 'one_notice_many_contracts', 'many_to_many')",
                        "ambiguity_consistent": "(link_cardinality = 'one_to_one') = (ambiguity_status = 'unambiguous')",
                        "verification_status_consistent": "verification_status = CASE WHEN ambiguity_status = 'unambiguous' "
                                                          "THEN 'evidence_supported_one_to_one' ELSE 'evidence_supported_ambiguous' END"},
        "comment": "Evidence-supported award-notice to contract pairs: same project, equal normalised reference, and a contract supplier ID among the awarded STEP IDs. "
                   "Strong evidence, not proof of a unique relationship; only ambiguity_status = unambiguous pairs are one-to-one. Ambiguity is kept, never resolved",
        "columns": {"link_cardinality": "one_to_one / many_notices_one_contract / one_notice_many_contracts / many_to_many (counted over evidence-supported pairs)",
                    "ambiguity_status": "unambiguous only for one_to_one; ambiguous pairs must not be treated as unique links",
                    "verification_status": "evidence_supported_one_to_one or evidence_supported_ambiguous (evidence, not proof)",
                    "supported_notices_for_contract": "Award notices with reference + supplier-ID evidence for this contract"},
    },
}


def latest_access_probe() -> dict:
    """Most recent D-14 probe result (silver/runs/d14_access_probe_*/access_control_probe.json); {} if none."""
    found = sorted(RUNS.glob("d14_access_probe_*/access_control_probe.json"))
    return json.loads(found[-1].read_text()) if found else {}


def evidence_inventory(rows) -> dict:
    """Counts of source fields / wording relevant to the ML label audit. Inventory only: no labels are created."""
    c: Counter = Counter()
    for r in rows:
        txt = clean(r.notice_text)
        if r.notice_type in T.TENDER_TYPES:
            c["tender_notices"] += 1
            c["tender_text_rebid_wording"] += bool(REBID_STRONG.search(txt))
            c["tender_description_rebid_wording"] += bool(REBID_STRONG.search(r.bid_description or ""))
            c["tender_reference_rebid_marker"] += bool(REF_REBID_STRONG.search(r.bid_reference_no or ""))
            c["tender_text_cancellation_wording"] += bool(NOTICE_CANCELLED.search(txt))
            c["tender_text_bidding_document_addendum (not a contract amendment)"] += bool(BID_DOC_AMENDMENT.search(txt))
        elif r.notice_type == "Contract Award":
            c["award_text_contract_amendment_wording"] += bool(CONTRACT_AMENDMENT.search(txt))
    return dict(c)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry-run", "write"], default="dry-run")
    ap.add_argument("--confirm-create", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--resume-run-id", help="after a partial write: reuse this run id and create only the missing targets "
                                            "(existing targets must carry this run id; nothing is dropped or overwritten)")
    args = ap.parse_args()
    if args.resume_run_id and args.mode != "write":
        ap.error("--resume-run-id requires --mode write --confirm-create")
    if args.mode == "write" and not args.confirm_create:
        ap.error("--mode write requires --confirm-create")

    run_id = args.resume_run_id or "notices_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spark = get_spark(args.profile)
    first_baseline = json.loads((run_dir / "baseline_before_write.json").read_text()) if args.resume_run_id else None
    baseline: dict = {"bronze": {}, "silver": {}, "targets_exist": {k: spark.catalog.tableExists(v) for k, v in TARGETS.items()},
                      "cohort_manifest_sha256": hashlib.sha256(COHORT.read_bytes()).hexdigest()}
    for group, tables in (("bronze", [T.BRONZE_CSV, T.BRONZE_API]), ("silver", [f"{S}.{t}" for t in EXISTING_SILVER])):
        for t in tables:
            h = spark.sql(f"DESCRIBE HISTORY {t} LIMIT 1").collect()[0]
            baseline[group][t] = {"version": h.version, "rows": spark.table(t).count()}
    if first_baseline is None:
        (run_dir / "baseline_before_write.json").write_text(json.dumps(baseline, indent=1))
    else:
        (run_dir / "baseline_before_resume.json").write_text(json.dumps(baseline, indent=1))

    csv, api = spark.table(T.BRONZE_CSV), spark.table(T.BRONZE_API)
    prov_csv, prov_api = T.load_provenance("bronze_procurement_notices_ds00979"), T.load_provenance("bronze_procurement_notices_api")
    projects = spark.table(f"{S}.projects").select("project_id", "country_code", "geography_type")
    contracts = spark.table(f"{S}.contract_awards")
    rep = DQReport(", ".join(TARGETS.values()))
    verify_csv_source_file(spark, csv, T.source_columns(csv), prov_csv["bronze_input_file_sha256"], "ds00979", rep)
    verify_jsonl_source_file(spark, api, prov_api["bronze_input_file_sha256"], "procnotices_api", rep)
    details = T.build_details(api, prov_api, run_id)
    notices = T.build_notices(csv, details, prov_csv, run_id)
    cand = T.build_link_candidates(details, contracts, run_id)
    links = T.build_links(cand, run_id)
    check_notices(notices, csv, projects, details, prov_csv, rep)
    ev = check_details(details, api, projects, notices, prov_api, rep)
    cov = check_links(cand, links, details, contracts, rep)
    out = {"notices": T.drop_validation(notices), "details": T.drop_validation(details), "candidates": cand, "links": links}
    check_contact_data_outside_text(out, rep)
    probe = latest_access_probe()
    rep.equals("d14:access_control_probe_column_mask_enforced (Free Edition, synthetic data)",
               {"probe": probe.get("probe_id"), "enforced": probe.get("column_mask_enforced")}, {"probe": probe.get("probe_id"), "enforced": True})
    baseline["mask_function_exists"] = spark.catalog.functionExists(T.TEXT_MASK_FUNCTION)
    if first_baseline is None:
        rep.equals("d14:mask_function_absent_before_write", baseline["mask_function_exists"], False)
    else:
        rep.equals("resume:existing_tables_unchanged_since_first_baseline", {**baseline["bronze"], **baseline["silver"]},
                   {**first_baseline["bronze"], **first_baseline["silver"]})
        rep.equals("resume:cohort_unchanged", baseline["cohort_manifest_sha256"], first_baseline["cohort_manifest_sha256"])
        done = [k for k, v in baseline["targets_exist"].items() if v]
        rep.equals("resume:existing_targets_carry_this_run_id",
                   {k: {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {TARGETS[k]}").collect()}.get("silver.run_id") for k in done},
                   {k: run_id for k in done})
        rep.equals("resume:existing_targets_row_counts", {k: spark.table(TARGETS[k]).count() for k in done}, {k: out[k].count() for k in done})
        rep.equals("resume:mask_function_present", baseline["mask_function_exists"], True)
    for k, spec in SPEC.items():
        if spec.get("validated_rules"):
            rep.equals(f"{k}:validated_rules (not expressible as CHECK constraints on a masked table)",
                       {n: out[k].filter(f"NOT ({e}) OR ({e}) IS NULL").count() for n, e in spec["validated_rules"].items()},
                       {n: 0 for n in spec["validated_rules"]})

    legacy = spark.table(f"{S}.contract_awards_legacy").join(details.select("project_id").distinct(), "project_id", "left_semi")
    nref = details.select("project_id", F.explode(F.array("bid_reference_normalized", "award_reference_in_text_normalized")).alias("r")).filter("r IS NOT NULL").distinct()
    leg_match = legacy.join(nref, (legacy.project_id == nref.project_id) & (T.normalize_ref(legacy.borrower_contract_reference) == nref.r), "left_semi")
    desc = (out["notices"].filter("notice_type = 'Contract Award' AND bid_description_normalized IS NOT NULL")
            .select("notice_op_id", "project_id", F.upper("bid_description_normalized").alias("k"))
            .join(contracts.select("wb_contract_number", "project_id", F.upper(F.trim(F.regexp_replace("contract_description", r"\s+", " "))).alias("k")), ["project_id", "k"]))
    dn, dc = desc.groupBy("notice_op_id").count().filter("count = 1"), desc.groupBy("wb_contract_number").count().filter("count = 1")
    uniq = desc.join(dn.select("notice_op_id"), "notice_op_id", "left_semi").join(dc.select("wb_contract_number"), "wb_contract_number", "left_semi")
    one = links.filter("supported_notices_for_contract = 1").select("wb_contract_number", F.col("notice_op_id").alias("v"))
    chk = uniq.join(one, "wb_contract_number")
    summary = {
        "rows": {k: df.count() for k, df in out.items()},
        "ds00979": {"projects": out["notices"].select("project_id").distinct().count(),
                    "with_api_detail": out["notices"].filter("has_api_detail").count()},
        "details": {"projects": out["details"].select("project_id").distinct().count(),
                    "award_notices": out["details"].filter("notice_type = 'Contract Award'").count(),
                    "award_ref_in_text_differs_from_bid_reference": out["details"].filter(
                        "award_reference_in_text_normalized IS NOT NULL AND bid_reference_normalized IS NOT NULL "
                        "AND award_reference_in_text_normalized != bid_reference_normalized").count()},
        "linkage": cov,
        "legacy_contracts_in_api_projects": {"contracts": legacy.count(), "reference_matches_a_notice (reference only, no supplier ID; not linked)": leg_match.count()},
        "description_match_assessment (not used for links)": {
            "unique_one_to_one_description_pairs": uniq.count(),
            "of_which_contract_has_single_supported_notice": chk.count(), "agree_with_supported_notice": chk.filter("notice_op_id = v").count()},
        "ml_evidence_inventory (no labels created)": evidence_inventory(ev["rows_for_evidence"]),
    }
    print(f"run_id={run_id} mode={args.mode} transform={T.TRANSFORM_VERSION}")
    for k, df in out.items():
        print(f"\n== proposed schema: {TARGETS[k]}")
        for f in df.schema.fields:
            print(f"   {f.name:40s} {f.dataType.simpleString()[:70]}")
    print("\n== summary\n" + json.dumps(summary, indent=1, default=str))
    print(rep.render())
    (run_dir / "dq_report.json").write_text(json.dumps({"run_id": run_id, "mode": args.mode, "transform_version": T.TRANSFORM_VERSION,
                                                        "targets": TARGETS, "provenance": {"ds00979": prov_csv, "procnotices_api": prov_api},
                                                        "summary": summary, "dq": rep.to_dict()}, indent=1, default=str))
    print(f"\nreports: {run_dir}")
    if not rep.passed:
        print("DQ FAILED: nothing written")
        return 1
    if args.mode == "dry-run":
        print("DRY RUN: nothing written to Databricks")
        return 0
    if first_baseline is None and (any(baseline["targets_exist"].values()) or baseline["mask_function_exists"]) and not pipeline.ENABLED:
        raise SystemExit(f"target(s) or mask function already exist: {baseline['targets_exist']}; refusing to overwrite")
    if first_baseline is None:
        # D-14: create-only mask function; the details table is created with the mask declared before any row is inserted
        spark.sql(f"CREATE {'OR REPLACE ' if pipeline.ENABLED else ''}FUNCTION {T.TEXT_MASK_FUNCTION}(t STRING) RETURNS STRING "
                  f"COMMENT 'D-14: restricted notice text; readable only by members of account group {T.TEXT_MASK_GROUP}' "
                  f"RETURN CASE WHEN t IS NULL THEN NULL WHEN is_account_group_member('{T.TEXT_MASK_GROUP}') THEN t ELSE '{T.TEXT_PLACEHOLDER}' END")
    sources = {"notices": T.BRONZE_CSV, "details": T.BRONZE_API, "candidates": f"{T.BRONZE_API},{S}.contract_awards",
               "links": f"{T.BRONZE_API},{S}.contract_awards"}
    for k in ("details", "notices", "candidates", "links"):
        if baseline["targets_exist"][k] and not pipeline.ENABLED:
            if k == "details":  # metadata only: record the rules that cannot be CHECK constraints on a masked table
                spark.sql(f"ALTER TABLE {TARGETS[k]} SET TBLPROPERTIES ('silver.validated_rules' = '{_sql_str(json.dumps(SPEC[k]['validated_rules']))}')")
            spec = SPEC[k]
            actions = complete_metadata(spark, TARGETS[k], spec["comment"], spec["columns"], spec["not_null"], spec["constraints"])
            print(f"exists from this run, data kept unchanged: {TARGETS[k]}; metadata completed: {actions}")
            continue
        spec = SPEC[k]
        create_table(spark, out[k], TARGETS[k],
                     {"silver.run_id": run_id, "silver.transform_version": T.TRANSFORM_VERSION, "silver.source_table": sources[k],
                      "silver.decisions": "D-06,D-09,D-14 (approved 2026-10-23: notice_text restricted, column mask),D-26; pending: D-05 (no package entity created), D-22",
                      **({"silver.restricted_columns": "notice_text (D-14, column mask " + T.TEXT_MASK_FUNCTION + ")",
                          "silver.validated_rules": json.dumps(spec["validated_rules"])} if k == "details" else {})},
                     spec["comment"], spec["columns"], spec["not_null"], spec["constraints"],
                     column_masks={"notice_text": T.TEXT_MASK_FUNCTION} if k == "details" else None)
        print(f"written {TARGETS[k]}: {spark.table(TARGETS[k]).count()} rows")
        if k == "details":
            exposed = spark.table(TARGETS[k]).filter(f"notice_text IS NOT NULL AND notice_text != '{T.TEXT_PLACEHOLDER}'").count()
            print(f"D-14 mask check on written table: unmasked notice_text visible to this identity = {exposed}")
            if exposed:
                raise SystemExit("D-14: notice_text visible unmasked after write; stopping before the remaining tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
