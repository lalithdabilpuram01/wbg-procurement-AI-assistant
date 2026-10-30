"""Parse every downloaded procurement-plan version for the 2 deep-dive projects (offline).

Uses the Phase 1 parser (scripts/p05: parse_tables) and header-variant merge / status normaliser
(wbdisc/procurement_plan.py). Output: derived/plan_activities_by_version.csv (one row per activity per
plan version), so status histories can be followed across versions. Raw PDFs are not modified.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "scripts"))

import pandas as pd  # noqa: E402
from p05_procurement_plan_tables import parse_tables  # noqa: E402

from wbdisc.procurement_plan import header_key, merge_header_variants, normalize_plan_status  # noqa: E402

OUT = HERE / "derived" / "plan_activities_by_version.csv"


def main() -> None:
    frames = []
    for pdf in sorted((HERE / "raw").glob("P*/pp_pdf/*.pdf")):
        pid = pdf.parts[-3]
        date, _, doc_id = pdf.stem.split("_")
        try:
            rows = parse_tables(pdf)
        except Exception as e:  # noqa: BLE001 - record and continue; one bad PDF must not stop the run
            print(f"  parse error {pdf.name}: {e}")
            continue
        if not rows:
            print(f"  no activity tables: {pid} {pdf.name}")
            continue
        df = merge_header_variants(pd.DataFrame(rows))
        df["_project_id"], df["_plan_date"], df["_wds_id"] = pid, date, doc_id
        frames.append(df)
        print(f"  {pid} {date} {doc_id}: {len(df)} activities")
    allv = pd.concat(frames, ignore_index=True)
    allv = merge_header_variants(allv)
    stat = next(c for c in allv.columns if header_key(c).startswith("processstatus"))
    allv["_status"] = allv[stat].map(normalize_plan_status)
    OUT.parent.mkdir(exist_ok=True)
    allv.to_csv(OUT, index=False)
    print("versions parsed:", allv._wds_id.nunique(), "rows:", len(allv))


if __name__ == "__main__":
    main()
