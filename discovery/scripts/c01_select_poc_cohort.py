"""Decision D-26: select the 2,000-project PoC cohort (offline, reproducible).

Eligible pool (as audited 2026-10-21): v3 projects with status Active or Closed that have
>= 1 DS00005 contract row (FY2020+) AND >= 1 DS00979 procurement notice -> 3,120 projects.

Method (details in cohort/COHORT_SELECTION_METHODOLOGY.md):
  1. explicit strata: region x status x approval-FY band x contract-volume quartile
  2. proportional allocation, largest-remainder rounding -> exactly 2,000
  3. systematic sample within each stratum over units ordered by (country, major sector, random key)
  4. within-stratum swaps so every country with >= 2 eligible projects is represented

Reads only local files (data_cache/, samples/, profiling/); verifies input checksums against the
MANIFEST files; makes no network calls. Writes new files to procurement-intelligence/cohort/:
  poc_cohort_2000_manifest.csv   the 2,000 selected projects
  poc_eligible_pool_3120.csv     the full eligible pool with selected flag and selection info
  poc_cohort_2000_summary.json   parameters, checks, representation and coverage figures
"""
from __future__ import annotations

import collections
import gzip
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from wbdisc import datasets as D  # noqa: E402
from wbdisc.cohort import (  # noqa: E402
    approval_fy, coverage_swaps, dominant_major_sector, first, fy_band, largest_remainder_allocation,
    share_table, split_project_ids, systematic_positions, volume_quartile,
)
from wbdisc.config import REPO_ROOT, SETTINGS  # noqa: E402
from wbdisc.countries import is_regional, to_iso2  # noqa: E402

COHORT_SIZE = 2000
SEED = 20261021  # fixed by decision D-26; changing it changes the cohort
EXPECTED_POOL = 3120
ELIGIBLE_STATUS = {"Active", "Closed"}
OUT = REPO_ROOT / "cohort"
SELECTED_ON = "2026-10-21"
DOC_TYPES = {
    "Project Appraisal Document": "PAD",
    "Project Paper": "PP_paper",
    "Implementation Status and Results Report": "ISR",
    "Implementation Completion and Results Report": "ICR",
    "Implementation Completion Report Review": "ICRR",
    "Project Performance Assessment Report": "PPAR",
    "Procurement Plan": "PROC_PLAN",
}
INPUTS = {
    "projects_v3": SETTINGS.cache_dir / "projects_v3_population.jsonl.gz",
    "ds00005_contracts": SETTINGS.cache_dir / "ds00005_contract_awards.csv",
    "ds01004_contracts_legacy": SETTINGS.cache_dir / "ds01004_contract_awards_legacy.csv",
    "ds00979_notices": SETTINGS.cache_dir / "ds00979_procurement_notice.csv",
    "ds00053_ieg": SETTINGS.cache_dir / "ds00053_ieg_ratings.csv",
    "ds00047_ibrd": SETTINGS.cache_dir / "ds00047_ibrd_latest.csv",
    "ds00001_ida": SETTINGS.cache_dir / "ds00001_ida_latest.csv",
    "procnotices_cluster": SETTINGS.samples_dir / "procnotices" / "procnotices_cluster_sample.jsonl.gz",
    "projects_v3_cluster_allfields": SETTINGS.samples_dir / "projects" / "projects_v3_cluster_allfields.jsonl.gz",
    "wds_v3_cluster_meta": SETTINGS.samples_dir / "documents" / "wds_v3_cluster_meta.jsonl.gz",
    "procurement_plan_activities": SETTINGS.profiling_dir / "procurement_plan_activities.csv",
}


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


def verify_inputs() -> dict:
    known = manifest_hashes()
    res = {}
    for name, p in INPUTS.items():
        digest = sha256(p)
        expected = known.get(str(p.resolve()))
        if expected and expected != digest:
            raise SystemExit(f"input {p} differs from its MANIFEST checksum; refusing to select")
        res[name] = {"path": str(p.relative_to(REPO_ROOT)), "bytes": p.stat().st_size, "sha256": digest,
                     "manifest_checksum": "match" if expected else "not in manifest"}
    return res


