"""Approved R1 + R2 (2026-10-22): download country-level reference data, unchanged.

R1  WDI series NY.GDP.MKTP.KD.ZG, FP.CPI.TOTL.ZG, NY.GDP.PCAP.CD for all countries, 2000-2025
    (api.worldbank.org/v2, one page per series). Also saved as source metadata: indicator
    definitions, WDI source record (lastupdated) and the country list used for validation.
R2  Historical income classification (OGHIST xlsx) from the official DDH resource.

Every response body is written byte-for-byte as received. A sidecar `<file>.meta.json` holds the
request URL and parameters, retrieval timestamps, HTTP status, response headers (cookies excluded),
size and SHA-256. Existing files are never overwritten. Outputs go only to
discovery/country_indicators/raw/ (separate from Phase 1 data and from procurement Bronze inputs).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
LOG = HERE / "evidence" / "access_log.jsonl"
API = "https://api.worldbank.org/v2"
UA = {"User-Agent": "procurement-intelligence-discovery/0.2 (public-data feasibility study)"}
WDI_SERIES = ["NY.GDP.MKTP.KD.ZG", "FP.CPI.TOTL.ZG", "NY.GDP.PCAP.CD"]
DATE_RANGE = "2000:2025"
OGHIST_URL = "https://ddh-openapi.worldbank.org/resources/DR0095334/download"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch(url: str, params: dict | None, dest_dir: Path, filename: str | None, purpose: str) -> Path:
    started = now()
    t0 = time.time()
    r = requests.get(url, params=params, headers=UA, timeout=120)
    finished = now()
    body = r.content
    if filename is None:  # use the server-provided file name (Content-Disposition)
        cd = r.headers.get("Content-Disposition", "")
        m = re.search(r'filename="?([^";]+)"?', cd)
        if not m:
            raise SystemExit(f"no file name in Content-Disposition for {url}")
        filename = m.group(1)
    dest = dest_dir / filename
    with LOG.open("a") as f:
        f.write(json.dumps({"ts": finished, "method": "GET", "url": r.url, "status": r.status_code, "bytes": len(body),
                            "seconds": round(time.time() - t0, 2), "purpose": purpose, "saved_as": str(dest.relative_to(HERE))}) + "\n")
    r.raise_for_status()
    if dest.exists():
        raise SystemExit(f"refusing to overwrite existing raw file {dest}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    meta = {"file": dest.name, "purpose": purpose, "request_url": url, "request_params": params or {},
            "final_url": r.url, "http_status": r.status_code, "retrieval_started_utc": started,
            "retrieval_finished_utc": finished,
            "response_headers": {k: v for k, v in r.headers.items() if k.lower() not in {"set-cookie"}},
            "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    if filename.endswith(".json"):
        payload = json.loads(body)
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            meta["api_page_header"] = payload[0]
            meta["api_records_in_body"] = len(payload[1] or []) if len(payload) > 1 else 0
    (dest_dir / f"{filename}.meta.json").write_text(json.dumps(meta, indent=1))
    time.sleep(0.5)
    return dest


def main() -> None:
    wdi = RAW / "wdi"
    fetch(f"{API}/sources/2", {"format": "json"}, wdi, "source_2_wdi.json", "WDI source record (lastupdated)")
    fetch(f"{API}/country", {"format": "json", "per_page": 400}, wdi, "countries.json",
          "country list (iso codes, region/aggregate flag; income level is today's value)")
    for code in WDI_SERIES:
        fetch(f"{API}/indicator/{code}", {"format": "json"}, wdi, f"indicator_{code}.json", f"indicator metadata {code}")
        p = fetch(f"{API}/country/all/indicator/{code}", {"format": "json", "date": DATE_RANGE, "per_page": 20000},
                  wdi, f"observations_{code}_2000-2025.json", f"R1 observations {code}")
        header = json.loads(p.read_bytes())[0]
        if header.get("pages") != 1:
            raise SystemExit(f"{code}: expected a single page, got {header}")
    fetch(OGHIST_URL, None, RAW / "income_classification", None, "R2 historical income classification (OGHIST)")
    print("downloaded:")
    for f in sorted(RAW.rglob("*")):
        if f.is_file() and not f.name.endswith(".meta.json"):
            print(f"  {f.relative_to(HERE)}  {f.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
