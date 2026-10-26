"""Task 6 / 7: can STEP procurement-plan PDFs supply planned vs actual procurement-stage dates?

STEP procurement-plan exports contain one table per category with columns:
  Activity Reference No. / Description | Loan / Credit No. | Component | Review Type | Method |
  Market Approach | Procurement Process | Prequalification | Estimated Amount | Actual Amount |
  Process Status | <stage> Planned | <stage> Actual  (x 9 stages)

Method:
  1. Choose the latest Procurement Plan (docdt >= 2018) for up to N seeded cluster projects (v3 wds).
  2. Download the PDF (samples/documents/pp_files, git-ignored; checksums in MANIFEST).
  3. pdfplumber.extract_tables(); detect header rows; forward-fill stage names over
     Planned/Actual sub-columns; emit one row per activity with source doc id + page.
  4. Measure stage-date population (planned vs actual), status distribution, and
     exact-after-normalisation reference matches to DS00005 contracts in the same project.

Outputs: profiling/procurement_plan_activities.csv, profiling/procurement_plan_summary.json
"""
from __future__ import annotations

import collections
import json
import os
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import pdfplumber  # noqa: E402

from wbdisc import datasets as D  # noqa: E402
from wbdisc.config import SETTINGS  # noqa: E402
from wbdisc.http import FetchError, download  # noqa: E402
from wbdisc.normalize import normalize_ref, parse_date  # noqa: E402
from wbdisc.procurement_plan import merge_header_variants, normalize_plan_status  # noqa: E402
from wbdisc.raw_store import record_file  # noqa: E402

N_PLANS = int(os.environ.get("WB_PP_SAMPLE", 40))
PP_DIR = SETTINGS.samples_dir / "documents" / "pp_files"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean(c) -> str:
    return re.sub(r"\s+", " ", (c or "")).strip()


def parse_tables(pdf_path: Path) -> list[dict]:
    rows_out = []
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables():
                header_idx = next((i for i, r in enumerate(table) if r and clean(r[0]).startswith("Activity Reference")), None)
                if header_idx is None or header_idx + 1 >= len(table):
                    continue
                header, sub = table[header_idx], table[header_idx + 1]
                category = clean(table[header_idx - 1][0]) if header_idx > 0 else ""
                names, stage = [], ""
                for h, s in zip(header, sub):
                    h, s = clean(h), clean(s)
                    if s in {"Planned", "Actual"}:
                        stage = h or stage
                        names.append(f"{stage} | {s}")
                    else:
                        names.append(h)
                for r in table[header_idx + 2:]:
                    if not r or not clean(r[0]) or clean(r[0]).startswith("Activity Reference"):
                        continue
                    rec = {n: clean(v) for n, v in zip(names, r) if n}
                    rec["_category"], rec["_page"] = category, pno
                    rows_out.append(rec)
    return rows_out


