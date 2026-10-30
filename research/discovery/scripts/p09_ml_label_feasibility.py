"""ML label feasibility audit (2026-10-23): contract-amendment and re-bidding evidence for the 2,000-project cohort.

Offline and read-only on all inputs: cohort manifest, cohort-bounded Bronze inputs (DS00005, DS00979),
Phase 1 samples (procnotices API cluster sample, WDS v3 metadata, document text), and the parsed
procurement-plan activities (profiling/procurement_plan_activities.csv).

No labels are created. Every text match is a *candidate* with its supporting passage, exported for
human verification; nothing is marked "confirmed" by this script and nothing is marked negative
because evidence is absent.

Outputs (new files only):
  profiling/ml_label_feasibility.json           all measured counts
  profiling/ml_label_evidence_candidates.csv    one row per candidate event with evidence passage
  reports/ML_LABEL_COVERAGE_SUMMARY.csv         metric, value, denominator, definition, source
"""
from __future__ import annotations

import collections
import csv
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

from wbdisc import datasets as D  # noqa: E402
from wbdisc.award_notice import parse_award_notice  # noqa: E402
from wbdisc.cohort import split_project_ids  # noqa: E402
from wbdisc.config import REPO_ROOT, SETTINGS  # noqa: E402
from wbdisc.normalize import normalize_ref, parse_date  # noqa: E402
from wbdisc.procurement_plan import header_key, normalize_plan_status  # noqa: E402

BI = REPO_ROOT / "databricks_upload" / "bronze_inputs"
OUT_JSON = SETTINGS.profiling_dir / "ml_label_feasibility.json"
OUT_EVID = SETTINGS.profiling_dir / "ml_label_evidence_candidates.csv"
OUT_SUMMARY = SETTINGS.reports_dir / "ML_LABEL_COVERAGE_SUMMARY.csv"
TENDER_TYPES = {"Invitation for Bids", "Request for Expression of Interest", "Invitation for Prequalification"}

# --- evidence patterns (narrow by design; every hit is reviewed by a person before it can become a label) ---
CONTRACT_AMENDMENT = re.compile(
    r"\bavenant\b|\btermo\s+aditivo\b|\baditivo\s+(?:contratual|de\s+prazo|de\s+valor|ao\s+contrato)"
    r"|\baddendum\s+(?:no\.?\s*\d+\s+)?(?:for|to)\b|\bamendment\s+(?:no\.?\s*\d+\s+)?(?:to|of)\s+(?:the\s+)?(?:\w+\s+){0,3}contract\b"
    r"|\bcontract\s+amendment|\bextension\s+of\s+(?:the\s+)?contract\b|\bprolongation\s+(?:du|de\s+la)\s+(?:contrat|garantie)"
    r"|\badenda\s+(?:al\s+|del\s+|de\s+)?contrato|\benmienda\s+(?:al|del)\s+contrato|\bvariation\s+order|\bcontract\s+variation"
    r"|\bmodificaci[oó]n\s+(?:al|del)\s+contrato", re.I)
REBID_STRONG = re.compile(
    r"\bre-?\s?bid(?:ding|ded)?\b|\bre-?\s?tender(?:ing|ed)?\b|\bre-?\s?advertis\w*|\bre-?\s?invit\w*|\bre-?\s?launch\w*"
    r"|\brelanc\w*|\bsecond\s+call\b|\b2nd\s*(?:call|tender|bid)|\bnouvel\s+appel|\binfructueu\w*|\bdesiert[oa]s?\b"
    r"|\bnueva\s+convocatoria|\bsegunda\s+convocatoria", re.I)
NOTICE_CANCELLED = re.compile(
    r"(?:notice|invitation|tender|bid(?:ding)?(?:\s+process)?|advertisement|publication|procurement|request\s+for\s+\w+|bids\s+received[^.]{0,80})"
    r"\s+(?:has\s+been|have\s+been|is|was|is\s+hereby|stands)\s+(?:cancel+ed|withdrawn|annul+ed)"
    r"|publication\s+cancel+ed|est\s+annul[ée]e?|sont\s+annul[ée]e?s|a\s+[ée]t[ée]\s+annul[ée]e?|ha\s+sido\s+cancelad[oa]"
    r"|queda\s+cancelad[oa]|fue\s+declarad[oa]\s+desiert[oa]", re.I)
BID_DOC_AMENDMENT = re.compile(
    r"\baddend(?:um|a)\b|\bcorrigend(?:um|a)\b|\berratum\b|extension\s+of\s+(?:the\s+)?(?:bid|submission|closing)\s+(?:deadline|date)"
    r"|deadline\s+(?:for\s+\w+\s+)?(?:has\s+been\s+|is\s+)?extended", re.I)
