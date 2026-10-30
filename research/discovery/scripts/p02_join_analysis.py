"""Task 4 / Task 6: cross-source join feasibility, match rates and tender-duration observability.

Match classes (reported separately, never merged):
  EXACT        identical identifier value in both sources
  EXACT_NORM   identical after deterministic normalisation (normalize_ref / loan-id separator removal)
  COMPOSITE    deterministic multi-field key (project_id + normalised reference [+ supplier id])
  PROBABILISTIC text-similarity candidate (rapidfuzz), reported by score band, NOT counted as a match

Notice <-> contract joins are evaluated only on the project cluster (all notices pulled for
276 projects), so the denominators are complete within those projects.

Output: profiling/join_analysis.json
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

from wbdisc import datasets as D  # noqa: E402
from wbdisc.award_notice import parse_award_notice  # noqa: E402
from wbdisc.config import SETTINGS  # noqa: E402
from wbdisc.countries import to_iso2  # noqa: E402
from wbdisc.normalize import normalize_ref, normalize_supplier_name, parse_date, wb_fiscal_year  # noqa: E402

TENDER_TYPES = {"Invitation for Bids", "Request for Expression of Interest", "Invitation for Prequalification"}


def pct(a: int, b: int) -> float | None:
    return round(100 * a / b, 2) if b else None


def loan_key(v: str | None) -> str | None:
    if not v:
        return None
    s = re.sub(r"[\s\-_]", "", str(v).upper())
    return s or None


def rate_block(left_keys: pd.Series, right_keys: set) -> dict:
    present = left_keys[left_keys.notna() & (left_keys != "")]
    hit = present.isin(right_keys)
    return {"left_rows": int(len(left_keys)), "left_rows_with_key": int(len(present)),
            "matched_rows": int(hit.sum()), "match_rate_pct_of_keyed": pct(int(hit.sum()), len(present)),
            "distinct_left_keys": int(present.nunique()), "distinct_left_keys_matched": int(present[hit].nunique())}


def main() -> None:
    out: dict = {}
    ca = D.contracts()
    projs = pd.DataFrame(D.projects())
    proj_ids = set(projs["id"])
    ieg = D.ieg()
    ieg_pid_col = next(c for c in ieg.columns if c.lower().replace(" ", "_") == "project_id")

    # ---------------- Project-level exact joins ----------------
    out["contract_to_project_api"] = rate_block(ca["Project ID"], proj_ids)
    v2_ids = {p["id"] for p in D.projects_v2()}
    out["contract_to_project_api_v2_frozen_index"] = rate_block(ca["Project ID"], v2_ids)
    out["contract_projects_unmatched_v3"] = ca[~ca["Project ID"].isin(proj_ids)]["Project ID"].value_counts().head(10).to_dict()
    out["contract_to_ieg"] = rate_block(ca["Project ID"], set(ieg[ieg_pid_col]))
    out["contract_projects_with_ieg_rating_pct"] = pct(ca["Project ID"].drop_duplicates().isin(set(ieg[ieg_pid_col])).sum(),
                                                       ca["Project ID"].nunique())
    ibrd, ida = D.ibrd_latest(), D.ida_latest()
    fin_pids = set(ibrd["Project ID"]) | set(ida["Project ID"])
    out["contract_to_ibrd_ida_statements_by_project"] = rate_block(ca["Project ID"], fin_pids)
    out["ieg_to_project_api"] = rate_block(ieg[ieg_pid_col], proj_ids)
    nb = D.notices_bulk()
    out["ds00979_notice_to_project_api"] = rate_block(nb["Project ID"], proj_ids)
    loans_per_project = pd.concat([ibrd[["Project ID", "Loan Number"]].rename(columns={"Loan Number": "loan"}),
                                   ida[["Project ID", "Credit Number"]].rename(columns={"Credit Number": "loan"})])
    out["loans_per_project_distribution"] = loans_per_project.groupby("Project ID").size().value_counts().sort_index().head(10).to_dict()
    out["statement_loans_without_project_id"] = int((loans_per_project["Project ID"].str.strip() == "").sum())

    # Attribute consistency for exact project matches
    m = ca.drop_duplicates("Project ID").merge(projs, left_on="Project ID", right_on="id", how="inner")
    code_ok = m.apply(lambda r: isinstance(r["countrycode"], list) and r["Borrower Country / Economy Code"] in r["countrycode"], axis=1)
    out["contract_vs_project_country_code_agreement_pct"] = pct(int(code_ok.sum()), len(m))
    out["contract_vs_project_name_exact_agreement_pct"] = pct(int((m["Project Name"].str.strip() == m["project_name"].str.strip()).sum()), len(m))
    out["contract_vs_project_region_agreement_pct"] = pct(int((m["Region"].str.lower() == m["regionname"].fillna("").str.lower()).sum()), len(m))

    # ---------------- Country joins to governance indicators ----------------
    wgi = D.indicator("GOV_WGI_CC.EST")
    wgi_iso2 = {r["country"]["id"] for r in wgi if r["value"] is not None}
    cpia = D.indicator("IQ.CPA.TRAN.XQ")
    cpia_iso2 = {r["country"]["id"] for r in cpia if r["value"] is not None}
    out["contract_country_to_wgi"] = rate_block(ca["Borrower Country / Economy Code"], wgi_iso2)
    out["contract_country_to_cpia"] = rate_block(ca["Borrower Country / Economy Code"], cpia_iso2)
    iso = ca["Borrower Country / Economy Code"].map(to_iso2)
    out["contract_country_to_wgi_after_crosswalk"] = rate_block(iso, wgi_iso2)
    out["contract_country_to_cpia_after_crosswalk"] = rate_block(iso, cpia_iso2)
    out["contract_rows_regional_or_unmappable_country"] = int(iso.isna().sum())
    out["contract_rows_regional_or_unmappable_country_pct"] = pct(int(iso.isna().sum()), len(ca))
    unmatched = ca[~iso.isin(wgi_iso2)]
    out["contract_country_unmatched_to_wgi_top"] = unmatched["Borrower Country / Economy"].value_counts().head(12).to_dict()
    wgi_years = sorted({int(r["date"]) for r in wgi if r["value"] is not None})
    out["wgi_years_available"] = [wgi_years[0], wgi_years[-1], f"missing: {sorted(set(range(wgi_years[0], wgi_years[-1] + 1)) - set(wgi_years))}"]
    sd = ca["Contract Signing Date"].map(parse_date)
    out["contracts_signed_after_latest_wgi_year_plus1_pct"] = pct(int((sd.map(lambda d: d.year if d else 0) > wgi_years[-1] + 1).sum()), len(ca))

    # ---------------- Notice <-> contract (cluster only) ----------------
    cluster = set(D.cluster_projects())
    notices = pd.DataFrame(D.notices_cluster())
    cc = ca[ca["Project ID"].isin(cluster)].copy()
    cc["_ref"] = cc["Borrower Contract Reference Number"].map(normalize_ref)
    notices["_ref"] = notices["bid_reference_no"].map(normalize_ref)
    notices["_nd"] = notices["noticedate"].map(parse_date)
    out["cluster"] = {"projects": len(cluster), "contract_rows": len(cc), "distinct_contracts": int(cc["WB Contract Number"].nunique()),
                      "notices": len(notices), "notice_types": notices["notice_type"].value_counts().to_dict(),
                      "contract_rows_with_ref_pct": pct(int(cc["_ref"].notna().sum()), len(cc))}

    award = notices[notices["notice_type"] == "Contract Award"].copy()
    parsed = award["notice_text"].map(parse_award_notice)
    award["_awarded_ids"] = parsed.map(lambda p: set(p.get("awarded_supplier_ids", [])))
    award["_bidders"] = parsed.map(lambda p: p.get("bidder_count_by_id"))
    award["_award_date"] = parsed.map(lambda p: parse_date(p.get("award_notification_date") or p.get("contract_signature_date")))
    award["_version"] = parsed.map(lambda p: p.get("notice_version_no"))
    tender = notices[notices["notice_type"].isin(TENDER_TYPES)].copy()

    def index(df: pd.DataFrame) -> dict:
        idx: dict = collections.defaultdict(list)
        for i, r in df.iterrows():
            if r["_ref"]:
                idx[(r["project_id"], r["_ref"])].append(i)
        return idx

    a_idx, t_idx = index(award), index(tender)
    # raw (un-normalised) exact ref matches for comparison
    raw_a = set(zip(award["project_id"], award["bid_reference_no"].fillna("")))
    raw_t = set(zip(tender["project_id"], tender["bid_reference_no"].fillna("")))

    contracts = cc.drop_duplicates("WB Contract Number").copy()  # contract grain (JV rows collapsed)
    res = collections.Counter()
    durations, bid_periods, eval_periods, supplier_agree = [], [], [], collections.Counter()
    by_method = collections.defaultdict(collections.Counter)
    rows_out = []
    for _, r in contracts.iterrows():
        key = (r["Project ID"], r["_ref"])
        meth = r["Procurement Method"]
        by_method[meth]["contracts"] += 1
        if not r["_ref"]:
            res["no_reference"] += 1
            continue
        res["raw_exact_award"] += (r["Project ID"], r["Borrower Contract Reference Number"]) in raw_a
        res["raw_exact_tender"] += (r["Project ID"], r["Borrower Contract Reference Number"]) in raw_t
        a_hits, t_hits = a_idx.get(key, []), t_idx.get(key, [])
        res["award_matches_0"] += not a_hits
        res["award_matches_1"] += len(a_hits) == 1
        res["award_matches_gt1"] += len(a_hits) > 1
        res["tender_matches_0"] += not t_hits
        res["tender_matches_1"] += len(t_hits) == 1
        res["tender_matches_gt1"] += len(t_hits) > 1
        by_method[meth]["award_match"] += bool(a_hits)
        by_method[meth]["tender_match"] += bool(t_hits)
        signing = parse_date(r["Contract Signing Date"])
        sup_ids = set(cc[cc["WB Contract Number"] == r["WB Contract Number"]]["Supplier ID"])
        if a_hits:
            ok = any(sup_ids & award.loc[i, "_awarded_ids"] for i in a_hits)
            any_ids = any(award.loc[i, "_awarded_ids"] for i in a_hits)
            supplier_agree["award_has_supplier_ids"] += any_ids
            supplier_agree["supplier_id_agrees"] += ok
            supplier_agree["unique_composite_match"] += ok and sum(bool(sup_ids & award.loc[i, "_awarded_ids"]) for i in a_hits) == 1
        if t_hits:
            first = min(tender.loc[i, "_nd"] for i in t_hits if tender.loc[i, "_nd"])
            dl = [parse_date(tender.loc[i, "submission_deadline_date"]) for i in t_hits]
            dl = [d for d in dl if d]
            if signing and first:
                durations.append((signing - first).days)
            if dl and first:
                bid_periods.append((max(dl) - first).days)
            if a_hits and dl:
                ad = [award.loc[i, "_award_date"] for i in a_hits if award.loc[i, "_award_date"]]
                if ad:
                    eval_periods.append((min(ad) - max(dl)).days)
            award_pub = min((award.loc[i, "_nd"] for i in a_hits if award.loc[i, "_nd"]), default=None)
            rows_out.append({"wb_contract_number": r["WB Contract Number"], "project_id": r["Project ID"],
                             "method": meth, "category": r["Procurement Category"],
                             "first_tender_notice": str(first), "max_deadline": str(max(dl)) if dl else None,
                             "award_notice_published": str(award_pub) if award_pub else None,
                             "signing": str(signing), "days": (signing - first).days if signing and first else None,
                             # validity rule: a real advertised tender precedes signing, has a deadline after
                             # publication, and was not published on/after the award notice (back-filled)
                             "valid_tender_start": bool(signing and first and first < signing and dl and max(dl) > first
                                                        and (award_pub is None or first < award_pub))})
    n_ref = len(contracts) - res["no_reference"]
    out["contract_to_notice"] = {
        "distinct_contracts": len(contracts), "with_reference": n_ref, **res,
        "award_match_rate_pct": pct(res["award_matches_1"] + res["award_matches_gt1"], n_ref),
        "tender_match_rate_pct": pct(res["tender_matches_1"] + res["tender_matches_gt1"], n_ref),
        "supplier_id_agreement": dict(supplier_agree),
        "by_procurement_method": {k: {**v, "award_match_pct": pct(v["award_match"], v["contracts"]),
                                       "tender_match_pct": pct(v["tender_match"], v["contracts"])}
                                  for k, v in sorted(by_method.items(), key=lambda kv: -kv[1]["contracts"])},
    }

    def dist(x: list[int]) -> dict:
        if not x:
            return {"n": 0}
        s = pd.Series(x)
        return {"n": len(x), "negative": int((s < 0).sum()), "zero": int((s == 0).sum()),
                **{f"p{int(q*100)}": float(s.quantile(q)) for q in (0.05, 0.25, 0.5, 0.75, 0.95)}, "max": int(s.max())}

    out["tender_duration_days_first_tender_notice_to_signing"] = dist(durations)
    out["bidding_period_days_notice_to_deadline"] = dist(bid_periods)
    out["evaluation_period_days_deadline_to_award"] = dist(eval_periods)
    cand = pd.DataFrame(rows_out)
    cand.to_csv(SETTINGS.profiling_dir / "tender_duration_candidates_cluster.csv", index=False)
    valid = cand[cand["valid_tender_start"]]
    out["tender_duration_after_validity_rule"] = {
        "candidates": len(cand), "valid": len(valid), "valid_pct": pct(len(valid), len(cand)),
        "excluded_backfilled_or_zero_deadline": len(cand) - len(valid),
        "valid_pct_of_all_cluster_contracts": pct(len(valid), len(contracts)),
        "days_overall": dist(valid["days"].astype(int).tolist()),
        "days_by_method": {m: dist(g["days"].astype(int).tolist()) for m, g in valid.groupby("method") if len(g) >= 20},
    }

    # Reverse: award notices (award date >= FY2020) that find a DS00005 contract
    cref = set(zip(cc["Project ID"], cc["_ref"]))
    recent = award[award["_award_date"].map(lambda d: bool(d) and wb_fiscal_year(d) >= 2020)]
    hit = recent.apply(lambda r: (r["project_id"], r["_ref"]) in cref, axis=1)
    out["award_notice_to_contract_fy2020plus"] = {"award_notices": len(recent), "matched": int(hit.sum()),
                                                  "match_rate_pct": pct(int(hit.sum()), len(recent))}
    out["award_notice_versions"] = award["_version"].value_counts(dropna=False).to_dict()
    bidders = award["_bidders"].dropna()
    out["award_notice_bidder_count"] = {"n": int(len(bidders)), "single_bidder_pct": pct(int((bidders == 1).sum()), len(bidders)),
                                        "distribution": bidders.astype(int).value_counts().sort_index().head(12).to_dict()}
    # Duplicate references within project (ambiguity source)
    dup_ref = tender.dropna(subset=["_ref"]).groupby(["project_id", "_ref"]).size()
    out["tender_refs_reused_within_project"] = {"keys": int(len(dup_ref)), "keys_with_gt1_notice": int((dup_ref > 1).sum()),
                                                "max_notices_per_key": int(dup_ref.max()) if len(dup_ref) else 0}
    # Re-bidding signal: same project + same ref, multiple IFB/REOI notices; or 'rebid'/'re-tender' text
    text = tender["bid_description"].fillna("") + " " + tender["bid_reference_no"].fillna("")
    out["tender_rebid_text_markers"] = int(text.str.contains(r"\bre-?(bid|tender|advertis|issu|launch)|relance|nouvel appel|\bREBID\b",
                                                             case=False, regex=True).sum())

    # Probabilistic fallback for contracts with no ref-based tender match (diagnostic only)
    unmatched = contracts[~contracts.apply(lambda r: bool(t_idx.get((r["Project ID"], r["_ref"]))), axis=1)].head(1500)
    bands = collections.Counter()
    tender_by_proj = {p: g for p, g in tender.groupby("project_id")}
    for _, r in unmatched.iterrows():
        g = tender_by_proj.get(r["Project ID"])
        if g is None or not len(g):
            bands["no_tender_notices_in_project"] += 1
            continue
        scores = g["bid_description"].fillna("").map(lambda d: fuzz.token_set_ratio(r["Contract Description"], d))
        top = scores.nlargest(2).tolist() + [0]
        best, second = top[0], top[1]
        band = ">=90" if best >= 90 else "80-89" if best >= 80 else "60-79" if best >= 60 else "<60"
        bands[f"best_{band}"] += 1
        bands["ambiguous_top2_within_5pts"] += (best >= 80 and best - second <= 5)
    out["probabilistic_description_candidates_for_unmatched"] = {"evaluated": len(unmatched), **bands}

    # Implementing agency: projects.impagency vs notices.contact_organization (cluster)
    pmap = projs.set_index("id")["impagency"].to_dict()
    org = notices.dropna(subset=["contact_organization"]).drop_duplicates(["project_id", "contact_organization"])
    sims = [fuzz.token_set_ratio(str(pmap.get(p) or ""), o) for p, o in zip(org["project_id"], org["contact_organization"]) if pmap.get(p)]
    out["impagency_vs_notice_contact_org"] = {"pairs": len(sims), "projects_with_impagency": int(sum(1 for p in cluster if pmap.get(p))),
                                              "distinct_orgs_per_project_median": float(org.groupby("project_id").size().median()) if len(org) else None,
                                              "similarity_ge90_pct": pct(sum(s >= 90 for s in sims), len(sims)),
                                              "similarity_lt60_pct": pct(sum(s < 60 for s in sims), len(sims))}

    # Documents <-> projects (cluster)
    docs = pd.DataFrame(D.docs_cluster())
    docs["docty_clean"] = docs["docty"].fillna("").str.replace(r"\s+", " ", regex=True).str.strip()
    per = docs.assign(p=docs["_extract"].map(lambda e: e["filter_projectid"]))
    out["documents_cluster"] = {
        "documents": len(docs), "distinct_ids": int(docs["id"].nunique()),
        "projectid_differs_from_filter": int((per["projectid"] != per["p"]).sum()),
        "projects_with_any_doc": int(per["p"].nunique()),
        "coverage_pct_by_type": {t: pct(int(per[per["docty_clean"] == t]["p"].nunique()), len(cluster)) for t in
                                 ["Project Appraisal Document", "Implementation Status and Results Report", "Procurement Plan",
                                  "Implementation Completion and Results Report", "Implementation Completion Report Review",
                                  "Project Performance Assessment Report", "Project Paper"]},
        "docs_per_project_median": float(per.groupby("p").size().median()),
        "duplicate_titles_same_project_type_date": int(per.duplicated(subset=["p", "docty_clean", "docdt", "display_title"]).sum()),
    }
    (SETTINGS.profiling_dir / "join_analysis.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
