"""Country-indicator discovery pass (2026-10-22): metadata and coverage probes only.

No indicator data is persisted. The script stores:
  * official metadata responses (indicator definitions, sources, WDI-archive concepts, country list)
  * coverage summaries: per indicator, the row total and `lastupdated` from a per_page=1 call, and the
    latest non-empty year per economy from an mrnev=1 call. The values in these responses are discarded.
  * HEAD responses (size, type) for the official income-classification and WGI files (no body downloaded)

It writes only inside discovery/country_indicators/ and never touches Phase 1 files or manifests.
Every request is logged to country_indicators/evidence/access_log.jsonl.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
META = HERE / "metadata"
LOG = HERE / "evidence" / "access_log.jsonl"
API = "https://api.worldbank.org/v2"
UA = {"User-Agent": "procurement-intelligence-discovery/0.2 (public-data feasibility study)"}
CANDIDATES = {
    "NY.GDP.MKTP.KD.ZG": None,      # GDP growth (annual %)
    "FP.CPI.TOTL.ZG": None,         # Inflation, consumer prices (annual %)
    "NY.GDP.PCAP.CD": None,         # GDP per capita (current US$)
    "NY.GNP.PCAP.CD": None,         # GNI per capita, Atlas method (basis of income classification)
    "GOV_WGI_GE.EST": 3,            # Government Effectiveness: estimate (already local)
    "GOV_WGI_GE.SC": 3,             # Government Effectiveness: score (0-100), if published
    "GOV_WGI_GE.PER.RNK": 3,        # Government Effectiveness: percentile rank, if published
    "GOV_WGI_GE.STD.ERR": 3,        # Government Effectiveness: standard error, if published
    "GOV_WGI_GE.NO.SRC": 3,         # Government Effectiveness: number of sources, if published
    "IQ.CPA.PUBS.XQ": None,         # CPIA public sector management (already local; freshness check)
}
HEADS = {
    "income_class_current": "https://ddh-openapi.worldbank.org/resources/DR0095333/download",
    "income_class_historical": "https://ddh-openapi.worldbank.org/resources/DR0095334/download",
    "wgi_dataset_2026_xlsx": "https://www.worldbank.org/content/dam/sites/govindicators/doc/wgidataset_with_sourcedata-2026.xlsx",
}


def log(entry: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def get(url: str, params: dict | None = None, purpose: str = "") -> object:
    params = {"format": "json", **(params or {})}
    t0 = time.time()
    r = requests.get(url, params=params, headers=UA, timeout=60)
    log({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "method": "GET", "url": r.url,
         "status": r.status_code, "bytes": len(r.content), "seconds": round(time.time() - t0, 2), "purpose": purpose})
    time.sleep(0.5)
    r.raise_for_status()
    return r.json()


def head(url: str, purpose: str) -> dict:
    t0 = time.time()
    r = requests.head(url, headers=UA, timeout=60, allow_redirects=True)
    out = {"url": url, "final_url": r.url, "status": r.status_code,
           "content_type": r.headers.get("Content-Type"), "content_length": r.headers.get("Content-Length"),
           "last_modified": r.headers.get("Last-Modified"),
           "content_disposition": r.headers.get("Content-Disposition")}
    log({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "method": "HEAD", "url": url,
         "status": r.status_code, "bytes": 0, "seconds": round(time.time() - t0, 2), "purpose": purpose})
    return out


def save(name: str, obj: object) -> None:
    META.mkdir(parents=True, exist_ok=True)
    (META / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False))


def main() -> None:
    save("sources.json", get(f"{API}/sources", {"per_page": 200}, "list of API sources"))
    save("countries.json", get(f"{API}/country", {"per_page": 400}, "country metadata (codes, region, current income)"))
    save("income_levels.json", get(f"{API}/incomeLevel", {"per_page": 50}, "income level codes"))
    save("wgi_source3_indicators.json", get(f"{API}/sources/3/indicators", {"per_page": 200}, "WGI series list"))
    for concept in ("concepts", "version"):
        try:
            save(f"source57_{concept}.json", get(f"{API}/sources/57/{concept}", {"per_page": 300},
                                                 f"WDI Database Archives {concept}"))
        except requests.HTTPError as e:
            save(f"source57_{concept}.json", {"error": str(e)})

    coverage = {}
    for code, source in CANDIDATES.items():
        extra = {"source": source} if source else {}
        try:
            meta = get(f"{API}/indicator/{code}", extra, f"indicator metadata {code}")
        except requests.HTTPError as e:
            meta = {"error": str(e)}
        save(f"indicator_{code}.json", meta)
        entry: dict = {"metadata_found": isinstance(meta, list) and len(meta) > 1 and bool(meta[1])}
        if not entry["metadata_found"]:
            coverage[code] = entry
            continue
        page = get(f"{API}/country/all/indicator/{code}", {"per_page": 1, **extra}, f"row count {code}")
        entry["page_header_all_years"] = page[0]
        page = get(f"{API}/country/all/indicator/{code}", {"per_page": 1, "date": "2000:2025", **extra},
                   f"row count 2000-2025 {code}")
        entry["page_header_2000_2025"] = page[0]
        latest = get(f"{API}/country/all/indicator/{code}", {"per_page": 1000, "mrnev": 1, **extra},
                     f"latest non-empty year per economy {code} (values discarded)")
        rows = latest[1] or []
        entry["latest_nonempty_year_by_economy"] = {r["countryiso3code"] or r["country"]["value"]: r["date"]
                                                    for r in rows}  # values intentionally not stored
        entry["mrnev_header"] = latest[0]
        coverage[code] = entry
    save("coverage_probe.json", coverage)
    save("file_heads.json", {k: head(u, f"HEAD {k}") for k, u in HEADS.items()})
    print(json.dumps({k: {"found": v["metadata_found"], "total": v.get("page_header_all_years", {}).get("total"),
                          "lastupdated": v.get("page_header_all_years", {}).get("lastupdated"),
                          "economies_with_any_value": len(v.get("latest_nonempty_year_by_economy", {}))}
                      for k, v in coverage.items()}, indent=1))


if __name__ == "__main__":
    main()
