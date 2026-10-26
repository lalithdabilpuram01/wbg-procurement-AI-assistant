"""Task 8: empirical probes of change-tracking / incremental-retrieval capabilities.

Every probe records the exact request and the observed outcome; nothing here is inferred
from documentation alone. Output: profiling/refresh_probe.json
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wbdisc import datasets as D  # noqa: E402
from wbdisc.config import SETTINGS  # noqa: E402
from wbdisc.http import FetchError, fetch, fetch_json  # noqa: E402
from wbdisc.normalize import parse_date  # noqa: E402
from wbdisc.sources import fone_page, search_page  # noqa: E402


def try_(fn):
    try:
        return fn()
    except (FetchError, ValueError, KeyError) as exc:
        return {"error": str(exc)[:300]}


def is_sorted(vals, reverse=False):
    v = [x for x in vals if x is not None]
    return v == sorted(v, reverse=reverse), len(v)


def main() -> None:
    out: dict = {}
    # ---- Finances One DS00005 ----
    # Each filter is probed independently so one rejection does not hide the others.
    out["fone_ds00005_filters"] = {}
    for f in ("fiscal_year=2027", "as_of_date='20-Oct-2026'", "fiscal_year=2027 and procurement_category='Goods'",
              "fiscal_year>2026", "contract_signing_date>='2026-10-01'", "contract_signing_date>='01-Oct-2026'",
              "contract_signing_date like '%Oct-2026'"):
        def probe(f=f):
            # NB: filter combined with a multi-column `select` is rejected by the WAF (HTTP 403); filter alone works.
            total, recs = fone_page("DS00005", "RS00005", top=5, filter_=f,
                                    purpose="refresh probe")
            return {"count": total, "sample_dates": [r.get("contract_signing_date") for r in recs]}
        out["fone_ds00005_filters"][f] = try_(probe)
    out["fone_ds00005_as_of_date_values_in_api_sample"] = dict(collections.Counter(r.get("as_of_date") for r in D.contracts_api_sample()))

    def fone_order():
        _, a = fone_page("DS00005", "RS00005", top=50, skip=0, select="wb_contract_number,contract_signing_date")
        _, b = fone_page("DS00005", "RS00005", top=50, skip=0, select="wb_contract_number,contract_signing_date")
        return {"same_first_page_twice": [r["wb_contract_number"] for r in a] == [r["wb_contract_number"] for r in b],
                "first_page_signing_dates_desc_sorted": is_sorted([parse_date(r["contract_signing_date"]) for r in a], True)[0]}
    out["fone_ds00005_paging_stability"] = try_(fone_order)

    def waf_probe():
        res = {}
        for label, kw in {"filter+multi-select": {"filter_": "fiscal_year=2027", "select": "contract_signing_date,fiscal_year"},
                          "filter+single-select": {"filter_": "fiscal_year=2027", "select": "fiscal_year"},
                          "filter only": {"filter_": "fiscal_year=2027"}}.items():
            res[label] = try_(lambda kw=kw: {"count": fone_page("DS00005", "RS00005", top=1, **kw)[0]})
        return res
    out["fone_ds00005_filter_select_combination"] = waf_probe()

    # ---- procnotices ----
    out["procnotices_sort_and_filter"] = {}
    for label, params in {"default": {}, "srt=submission_date desc": {"srt": "submission_date", "order": "desc"},
                          "srt=noticedate desc": {"srt": "noticedate", "order": "desc"}}.items():
        def sort_probe(params=params):
            total, recs = search_page("procnotices", {"rows": 30, "fl": "id,noticedate,submission_date", **params})
            sd = [parse_date(r.get("submission_date")) for r in recs]
            nd = [parse_date(r.get("noticedate")) for r in recs]
            return {"total": total, "submission_date_desc_sorted": is_sorted(sd, True), "noticedate_desc_sorted": is_sorted(nd, True),
                    "first": recs[0] if recs else None}
        out["procnotices_sort_and_filter"][label] = try_(sort_probe)
    for label, params in {"strdate/enddate": {"strdate": "2021-01-01", "enddate": "2021-01-31"},
                          "noticedate=19-Oct-2026": {"noticedate": "19-Oct-2026"},
                          "submission_date=2026-10-19T00:00:00Z": {"submission_date": "2026-10-19T00:00:00Z"}}.items():
        def filt_probe(params=params):
            total, _ = search_page("procnotices", {"rows": 0, **params})
            return {"total": total, "note": "total unchanged from unfiltered => parameter ignored"}
        out["procnotices_sort_and_filter"][f"filter {label}"] = try_(filt_probe)
    nc = D.notices_cluster()
    out["procnotices_fields_with_update_semantics"] = sorted({k for r in nc for k in r if any(t in k for t in ("upd", "modif", "version"))})
    out["procnotices_status_values"] = dict(collections.Counter(r.get("notice_status") for r in nc))

    # ---- WDS ----
    def wds_probe():
        res = {}
        for label, params in {"srt=last_modified_date desc": {"srt": "last_modified_date", "order": "desc"},
                              "srt=docdt desc": {"srt": "docdt", "order": "desc"}}.items():
            total, recs = search_page("wds", {"rows": 30, "fl": "id,docdt,last_modified_date,datestored,disclosure_date", **params})
            lm = [parse_date(r.get("last_modified_date")) for r in recs]
            res.setdefault("records_missing_last_modified_in_top30", {})[label] = sum(1 for r in recs if not r.get("last_modified_date"))
            dd = [parse_date(r.get("docdt")) for r in recs]
            res[label] = {"total": total, "last_modified_desc_sorted": is_sorted(lm, True),
                          "docdt_desc_sorted": is_sorted(dd, True), "first3": recs[:3]}
        total, _ = search_page("wds", {"rows": 0, "strdate": "2021-01-01", "enddate": "2021-01-31"})
        res["strdate/enddate (docdt window Jan-2021)"] = {"total": total}
        total, recs = search_page("wds", {"rows": 5, "fl": "id,docdt", "srt": "docdt", "order": "desc"})
        res["future_docdt_examples"] = [r for r in recs if (parse_date(r.get("docdt")) or parse_date("2000-01-01")) > parse_date("2026-10-20")]
        return res
    out["wds_sort_and_filter"] = try_(wds_probe)
    docs = D.docs_cluster()
    out["wds_versiontyp_values"] = dict(collections.Counter((d.get("versiontyp") or "").strip() for d in docs).most_common(15))
    guid_counts = collections.Counter(d.get("guid") for d in docs if d.get("guid"))
    docm = collections.Counter(d.get("chronical_docm_id") for d in docs if d.get("chronical_docm_id"))
    out["wds_identity"] = {"docs": len(docs), "distinct_id": len({d['id'] for d in docs}),
                           "guid_reused": sum(1 for c in guid_counts.values() if c > 1),
                           "chronical_docm_id_reused": sum(1 for c in docm.values() if c > 1),
                           "pdfurl_scheme": dict(collections.Counter((d.get("pdfurl") or "none").split(":")[0] for d in docs)),
                           "last_modified_populated_pct": round(100 * sum(1 for d in docs if d.get("last_modified_date")) / max(1, len(docs)), 2)}

    def url_probe():
        res = []
        for d in [x for x in docs if x.get("pdfurl")][:3]:
            r = fetch(d["pdfurl"], stream=True, purpose="url redirect probe")
            res.append({"requested": d["pdfurl"], "final": r.url, "redirects": [h.status_code for h in r.history],
                        "status": r.status_code, "content_type": r.headers.get("Content-Type"),
                        "last_modified_header": r.headers.get("Last-Modified"), "etag": r.headers.get("ETag")})
            r.close()
        return res
    out["document_url_behaviour"] = try_(url_probe)

    # ---- Projects ----
    def proj_probe():
        res = {}
        for f in ("proj_last_upd_date", "p2a_updated_date"):
            total, recs = search_page("projects", {"rows": 30, "fl": f"id,{f}", "srt": f, "order": "desc"})
            vals = [parse_date(r.get(f)) for r in recs]
            res[f"srt={f} desc"] = {"sorted": is_sorted(vals, True), "first3": recs[:3]}
        return res
    out["projects_sort"] = try_(proj_probe)
    pr = D.projects()
    out["projects_update_field_population_pct"] = {f: round(100 * sum(1 for r in pr if r.get(f)) / len(pr), 2)
                                                  for f in ("proj_last_upd_date", "p2a_updated_date")}

    # ---- Indicators ----
    def ind_probe():
        src = fetch_json(f"{SETTINGS.indicators_api_base}/sources", {"format": "json", "per_page": 100})
        return {s["name"]: s.get("lastupdated") for s in src[1] if s["id"] in ("2", "3", "31")}
    out["indicator_source_lastupdated"] = try_(ind_probe)

    # ---- Endpoint existence probes (reproduces the access-failure log in DATA_SOURCE_INVENTORY.md) ----
    out["endpoint_existence"] = {}
    for label, url in {
        "search v2 contractdata (guessed)": "https://search.worldbank.org/api/v2/contractdata?format=json&rows=1",
        "search v3 procnotices": "https://search.worldbank.org/api/v3/procnotices?format=json&rows=0",
        "search v3 projects": "https://search.worldbank.org/api/v3/projects?format=json&rows=0",
        "search v3 wds": "https://search.worldbank.org/api/v3/wds?format=json&rows=0",
        "legacy Socrata kdui-wcs3": "https://finances.worldbank.org/resource/kdui-wcs3.json?$limit=1",
        "DDH DatasetView 0037795": "https://datacatalogapi.worldbank.org/ddhxext/DatasetView?dataset_unique_id=0037795",
        "WGI legacy code GE.EST": "https://api.worldbank.org/v2/country/all/indicator/GE.EST?source=3&format=json&per_page=1",
        "procnotices os=100001": "https://search.worldbank.org/api/v2/procnotices?format=json&rows=1&os=100001",
    }.items():
        def probe(url=url):
            r = fetch(url, purpose="endpoint existence probe")
            return {"status": r.status_code, "content_type": r.headers.get("Content-Type"), "body_head": r.text[:160]}
        out["endpoint_existence"][label] = try_(probe)

    (SETTINGS.profiling_dir / "refresh_probe.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str)[:7000])


if __name__ == "__main__":
    main()
