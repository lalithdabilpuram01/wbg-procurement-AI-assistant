"""Assessment-alignment gap probes (read-only), added 2026-10-21 after the technical assessment
(`procurement-intelligence/assessment.md`) became available.

Each probe targets a requirement that Phase 1 did not cover or covered only partly. Offline
probes reuse cached samples. Online probes are small (< 200 requests in total) and logged in
evidence/access_log.jsonl.

  G1  Projects: development objective, E&S risk classification, co-financing (assessment §3.1)
  G2  Conflicting evidence: ISR latest ratings vs ICR vs IEG outcome (Module 3)
  G3  Long-form documents: page-count distribution by type (Module 1, "100+ pages")
  G4  Semi-structured content in notices: ToR / qualification / evaluation-criteria signals,
      rejection reasons in award notices (§2 Tender Design, §3.2)
  G5  Sector taxonomy alignment across sources (Module 1 linking "via sector codes")
  G6  Other named repositories: CPF, SCD, IEG thematic evaluations, Aide Memoires, audits (WDS v3);
      Open Knowledge Repository API; procurement guidance pages; debarment list page (§3.3)
  G7  ISR rating history extraction from ISR text across a project's ISR series (Module 3/4)

Output: profiling/assessment_gap_probes.json
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
from wbdisc.config import SETTINGS  # noqa: E402
from wbdisc.http import FetchError, download, fetch, fetch_json  # noqa: E402
from wbdisc.isr import parse_isr_text  # noqa: E402
from wbdisc.normalize import clean_label, parse_date, strip_html  # noqa: E402
from wbdisc.raw_store import record_file, write_jsonl  # noqa: E402
from wbdisc.sources import search_all, search_facets  # noqa: E402

NEG6 = {"Moderately Unsatisfactory", "Unsatisfactory", "Highly Unsatisfactory"}
POS6 = {"Moderately Satisfactory", "Satisfactory", "Highly Satisfactory"}
ISR_DIR = SETTINGS.samples_dir / "documents" / "isr_series"


def pct(a, b):
    return round(100 * a / b, 2) if b else None


def side(v):
    return "neg" if v in NEG6 else "pos" if v in POS6 else None


def g1_projects() -> dict:
    cl = D.projects_v3_cluster()
    pop = D.projects()
    esrc = collections.Counter(r.get("esrc_ovrl_risk_rate") for r in cl)
    ft = collections.Counter(f.get("projectfinancialtype") for p in pop for f in (p.get("financers") or []))
    with_cofin = sum(1 for p in pop if any(f.get("projectfinancialtype") in {"Multilateral Agency", "Bilateral Agency",
                                                                           "Trust Funds", "Private Organization",
                                                                           "Export Credit"} for f in (p.get("financers") or [])))
    return {
        "pdo_populated_pct_population": pct(sum(1 for p in pop if p.get("pdo")), len(pop)),
        "pdo_populated_pct_cluster": pct(sum(1 for p in cl if p.get("pdo")), len(cl)),
        "env_assessment_category_distribution_population": dict(collections.Counter(p.get("envassesmentcategorycode") for p in pop).most_common()),
        "esf_overall_risk_rating_cluster": dict(esrc.most_common()),
        "esf_overall_risk_rating_cluster_populated_pct": pct(sum(v for k, v in esrc.items() if k), len(cl)),
        "esf_note": "esrc_ovrl_risk_rate (ESF-era) exists in v3 fl=* but was not in the Phase 1 population field list",
        "financer_type_counts_population": dict(ft.most_common()),
        "projects_with_cofinancing_pct": pct(with_cofin, len(pop)),
        "projects_with_financers_pct": pct(sum(1 for p in pop if p.get("financers")), len(pop)),
    }


def g2_conflicts() -> dict:
    pop = {p["id"]: p for p in D.projects()}
    ieg = D.ieg().set_index("Project ID")
    rows = []
    for pid, p in pop.items():
        isr = {x.get("name"): x.get("value") for x in (p.get("isr_ratings") or [])}
        isr_date = next((x.get("isr_date") for x in (p.get("isr_ratings") or [])), None)
        icr = (p.get("icr_ratings") or [{}])[0]
        ieg_v3 = (p.get("ieg_ratings") or [{}])[0]
        out = ieg.loc[pid, "Outcome"] if pid in ieg.index else None
        if isinstance(out, pd.Series):
            out = out.iloc[0]
        rows.append({"project_id": pid, "status": p.get("status"), "isr_pdo": isr.get("Progress towards achievement of PDO"),
                     "isr_ip": isr.get("Overall Implementation Progress (IP)"), "isr_proc": isr.get("Procurement"),
                     "isr_date": isr_date, "icr_outratingind": icr.get("outratingind"), "icr_overallrating": icr.get("overallrating"),
                     "ieg_v3_outcome": ieg_v3.get("outcome"), "ieg_ds00053_outcome": out})
    df = pd.DataFrame(rows)
    both = df[df["isr_pdo"].map(side).notna() & df["ieg_ds00053_outcome"].map(side).notna()]
    agree = (both["isr_pdo"].map(side) == both["ieg_ds00053_outcome"].map(side))
    cross = pd.crosstab(both["isr_pdo"].map(side), both["ieg_ds00053_outcome"].map(side))
    v3_vs_ds = df[df["ieg_v3_outcome"].notna() & df["ieg_ds00053_outcome"].notna()]
    proc_vs_ieg = df[df["isr_proc"].map(side).notna() & df["ieg_ds00053_outcome"].map(side).notna()]
    return {
        "projects_with_latest_isr_pdo_and_ieg_outcome": len(both),
        "isr_pdo_vs_ieg_outcome_side_agreement_pct": pct(int(agree.sum()), len(both)),
        "isr_pdo_vs_ieg_outcome_crosstab": {f"isr_{i}|ieg_{j}": int(cross.loc[i, j]) for i in cross.index for j in cross.columns},
        "isr_positive_but_ieg_negative": int(((both["isr_pdo"].map(side) == "pos") & (both["ieg_ds00053_outcome"].map(side) == "neg")).sum()),
        "isr_negative_but_ieg_positive": int(((both["isr_pdo"].map(side) == "neg") & (both["ieg_ds00053_outcome"].map(side) == "pos")).sum()),
        "isr_procurement_vs_ieg_outcome_n": len(proc_vs_ieg),
        "isr_procurement_vs_ieg_outcome_side_agreement_pct": pct(int((proc_vs_ieg["isr_proc"].map(side) == proc_vs_ieg["ieg_ds00053_outcome"].map(side)).sum()), len(proc_vs_ieg)),
        "ieg_v3_vs_ds00053_outcome_n": len(v3_vs_ds),
        "ieg_v3_vs_ds00053_outcome_exact_agreement_pct": pct(int((v3_vs_ds["ieg_v3_outcome"].str.lower() == v3_vs_ds["ieg_ds00053_outcome"].str.lower()).sum()), len(v3_vs_ds)),
        "icr_outratingind_values": dict(collections.Counter(df["icr_outratingind"].dropna()).most_common(10)),
        "icr_overallrating_values": dict(collections.Counter(df["icr_overallrating"].dropna()).most_common(10)),
        "caveat": "Latest ISR only (one isr_date per project); the ISR may precede or follow the evaluation by years. "
                  "Agreement is indicative of conflict frequency, not a validated conflict rate.",
    }


def g3_pages() -> dict:
    df = pd.DataFrame(D.docs_by_type("v2"))
    df["dt"] = df["_extract"].map(lambda e: e["docty_exact"])
    df["pages"] = pd.to_numeric(df["no_of_pages"], errors="coerce")
    out = {}
    for dt, g in df.groupby("dt"):
        p = g["pages"].dropna()
        out[dt] = {"n": len(p), "median": float(p.median()) if len(p) else None, "p90": float(p.quantile(0.9)) if len(p) else None,
                   "max": float(p.max()) if len(p) else None, "ge100_pct": pct(int((p >= 100).sum()), len(p))}
    return {"source": "v2 wds type sample `no_of_pages` (v3 lacks the field)", "by_type": out}


TOR = re.compile(r"terms of reference|termes de r[ée]f[ée]rence|t[ée]rminos de referencia|\bTdR\b|\bToR\b", re.I)
QUAL = re.compile(r"qualification|eligib|experience|exp[ée]rience|experiencia|criteria|crit[èe]re|criterio|shortlist", re.I)
SPEC = re.compile(r"specification|sp[ée]cification|especificaci|technical requirement", re.I)
EVAL = re.compile(r"evaluation criteria|evaluated|crit[èe]res d.?[ée]valuation|criterios de evaluaci|selection method", re.I)
REASON = re.compile(r"(Reason for Rejection|Non Responsive|Does not meet[^\n]{0,60}|Failure to meet[^\n]{0,60}|Not substantially responsive)", re.I)


def g4_notice_content() -> dict:
    n = pd.DataFrame(D.notices_cluster())
    n["text"] = n["notice_text"].map(strip_html)
    n["len"] = n["text"].str.len()
    out = {}
    for t in ("Request for Expression of Interest", "Invitation for Bids", "Invitation for Prequalification", "General Procurement Notice"):
        g = n[n["notice_type"] == t]
        out[t] = {"n": len(g), "median_chars": float(g["len"].median()), "p90_chars": float(g["len"].quantile(0.9)),
                  "mentions_tor_pct": pct(int(g["text"].str.contains(TOR).sum()), len(g)),
                  "mentions_qualification_or_experience_pct": pct(int(g["text"].str.contains(QUAL).sum()), len(g)),
                  "mentions_specification_pct": pct(int(g["text"].str.contains(SPEC).sum()), len(g)),
                  "mentions_evaluation_criteria_pct": pct(int(g["text"].str.contains(EVAL).sum()), len(g))}
    award = n[n["notice_type"] == "Contract Award"]
    reasons: collections.Counter[str] = collections.Counter()
    for t in award["text"]:
        for m in REASON.findall(t):
            reasons[clean_label(m)[:60]] += 1
    out["award_rejection_reason_phrases_top"] = dict(reasons.most_common(12))
    out["award_notices_with_rejection_reason_pct"] = pct(int(award["text"].str.contains(REASON).sum()), len(award))
    return out


def g5_sectors() -> dict:
    ca = D.contracts()
    gp_ds5 = collections.Counter(x.strip() for v in ca["Project Global Practice"] for x in v.split(";") if x.strip())
    gp_ieg = collections.Counter(v for v in D.ieg()["Global Practice"] if v)
    pop = D.projects()
    # v3 `major_sectors` is structured (code + name + nested sectors with %); `major_sector_name` is a
    # comma-joined string whose items themselves contain commas, so it is not split here.
    codes, names, populated = collections.Counter(), collections.Counter(), 0
    for p in pop:
        ms = p.get("major_sectors") or []
        populated += bool(ms)
        for m in ms:
            mj = m.get("major_sector") or {}
            codes[mj.get("major_sector_code")] += 1
            names[mj.get("major_sector_name")] += 1
    raw_names = [p.get("major_sector_name") for p in pop if p.get("major_sector_name")]
    wds = collections.Counter(v.get("sector") for d in D.docs_cluster() for v in (d.get("sectr") or {}).values()
                              if isinstance(v, dict))
    return {"ds00005_gp_atomic_labels": len(gp_ds5), "ds00005_gp_examples": [k for k, _ in gp_ds5.most_common(16)],
            "ieg_global_practice_labels": len(gp_ieg), "ieg_gp_examples": [k for k, _ in gp_ieg.most_common(18)],
            "exact_label_overlap_ds00005_vs_ieg_gp": sorted(set(gp_ds5) & set(gp_ieg)),
            "v3_major_sectors_populated_pct": pct(populated, len(pop)),
            "v3_major_sector_codes": len(codes), "v3_major_sector_names_structured": dict(names.most_common(25)),
            "v3_major_sector_name_string_multi_valued_pct": pct(sum(1 for x in raw_names if "," in x), len(raw_names)),
            "v3_major_sector_name_with_fy17_prefix_pct": pct(sum(1 for x in raw_names if "FY17 - " in x), len(raw_names)),
            "wds_v3_sectr_structured_labels_cluster": len(wds), "wds_sectr_examples": [k for k, _ in wds.most_common(10)],
            "wds_vs_v3_sector_name_overlap": len(set(wds) & set(names)),
            "note": "DS00005 uses abbreviated Global Practice labels; IEG uses current GP names; v3 projects and WDS use the "
                    "sector taxonomy (with FY17 and (Historic) versions). There is no shared code between the GP and sector "
                    "taxonomies; a curated mapping is required."}


def g6_repositories() -> dict:
    out: dict = {}
    types = ["Country Partnership Framework", "Systematic Country Diagnostic", "IEG Evaluation", "Aide Memoire",
             "Auditing Document", "Procurement Plan", "Note on Cancelled Operation", "Procedure and Checklist",
             "Financial Monitoring Report", "Environmental and Social Commitment Plan"]
    fac = search_facets("v3/wds", ["docty_exact"])["docty_exact"]
    out["wds_v3_counts"] = {t: fac.get(t) for t in types}
    cl = pd.DataFrame(D.docs_cluster())
    cl["dt"] = cl["docty"].map(clean_label)
    cl["p"] = cl["_extract"].map(lambda e: e["filter_projectid"])
    nproj = len(D.cluster_projects())
    out["cluster_project_coverage_pct"] = {t: pct(int(cl[cl["dt"] == t]["p"].nunique()), nproj) for t in types}
    # Procurement guidance discoverable in WDS?
    for q in ("Procurement Regulations for IPF Borrowers", "procurement guidance note", "Standard Procurement Document"):
        total = int(fetch_json(f"{SETTINGS.search_api_base.rsplit('/', 1)[0]}/v3/wds",
                               {"format": "json", "rows": 0, "qterm": q}, purpose="guidance search").get("total", 0))
        out[f"wds_v3_qterm::{q}"] = total
    # Open Knowledge Repository (DSpace 7 REST)
    try:
        d = fetch_json("https://openknowledge.worldbank.org/server/api/discover/search/objects",
                       {"query": "procurement lessons learned", "size": 5}, purpose="OKR probe")
        res = d["_embedded"]["searchResult"]
        objs = [o["_embedded"]["indexableObject"] for o in res["_embedded"]["objects"]]
        guids = [((o["metadata"].get("okr.guid") or [{}])[0]).get("value") for o in objs]
        wds_hits = 0
        for g in [g for g in guids if g]:
            total = int(fetch_json(f"{SETTINGS.search_api_base.rsplit('/', 1)[0]}/v3/wds",
                                   {"format": "json", "rows": 0, "guid": g}, purpose="OKR guid -> WDS").get("total", 0))
            wds_hits += total > 0
        out["okr"] = {"status": "reachable (DSpace 7 REST, JSON)", "query_total": res["page"]["totalElements"],
                      "metadata_keys_example": sorted(objs[0]["metadata"].keys()) if objs else [],
                      "okr_guid_populated": sum(1 for g in guids if g), "okr_guid_found_in_wds_v3": wds_hits,
                      "sample_titles": [o.get("name") for o in objs]}
    except (FetchError, KeyError, ValueError) as exc:
        out["okr"] = {"error": str(exc)[:200]}
    # Procurement Regulations for IPF Borrowers (published PDF; URL observed 2026-10-21)
    regs = SETTINGS.samples_dir / "documents" / "files" / "GUIDANCE_ProcurementRegulations_IPF.pdf"
    regs_url = "https://thedocs.worldbank.org/en/doc/178331533065871195-0290022020/original/ProcurementRegulations.pdf"
    try:
        if not regs.exists():
            n = download(regs_url, regs, purpose="procurement regulations pdf")
            record_file(regs, source="thedocs.worldbank.org", method="GET published PDF", records=None,
                        notes=f"url={regs_url}; bytes={n}")
        import fitz  # PyMuPDF

        doc = fitz.open(regs)
        out["procurement_regulations_pdf"] = {"status": "downloaded", "pages": doc.page_count,
                                              "title_page": " ".join(doc[0].get_text().split())[:160],
                                              "text_layer_chars_p1_10": sum(len(doc[i].get_text()) for i in range(min(10, doc.page_count)))}
    except FetchError as exc:
        out["procurement_regulations_pdf"] = {"error": str(exc)[:200]}
    for label, url in {
        "procurement_framework_page": "https://www.worldbank.org/en/projects-operations/products-and-services/brief/procurement-new-framework",
        "debarred_firms_page": "https://www.worldbank.org/en/projects-operations/procurement/debarred-firms",
    }.items():
        try:
            r = fetch(url, purpose=f"page probe {label}")
            out[label] = {"status": r.status_code, "content_type": r.headers.get("Content-Type"),
                          "note": "HTML page; listing data is loaded by client-side script (not an open dataset); not scraped"
                          if "debarred" in label else "HTML landing page linking regulations / guidance PDFs"}
        except FetchError as exc:
            out[label] = {"error": str(exc)[:200]}
    return out


def g7_isr_series() -> dict:
    """Pull the full ISR series (metadata + .txt) for 2 cluster projects and extract ratings per ISR."""
    v3 = {p["id"]: p for p in D.projects_v3_cluster()}
    cl = pd.DataFrame(D.docs_cluster())
    cl["dt"] = cl["docty"].map(clean_label)
    cl["p"] = cl["_extract"].map(lambda e: e["filter_projectid"])
    isr_counts = cl[cl["dt"] == "Implementation Status and Results Report"].groupby("p").size()
    # one long-running active project and one closed project, both with v3 isr_ratings
    cand = [p for p in isr_counts.sort_values(ascending=False).index if v3.get(p, {}).get("isr_ratings")]
    picks = []
    for status in ("Active", "Closed"):
        for p in cand:
            if v3[p].get("status") == status and p not in picks:
                picks.append(p)
                break
    series: dict[str, dict] = {}
    out = {"projects": picks, "series": series}
    meta_rows = []
    for pid in picks:
        docs = [d for d in search_all("v3/wds", {"projectid": pid, "docty_exact": "Implementation Status and Results Report"},
                                      purpose=f"isr series {pid}")]
        docs.sort(key=lambda d: d.get("docdt") or "")
        meta_rows.extend(docs)
        parsed = []
        for d in docs[-12:]:  # most recent 12 ISRs: bounded download
            url = (d.get("txturl") or "").replace("http://", "https://")
            if not url:
                continue
            dest = ISR_DIR / f"{pid}_ISR_{d['id']}.txt"
            if not dest.exists():
                try:
                    n = download(url, dest, purpose=f"isr txt {pid}")
                    record_file(dest, source="documents.worldbank.org", method="GET txturl (v3 wds ISR series)",
                                records=None, notes=f"wds_id={d['id']}; docdt={d.get('docdt')}; bytes={n}")
                except FetchError as exc:
                    parsed.append({"wds_id": d["id"], "error": str(exc)[:120]})
                    continue
            r = parse_isr_text(dest.read_text(errors="replace"))
            r.update({"wds_id": d["id"], "docdt": (d.get("docdt") or "")[:10]})
            parsed.append(r)
        latest_api = {x["name"]: x["value"] for x in v3[pid].get("isr_ratings") or []}
        newest = next((x for x in reversed(parsed) if x.get("ratings")), {})
        series[pid] = {
            "status": v3[pid].get("status"), "isr_docs_total": len(docs), "isr_parsed": len(parsed),
            "isr_lang_distribution": dict(collections.Counter(d.get("lang") for d in docs)),
            "parsed_records_per_seq_no": dict(collections.Counter(x.get("seq_no") for x in parsed)),
            "templates": dict(collections.Counter(x.get("template") for x in parsed)),
            "parsed_without_ratings": sum(1 for x in parsed if not x.get("ratings")),
            "with_seq_no": sum(1 for x in parsed if x.get("seq_no") is not None),
            "with_pdo_ip_ratings": sum(1 for x in parsed if {"Progress towards achievement of PDO", "Overall Implementation Progress (IP)"} <= set(x.get("ratings", {}))),
            "with_procurement_component_rating": sum(1 for x in parsed if "Procurement" in x.get("ratings", {})),
            "with_procurement_risk_rating": sum(1 for x in parsed if any("fiduciary" in k.lower() or "procurement" in k.lower() for k in x.get("risk_ratings", {}))),
            "rating_names_seen": sorted({k for x in parsed for k in x.get("ratings", {})}),
            "timeline": [{k: x.get(k) for k in ("docdt", "seq_no", "archived_on")} | {"pdo": x.get("ratings", {}).get("Progress towards achievement of PDO"),
                         "ip": x.get("ratings", {}).get("Overall Implementation Progress (IP)"),
                         "procurement": x.get("ratings", {}).get("Procurement")} for x in parsed],
            "api_latest_isr_ratings": latest_api,
            "newest_parsed_matches_api_on_shared_names": {k: newest.get("ratings", {}).get(k) == v for k, v in latest_api.items()
                                                           if k in newest.get("ratings", {})},
        }
    write_jsonl(meta_rows, ISR_DIR / "isr_series_meta.jsonl.gz", source="search.worldbank.org/api/v3/wds",
                method=f"projectid=<P>, docty_exact=ISR for {picks}")
    return out


def main() -> None:
    results = {}
    for name, fn in (("G1_projects", g1_projects), ("G2_conflicts", g2_conflicts), ("G3_pages", g3_pages),
                     ("G4_notice_content", g4_notice_content), ("G5_sectors", g5_sectors),
                     ("G6_repositories", g6_repositories), ("G7_isr_series", g7_isr_series)):
        print("running", name)
        results[name] = fn()
    (SETTINGS.profiling_dir / "assessment_gap_probes.json").write_text(json.dumps(results, indent=1, default=str))
    print(json.dumps(results, indent=1, default=str)[:12000])


if __name__ == "__main__":
    main()
