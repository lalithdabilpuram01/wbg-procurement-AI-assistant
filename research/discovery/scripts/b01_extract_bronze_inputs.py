"""Cohort-bounded raw extracts for Bronze ingestion (decision D-26; offline).

Selects, from the Phase 1 downloads, only the records whose project ID is one of the 2,000 projects
in cohort/poc_cohort_2000_manifest.csv. Row selection is the only operation: records are copied
byte-for-byte (see wbdisc/bronze.py). Original files are read-only; their checksums are verified
against the Phase 1 MANIFEST files before and after extraction.

Output: procurement-intelligence/databricks_upload/bronze_inputs/
  <bronze table>/<source stem>__cohort2000.<csv|jsonl>   one directory per recommended Bronze table
  _control/poc_cohort_2000_manifest.csv                   byte copy of the approved cohort
  _manifest/bronze_inputs_manifest.json|.csv              provenance, counts, checksums, validation
  _manifest/excluded_and_unlinked_sources.json            sources / records not extracted, and why
"""
from __future__ import annotations

import collections
import csv
import gzip
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from wbdisc import datasets as D  # noqa: E402
from wbdisc.award_notice import parse_award_notice  # noqa: E402
from wbdisc.bronze import PROJECT_ID, select_csv, select_jsonl  # noqa: E402
from wbdisc.config import REPO_ROOT, SETTINGS  # noqa: E402
from wbdisc.normalize import normalize_ref  # noqa: E402

COHORT = REPO_ROOT / "cohort" / "poc_cohort_2000_manifest.csv"
OUT = REPO_ROOT / "databricks_upload" / "bronze_inputs"
VOLUME = "/Volumes/workspace/default/wbg_procurement_test"
COHORT_SIZE = 2000
SELECTION_RULE = ("exact string equality between the raw source key value and a cohort project_id "
                  "(no trimming, case-folding or other normalisation)")