REF_REBID_STRONG = re.compile(r"re-?\s?bid|re-?tender|re-?advert|re-?invit|re-?launch|relance|2\s*(?:nd|ème|eme|e)\s*(?:call|tender|bid|relance)", re.I)
REF_REBID_WEAK = re.compile(r"(?:[-/ ]R\d?$)|(?:\bR\d$)", re.I)
MARKER_TOKENS = re.compile(REBID_STRONG.pattern + r"|\(\s*\)|\b(?:lot|lots)\b", re.I)


def clean(text: str | None) -> str:
    text = text if isinstance(text, str) else ""  # missing notice_text arrives as NaN
    t = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", t).strip()


def passage(text: str, m: re.Match | None, width: int = 160) -> str:
    if m is None:
        return ""
    return text[max(0, m.start() - width): m.end() + width]


def strip_markers(s: str) -> str:
    return re.sub(r"\s+", " ", MARKER_TOKENS.sub(" ", s or "")).strip().lower()


def stem_ref(ref: str | None) -> str | None:
    if not isinstance(ref, str) or not ref:
        return None
    s = REF_REBID_STRONG.sub("", ref)
    s = REF_REBID_WEAK.sub("", s.strip(" -/_()"))
    return normalize_ref(s)


def pct(a: float, b: float) -> float | None:
    return round(100 * a / b, 2) if b else None


