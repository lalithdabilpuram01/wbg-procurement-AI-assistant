"""Phase 1, Task 3: re-check contract-amendment and re-bidding evidence on the current Silver tables.

Usage:  /opt/anaconda3/bin/python3 ml/phase1/s03_label_evidence_recheck.py <extract_dir>

Re-measures the p09 (2026-10-23) candidate counts from the s01 extract (Silver), triages the existing candidate
passages with transparent rules, and tests one new linking route: amendment records that quote the original
contract's reference. Everything is a *candidate*: nothing here is human verification, and no label is created.
Only counts are written; no passage, description or reference text leaves memory.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO_ROOT, SNAPSHOT_DATE, TENDER_TYPES, new_run_dir, ro_table, spark, table_versions, write_json  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "discovery" / "scripts"))
import p09_ml_label_feasibility as p09  # noqa: E402
from wbdisc.normalize import normalize_ref  # noqa: E402

HIST = REPO_ROOT / "discovery" / "profiling" / "ml_label_feasibility.json"
EVID = REPO_ROOT / "discovery" / "profiling" / "ml_label_evidence_candidates.csv"

# Triage rules for amendment candidates (applied to the stored passage; order matters)
AMEND_UNRELATED = re.compile(r"\b(?:act|law|bill|code|legislation|regulation|policy|plan|framework|manual|strategy|"
                             r"statute|decree|resettlement|guidelines?)\b[^.]{0,40}\bamend|amend\w*\s+(?:of|to)\s+(?:the\s+)?"
                             r"(?:\w+\s+){0,3}(?:act|law|bill|code|legislation|regulation|policy|plan|framework|manual|decree)\b"
                             r"|prepar\w+\s+(?:an?\s+)?addendum", re.I)
AMEND_EXPLICIT = re.compile(r"\bavenant\b|\btermo\s+aditivo\b|\baditivo\b|\baddendum\s+(?:no\.?\s*\d+\s+)?to\s+(?:the\s+)?contract"
                            r"|\bamendment\s+(?:no\.?\s*\d+\s+)?(?:to|of)\s+(?:the\s+)?(?:\w+\s+){0,3}contract\b|\bcontract\s+amendment"
                            r"|\bvariation\s+order|\bcontract\s+variation|\badenda\b|\benmienda\b|\bmodificaci[oó]n\s+(?:al|del)\s+contrato", re.I)
AMEND_EXTENSION = re.compile(r"\bextension\s+of\s+(?:the\s+)?contract|\bprolongation\b|\bcontract\s+extension", re.I)
REF_TOKEN = re.compile(r"[A-Z0-9][A-Z0-9./\-_ ]{6,}[A-Z0-9]", re.I)
REBID_TERMS = {
    "rebid / re-tender": re.compile(r"re-?\s?bid|re-?\s?tender", re.I),
    "re-advertise": re.compile(r"re-?\s?advertis", re.I),
    "re-invite": re.compile(r"re-?\s?invit", re.I),
    "re-launch / relance": re.compile(r"re-?\s?launch|relanc", re.I),
    "second call / nouvel appel / segunda convocatoria": re.compile(r"second\s+call|2nd\s*(?:call|tender|bid)|nouvel\s+appel|segunda\s+convocatoria|nueva\s+convocatoria", re.I),
    "failed (infructueux / desierto)": re.compile(r"infructueu|desiert", re.I),
}
KNOWN_FALSE_CONTEXT = re.compile(r"relance\s+commerciale|sahel\s+relance|desierto\s+de|relancer\s+l['e]|soit\s+relanc", re.I)


def main(extract_dir: str) -> None:
    ex = Path(extract_dir) / "data"
    out = new_run_dir("label_evidence")
    hist = __import__("json").loads(HIST.read_text())
    ck = pd.read_parquet(ex / "contract_keys.parquet")
    nd = pd.read_parquet(ex / "notice_details_keys.parquet")
    dsf = pd.read_parquet(ex / "ds00979_rebid_flags.parquet")
    ev = pd.read_csv(EVID, dtype=str)
    res: dict = {"note": "Re-measured on Silver; historical values from discovery/profiling/ml_label_feasibility.json "
                         "(2026-10-23). 'Candidate' everywhere; confirmed labels remain 0 (no human verification)."}

    # ---------------- contract amendment ----------------
    am = ck[ck["description_amendment_pattern"]]
    res["amendment"] = {
        "contracts_with_amendment_pattern": {"now": int(len(am)), "historical": hist["contract_amendment"]["ds00005_amendment_described_contracts"]},
        "projects": int(am["project_id"].nunique()),
        "share_of_cohort_contracts_pct": round(100 * len(am) / len(ck), 4),
    }
    a_ev = ev[ev["use_case"] == "contract_amendment"].copy()
    def triage(p: str) -> str:
        p = p or ""
        if AMEND_UNRELATED.search(p):
            return "likely_unrelated"
        if AMEND_EXPLICIT.search(p):
            return "explicit_contract_amendment_wording"
        if AMEND_EXTENSION.search(p):
            return "contract_extension_wording"
        return "ambiguous"
    a_ev["triage"] = a_ev["passage"].map(triage)
    res["amendment"]["rule_triage_of_61_candidates"] = a_ev.groupby(["evidence_type", "triage"]).size().unstack(fill_value=0).to_dict("index")
    res["amendment"]["historical_reading_not_verification"] = {"look_genuine": 49, "ambiguous": 4, "not_amendments": 3,
                                                               "source": "ML_LABEL_FEASIBILITY_AUDIT.md §4"}

    # New linking route: the amendment record quotes the original contract reference
    s = spark()
    tabs = ["wbg_silver.contract_awards"]
    v0 = table_versions(s, tabs)
    proj = sorted(set(am["project_id"]))
    sc = ro_table(s, "wbg_silver.contract_awards").where(__import__("pyspark.sql.functions", fromlist=["col"]).col("project_id").isin(proj)) \
        .select("wb_contract_number", "project_id", "borrower_contract_reference", "contract_description",
                "contract_signing_date", "supplier_ids").toPandas()
    sc["_ref"] = sc["borrower_contract_reference"].map(normalize_ref)
    amset = set(am["wb_contract_number"])
    linked_quote, linked_quote_supplier, linked_quote_unique = 0, 0, 0
    for _, r in sc[sc["wb_contract_number"].isin(amset)].iterrows():
        others = sc[(sc["project_id"] == r["project_id"]) & (~sc["wb_contract_number"].isin(amset))]
        toks = {normalize_ref(t) for t in REF_TOKEN.findall(r["contract_description"] or "")}
        toks |= {normalize_ref(r["borrower_contract_reference"])}
        toks.discard(None)
        hits = others[others["_ref"].isin(toks) & (others["contract_signing_date"] < r["contract_signing_date"])]
        if len(hits):
            linked_quote += 1
            linked_quote_unique += len(hits) == 1
            mine = set(r["supplier_ids"] if r["supplier_ids"] is not None else [])
            linked_quote_supplier += any(mine & set(x if x is not None else []) for x in hits["supplier_ids"])
    res["amendment"]["link_to_earlier_contract_by_reference_quoted_or_shared"] = {
        "amendment_records": int(len(amset)), "with_earlier_contract_same_project_matching_reference": linked_quote,
        "exactly_one_earlier_match": linked_quote_unique, "of_which_same_supplier_id": linked_quote_supplier,
        "historical_same_supplier_route": {"any": 13, "exactly_one": 8},
        "note": "New observation (2026-10-25). A matching earlier reference is a candidate link, not proof.",
    }
    res["amendment"]["negatives"] = ("None defensible: no public source lists all amendments of a contract over a "
                                     "defined window; absence of an amendment record is not evidence of no amendment.")
    res["amendment"]["not_amendments_measured"] = {
        "award_notices_version_gt0": int((nd["award_notice_version_no"].fillna(0) > 0).sum()),
        "note": "notice revisions, not contract amendments; bidding-document addenda (167 historically) likewise excluded"}

    # ---------------- re-bidding ----------------
    t979 = dsf[dsf["notice_type"].isin(TENDER_TYPES)]
    tn = nd[nd["is_tender_type"]].copy()
    tn["notice_date"] = pd.to_datetime(tn["notice_date"])
    reps = tn.dropna(subset=["package_key"]).groupby("package_key")["notice_date"].agg(["count", "min", "max"])
    rh = hist["rebidding"]
    res["rebidding"] = {
        "ds00979_notices_with_marker": {"now": int(dsf["desc_rebid_marker"].sum()), "historical": rh["ds00979_notices_with_marker"]},
        "ds00979_marker_by_type": dsf[dsf["desc_rebid_marker"]]["notice_type"].value_counts().to_dict(),
        "ds00979_tender_notices": {"now": int(len(t979)), "historical": 55223},
        "ds00979_marked_tender_notices": {"now": int(t979["desc_rebid_marker"].sum()), "historical": rh["ds00979_marked_tender_notices"]},
        "ds00979_projects_with_marker": {"now": int(dsf[dsf["desc_rebid_marker"]]["project_id"].nunique()), "historical": rh["ds00979_projects_with_marker"]},
        "api_tender_notices": {"now": int(len(tn)), "historical": rh["api_tender_notices"]},
        "api_tender_description_marker": int(tn["desc_rebid_marker"].sum()),
        "api_tender_reference_marker": {"now": int(tn["ref_rebid_marker"].sum()), "historical": rh["api_tender_reference_rebid_marker"]},
        "api_tender_status": tn["notice_status"].value_counts().to_dict(),
        "api_tender_packages": {"now": int(len(reps)), "historical_groups": rh["api_tender_ref_groups"]},
        "api_packages_with_ge2_tender_notices_ge30d_apart": {
            "now": int(((reps["count"] >= 2) & ((reps["max"] - reps["min"]).dt.days >= 30)).sum()),
            "historical": rh["api_repeated_tender_ref_groups_ge30d"]},
        "contracts_reference_strong_marker": {"now": int(ck["ref_rebid_marker_strong"].sum()), "historical": rh["ds00005_contracts_ref_strong_rebid_marker"]},
        "contracts_reference_weak_R_only": {"now": int(ck["ref_rebid_marker_weak_only"].sum()), "historical": rh["ds00005_contracts_ref_weak_R_suffix"]},
        "not_rechecked_here": {
            "ds00979_marked_tenders_with_earlier_similar_tender_ge90": rh["ds00979_marked_tenders_with_earlier_similar_tender_ge90"],
            "plan_cancelled_activities": rh["plan_cancelled_activities"], "plan_cancelled_with_successor": 38,
            "reason": "Description-similarity matching and the 25 parsed plans are local discovery artefacts, not Silver; "
                      "re-running p09 would overwrite approved historical outputs.",
        },
    }
    r_ev = ev[ev["use_case"] == "rebidding"].copy()
    def term(p: str) -> str:
        for k, rx in REBID_TERMS.items():
            if rx.search(p or ""):
                return k
        return "other"
    r_ev["marker_term"] = r_ev["passage"].map(term)
    r_ev["known_false_context"] = r_ev["passage"].fillna("").str.contains(KNOWN_FALSE_CONTEXT)
    r_ev["has_link_to_original"] = r_ev["linked_original"].notna() & (r_ev["linked_original"].fillna("") != "")
    res["rebidding"]["candidate_triage_by_evidence_type"] = {
        et: {"rows": int(len(g)), "marker_terms": g["marker_term"].value_counts().to_dict(),
             "known_false_positive_context": int(g["known_false_context"].sum()),
             "with_a_candidate_original": int(g["has_link_to_original"].sum())}
        for et, g in r_ev.groupby("evidence_type")}
    res["rebidding"]["what_counts_as_evidence"] = {
        "not_rebidding_by_itself": ["notice status Cancelled", "notice status Revised / award notice versions",
                                    "bidding-document addenda or deadline extensions", "repeated reference (lots, revisions)",
                                    "plan status 'cancelled' (may be descoped, merged or moved to another plan)",
                                    "description similarity >= 90 without a reference link"],
        "positive_requires": "(a) official statement that the tender failed / was cancelled AND re-launched, plus "
                             "(b) a resolved link to the original tender record; verified by a person",
    }
    res["observation_window_snapshot"] = str(SNAPSHOT_DATE)
    v1 = table_versions(s, tabs)
    res["delta_versions_unchanged"] = v0 == v1
    write_json(out / "label_evidence_recheck.json", res)
    print(out)
    print(__import__("json").dumps(res, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main(sys.argv[1])