SOURCES = [
    # table, source path, format, key, coverage, description
    ("projects_v3", SETTINGS.cache_dir / "projects_v3_population.jsonl.gz", "jsonl", "id", "complete",
     "search.worldbank.org/api/v3/projects population, 39 selected fields"),
    ("projects_v3_allfields", SETTINGS.samples_dir / "projects" / "projects_v3_cluster_allfields.jsonl.gz", "jsonl",
     "id", "partial", "v3 projects fl=* (ISR/ICR ratings, impagency, indicators, loan_transactions); Phase 1 cluster only"),
    ("contract_awards_ds00005", SETTINGS.cache_dir / "ds00005_contract_awards.csv", "csv", "Project ID", "complete",
     "Finances One DS00005 Contract Awards in Investment Project Financing (FY2020+), bulk CSV"),
    ("contract_awards_legacy_ds01004", SETTINGS.cache_dir / "ds01004_contract_awards_legacy.csv", "csv", "Project ID",
     "complete", "Finances One DS01004 Major Contract Awards (prior review, FY2001-FY2016, frozen), bulk CSV"),
    ("procurement_notices_ds00979", SETTINGS.cache_dir / "ds00979_procurement_notice.csv", "csv", "Project ID",
     "complete", "Finances One DS00979 Procurement Notices (metadata only: no notice text or bid reference), bulk CSV"),
    ("procurement_notices_api", SETTINGS.samples_dir / "procnotices" / "procnotices_cluster_sample.jsonl.gz", "jsonl",
     "project_id", "partial", "search.worldbank.org/api/v2/procnotices with notice_text and bid_reference_no "
     "(contact_name/email/phone fields excluded at extraction); Phase 1 cluster only"),
    ("ieg_ratings_ds00053", SETTINGS.cache_dir / "ds00053_ieg_ratings.csv", "csv", "Project ID", "complete",
     "Finances One DS00053 IEG Project Performance Ratings (latest evaluation per project), bulk CSV"),
    ("ibrd_loans_ds00047", SETTINGS.cache_dir / "ds00047_ibrd_latest.csv", "csv", "Project ID", "complete",
     "Finances One DS00047 IBRD Statement of Loans and Guarantees, latest snapshot, bulk CSV"),
    ("ida_credits_ds00001", SETTINGS.cache_dir / "ds00001_ida_latest.csv", "csv", "Project ID", "complete",
     "Finances One DS00001 IDA Statement of Credits, Grants and Guarantees, latest snapshot, bulk CSV"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_hashes() -> dict[str, str]:
    out = {}
    for mf in (SETTINGS.cache_dir / "MANIFEST.jsonl", SETTINGS.samples_dir / "MANIFEST.jsonl"):
        for line in mf.read_text().splitlines():
            r = json.loads(line)
            if r.get("sha256"):
                out[str((SETTINGS.cache_dir.parent / r["file"]).resolve())] = r["sha256"]
    return out


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_cohort() -> tuple[set[str], dict]:
    ids = pd.read_csv(COHORT, dtype=str, keep_default_na=False)["project_id"]
    checks = {"rows": len(ids), "unique": int(ids.nunique()),
              "all_valid_pnumber": bool(ids.map(lambda v: bool(PROJECT_ID.fullmatch(v))).all())}
    if not (checks["rows"] == checks["unique"] == COHORT_SIZE and checks["all_valid_pnumber"]):
        raise SystemExit(f"cohort manifest failed checks: {checks}")
    return set(ids), checks


def cross_check_csv(src: Path, dst: Path, key: str, keep: set[str]) -> dict:
    """Independent check: pandas-filtered source equals pandas-parsed extract, row for row."""
    s = pd.read_csv(src, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    s = s[s[key].isin(keep)].reset_index(drop=True)
    o = pd.read_csv(dst, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    with src.open("rb") as a, dst.open("rb") as b:
        header_identical = a.readline() == b.readline()
    return {"pandas_rows_equal": len(s) == len(o), "pandas_frames_equal": bool(s.equals(o)),
            "header_bytes_identical": header_identical, "all_keys_in_cohort": bool(o[key].isin(keep).all())}


def cross_check_jsonl(src: Path, dst: Path, key: str, keep: set[str]) -> dict:
    """Independent check: every extract line is a byte-identical source line, and counts agree."""
    raw = gzip.decompress(src.read_bytes()) if src.suffix == ".gz" else src.read_bytes()
    src_lines = [ln if ln.endswith(b"\n") else ln + b"\n" for ln in raw.splitlines(keepends=True) if ln.strip()]
    expected = [ln for ln in src_lines if json.loads(ln).get(key) in keep]
    got = dst.read_bytes().splitlines(keepends=True)
    return {"lines_identical_in_order": got == expected, "rows_equal": len(got) == len(expected),
            "all_keys_in_cohort": all(json.loads(ln).get(key) in keep for ln in got)}


def contract_and_notice_metrics(paths: dict[str, Path], keep: set[str]) -> dict:
    c5 = pd.read_csv(paths["contract_awards_ds00005"], dtype=str, keep_default_na=False, encoding="utf-8-sig")
    c1 = pd.read_csv(paths["contract_awards_legacy_ds01004"], dtype=str, keep_default_na=False, encoding="utf-8-sig")
    nb = pd.read_csv(paths["procurement_notices_ds00979"], dtype=str, keep_default_na=False, encoding="utf-8-sig")
    with paths["procurement_notices_api"].open("rb") as f:
        api = pd.DataFrame([json.loads(ln) for ln in f])
    m: dict = {
        "contracts_ds00005": {
            "rows_contract_x_supplier_lines": len(c5),
            "distinct_contracts_wb_contract_number": int(c5["WB Contract Number"].nunique()),
            "distinct_contract_supplier_pairs": int(len(c5.drop_duplicates(["WB Contract Number", "Supplier ID", "Supplier"]))),
            "contracts_with_more_than_one_row": int((c5.groupby("WB Contract Number").size() > 1).sum()),
            "exact_duplicate_rows_preserved": int(c5.duplicated().sum()),
            "projects": int(c5["Project ID"].nunique())},
        "contracts_ds01004_legacy": {
            "rows": len(c1), "distinct_contracts_contract_number": int(c1["Contract Number"].nunique()),
            "exact_duplicate_rows_preserved": int(c1.duplicated().sum()), "projects": int(c1["Project ID"].nunique())},
        "notices_ds00979_project_level_links": {
            "rows": len(nb), "distinct_notice_ids": int(nb["ID"].nunique()), "projects": int(nb["Project ID"].nunique()),
            "by_notice_type": nb["Notice Type"].value_counts().to_dict(),
            "note": "Linked to the cohort by Project ID only. DS00979 has no bid reference number, so these rows "
                    "cannot be linked to individual contracts."},
        "notices_api_with_text": {
            "rows": len(api), "distinct_ids": int(api["id"].nunique()), "projects": int(api["project_id"].nunique()),
            "rows_with_bid_reference_no": int(api["bid_reference_no"].fillna("").astype(str).str.strip().ne("").sum()),
            "by_notice_type": api["notice_type"].value_counts().to_dict()},
    }
    # Contract-to-notice links (Phase 1 method, p02_join_analysis), counted only where notices with
    # references exist. These are reported figures; no link table is written to Bronze.
    projects = set(api["project_id"])
    cc = c5[c5["Project ID"].isin(projects)].copy()
    cc["_ref"] = cc["Borrower Contract Reference Number"].map(normalize_ref)
    api["_ref"] = api["bid_reference_no"].map(normalize_ref)
    award = api[api["notice_type"] == "Contract Award"].copy()
    award["_ids"] = award["notice_text"].map(lambda t: set(parse_award_notice(t).get("awarded_supplier_ids", [])))
    tender = api[api["notice_type"].isin(["Invitation for Bids", "Request for Expression of Interest",
                                           "Invitation for Prequalification"])]
    a_idx: dict = collections.defaultdict(list)
    for i, r in award.iterrows():
        if r["_ref"]:
            a_idx[(r["project_id"], r["_ref"])].append(i)
    t_keys = {(p, r) for p, r in zip(tender["project_id"], tender["_ref"]) if r}
    sup = cc.groupby("WB Contract Number")["Supplier ID"].agg(set)
    res: collections.Counter = collections.Counter()
    for _, r in cc.drop_duplicates("WB Contract Number").iterrows():
        res["distinct_contracts"] += 1
        hits = a_idx.get((r["Project ID"], r["_ref"]), []) if r["_ref"] else []
        res["award_notice_reference_match"] += bool(hits)
        res["tender_notice_reference_match"] += (r["Project ID"], r["_ref"]) in t_keys
        agree = [i for i in hits if sup[r["WB Contract Number"]] & award.at[i, "_ids"]]
        res["award_link_verified_by_supplier_id"] += bool(agree)
        res["award_link_verified_unique"] += len(agree) == 1
    m["contract_to_notice_links"] = {
        "scope_projects": len(projects), **dict(res),
        "contracts_in_cohort_without_reference_notices": int(c5.loc[~c5["Project ID"].isin(projects), "WB Contract Number"].nunique()),
        "definitions": {
            "award_notice_reference_match": "same project and identical normalize_ref(Borrower Contract Reference Number) "
                                            "= normalize_ref(bid_reference_no) on a Contract Award notice (candidate link)",
            "award_link_verified_by_supplier_id": "reference match AND the contract's STEP Supplier ID appears among the "
                                                  "awarded supplier IDs parsed from the award notice text",
            "award_link_verified_unique": "exactly one award notice satisfies the verified condition"},
    }
    return m


def excluded_sources(keep: set[str]) -> list[dict]:
    def count_jsonl(path: Path, key: str) -> int:
        with gzip.open(path, "rt") if path.suffix == ".gz" else path.open() as f:
            return sum(1 for ln in f if ln.strip() and json.loads(ln).get(key) in keep)

    s = SETTINGS.samples_dir
    return [
        {"source": "data_cache/projects_population.jsonl.gz", "reason": "v2 projects index, frozen since ~Jan 2025; "
         "superseded by v3 (D-01). Linkable, not extracted.", "cohort_records_available": count_jsonl(
             SETTINGS.cache_dir / "projects_population.jsonl.gz", "id")},
        {"source": "samples/contract_awards/ds00005_api_sample.jsonl, ds00005_stratified_sample.csv, "
         "ds01004_stratified_sample.csv", "reason": "Phase 1 samples drawn from DS00005/DS01004, which are extracted "
         "in full for the cohort; including them would duplicate source records."},
        {"source": "samples/procnotices/procnotices_global_sample.jsonl.gz", "reason": "800-record random API sample "
         "that still carries contact_name/email/phone fields (D-14); the cohort's notices with text come from the "
         "cluster pull.", "cohort_records_available": count_jsonl(s / "procnotices" / "procnotices_global_sample.jsonl.gz",
                                                                   "project_id")},
        {"source": "samples/finance/ds00975_history_sample.jsonl.gz, ds00976_history_sample.jsonl.gz",
         "reason": "Probe samples of monthly loan history for a few loans; not a complete history for any cohort "
         "project. Full history is decision D-12.", "cohort_records_available":
             count_jsonl(s / "finance" / "ds00975_history_sample.jsonl.gz", "project_id")
             + count_jsonl(s / "finance" / "ds00976_history_sample.jsonl.gz", "project_id")},
        {"source": "samples/indicators/*.jsonl.gz (WGI, CPIA)", "reason": "Country-year indicators with no project "
         "key. Linking them to projects needs the country crosswalk (D-07), which is a Silver step. Not extracted; "
         "a decision is needed on whether to load them as unfiltered reference data."},
        {"source": "samples/documents/* (WDS metadata, PDFs, text), profiling/procurement_plan_activities.csv",
         "reason": "Outside the five requested domains. WDS projectid is multi-valued (151 multi-project documents "
         "in the cluster metadata); procurement_plan_activities.csv is parsed output, not a raw source."},
    ]


def main() -> None:
    known = manifest_hashes()
    keep, cohort_checks = load_cohort()
    if OUT.exists() and any(OUT.iterdir()):
        raise SystemExit(f"{OUT} is not empty; move it aside to re-extract (outputs are never overwritten)")
    OUT.mkdir(parents=True, exist_ok=True)
    run_started = utc_now()

    files, paths, unlinked = [], {}, []
    for table, src, fmt, key, coverage, desc in SOURCES:
        before = sha256(src)
        expected = known.get(str(src.resolve()))
        if expected and expected != before:
            raise SystemExit(f"{src} differs from its MANIFEST checksum")
        stem = src.name.split(".")[0]
        dst = OUT / table / f"{stem}__cohort2000.{fmt}"
        if fmt == "csv":
            res = select_csv(src, dst, key, keep)
            validation = cross_check_csv(src, dst, key, keep)
        else:
            res = select_jsonl(src, dst, key, keep)
            validation = cross_check_jsonl(src, dst, key, keep)
        extracted_at = utc_now()
        after = sha256(src)
        validation["source_unchanged"] = before == after
        if not all(validation.values()):
            raise SystemExit(f"validation failed for {table}: {validation}")
        sel = res["selected_keys"]
        entry = {
            "bronze_table": f"bronze_{table}", "output_file": str(dst.relative_to(REPO_ROOT)),
            "volume_path": f"{VOLUME}/bronze_inputs/{table}/{dst.name}", "format": fmt,
            "source_file": str(src.relative_to(REPO_ROOT)), "source_description": desc,
            "source_sha256": before, "source_manifest_checksum": "match" if expected else "not in manifest",
            "source_bytes": src.stat().st_size, "source_compression": "gzip" if src.suffix == ".gz" else "none",
            "selection_key": key, "selection_rule": SELECTION_RULE, "coverage": coverage,
            "source_records": res["stats"].get("source_records", 0), "records_selected": res["stats"].get("selected_records", 0),
            "cohort_projects_with_records": len(sel), "cohort_projects_without_records": COHORT_SIZE - len(sel),
            "max_records_per_project": max(sel.values()) if sel else 0,
            "output_bytes": dst.stat().st_size, "output_sha256": sha256(dst), "extracted_at_utc": extracted_at,
            "validation": validation,
        }
        if fmt == "csv":
            entry.update({"columns": res["columns"], "header_preserved_raw": True, "utf8_bom_preserved": res["utf8_bom"],
                          "line_terminator": res["line_terminator"],
                          "records_with_field_count_differing_from_header": res["stats"].get("field_count_differs_from_header", 0)})
        else:
            entry.update({"output_compression": "none (source lines written decompressed, byte-identical)"})
        files.append(entry)
        paths[table] = dst
        unlinked.append({"source_file": entry["source_file"],
                         "records_key_not_a_project_id": res["stats"].get("key_not_a_project_id", 0),
                         "examples": res["examples"].get("key_not_a_project_id", []),
                         "records_near_match_not_selected": res["stats"].get("near_match_not_selected", 0),
                         "near_match_examples": res["examples"].get("near_match_not_selected", [])[:5]})
        print(f"{table:34s} {entry['records_selected']:>8,} of {entry['source_records']:>8,}  "
              f"{entry['cohort_projects_with_records']:>5} projects  {entry['output_bytes'] / 1e6:7.1f} MB")

    ctrl = OUT / "_control" / COHORT.name
    ctrl.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(COHORT, ctrl)
    projects = next(f for f in files if f["bronze_table"] == "bronze_projects_v3")
    with paths["projects_v3"].open("rb") as f:
        project_ids = [json.loads(ln)["id"] for ln in f]
    validation = {
        "cohort_manifest": cohort_checks,
        "projects_file_records": len(project_ids), "projects_file_unique_ids": len(set(project_ids)),
        "projects_file_ids_equal_cohort": set(project_ids) == keep,
        "exactly_2000_unique_projects": len(project_ids) == len(set(project_ids)) == COHORT_SIZE and set(project_ids) == keep,
        "every_cohort_project_has_contract_rows": next(f for f in files if f["bronze_table"] ==
                                                       "bronze_contract_awards_ds00005")["cohort_projects_without_records"] == 0,
        "every_cohort_project_has_notice_rows": next(f for f in files if f["bronze_table"] ==
                                                     "bronze_procurement_notices_ds00979")["cohort_projects_without_records"] == 0,
        "all_outputs_validated": all(all(f["validation"].values()) for f in files),
        "control_copy_sha256_matches_cohort": sha256(ctrl) == sha256(COHORT),
    }
    if not validation["exactly_2000_unique_projects"] or projects["records_selected"] != COHORT_SIZE:
        raise SystemExit(f"projects extract is not exactly the cohort: {validation}")

    manifest = {
        "purpose": "Raw, cohort-bounded inputs for Bronze ingestion (decision D-26). Row selection only.",
        "cohort_manifest": str(COHORT.relative_to(REPO_ROOT)), "cohort_manifest_sha256": sha256(COHORT),
        "cohort_size": COHORT_SIZE, "selection_rule": SELECTION_RULE, "run_started_utc": run_started,
        "run_finished_utc": utc_now(), "target_volume": VOLUME,
        "control_files": [{"file": str(ctrl.relative_to(REPO_ROOT)), "sha256": sha256(ctrl), "bytes": ctrl.stat().st_size,
                           "volume_path": f"{VOLUME}/bronze_inputs/_control/{ctrl.name}"}],
        "files": files, "validation": validation,
        "relationship_counts": contract_and_notice_metrics(paths, keep),
    }
    mdir = OUT / "_manifest"
    mdir.mkdir(exist_ok=True)
    (mdir / "bronze_inputs_manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    flat = ["bronze_table", "output_file", "format", "coverage", "source_file", "source_sha256", "selection_key",
            "source_records", "records_selected", "cohort_projects_with_records", "output_bytes", "output_sha256",
            "extracted_at_utc"]
    with (mdir / "bronze_inputs_manifest.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=flat)
        w.writeheader()
        w.writerows({k: e[k] for k in flat} for e in files)
    (mdir / "excluded_and_unlinked_sources.json").write_text(json.dumps(
        {"unlinked_records_in_extracted_sources": unlinked, "sources_not_extracted": excluded_sources(keep)},
        indent=1, default=str))
    print(json.dumps(validation, indent=1))


if __name__ == "__main__":
    main()
