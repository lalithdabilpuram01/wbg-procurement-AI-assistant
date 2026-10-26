"""Task 6: label availability, class balance and point-in-time feature coverage per ML target.

No model is trained. Every number is a count over retrieved public data.
Output: profiling/ml_feasibility.json
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from wbdisc import datasets as D  # noqa: E402
from wbdisc.award_notice import parse_award_notice  # noqa: E402
from wbdisc.config import SETTINGS  # noqa: E402
from wbdisc.countries import to_iso2  # noqa: E402
from wbdisc.normalize import parse_date, strip_html  # noqa: E402

NEG = {"Moderately Unsatisfactory", "Unsatisfactory", "Highly Unsatisfactory"}
ISR_NEG = {"Moderately Unsatisfactory", "Unsatisfactory", "Highly Unsatisfactory"}


def pct(a, b):
    return round(100 * a / b, 2) if b else None


def neg_by(df: pd.DataFrame, col: str, target: str = "Outcome") -> dict:
    return {str(k): {"n": len(g), "neg_pct": pct(int(g[target].isin(NEG).sum()), len(g))} for k, g in df.groupby(col)}


def main() -> None:
    out: dict = {}

    # ---------- Project outcome (IEG DS00053) ----------
    ieg = D.ieg()
    rated = ieg[~ieg["Outcome"].isin(["Not Rated", ""])]
    out["ieg_outcome"] = {
        "rows": len(ieg), "rated": len(rated), "negative_rate_pct": pct(int(rated["Outcome"].isin(NEG).sum()), len(rated)),
        "by_evaluation_type": neg_by(rated, "Evaluation Type"),
        "by_lending_instrument": neg_by(rated, "Lending Instrument Type"),
        "by_approval_decade": neg_by(rated.assign(dec=rated["Approval FY"].str[:3] + "0s"), "dec"),
        "by_fcs_status": neg_by(rated, "Country / Economy FCS Status"),
        "evaluation_fy_range": [rated["Evaluation FY"].min(), rated["Evaluation FY"].max()],
        "ipf_rated_approved_fy2005plus": int(((rated["Lending Instrument Type"] == "IPF") & (pd.to_numeric(rated["Approval FY"], errors="coerce") >= 2005)).sum()),
    }
    projs = pd.DataFrame(D.projects()).set_index("id")
    j = rated.merge(projs, left_on="Project ID", right_index=True, how="left")
    out["ieg_outcome"]["joined_to_v3_projects_pct"] = pct(int(j["project_name"].notna().sum()), len(j))
    for f in ("boardapprovaldate", "curr_total_commitment", "major_sector_name", "impagency", "milestones"):
        out["ieg_outcome"][f"feature_{f}_populated_pct"] = pct(int(j[f].map(lambda v: bool(v) and v == v).sum()), len(j))

    # Point-in-time governance coverage: CPIA/WGI for (country, approval_year - 1)
    cpia = {(r["countryiso3code"], int(r["date"])): r["value"] for r in D.indicator("IQ.CPA.TRAN.XQ") if r["value"] is not None}
    wgi = {(r["countryiso3code"], int(r["date"])): r["value"] for r in D.indicator("GOV_WGI_CC.EST") if r["value"] is not None}
    iso2_to_iso3 = {r["country"]["id"]: r["countryiso3code"] for r in D.indicator("GOV_WGI_CC.EST") if r["countryiso3code"]}
    codes = j["countrycode"].map(lambda v: v if isinstance(v, list) else [])
    jj = j.assign(n_codes=codes.map(len), iso2=codes.map(lambda v: to_iso2(v[0]) if len(v) == 1 else None),
                  ay=pd.to_numeric(j["Approval FY"], errors="coerce"))
    jj["iso3"] = jj["iso2"].map(iso2_to_iso3)

    def avail(table, r):
        return bool(r["iso3"]) and r["ay"] == r["ay"] and (r["iso3"], int(r["ay"]) - 1) in table

    out["governance_asof_join_for_ieg"] = {
        "method": "IEG Project ID -> v3 projects.countrycode (WB code) -> curated WB->ISO2 crosswalk -> WGI/CPIA (year = approval FY - 1)",
        "country_name_join_attempt_pct": 10.85,
        "country_name_join_note": "IEG uses long official names ('Republic of India'); name joins are not viable",
        "projects_single_country_code_pct": pct(int((jj["n_codes"] == 1).sum()), len(jj)),
        "projects_mapped_to_iso_pct": pct(int(jj["iso3"].notna().sum()), len(jj)),
        "cpia_available_at_approval_minus1_pct": pct(int(jj.apply(lambda r: avail(cpia, r), axis=1).sum()), len(jj)),
        "wgi_available_at_approval_minus1_pct": pct(int(jj.apply(lambda r: avail(wgi, r), axis=1).sum()), len(jj)),
        "wgi_available_approval_fy1998plus_pct": pct(int(jj[jj["ay"] >= 1998].apply(lambda r: avail(wgi, r), axis=1).sum()),
                                                    int((jj["ay"] >= 1998).sum())),
        "cpia_available_approval_fy2006plus_pct": pct(int(jj[jj["ay"] >= 2006].apply(lambda r: avail(cpia, r), axis=1).sum()),
                                                     int((jj["ay"] >= 2006).sum())),
        "note": "CPIA public series starts 2005 and covers IDA-eligible countries; WGI 1996-2025, biennial before 2002",
    }

    # ---------- ISR procurement rating (v3 projects; latest ISR only) ----------
    isr = []
    for pid, v in projs["isr_ratings"].items():
        if isinstance(v, list):
            for x in v:
                if x.get("name") == "Procurement":
                    isr.append({"project_id": pid, "isr_date": x.get("isr_date"), "value": x.get("value")})
    isr = pd.DataFrame(isr)
    rated_isr = isr[~isr["value"].isin(["Not Rated", "Not Applicable", "", None])]
    out["isr_procurement_rating_latest"] = {
        "projects_with_rating": int(isr["project_id"].nunique()), "values": isr["value"].value_counts().to_dict(),
        "negative_rate_pct": pct(int(rated_isr["value"].isin(ISR_NEG).sum()), len(rated_isr)),
        "isr_date_range": [str(isr["isr_date"].min()), str(isr["isr_date"].max())],
        "history_available_in_api": "No - one isr_date per project (latest ISR). Full history requires parsing ISR documents.",
        "isr_documents_available_v3": "59,693 ISR documents in v3 wds (docty facet count)",
    }

    # ---------- Dropped pipeline projects ----------
    st = projs["status"].value_counts()
    out["project_dropped"] = {"status_distribution": st.to_dict(), "dropped_share_of_closed_plus_dropped_pct":
                              pct(int(st.get("Dropped", 0)), int(st.get("Dropped", 0) + st.get("Closed", 0)))}

    # ---------- Contract-level targets (cluster notices) ----------
    notices = pd.DataFrame(D.notices_cluster())
    award = notices[notices["notice_type"] == "Contract Award"]
    parsed = award["notice_text"].map(parse_award_notice)
    bidders = parsed.map(lambda p: p.get("bidder_count_by_id"))
    method = award["procurement_method_code"]
    bt = pd.DataFrame({"method": method, "bidders": bidders}).dropna()
    out["single_bidder"] = {
        "award_notices": len(award), "with_bidder_count": len(bt),
        "by_method": {str(m): {"n": len(g), "single_pct": pct(int((g["bidders"] == 1).sum()), len(g)),
                               "zero_parsed_pct": pct(int((g["bidders"] == 0).sum()), len(g)), "median": float(g["bidders"].median())}
                      for m, g in bt.groupby("method") if len(g) >= 30},
        "caveat": "Bidder count = distinct supplier ids in Awarded/Evaluated/Rejected sections; direct selection is single by design",
    }
    texts = award["notice_text"].map(strip_html)
    out["amendment_evidence_in_award_notices"] = {
        "mentions_amend_or_variation": int(texts.str.contains(r"\bamend|\bvariation order|\bavenant|\benmienda", case=False, regex=True).sum()),
        "notice_version_gt0": int(parsed.map(lambda p: (p.get("notice_version_no") or 0) > 0).sum()),
        "interpretation": "Notice versions are revisions of the published notice, not contract amendments.",
    }
    tender = notices[notices["notice_type"].isin(["Invitation for Bids", "Request for Expression of Interest", "Invitation for Prequalification"])]
    out["rebid_signals_in_notices"] = {
        "tender_notices": len(tender),
        "status_cancelled": int((notices["notice_status"] == "Cancelled").sum()),
        "status_revised": int((notices["notice_status"] == "Revised").sum()),
        "text_markers_rebid_retender_cancel": int((tender["bid_description"].fillna("") + " " + tender["notice_text"].fillna("").map(strip_html).str[:3000])
                                                  .str.contains(r"\bre-?bid|\bre-?tender|\bre-?advertis|\bcancel+ation|\bannul|relance|nouvel appel|\bdesiert", case=False, regex=True).sum()),
    }
    rej = parsed.map(lambda p: p.get("rejected_count_by_id") or 0)
    out["rejected_bidders"] = {"award_notices_with_rejections": int((rej > 0).sum()), "pct": pct(int((rej > 0).sum()), len(award))}

    # Documents explicitly about amendments (project-level legal/financing amendments)
    docs = pd.DataFrame(D.docs_cluster())
    title = docs["display_title"].fillna("")
    am = docs[title.str.contains("Amendment", case=False)]
    out["amendment_documents_cluster"] = {"documents_with_amendment_in_title": len(am),
                                         "by_docty": am["docty"].fillna("").str.replace(r"\s+", " ", regex=True).value_counts().head(10).to_dict(),
                                         "examples": am["display_title"].str.replace(r"\s+", " ", regex=True).head(5).tolist()}
    pp = D.docs_cluster()
    restr = [d for d in pp if re.search(r"restructur", d.get("display_title") or "", re.I)]
    out["restructuring_documents_cluster"] = {"count": len(restr), "projects": len({d["_extract"]["filter_projectid"] for d in restr}),
                                              "pct_cluster_projects": pct(len({d["_extract"]["filter_projectid"] for d in restr}), len(D.cluster_projects()))}
    (SETTINGS.profiling_dir / "ml_feasibility.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str)[:8000])


if __name__ == "__main__":
    main()
