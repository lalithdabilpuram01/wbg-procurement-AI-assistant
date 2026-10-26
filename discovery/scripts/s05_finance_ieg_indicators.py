"""Tasks 2E/2F: financial statements, IEG ratings, WGI and CPIA.

Extraction methods:
  * IBRD (DS00047) and IDA (DS00001) latest-snapshot statements: bulk CSV -> data_cache;
    seeded random 500-row sample of each -> samples/finance/
  * IBRD (DS00975) / IDA (DS00976) monthly historical snapshots: API filter on the loan
    numbers of 5 cluster projects (full monthly history of those loans) -> samples/finance/
  * IEG ratings DS00053: bulk CSV (small) -> samples/ieg/ (full population; 12.6k rows)
  * WGI (source 3, GOV_WGI_*.EST) and CPIA (IQ.CPA.*): Indicators API, all countries,
    all years -> samples/indicators/
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from wbdisc.config import SETTINGS  # noqa: E402
from wbdisc.http import download  # noqa: E402
from wbdisc.raw_store import record_file, write_jsonl  # noqa: E402
from wbdisc.sources import fone_all, fone_page, indicator_all  # noqa: E402

FONE = {
    "DS00047": ("RS00049", "IBRD_Statement_of_Loans_and_Guarantees___Latest_Available_Snapshot.csv", "ibrd_latest"),
    "DS00001": ("RS00001", "IDA_Statement_of_Credits__Grants_and_Guarantees___Latest_Available_Snapshot.csv", "ida_latest"),
    "DS00053": ("RS00055", "IEG_World_Bank_Project_Performance_Ratings.csv", "ieg_ratings"),
}
HIST = {"DS00975": "RS00905", "DS00976": "RS00906"}
WGI = ["GOV_WGI_CC.EST", "GOV_WGI_GE.EST", "GOV_WGI_PV.EST", "GOV_WGI_RL.EST", "GOV_WGI_RQ.EST", "GOV_WGI_VA.EST"]
CPIA = ["IQ.CPA.TRAN.XQ", "IQ.CPA.PUBS.XQ", "IQ.CPA.PADM.XQ", "IQ.CPA.FINQ.XQ", "IQ.CPA.IRAI.XQ", "IQ.CPA.PROP.XQ"]


def bulk(ds: str) -> Path:
    res, fname, stem = FONE[ds]
    dest = SETTINGS.cache_dir / f"{ds.lower()}_{stem}.csv"
    if not dest.exists():
        n = download(f"{SETTINGS.fone_files_base}/{ds}/{res}/{fname}", dest, purpose=f"bulk {ds}")
        record_file(dest, source=f"Finances One {ds} bulk CSV", method="HTTP GET of published CSV", records=None,
                    notes=f"{n} bytes; git-ignored cache")
    return dest


def main() -> None:
    rng = random.Random(SETTINGS.seed)
    fin = SETTINGS.samples_dir / "finance"
    fin.mkdir(parents=True, exist_ok=True)
    for ds in ("DS00047", "DS00001"):
        p = bulk(ds)
        df = pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        s = df.sample(n=min(500, len(df)), random_state=SETTINGS.seed)
        dest = fin / f"{p.stem}_sample.csv"
        s.to_csv(dest, index=False)
        record_file(dest, source=f"Finances One {ds}", method=f"seeded random 500-row sample of bulk CSV",
                    records=len(s), notes=f"population rows={len(df)}")
        print(ds, "rows", len(df), "cols", list(df.columns))

    # Historical monthly snapshots for a handful of loans (verifies point-in-time availability)
    ida = pd.read_csv(bulk("DS00001"), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    ibrd = pd.read_csv(bulk("DS00047"), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    for ds, latest in (("DS00976", ida), ("DS00975", ibrd)):
        dest = fin / f"{ds.lower()}_history_sample.jsonl.gz"
        if dest.exists():
            continue
        loan_col = next(c for c in latest.columns if c.lower().replace(" ", "_") in {"credit_number", "loan_number"})
        status_col = next(c for c in latest.columns if "status" in c.lower())
        candidates = latest[latest[status_col].str.contains("Disburs|Repay|Fully", case=False, regex=True)]
        loans = rng.sample(sorted(candidates[loan_col].unique()), 3)
        _, probe = fone_page(ds, HIST[ds], top=1, purpose=f"probe {ds}")
        api_loan_col = next(k for k in probe[0] if k in {"credit_number", "loan_number"})
        rows = []
        for ln in loans:
            for r in fone_all(ds, HIST[ds], filter_=f"{api_loan_col}='{ln}'", purpose=f"history {ds} {ln}"):
                rows.append(r)
        write_jsonl(rows, dest, source=f"Finances One {ds} apiservice",
                    method=f"filter {api_loan_col}='<loan>' for 3 seeded random loans {loans}")
        print(ds, "history rows", len(rows))

    ieg_dir = SETTINGS.samples_dir / "ieg"
    ieg_dir.mkdir(parents=True, exist_ok=True)
    p = bulk("DS00053")
    dest = ieg_dir / "ds00053_ieg_ratings_full.csv"
    if not dest.exists():
        dest.write_bytes(p.read_bytes())
        record_file(dest, source="Finances One DS00053 bulk CSV", method="copy of full bulk CSV (small population)",
                    records=len(pd.read_csv(p, dtype=str, encoding="utf-8-sig")))

    ind_dir = SETTINGS.samples_dir / "indicators"
    for code in WGI + CPIA:
        dest = ind_dir / f"{code}.jsonl.gz"
        if dest.exists():
            continue
        meta, rows = indicator_all(code, source=3 if code.startswith("GOV_WGI") else None)
        write_jsonl(rows, dest, source="api.worldbank.org/v2 indicators",
                    method=f"country/all/indicator/{code}" + ("?source=3" if code.startswith("GOV_WGI") else ""),
                    notes=f"meta={meta}")
        print(code, len(rows), meta.get("lastupdated"))


if __name__ == "__main__":
    main()