def local_files() -> tuple[collections.Counter, collections.Counter, dict[str, list[str]]]:
    """Per-project counts of local PDFs and text files, and PDF types, parsed from file names."""
    pdfs: collections.Counter[str] = collections.Counter()
    txts: collections.Counter[str] = collections.Counter()
    types: dict[str, list[str]] = collections.defaultdict(list)
    docs = SETTINGS.samples_dir / "documents"
    for p in list((docs / "files").glob("*.pdf")) + list((docs / "pp_files").glob("*.pdf")):
        dtype = p.stem.split("_")[-2]
        for pid in split_project_ids(p.stem.split("_")[0].replace(",", " ")):
            pdfs[pid] += 1
            types[pid].append("PROC_PLAN" if dtype == "PP" else dtype)
    for p in list((docs / "files").glob("*.txt")) + list((docs / "isr_series").glob("*.txt")):
        for pid in split_project_ids(p.stem.split("_")[0].replace(",", " ")):
            txts[pid] += 1
    return pdfs, txts, types


def build_pool() -> tuple[pd.DataFrame, dict]:
    c5 = D.contracts()
    nb = D.notices_bulk()
    amt = pd.to_numeric(c5["Supplier Contract Amount (USD)"].str.replace(",", ""), errors="coerce")
    by_pid = c5.assign(_amt=amt).groupby("Project ID")
    contracts = pd.DataFrame({
        "contract_count": by_pid["WB Contract Number"].nunique(),
        "contract_line_count": by_pid.size(),
        "contract_amount_usd": by_pid["_amt"].sum().round(2),
        "first_contract_signing": by_pid["Contract Signing Date"].apply(
            lambda s: pd.to_datetime(s, format="%m/%d/%Y", errors="coerce").min()),
        "last_contract_signing": by_pid["Contract Signing Date"].apply(
            lambda s: pd.to_datetime(s, format="%m/%d/%Y", errors="coerce").max()),
    })
    ntype = {"Contract Award": "notice_award_count", "Invitation for Bids": "notice_ifb_count",
             "Request for Expression of Interest": "notice_reoi_count",
             "General Procurement Notice": "notice_gpn_count", "Invitation for Prequalification": "notice_prequal_count"}
    notices = pd.crosstab(nb["Project ID"], nb["Notice Type"].map(ntype).fillna("notice_other_count"))
    notices["notice_count"] = notices.sum(axis=1)

    rows, fy_mismatch = [], []
    for r in D.projects():
        pid = r["id"]
        status = first(r.get("status"))
        if pid not in contracts.index or pid not in notices.index or status not in ELIGIBLE_STATUS:
            continue
        fy, fy_src = approval_fy(r.get("fiscalyear"), r.get("boardapprovaldate"))
        derived, _ = approval_fy(None, r.get("boardapprovaldate"))
        if fy_src == "v3.fiscalyear" and derived is not None and derived != fy:
            fy_mismatch.append({"project_id": pid, "v3_fiscalyear": fy, "derived_from_boardapprovaldate": derived})
        code = first(r.get("countrycode")) or ""
        rows.append({
            "project_id": pid, "project_name": first(r.get("project_name")),
            "country_code": code, "country_name": first(r.get("countryshortname")) or first(r.get("countryname")),
            "country_iso2": to_iso2(code), "country_is_regional": is_regional(code),
            "region": first(r.get("regionname")), "major_sector": dominant_major_sector(r.get("major_sectors")),
            "approval_fy": fy, "approval_fy_source": fy_src, "fy_band": fy_band(fy), "status": status,
            "financing_type": first(r.get("projectfinancialtype")),
            "board_approval_date": str(first(r.get("boardapprovaldate")) or "")[:10],
            "closing_date": str(first(r.get("closingdate")) or "")[:10],
        })
    pool = pd.DataFrame(rows).sort_values("project_id").reset_index(drop=True)
    pool = pool.join(contracts, on="project_id").join(notices, on="project_id")
    for c in list(ntype.values()) + ["notice_other_count"]:
        if c not in pool:
            pool[c] = 0
    pool["contract_volume_quartile"] = volume_quartile(pool.set_index("project_id")["contract_count"]).values
    return pool, {"approval_fy_mismatches_kept_v3": fy_mismatch}


