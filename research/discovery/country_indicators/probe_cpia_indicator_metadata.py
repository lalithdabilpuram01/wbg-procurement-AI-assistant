"""Read-only lookup of official metadata for the six CPIA series (2026-10-23), for documentation only.

Saves the raw /v2/indicator responses byte-for-byte under metadata/cpia/ and logs every request to
evidence/access_log.jsonl. Not a data download and not an input to any Silver table.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
OUT = HERE / "metadata" / "cpia"
LOG = HERE / "evidence" / "access_log.jsonl"
CODES = ["IQ.CPA.FINQ.XQ", "IQ.CPA.IRAI.XQ", "IQ.CPA.PADM.XQ", "IQ.CPA.PROP.XQ", "IQ.CPA.PUBS.XQ", "IQ.CPA.TRAN.XQ"]
UA = {"User-Agent": "procurement-intelligence-discovery/0.2 (public-data feasibility study)"}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for code in CODES:
        dest = OUT / f"indicator_{code}.json"
        if dest.exists():
            continue
        r = requests.get(f"https://api.worldbank.org/v2/indicator/{code}", params={"format": "json"}, headers=UA, timeout=60)
        with LOG.open("a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "method": "GET", "url": r.url,
                                "status": r.status_code, "bytes": len(r.content), "purpose": f"CPIA indicator metadata {code}"}) + "\n")
        r.raise_for_status()
        dest.write_bytes(r.content)
        time.sleep(0.5)
    for code in CODES:
        m = json.loads((OUT / f"indicator_{code}.json").read_bytes())[1][0]
        print(f"{code} | {m['name']} | source={m['source']['value']} ({m['source']['id']}) | org={m.get('sourceOrganization', '')[:60]}")
        print(f"   {m.get('sourceNote', '')[:400]}")


if __name__ == "__main__":
    main()