def main() -> None:
    rng = random.Random(SETTINGS.seed)
    docs = [d for d in D.docs_cluster() if clean(d.get("docty")) == "Procurement Plan" and d.get("pdfurl")]
    latest: dict[str, dict] = {}
    for d in docs:
        pid = d["_extract"]["filter_projectid"]
        if (parse_date(d.get("docdt")) or parse_date("1900-01-01")) < parse_date("2018-01-01"):
            continue
        if pid not in latest or (d.get("docdt") or "") > (latest[pid].get("docdt") or ""):
            latest[pid] = d
    chosen = rng.sample(sorted(latest), min(N_PLANS, len(latest)))
    print(f"projects with STEP-era PP: {len(latest)}; sampled {len(chosen)}")

    all_rows, failures = [], []
    for pid in chosen:
        d = latest[pid]
        dest = PP_DIR / f"{pid}_PP_{d['id']}.pdf"
        if not dest.exists():
            try:
                n = download(d["pdfurl"].replace("http://", "https://"), dest, purpose="pp table sample")
                record_file(dest, source="documents.worldbank.org", method="GET pdfurl (v3 wds Procurement Plan)",
                            records=None, notes=f"wds_id={d['id']}; docdt={d.get('docdt')}; bytes={n}")
            except FetchError as exc:
                failures.append({"project": pid, "error": str(exc)[:200]})
                continue
        try:
            rows = parse_tables(dest)
        except Exception as exc:
            failures.append({"project": pid, "error": f"parse: {exc!r}"[:200]})
            continue
        for r in rows:
            r.update({"_project_id": pid, "_wds_id": d["id"], "_docdt": d.get("docdt")})
        all_rows.extend(rows)
        print(f"  {pid}: {len(rows)} activities")

    # Wrapped header cells produce whitespace variants of the same column; merge them
    # (Phase 1 read only the first variant; corrected 2026-10-21).
    raw_cols = len(pd.DataFrame(all_rows).columns)
    df = merge_header_variants(pd.DataFrame(all_rows))
    out_csv = SETTINGS.profiling_dir / "procurement_plan_activities.csv"
    df.to_csv(out_csv, index=False)
    summary: dict = {"plans_sampled": len(chosen), "plans_parsed_with_activities": int(df["_wds_id"].nunique()) if len(df) else 0,
                     "activities": len(df), "failures": failures,
                     "columns_before_header_merge": raw_cols, "columns_after_header_merge": len(df.columns)}
    if len(df):
        stage_cols = [c for c in df.columns if " | " in c]
        pop = {}
        for c in stage_cols:
            vals = df[c].fillna("")
            pop[c] = round(100 * vals.str.match(DATE.pattern).mean(), 2)
        summary["stage_date_populated_pct"] = pop
        status_col = next((c for c in df.columns if c.startswith("Process Status")), None)
        if status_col:
            summary["process_status_distribution"] = df[status_col].value_counts().to_dict()
            df["_status"] = df[status_col].map(normalize_plan_status)
            summary["process_status_normalized"] = df["_status"].value_counts().to_dict()
            summary["cancelled_pct"] = round(100 * (df["_status"] == "cancelled").mean(), 2)
            summary["plans_with_ge1_cancelled_pct"] = round(100 * df.groupby("_wds_id")["_status"].apply(
                lambda s: (s == "cancelled").any()).mean(), 2)
        for label in ("Market Approach", "Estimated Amount", "Actual Amount", "Procurement Process"):
            col = next((c for c in df.columns if c.replace(" ", "").lower().startswith(label.replace(" ", "").lower())), None)
            if col:
                summary[f"{label.lower().replace(' ', '_')}_populated_pct"] = round(100 * df[col].fillna("").str.strip().ne("").mean(), 2)
        signed_actual = next((c for c in stage_cols if c.startswith("Signed Contract") and c.endswith("Actual")), None)
        spn_actual = next((c for c in stage_cols if c.startswith("Specific Procurement Notice") and c.endswith("Actual")), None)
        if signed_actual and spn_actual:
            both = df[signed_actual].fillna("").str.match(DATE.pattern) & df[spn_actual].fillna("").str.match(DATE.pattern)
            summary["activities_with_actual_invitation_and_actual_signing"] = int(both.sum())
            dd = (df.loc[both, signed_actual].map(parse_date) - df.loc[both, spn_actual].map(parse_date)).map(lambda x: x.days)
            if len(dd):
                summary["actual_invitation_to_signing_days"] = {k: float(v) for k, v in dd.describe().items()}
        # Reference join to DS00005 within the same project
        ref_col = next(c for c in df.columns if c.startswith("Activity Reference"))
        df["_ref"] = df[ref_col].map(lambda v: normalize_ref(str(v).split(" / ")[0]))
        ca = D.contracts()
        ca = ca[ca["Project ID"].isin(set(df["_project_id"]))]
        cref = set(zip(ca["Project ID"], ca["Borrower Contract Reference Number"].map(normalize_ref)))
        hit = df.apply(lambda r: (r["_project_id"], r["_ref"]) in cref, axis=1)
        summary["activity_ref_matches_ds00005_contract_pct"] = round(100 * hit.mean(), 2)
        if status_col:
            summary["activity_ref_match_pct_by_status"] = df.assign(_h=hit).groupby("_status")["_h"].mean().mul(100).round(1).to_dict()
        summary["contracts_in_sampled_projects"] = int(ca["WB Contract Number"].nunique())
        summary["contracts_found_in_plan_pct"] = round(100 * ca.drop_duplicates("WB Contract Number").apply(
            lambda r: (r["Project ID"], normalize_ref(r["Borrower Contract Reference Number"])) in set(zip(df["_project_id"], df["_ref"])),
            axis=1).mean(), 2)
        summary["columns_observed"] = list(df.columns)
    (SETTINGS.profiling_dir / "procurement_plan_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: v for k, v in summary.items() if k != "columns_observed"}, indent=1, default=str)[:5000])


if __name__ == "__main__":
    main()