def main() -> None:
    res: dict = {}
    evid: list[dict] = []
    summary: list[dict] = []

    def metric(section: str, name: str, value, denominator=None, definition: str = "", source: str = "") -> None:
        summary.append({"section": section, "metric": name, "value": value, "denominator": denominator,
                        "pct": pct(value, denominator) if isinstance(value, (int, float)) and denominator else None,
                        "definition": definition, "source": source})

    cohort = pd.read_csv(REPO_ROOT / "cohort" / "poc_cohort_2000_manifest.csv", dtype=str, keep_default_na=False)
    ids = set(cohort.project_id)
    cluster = set(D.cluster_projects()) & ids  # projects with full notice text and complete document lists

    # ================= 1. structured sources (cohort rows) =================
    c5 = pd.read_csv(BI / "contract_awards_ds00005" / "ds00005_contract_awards__cohort2000.csv", dtype=str,
                     keep_default_na=False, encoding="utf-8-sig")
    nb = pd.read_csv(BI / "procurement_notices_ds00979" / "ds00979_procurement_notice__cohort2000.csv", dtype=str,
                     keep_default_na=False, encoding="utf-8-sig")
    api = pd.DataFrame([r for r in D.notices_cluster() if r.get("project_id") in ids])
    # missing text fields arrive as NaN; treat them as empty strings for matching (values are not rewritten anywhere)
    api = api.fillna({c: "" for c in ("bid_reference_no", "bid_description", "notice_text", "noticedate", "submission_deadline_date")})
    pp = pd.read_csv(SETTINGS.profiling_dir / "procurement_plan_activities.csv", dtype=str)
    pp = pp[pp["_project_id"].isin(ids)].copy()

    def filled(df: pd.DataFrame, col: str) -> float | None:
        return pct(int(df[col].fillna("").astype(str).str.strip().ne("").sum()), len(df)) if col in df else None

    inv = {
        "DS00005 contract awards (FY2020+), cohort extract": {
            "rows": len(c5), "projects": int(c5["Project ID"].nunique()), "distinct_contracts": int(c5["WB Contract Number"].nunique()),
            "fields_populated_pct": {c: filled(c5, c) for c in ["Project ID", "WB Contract Number", "Borrower Contract Reference Number",
                                                                "Contract Signing Date", "Supplier Contract Amount (USD)", "Supplier ID",
                                                                "Procurement Method", "Review type", "Contract Description"]},
            "amendment_or_status_fields": [c for c in c5.columns if re.search(r"amend|revis|status|cancel|extens|original|variation", c, re.I)]},
        "DS00979 procurement notices (bulk metadata), cohort extract": {
            "rows": len(nb), "projects": int(nb["Project ID"].nunique()),
            "fields_populated_pct": {c: filled(nb, c) for c in ["ID", "Notice Type", "Publication Date", "Deadline Date", "Bid Description"]},
            "reference_or_status_fields": [c for c in nb.columns if re.search(r"ref|status|cancel|version|amend", c, re.I)]},
        "procnotices API (full text), cohort projects in Phase 1 sample": {
            "rows": len(api), "projects": int(api["project_id"].nunique()),
            "fields_populated_pct": {c: filled(api, c) for c in ["id", "bid_reference_no", "notice_type", "notice_status", "noticedate",
                                                                 "submission_deadline_date", "notice_text"]},
            "notice_status_counts": api.groupby(["notice_type", "notice_status"]).size().rename("n").reset_index().to_dict(orient="records")},
        "Procurement plans (parsed STEP PDFs), cohort projects": {
            "activities": len(pp), "projects": int(pp["_project_id"].nunique()), "plan_documents": int(pp["_wds_id"].nunique())},
    }
    stat_col = next(c for c in pp.columns if header_key(c).startswith("processstatus"))
    ref_col = next(c for c in pp.columns if header_key(c).startswith("activityreference"))
    pp["_status"] = pp[stat_col].map(normalize_plan_status)
    amend_cols = [c for c in pp.columns if "amend" in header_key(c)]
    inv["Procurement plans (parsed STEP PDFs), cohort projects"].update({
        "process_status_counts": pp["_status"].value_counts().to_dict(),
        "amendment_columns": {c: int(pp[c].notna().sum()) for c in amend_cols},
        "fields_populated_pct": {c: filled(pp, c) for c in [ref_col, stat_col, "Signed Contract | Actual", "Contract Completion | Actual",
                                                            "Specific Procurement Notice / Invitation | Actual",
                                                            "Estimated Amount (US$)", "Actual Amount (US$)"] if c in pp}})
    # award-notice structured fields (parsed from STEP award notice text)
    aw = api[api.notice_type == "Contract Award"].copy()
    parsed = aw["notice_text"].map(parse_award_notice)
    aw["_version"] = parsed.map(lambda p: p.get("notice_version_no"))
    aw["_award_date"] = parsed.map(lambda p: p.get("award_notification_date"))
    aw["_ids"] = parsed.map(lambda p: set(p.get("awarded_supplier_ids", [])))
    aw["_amount"] = parsed.map(lambda p: p.get("signed_price_amount"))
    inv["procnotices API (full text), cohort projects in Phase 1 sample"]["award_notice_parsed_fields_pct"] = {
        "notice_version_no": pct(int(aw._version.notna().sum()), len(aw)),
        "award_notification_date": pct(int(aw._award_date.notna().sum()), len(aw)),
        "awarded_supplier_ids": pct(int(aw._ids.map(bool).sum()), len(aw)),
        "signed_price_amount": pct(int(aw._amount.notna().sum()), len(aw))}
    res["structured_inventory"] = inv
    metric("inventory", "cohort_projects", len(ids), None, "Approved cohort (D-26)", "cohort manifest")
    metric("inventory", "ds00005_contract_rows", len(c5), None, "Contract x supplier lines, FY2020+", "DS00005 cohort extract")
    metric("inventory", "ds00005_distinct_contracts", int(c5["WB Contract Number"].nunique()), None, "Distinct WB Contract Number", "DS00005")
    metric("inventory", "ds00979_notices", len(nb), None, "Notices linked to cohort projects by Project ID (no reference no., no text)", "DS00979")
    tenders_nb = nb[nb["Notice Type"].isin(TENDER_TYPES)]
    metric("inventory", "ds00979_tender_notices", len(tenders_nb), len(nb), "IFB + REOI + prequalification notices", "DS00979")
    metric("inventory", "api_fulltext_projects", int(api.project_id.nunique()), len(ids), "Cohort projects with full notice text + bid reference", "procnotices API sample")
    metric("inventory", "api_fulltext_notices", len(api), len(nb), "Notices with text for cohort projects", "procnotices API sample")
    metric("inventory", "plan_projects", int(pp._project_id.nunique()), len(ids), "Cohort projects with a parsed STEP plan", "procurement_plan_activities.csv")
    metric("inventory", "plan_activities", len(pp), None, "Parsed plan activities, cohort projects", "procurement_plan_activities.csv")

    # ================= 2. contract amendment evidence =================
    am: dict = {}
    k5 = c5.drop_duplicates("WB Contract Number").copy()
    k5["_sign"] = k5["Contract Signing Date"].map(parse_date)
    hits = k5[k5["Contract Description"].str.contains(CONTRACT_AMENDMENT, regex=True)]
    sup = c5.groupby("WB Contract Number")["Supplier ID"].agg(lambda s: {x for x in s if x.strip()})
    linkable = unique = 0
    for _, r in hits.iterrows():
        m = CONTRACT_AMENDMENT.search(r["Contract Description"])
        sids = sup.get(r["WB Contract Number"], set())
        prior = k5[(k5["Project ID"] == r["Project ID"]) & (k5["WB Contract Number"] != r["WB Contract Number"])
                   & k5["WB Contract Number"].map(lambda c: bool(sup.get(c, set()) & sids))
                   & k5["_sign"].map(lambda d: bool(d and r["_sign"] and d <= r["_sign"]))]
        linkable += len(prior) > 0
        unique += len(prior) == 1
        evid.append({"use_case": "contract_amendment", "evidence_type": "contract_record_describes_amendment",
                     "source": "DS00005", "project_id": r["Project ID"], "record_id": r["WB Contract Number"],
                     "reference": r["Borrower Contract Reference Number"], "event_date": r["Contract Signing Date"],
                     "linked_original": ";".join(prior["WB Contract Number"].head(5)), "n_link_candidates": len(prior),
                     "passage": passage(r["Contract Description"], m), "status": "candidate - needs human verification"})
    am["ds00005_amendment_described_contracts"] = len(hits)
    am["ds00005_amendment_with_prior_same_supplier_contract"] = linkable
    am["ds00005_amendment_with_exactly_one_prior_candidate"] = unique
    am["ds00005_fields_for_amendments"] = "none (no amendment, revision, extension or original-contract fields)"
    # award notices whose text describes an amendment (STEP sometimes publishes an award notice per amendment)
    aw["_clean"] = aw["notice_text"].map(clean)
    aw_hits = aw[aw._clean.str.contains(CONTRACT_AMENDMENT, regex=True)]
    for _, r in aw_hits.iterrows():
        m = CONTRACT_AMENDMENT.search(r["_clean"])
        evid.append({"use_case": "contract_amendment", "evidence_type": "award_notice_describes_amendment", "source": "procnotices API",
                     "project_id": r["project_id"], "record_id": r["id"], "reference": r.get("bid_reference_no"),
                     "event_date": r.get("noticedate"), "linked_original": "", "n_link_candidates": "",
                     "passage": passage(r["_clean"], m), "status": "candidate - needs human verification"})
    am["api_award_notices_describing_amendment"] = len(aw_hits)
    am["api_award_notices_version_gt0 (notice revisions, NOT contract amendments)"] = int((pd.to_numeric(aw._version, errors="coerce") > 0).sum())
    am["api_award_notices"] = len(aw)
    # bidding-document addenda in tender notices (procurement-notice amendments, NOT contract amendments)
    api["_clean"] = api["notice_text"].map(clean)
    tn = api[api.notice_type.isin(TENDER_TYPES)].copy()
    am["api_tender_notices_with_bidding_document_addendum (NOT contract amendments)"] = int(tn._clean.str.contains(BID_DOC_AMENDMENT, regex=True).sum())
    am["plan_amendment_columns_nonempty_values"] = int(sum(pp[c].notna().sum() for c in amend_cols))
    res["contract_amendment"] = am
    metric("amendment", "ds00005_contracts_describing_amendment", len(hits), len(k5),
           "Contracts whose description explicitly names a contract amendment, addendum, avenant, aditivo or extension (narrow pattern)", "DS00005")
    metric("amendment", "ds00005_amendment_contracts_linkable_to_prior_contract", linkable, len(hits),
           "Of those, with >=1 earlier contract in the same project sharing a STEP Supplier ID", "DS00005")
    metric("amendment", "ds00005_amendment_contracts_unique_prior_candidate", unique, len(hits),
           "Of those, exactly one earlier same-supplier contract (still needs verification)", "DS00005")
    metric("amendment", "api_award_notices_describing_amendment", len(aw_hits), len(aw), "Award notices whose text explicitly describes a contract amendment", "procnotices API")
    metric("amendment", "api_award_notice_revisions_version_gt0", am["api_award_notices_version_gt0 (notice revisions, NOT contract amendments)"], len(aw),
           "Award notices with Notice Version No > 0: revisions of the notice, not contract amendments", "procnotices API")
    metric("amendment", "api_tender_notices_bidding_doc_addenda", am["api_tender_notices_with_bidding_document_addendum (NOT contract amendments)"],
           len(tn), "Tender notices mentioning addenda/corrigenda/deadline extensions: bidding-document changes, not contract amendments", "procnotices API")
    metric("amendment", "plan_amendment_step_values", am["plan_amendment_columns_nonempty_values"], len(pp),
           "Non-empty values in STEP plan 'Contract Amendments' columns (header present, no values)", "procurement plans")
    metric("amendment", "confirmed_positive_labels", 0, len(k5), "Verified amendment events linked to an original contract (no human verification done yet)", "-")
    metric("amendment", "confirmed_negative_labels", 0, len(k5), "No source records the absence of amendments over a defined window", "-")
    metric("amendment", "unknown_contracts", len(k5), len(k5), "All cohort contracts: outcome not established", "-")

    # ================= 3. re-bidding evidence =================
    rb: dict = {}
    tn["_ref"] = tn["bid_reference_no"].map(normalize_ref)
    tn["_stem"] = tn["bid_reference_no"].map(stem_ref)
    tn["_date"] = tn["noticedate"].map(parse_date)
    tn["_marker_text"] = tn._clean.str.contains(REBID_STRONG, regex=True) | tn["bid_description"].fillna("").str.contains(REBID_STRONG, regex=True)
    tn["_marker_ref"] = tn["bid_reference_no"].fillna("").str.contains(REF_REBID_STRONG, regex=True)
    tn["_cancel"] = tn._clean.str.contains(NOTICE_CANCELLED, regex=True) | (tn.notice_status == "Cancelled")
    rb["api_tender_notices"] = len(tn)
    rb["api_tender_status_cancelled"] = int((tn.notice_status == "Cancelled").sum())
    rb["api_tender_status_revised (in-place notice revision)"] = int((tn.notice_status == "Revised").sum())
    rb["api_tender_text_or_description_rebid_marker"] = int(tn._marker_text.sum())
    rb["api_tender_reference_rebid_marker"] = int(tn._marker_ref.sum())
    rb["api_tender_notice_cancellation_wording"] = int(tn._cancel.sum())
    marked = tn[tn._marker_text | tn._marker_ref]
    linked = 0
    for _, r in marked.iterrows():
        prior = tn[(tn.project_id == r.project_id) & (tn.id != r.id) & (tn._ref == r._stem) & ~(tn._marker_text | tn._marker_ref)
                   & tn._date.map(lambda d: bool(d and r._date and d < r._date))] if r._stem else tn.iloc[0:0]
        linked += len(prior) > 0
        m = REBID_STRONG.search(r._clean) or REBID_STRONG.search(r.get("bid_description") or "") or REF_REBID_STRONG.search(r.get("bid_reference_no") or "")
        src_text = r._clean if REBID_STRONG.search(r._clean) else (r.get("bid_description") or r.get("bid_reference_no") or "")
        evid.append({"use_case": "rebidding", "evidence_type": "tender_notice_rebid_marker", "source": "procnotices API",
                     "project_id": r.project_id, "record_id": r.id, "reference": r.get("bid_reference_no"), "event_date": r.get("noticedate"),
                     "linked_original": ";".join(prior["id"].head(5)), "n_link_candidates": len(prior),
                     "passage": passage(src_text, m) if m else "", "status": "candidate - needs human verification"})
    rb["api_marked_tenders_linked_to_earlier_unmarked_tender_same_ref_stem"] = linked
    for _, r in tn[tn._cancel].iterrows():
        m = NOTICE_CANCELLED.search(r._clean)
        evid.append({"use_case": "rebidding", "evidence_type": "tender_notice_cancelled", "source": "procnotices API",
                     "project_id": r.project_id, "record_id": r.id, "reference": r.get("bid_reference_no"), "event_date": r.get("noticedate"),
                     "linked_original": "", "n_link_candidates": "", "passage": passage(r._clean, m) if m else f"notice_status={r.notice_status}",
                     "status": "candidate - cancellation is not proof of re-bidding"})
    # repeated tender references: >=2 distinct tender notices, same project + normalised ref, >=30 days apart
    g = tn[tn._ref.notna() & tn._date.notna()].groupby(["project_id", "_ref"])
    rep = [k for k, grp in g if grp.id.nunique() >= 2 and (max(grp._date) - min(grp._date)).days >= 30]
    rb["api_repeated_tender_ref_groups_ge30d"] = len(rep)
    rb["api_tender_ref_groups"] = int(g.ngroups)
    # candidate negatives: single tender notice with a matching award notice and no cancellation / re-bid signal
    aw["_ref"] = aw["bid_reference_no"].map(normalize_ref)
    aw_keys = aw.groupby(["project_id", "_ref"]).size()
    signal_keys = set(zip(tn[tn._marker_text | tn._marker_ref | tn._cancel].project_id, tn[tn._marker_text | tn._marker_ref | tn._cancel]._ref)) | \
        set(zip(tn[tn._marker_text | tn._marker_ref].project_id, tn[tn._marker_text | tn._marker_ref]._stem))
    single = [(p, rf) for (p, rf), grp in g if grp.id.nunique() == 1]
    cand_neg = sum(1 for k in single if aw_keys.get(k, 0) == 1 and k not in signal_keys)
    rb["api_candidate_negative_single_tender_one_award_no_signal"] = cand_neg
    rb["api_tender_refs_without_award_match"] = sum(1 for k in single if aw_keys.get(k, 0) == 0)
    # DS00979 bulk (all cohort projects): markers in bid description, linked to an earlier similar notice
    nb["_date"] = nb["Publication Date"].map(parse_date)
    nb["_marker"] = nb["Bid Description"].str.contains(REBID_STRONG, regex=True)
    rb["ds00979_notices_with_marker"] = int(nb._marker.sum())
    rb["ds00979_marker_by_type"] = nb[nb._marker]["Notice Type"].value_counts().to_dict()
    rb["ds00979_projects_with_marker"] = int(nb[nb._marker]["Project ID"].nunique())
    tm = nb[nb._marker & nb["Notice Type"].isin(TENDER_TYPES)]
    by_proj = {p: grp for p, grp in nb[~nb._marker & nb["Notice Type"].isin(TENDER_TYPES)].groupby("Project ID")}
    ge90 = 0
    for _, r in tm.iterrows():
        cands = by_proj.get(r["Project ID"])
        best, best_id = 0.0, ""
        if cands is not None:
            target = strip_markers(r["Bid Description"])
            for _, c in cands.iterrows():
                if c._date and r._date and c._date < r._date:
                    s = fuzz.token_set_ratio(target, strip_markers(c["Bid Description"]))
                    if s > best:
                        best, best_id = s, c["ID"]
        ge90 += best >= 90
        m = REBID_STRONG.search(r["Bid Description"])
        evid.append({"use_case": "rebidding", "evidence_type": "ds00979_tender_description_rebid_marker", "source": "DS00979",
                     "project_id": r["Project ID"], "record_id": r["ID"], "reference": "", "event_date": r["Publication Date"],
                     "linked_original": best_id if best >= 90 else "", "n_link_candidates": round(best, 1),
                     "passage": passage(r["Bid Description"], m, 120), "status": "candidate - needs human verification"})
    rb["ds00979_marked_tender_notices"] = len(tm)
    rb["ds00979_marked_tenders_with_earlier_similar_tender_ge90"] = ge90
    # DS00005: contracts whose reference carries a re-bid marker (contract awarded after a re-bid)
    refm = k5[k5["Borrower Contract Reference Number"].str.contains(REF_REBID_STRONG, regex=True)]
    rb["ds00005_contracts_ref_strong_rebid_marker"] = len(refm)
    rb["ds00005_contracts_ref_weak_R_suffix"] = int(k5["Borrower Contract Reference Number"].str.contains(REF_REBID_WEAK, regex=True).sum()) - \
        int((k5["Borrower Contract Reference Number"].str.contains(REF_REBID_WEAK, regex=True) &
             k5["Borrower Contract Reference Number"].str.contains(REF_REBID_STRONG, regex=True)).sum())
    for _, r in refm.iterrows():
        m = REF_REBID_STRONG.search(r["Borrower Contract Reference Number"])
        evid.append({"use_case": "rebidding", "evidence_type": "contract_reference_rebid_marker", "source": "DS00005",
                     "project_id": r["Project ID"], "record_id": r["WB Contract Number"], "reference": r["Borrower Contract Reference Number"],
                     "event_date": r["Contract Signing Date"], "linked_original": "", "n_link_candidates": "",
                     "passage": r["Borrower Contract Reference Number"] + " | " + r["Contract Description"][:200],
                     "status": "candidate - needs human verification"})
    # procurement plans (cohort): cancelled activities and possible successors
    pp["_ref"] = pp[ref_col].fillna("").map(lambda v: normalize_ref(v.split(" / ")[0]))
    pp["_desc"] = pp[ref_col].fillna("").map(lambda v: v.split(" / ", 1)[1] if " / " in v else v).str.strip()
    canc = pp[pp._status == "cancelled"]
    succ_exact = succ_desc = 0
    for _, r in canc.iterrows():
        others = pp[(pp._wds_id == r._wds_id) & (pp._status != "cancelled") & (pp.index != r.name)]
        ex = others[others._ref == r._ref] if r._ref else others.iloc[0:0]
        ds = others[others._desc.map(lambda d: fuzz.token_set_ratio(r._desc.lower(), d.lower()) >= 90)] if r._desc else others.iloc[0:0]
        succ_exact += len(ex) > 0
        succ_desc += len(ds) > 0 and len(ex) == 0
    rb["plan_activities"] = len(pp)
    rb["plan_cancelled_activities"] = len(canc)
    rb["plan_cancelled_with_successor_same_ref"] = succ_exact
    rb["plan_cancelled_with_successor_similar_description_ge90"] = succ_desc
    rb["plan_signed_or_completed"] = int(pp._status.isin(["signed", "completed"]).sum())
    rb["plan_rebid_marker_in_reference_or_description"] = int(pp[ref_col].fillna("").str.contains(REBID_STRONG, regex=True).sum())
    res["rebidding"] = rb
    for k in ["api_tender_status_cancelled", "api_tender_text_or_description_rebid_marker", "api_tender_reference_rebid_marker",
              "api_tender_notice_cancellation_wording", "api_marked_tenders_linked_to_earlier_unmarked_tender_same_ref_stem",
              "api_repeated_tender_ref_groups_ge30d", "api_candidate_negative_single_tender_one_award_no_signal"]:
        metric("rebidding", k, rb[k], len(tn) if "groups" not in k and "negative" not in k else rb["api_tender_ref_groups"],
               {"api_tender_status_cancelled": "Tender notices with notice_status = Cancelled",
                "api_tender_text_or_description_rebid_marker": "Tender notices whose text or description contains explicit re-bid wording (rebid, re-tender, re-advertisement, relance, second call, infructueux, desierto...)",
                "api_tender_reference_rebid_marker": "Tender notices whose bid reference carries a re-bid marker",
                "api_tender_notice_cancellation_wording": "Tender notices stating the notice/tender/bids were cancelled or annulled (or status Cancelled)",
                "api_marked_tenders_linked_to_earlier_unmarked_tender_same_ref_stem": "Marked re-bid notices with an earlier unmarked tender notice in the same project whose normalised reference equals the marked reference without its re-bid suffix",
                "api_repeated_tender_ref_groups_ge30d": "Project + normalised reference groups with >=2 tender notices >=30 days apart (repetition alone does not prove re-bidding)",
                "api_candidate_negative_single_tender_one_award_no_signal": "Reference groups with exactly one tender notice, exactly one matching award notice, no cancellation or re-bid signal (candidate negative only)"}[k],
               "procnotices API (184 cohort projects)")
    metric("rebidding", "ds00979_notices_with_rebid_marker", rb["ds00979_notices_with_marker"], len(nb),
           "Notices (all types) whose Bid Description contains explicit re-bid wording", "DS00979 (2,000 projects)")
    metric("rebidding", "ds00979_projects_with_rebid_marker", rb["ds00979_projects_with_marker"], len(ids), "Cohort projects with >=1 such notice", "DS00979")
    metric("rebidding", "ds00979_marked_tender_notices", len(tm), len(tenders_nb), "Tender notices (IFB/REOI/prequal) with explicit re-bid wording", "DS00979")
    metric("rebidding", "ds00979_marked_tenders_with_earlier_similar_tender", ge90, len(tm),
           "Of those, an earlier unmarked tender notice in the same project with description similarity >=90 (candidate original)", "DS00979")
    metric("rebidding", "ds00005_contracts_ref_rebid_marker", len(refm), len(k5), "Contracts whose reference carries an explicit re-bid marker (awarded after a re-bid)", "DS00005")
    metric("rebidding", "ds00005_contracts_ref_weak_R_suffix", rb["ds00005_contracts_ref_weak_R_suffix"], len(k5), "References ending in -R/R1/R2 (ambiguous; not counted as evidence)", "DS00005")
    metric("rebidding", "plan_cancelled_activities", len(canc), len(pp), "Plan activities with Process Status = cancelled", "procurement plans (25 projects)")
    metric("rebidding", "plan_cancelled_with_successor_candidate", succ_exact + succ_desc, len(canc),
           "Cancelled activities with a non-cancelled activity in the same plan having the same reference or description similarity >=90", "procurement plans")
    metric("rebidding", "confirmed_positive_labels", 0, None, "Verified original tender -> re-bid pairs (no human verification done yet)", "-")
    metric("rebidding", "confirmed_negative_labels", 0, None, "Verified first-round success over a defined window (none verified)", "-")

    # ================= observation windows (right-censoring) =================
    snap = parse_date(c5["As of Date"].iloc[0])  # DS00005 snapshot date (all rows identical)
    if snap is None:
        raise SystemExit("DS00005 As of Date not parseable")
    win: dict[str, object] = {"snapshot_date": str(snap)}
    for months in (12, 24):
        cutoff = snap.replace(year=snap.year - months // 12)
        win[f"contracts_signed_ge{months}m_before_snapshot"] = int(k5["_sign"].map(lambda d: bool(d and d <= cutoff)).sum())
        win[f"tender_notices_ge{months}m_before_snapshot"] = int(tenders_nb["Publication Date"].map(parse_date).map(lambda d: bool(d and d <= cutoff)).sum())
    res["observation_windows"] = win
    metric("windows", "contracts_with_ge24m_observation", win["contracts_signed_ge24m_before_snapshot"], len(k5),
           f"Contracts signed >=24 months before the DS00005 snapshot ({snap}); later contracts are right-censored for amendments", "DS00005")
    metric("windows", "tender_notices_with_ge12m_observation", win["tender_notices_ge12m_before_snapshot"], len(tenders_nb),
           "Tender notices published >=12 months before the snapshot", "DS00979")

    # ================= 4. documents =================
    dv = D.docs_cluster("v3")
    dt = D.docs_by_type("v3")
    with_meta: collections.Counter[str] = collections.Counter()
    partial_meta: collections.Counter[str] = collections.Counter()
    types: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for d in dv:
        for p in split_project_ids(d.get("projectid")):
            if p in ids:
                (with_meta if p in cluster else partial_meta)[p] += 1
                if p in cluster:
                    types[str(d.get("docty"))][p] += 1
    docs = {
        "local_v3_metadata_records_total": len(dv) + len(dt),
        "local_v3_metadata_records_cluster": len(dv), "local_v3_metadata_records_by_type_sample": len(dt),
        "local_v2_metadata_records_total": len(D.docs_cluster("v2")) + len(D.docs_by_type("v2")),
        "cohort_projects_complete_metadata": len(with_meta), "cohort_docs_complete_metadata": sum(with_meta.values()),
        "cohort_projects_partial_metadata (co-tagged only)": len(partial_meta), "cohort_docs_partial_metadata": sum(partial_meta.values()),
        "cohort_doc_types_complete_projects": {t: {"docs": sum(v.values()), "projects": len(v)} for t, v in
                                               sorted(types.items(), key=lambda kv: -sum(kv[1].values()))[:30]},
    }
    pdfs = list((SETTINGS.samples_dir / "documents" / "files").glob("*.pdf")) + list((SETTINGS.samples_dir / "documents" / "pp_files").glob("*.pdf"))
    txts = list((SETTINGS.samples_dir / "documents" / "files").glob("*.txt")) + list((SETTINGS.samples_dir / "documents" / "isr_series").glob("*.txt"))
    pdf_c = [p for p in pdfs if set(split_project_ids(p.stem.split("_")[0].replace(",", " "))) & ids]
    txt_c = [p for p in txts if set(split_project_ids(p.stem.split("_")[0].replace(",", " "))) & ids]
    docs.update({"local_pdfs_total": len(pdfs), "local_pdfs_cohort": len(pdf_c),
                 "local_pdf_cohort_projects": len({x for p in pdf_c for x in split_project_ids(p.stem.split("_")[0].replace(",", " ")) if x in ids}),
                 "local_pdf_cohort_types": dict(collections.Counter(p.stem.split("_")[-2] for p in pdf_c)),
                 "local_text_total": len(txts), "local_text_cohort": len(txt_c),
                 "local_text_cohort_projects": len({x for p in txt_c for x in split_project_ids(p.stem.split("_")[0].replace(",", " ")) if x in ids})})
    # keyword evidence in local text (all files; cohort flagged). Text documents describe events in prose:
    # useful for showing what documents contain, not for contract-level labels.
    kw = {"contract_amendment": CONTRACT_AMENDMENT, "rebid": REBID_STRONG, "cancelled_tender": NOTICE_CANCELLED}
    text_hits = []
    for fp in txts:
        t = re.sub(r"\s+", " ", fp.read_text(errors="ignore"))
        pid = split_project_ids(fp.stem.split("_")[0].replace(",", " "))
        for k, pat in kw.items():
            ms = list(pat.finditer(t))
            if ms:
                text_hits.append({"file": fp.name, "doc_type": fp.stem.split("_")[-2], "cohort": bool(set(pid) & ids), "category": k,
                                  "hits": len(ms), "example": passage(t, ms[0], 140)})
    docs["local_text_keyword_hits"] = text_hits
    res["documents"] = docs
    metric("documents", "local_v3_metadata_records", docs["local_v3_metadata_records_total"], None, "WDS v3 metadata records on disk (cluster + by-type sample)", "samples/documents")
    metric("documents", "cohort_projects_with_complete_doc_metadata", len(with_meta), len(ids), "Cohort projects whose full document list was retrieved in Phase 1", "WDS v3 cluster metadata")
    metric("documents", "cohort_docs_complete_metadata", sum(with_meta.values()), None, "Document records for those projects", "WDS v3 cluster metadata")
    metric("documents", "cohort_projects_partial_doc_metadata", len(partial_meta), len(ids), "Cohort projects appearing only as co-tagged projects (partial lists)", "WDS v3 cluster metadata")
    metric("documents", "local_pdfs_cohort", len(pdf_c), len(pdfs), "Downloaded PDFs belonging to cohort projects", "samples/documents")
    metric("documents", "local_pdf_cohort_projects", docs["local_pdf_cohort_projects"], len(ids), "Cohort projects with >=1 downloaded PDF", "samples/documents")
    metric("documents", "local_text_cohort", len(txt_c), len(txts), "Text files (extracted or WDS txt) belonging to cohort projects", "samples/documents")
    for doc_type in ("Procurement Plan", "Implementation Status and Results Report", "Implementation Completion and Results Report",
                     "Implementation Completion Report Review", "Project Paper", "Aide Memoire"):
        v: collections.Counter[str] = types.get(doc_type, collections.Counter())
        metric("documents", f"docs_{doc_type.lower().replace(' ', '_')}", sum(v.values()), None,
               f"{doc_type} documents listed for the {len(with_meta)} cohort projects with complete metadata ({len(v)} projects)", "WDS v3 cluster metadata")

    SETTINGS.profiling_dir.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=1, default=str))
    with OUT_EVID.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(evid[0]))
        w.writeheader()
        w.writerows(evid)
    with OUT_SUMMARY.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)
    print(json.dumps({k: v for k, v in res.items() if k != "structured_inventory"}, indent=1, default=str)[:12000])
    print("evidence candidates:", len(evid), collections.Counter(e["evidence_type"] for e in evid))


if __name__ == "__main__":
    main()
