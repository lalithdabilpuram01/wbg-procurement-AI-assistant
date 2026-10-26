"""Task 2A: Contract awards (Finances One DS00005, plus legacy DS01004).

Extraction methods (all recorded in samples/MANIFEST.jsonl):
  1. API sample: Finances One apiservice, filter fiscal_year=<FY>, random `skip`
     blocks per FY (2020..current) -> samples/contract_awards/ds00005_api_sample.jsonl
  2. Bulk CSV (population, git-ignored cache) -> data_cache/ds00005_contract_awards.csv
  3. Seeded stratified random sample from bulk (FY x region) -> samples/.../ds00005_stratified_sample.csv
  4. Legacy DS01004 (FY2001-FY2016) bulk to cache + stratified sample.

Raw values are never modified; CSV rows are copied byte-for-byte via pandas with dtype=str.
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
from wbdisc.sources import fone_page  # noqa: E402

DATASETS = {
    "DS00005": {
        "resource": "RS00005",
        "csv": "DS00005/RS00005/Contract_Awards_in_Investment_Project_Financing__Since_FY_2020_.csv",
        "cache": "ds00005_contract_awards.csv",
    },
    "DS01004": {
        "resource": "RS00934",
        "csv": "DS01004/RS00934/Contract_Awards_in_Investment_Project_Financing__since_FY_2001___FY_2016_.csv",
        "cache": "ds01004_contract_awards_legacy.csv",
    },
}
OUT = SETTINGS.samples_dir / "contract_awards"


def api_sample(rng: random.Random) -> None:
    rows: list[dict] = []
    for fy in range(2016, 2028):
        total, _ = fone_page("DS00005", "RS00005", top=1, filter_=f"fiscal_year={fy}", purpose=f"count FY{fy}")
        print(f"DS00005 FY{fy}: {total} rows (API count)")
        if total == 0:
            continue
        block = 25
        n_blocks = max(1, SETTINGS.contract_sample_per_fy // block)
        for skip in sorted(rng.sample(range(0, max(1, total - block)), min(n_blocks, max(1, total - block)))):
            _, recs = fone_page("DS00005", "RS00005", top=block, skip=skip, filter_=f"fiscal_year={fy}",
                                purpose=f"sample FY{fy}")
            for r in recs:
                r["_extract"] = {"filter": f"fiscal_year={fy}", "skip": skip, "top": block}
            rows.extend(recs)
    write_jsonl(rows, OUT / "ds00005_api_sample.jsonl", source="Finances One DS00005 apiservice",
                method="filter fiscal_year=FY; seeded random skip blocks of 25 per FY; _extract holds request params")


def bulk(dataset_id: str) -> Path | None:
    meta = DATASETS[dataset_id]
    dest = SETTINGS.cache_dir / meta["cache"]
    if dest.exists():
        print(f"{dataset_id}: using cached {dest.name}")
        return dest
    if not SETTINGS.allow_bulk:
        print(f"{dataset_id}: bulk download disabled (WB_ALLOW_BULK_DOWNLOADS=false)")
        return None
    n = download(f"{SETTINGS.fone_files_base}/{meta['csv']}", dest, purpose=f"bulk {dataset_id}")
    record_file(dest, source=f"Finances One {dataset_id} bulk CSV", method="HTTP GET of published CSV",
                records=None, notes=f"{n} bytes; git-ignored cache")
    return dest


def stratified_sample(dataset_id: str, path: Path, strata: list[str], n_total: int) -> None:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    groups = df.groupby(strata, dropna=False)
    per = max(1, n_total // max(1, groups.ngroups))
    sample = pd.concat([g.sample(n=min(per, len(g)), random_state=SETTINGS.seed) for _, g in groups])
    dest = OUT / f"{dataset_id.lower()}_stratified_sample.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    sample.to_csv(dest, index=False)
    record_file(dest, source=f"Finances One {dataset_id} bulk CSV", records=len(sample),
                method=f"seeded (seed={SETTINGS.seed}) stratified random sample by {strata}, <= {per} rows/stratum",
                notes=f"population rows={len(df)}; values unmodified (read as str)")
    print(f"{dataset_id}: population {len(df)} rows, sample {len(sample)} rows -> {dest.name}")


def main() -> None:
    rng = random.Random(SETTINGS.seed)
    if not (OUT / "ds00005_api_sample.jsonl").exists():
        api_sample(rng)
    p = bulk("DS00005")
    if p:
        stratified_sample("DS00005", p, _strata(p), 1000)
    p = bulk("DS01004")
    if p:
        stratified_sample("DS01004", p, _strata(p), 600)


def _strata(path: Path) -> list[str]:
    """Bulk CSVs use display headers ('Fiscal Year'), unlike the API (fiscal_year)."""
    cols = list(pd.read_csv(path, dtype=str, nrows=2, encoding="utf-8-sig").columns)
    wanted = [c for c in cols if c.strip().lower() in {"fiscal year", "fiscal_year", "region"}]
    return wanted or [cols[0]]


if __name__ == "__main__":
    main()