def select(pool: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    pool = pool.copy()
    pool["stratum_id"] = pool[["region", "status", "fy_band", "contract_volume_quartile"]].agg(" | ".join, axis=1)
    keys_rng = np.random.default_rng([SEED, 1])
    pool["random_key"] = keys_rng.random(len(pool)).round(12)  # pool is sorted by project_id
    sizes = pool.groupby("stratum_id").size().to_dict()
    alloc = largest_remainder_allocation(sizes, COHORT_SIZE)
    start_rng = np.random.default_rng([SEED, 2])
    selected: set[str] = set()
    order = {}
    for h in sorted(sizes):
        units = pool[pool.stratum_id == h].sort_values(["country_code", "major_sector", "random_key"])
        start = float(start_rng.random())
        for pos in systematic_positions(len(units), alloc[h], start):
            selected.add(units.iloc[pos]["project_id"])
        order.update({pid: i + 1 for i, pid in enumerate(units["project_id"])})
    initial = set(selected)
    swaps = coverage_swaps(pool, selected, COHORT_SIZE / len(pool))
    pool["stratum_pool_n"] = pool.stratum_id.map(sizes)
    pool["stratum_alloc_n"] = pool.stratum_id.map(alloc)
    pool["inclusion_probability"] = (pool.stratum_alloc_n / pool.stratum_pool_n).round(6)
    pool["design_weight"] = (pool.stratum_pool_n / pool.stratum_alloc_n.replace(0, np.nan)).round(6)
    pool["within_stratum_order"] = pool.project_id.map(order)
    pool["selected"] = pool.project_id.isin(selected)
    swapped_in = {s["swapped_in"] for s in swaps}
    pool["selection_method"] = np.where(
        ~pool.selected, np.where(pool.project_id.isin(initial), "removed_by_country_coverage_swap", "not_selected"),
        np.where(pool.project_id.isin(swapped_in), "country_coverage_swap_in", "systematic_within_stratum"))
    pool["selection_seed"] = SEED
    pool["selected_on"] = SELECTED_ON
    info = {"strata": len(sizes), "strata_with_zero_allocation": sum(1 for v in alloc.values() if v == 0),
            "country_coverage_swaps": swaps}
    return pool, info


def coverage(cohort: pd.DataFrame, pool: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    ids = set(cohort.project_id)
    # IEG and finance
    ieg = D.ieg().drop_duplicates("Project ID", keep="last").set_index("Project ID")
    v3_ieg = {r["id"] for r in D.projects() if r.get("ieg_ratings")}
    c1 = D.contracts_legacy()
    legacy = c1.groupby("Project ID")["Contract Number"].nunique()
    loans: collections.Counter[str] = collections.Counter()
    for fn in ("ds00047_ibrd_latest.csv", "ds00001_ida_latest.csv"):
        d = D.read_csv_raw(SETTINGS.cache_dir / fn)
        loans.update(d["Project ID"].str.strip())
    # local samples
    cluster = set(D.cluster_projects())
    notice_text = collections.Counter(r.get("project_id") for r in D.notices_cluster())
    allfields = {r["id"] for r in D.projects_v3_cluster()}
    doc_meta: collections.Counter[str] = collections.Counter()
    doc_types: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for d in D.docs_cluster("v3"):
        for pid in split_project_ids(d.get("projectid")):
            doc_meta[pid] += 1
            if d.get("docty") in DOC_TYPES:
                doc_types[pid][DOC_TYPES[d["docty"]]] += 1
    pdfs, txts, pdf_types = local_files()
    pp = pd.read_csv(INPUTS["procurement_plan_activities"], dtype=str)
    plans = pp.groupby("_project_id")["_wds_id"].nunique()
    with gzip.open(SETTINGS.samples_dir / "documents" / "isr_series" / "isr_series_meta.jsonl.gz", "rt") as f:
        isr_series = {pid for line in f for pid in split_project_ids(json.loads(line).get("projectid"))}

    c = cohort.copy()
    c["ieg_rating_available"] = c.project_id.isin(ieg.index)
    c["ieg_evaluation_type"] = c.project_id.map(ieg["Evaluation Type"])
    c["ieg_outcome"] = c.project_id.map(ieg["Outcome"])
    c["ieg_evaluation_fy"] = c.project_id.map(ieg["Evaluation FY"])
    c["v3_ieg_ratings_available"] = c.project_id.isin(v3_ieg)
    c["legacy_contract_count_ds01004"] = c.project_id.map(legacy).fillna(0).astype(int)
    c["loan_statement_count"] = c.project_id.map(loans).fillna(0).astype(int)
    c["local_notice_fulltext_count"] = c.project_id.map(notice_text).fillna(0).astype(int)
    c["local_project_allfields"] = c.project_id.isin(allfields)
    c["local_doc_metadata_count"] = c.project_id.map(doc_meta).fillna(0).astype(int)
    # Only the Phase 1 cluster projects were queried by projectid (complete document list); other
    # projects appear only as co-tagged projects on those documents (partial list).
    c["local_doc_metadata_complete"] = c.project_id.isin(cluster)
    c["local_pdf_count"] = c.project_id.map(pdfs).fillna(0).astype(int)
    c["local_pdf_types"] = c.project_id.map(lambda p: ";".join(sorted(set(pdf_types.get(p, [])))))
    c["local_text_count"] = c.project_id.map(txts).fillna(0).astype(int)
    c["local_parsed_procurement_plans"] = c.project_id.map(plans).fillna(0).astype(int)
    c["local_isr_series"] = c.project_id.isin(isr_series)

    # Estimates for projects without a complete local document list: per-status rates observed in
    # the Phase 1 cluster projects that are in the eligible pool (all of their documents were listed).
    ref = pool[pool.project_id.isin(cluster)]
    est: dict[str, dict] = {}
    for status, grp in ref.groupby("status"):
        n = len(grp)
        est[status] = {"reference_projects": n,
                       "docs_per_project_mean": round(sum(doc_meta[p] for p in grp.project_id) / n, 1)}
        for t in DOC_TYPES.values():
            have = [doc_types[p][t] for p in grp.project_id]
            est[status][t] = {"projects_with_type_pct": round(100 * sum(1 for x in have if x) / n, 1),
                              "docs_per_project_mean": round(sum(have) / n, 2)}
    missing_meta = c[~c.local_doc_metadata_complete]
    need_docs: dict[str, dict[str, int]] = {}
    for t in DOC_TYPES.values():
        need_docs[t] = {
            "projects_expected_with_type": int(round(sum(est[s][t]["projects_with_type_pct"] / 100 * n
                                                         for s, n in missing_meta.status.value_counts().items()))),
            "documents_expected": int(round(sum(est[s][t]["docs_per_project_mean"] * n
                                                for s, n in missing_meta.status.value_counts().items()))),
        }
    closed = c[c.status == "Closed"]
    summary = {
        "contracts": {"distinct_contracts": int(c.contract_count.sum()), "contract_lines": int(c.contract_line_count.sum()),
                      "amount_usd": round(float(c.contract_amount_usd.sum()), 0),
                      "per_project_quantiles": c.contract_count.quantile([.1, .25, .5, .75, .9]).to_dict(),
                      "legacy_ds01004_contracts": int(c.legacy_contract_count_ds01004.sum()),
                      "projects_with_legacy_contracts": int((c.legacy_contract_count_ds01004 > 0).sum()),
                      "projects_approved_before_fy2020_partial_contract_history": int((c.approval_fy < 2020).sum())},
        "notices": {"total": int(c.notice_count.sum()),
                    "by_type": {k: int(c[k].sum()) for k in c.columns if k.startswith("notice_") and k != "notice_count"},
                    "per_project_quantiles": c.notice_count.quantile([.1, .25, .5, .75, .9]).to_dict()},
        "ieg": {"projects_with_ds00053_rating": int(c.ieg_rating_available.sum()),
                "projects_with_v3_ieg_ratings": int(c.v3_ieg_ratings_available.sum()),
                "closed_projects": len(closed), "closed_without_rating": int((~closed.ieg_rating_available).sum()),
                "outcomes": c.ieg_outcome.value_counts().to_dict(),
                "evaluation_types": c.ieg_evaluation_type.value_counts().to_dict()},
        "finance": {"projects_with_loan_statements": int((c.loan_statement_count > 0).sum())},
        "local_coverage": {
            "notice_fulltext_projects": int((c.local_notice_fulltext_count > 0).sum()),
            "notice_fulltext_records": int(c.local_notice_fulltext_count.sum()),
            "project_allfields_projects": int(c.local_project_allfields.sum()),
            "doc_metadata_complete_projects": int(c.local_doc_metadata_complete.sum()),
            "doc_metadata_partial_projects": int(((c.local_doc_metadata_count > 0) & ~c.local_doc_metadata_complete).sum()),
            "doc_metadata_records": int(c.local_doc_metadata_count.sum()),
            "pdf_projects": int((c.local_pdf_count > 0).sum()), "pdf_files": int(c.local_pdf_count.sum()),
            "pdf_types": dict(collections.Counter(t for v in c.local_pdf_types if v for t in v.split(";"))),
            "text_projects": int((c.local_text_count > 0).sum()), "text_files": int(c.local_text_count.sum()),
            "parsed_procurement_plan_projects": int((c.local_parsed_procurement_plans > 0).sum()),
            "isr_series_projects": int(c.local_isr_series.sum()),
        },
        "needs": {
            "notice_fulltext_projects": int((c.local_notice_fulltext_count == 0).sum()),
            "notice_fulltext_records_per_ds00979": int(c.loc[c.local_notice_fulltext_count == 0, "notice_count"].sum()),
            "project_allfields_projects": int((~c.local_project_allfields).sum()),
            "doc_metadata_projects": len(missing_meta),
            "doc_metadata_records_estimate": int(round(sum(est[s]["docs_per_project_mean"] * n
                                                           for s, n in missing_meta.status.value_counts().items()))),
            "documents_by_type_estimate_for_projects_without_metadata": need_docs,
            "procurement_plans_latest_version_projects": int((c.local_parsed_procurement_plans == 0).sum()),
            "estimate_basis": est,
        },
    }
    return c, summary


def main() -> None:
    OUT.mkdir(exist_ok=True)
    inputs = verify_inputs()
    pool, fy_info = build_pool()
    if len(pool) != EXPECTED_POOL:
        raise SystemExit(f"eligible pool is {len(pool)}, expected {EXPECTED_POOL} (audit 2026-10-21)")
    pool, sel_info = select(pool)
    cohort = pool[pool.selected].copy()

    checks = {
        "pool_unique_ids": bool(pool.project_id.is_unique),
        "cohort_rows": len(cohort), "cohort_unique_ids": int(cohort.project_id.nunique()),
        "cohort_exactly_2000_unique": len(cohort) == COHORT_SIZE == cohort.project_id.nunique(),
        "all_ids_valid_pnumber": bool(cohort.project_id.str.fullmatch(r"P\d{6}").all()),
        "all_selected_eligible": bool(((cohort.contract_count > 0) & (cohort.notice_count > 0)
                                       & cohort.status.isin(ELIGIBLE_STATUS)).all()),
        "stratum_counts_equal_allocation": bool(
            cohort.groupby("stratum_id").size().reindex(pool.groupby("stratum_id").stratum_alloc_n.first().index,
                                                        fill_value=0)
            .eq(pool.groupby("stratum_id").stratum_alloc_n.first()).all()),
    }
    if not all(v for k, v in checks.items() if isinstance(v, bool)):
        raise SystemExit(f"cohort checks failed: {checks}")

    representation = {}
    for dim in ("region", "status", "fy_band", "contract_volume_quartile", "major_sector", "financing_type"):
        t = share_table(pool, cohort, dim)
        representation[dim] = {"max_abs_diff_pp": float(t.abs_diff_pp.max()),
                               "table": t.reset_index(names=dim).to_dict(orient="records")}
    ct = share_table(pool, cohort, "country_code")
    expected = ct.pool_n * COHORT_SIZE / len(pool)
    representation["country_code"] = {
        "pool_countries": len(ct), "cohort_countries": int((ct.cohort_n > 0).sum()),
        "countries_with_ge2_eligible_unrepresented": int(((ct.pool_n >= 2) & (ct.cohort_n == 0)).sum()),
        "single_project_countries_in_pool": int((ct.pool_n == 1).sum()),
        "single_project_countries_selected": int(((ct.pool_n == 1) & (ct.cohort_n == 1)).sum()),
        "max_abs_count_deviation_from_proportional": round(float((ct.cohort_n - expected).abs().max()), 2),
        "max_abs_diff_pp": float(ct.abs_diff_pp.max()),
        "table": ct.reset_index(names="country_code").to_dict(orient="records"),
    }

    manifest, cov = coverage(cohort, pool)
    cols = ["project_id", "project_name", "country_code", "country_name", "country_iso2", "country_is_regional",
            "region", "major_sector", "approval_fy", "approval_fy_source", "fy_band", "status", "financing_type",
            "board_approval_date", "closing_date", "contract_count", "contract_line_count", "contract_amount_usd",
            "contract_volume_quartile", "first_contract_signing", "last_contract_signing", "notice_count",
            "notice_award_count", "notice_ifb_count", "notice_reoi_count", "notice_gpn_count", "notice_prequal_count",
            "notice_other_count", "ieg_rating_available", "ieg_evaluation_type", "ieg_outcome", "ieg_evaluation_fy",
            "v3_ieg_ratings_available", "legacy_contract_count_ds01004", "loan_statement_count",
            "local_notice_fulltext_count", "local_project_allfields", "local_doc_metadata_count", "local_doc_metadata_complete",
            "local_pdf_count",
            "local_pdf_types", "local_text_count", "local_parsed_procurement_plans", "local_isr_series",
            "stratum_id", "stratum_pool_n", "stratum_alloc_n", "inclusion_probability", "design_weight",
            "within_stratum_order", "random_key", "selection_method", "selection_seed", "selected_on"]
    for col in ("first_contract_signing", "last_contract_signing"):
        manifest[col] = manifest[col].dt.strftime("%Y-%m-%d")
    manifest[cols].sort_values("project_id").to_csv(OUT / "poc_cohort_2000_manifest.csv", index=False)
    pool_cols = [c for c in cols if c in pool.columns and not c.startswith(("local_", "ieg_", "v3_", "legacy_", "loan_"))]
    pool_out = pool.copy()
    for col in ("first_contract_signing", "last_contract_signing"):
        pool_out[col] = pool_out[col].dt.strftime("%Y-%m-%d")
    pool_out[pool_cols[:2] + ["selected"] + pool_cols[2:]].to_csv(OUT / "poc_eligible_pool_3120.csv", index=False)

    summary = {
        "decision": "D-26: PoC cohort = exactly 2,000 projects (replaces the 3,000 target)",
        "selected_on": SELECTED_ON, "seed": SEED, "cohort_size": COHORT_SIZE, "eligible_pool": len(pool),
        "eligibility": "v3 status in {Active, Closed} AND >=1 DS00005 contract row AND >=1 DS00979 notice",
        "sampling_fraction": round(COHORT_SIZE / len(pool), 6),
        "strata_definition": "region x status x fy_band x contract_volume_quartile",
        "contract_volume_quartile_bounds": pool.groupby("contract_volume_quartile").contract_count.agg(["min", "max"])
        .to_dict(orient="index"),
        "inputs": inputs, "checks": checks, **sel_info, **fy_info,
        "representation": representation, "coverage": cov,
    }
    (OUT / "poc_cohort_2000_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({"checks": checks, "swaps": len(sel_info["country_coverage_swaps"]),
                      "max_diff_pp": {k: v["max_abs_diff_pp"] for k, v in representation.items()}}, indent=1))


if __name__ == "__main__":
    main()
