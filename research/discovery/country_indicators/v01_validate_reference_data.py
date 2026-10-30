"""Validate the R1/R2 downloads and the reused R3 files (offline, read-only on all inputs).

Checks: record counts vs API totals, indicator codes, duplicates, year coverage, country resolution
(economies vs aggregates), missing values, cohort coverage per source under source-specific
crosswalks (D-31; regional projects excluded per D-29), OGHIST structure, and integrity of the
Phase 1 WGI/CPIA files. No values are cleaned, imputed or reshaped into new datasets.

Outputs: validation/reference_validation.json, validation/cohort_country_match_by_source.csv
"""
from __future__ import annotations

import collections
import gzip
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import openpyxl  # noqa: E402
import pandas as pd  # noqa: E402

from wbdisc.countries import WB_TO_ISO2, is_regional  # noqa: E402

RAW = HERE / "raw"
REPO = HERE.parent.parent
OUT = HERE / "validation"
SERIES = ["NY.GDP.MKTP.KD.ZG", "FP.CPI.TOTL.ZG", "NY.GDP.PCAP.CD"]
YEARS = list(range(2000, 2026))
LOCAL = {"wgi": "GOV_WGI_GE.EST", "cpia": "IQ.CPA.PUBS.XQ"}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def wb_to_iso2_source_specific(code: str) -> str | None:
    """D-31 base mapping: WB code -> ISO2 using only the curated aliases. Per-source availability is then
    checked against each source's own country list (no fuzzy matching, no regional mapping)."""
    if not code or is_regional(code):
        return None
    return WB_TO_ISO2.get(code, code)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    res: dict = {}
    countries = json.loads((RAW / "wdi" / "countries.json").read_bytes())[1]
    iso2_to_iso3 = {c["iso2Code"]: c["id"] for c in countries}
    economies = {c["iso2Code"] for c in countries if c["region"]["id"] != "NA"}
    aggregates = {c["iso2Code"] for c in countries if c["region"]["id"] == "NA"}
    src = json.loads((RAW / "wdi" / "source_2_wdi.json").read_bytes())[1][0]

    cohort = pd.read_csv(REPO / "cohort" / "poc_cohort_2000_manifest.csv", dtype=str, keep_default_na=False)
    cohort["regional"] = cohort.country_code.map(is_regional)
    cohort["iso2"] = cohort.country_code.map(wb_to_iso2_source_specific)
    single = cohort[~cohort.regional]

    # ---------------- R1: WDI ----------------
    wdi_frames, res["wdi"] = {}, {}
    for code in SERIES:
        raw = json.loads((RAW / "wdi" / f"observations_{code}_2000-2025.json").read_bytes())
        header, recs = raw[0], raw[1]
        meta = json.loads((RAW / "wdi" / f"observations_{code}_2000-2025.json.meta.json").read_text())
        df = pd.DataFrame({"indicator": [r["indicator"]["id"] for r in recs], "iso2": [r["country"]["id"] for r in recs],
                           "iso3": [r["countryiso3code"] for r in recs], "name": [r["country"]["value"] for r in recs],
                           "year": [int(r["date"]) for r in recs], "value": [r["value"] for r in recs],
                           "obs_status": [r.get("obs_status") for r in recs], "unit": [r.get("unit") for r in recs]})
        wdi_frames[code] = df
        eco = df[df.iso2.isin(economies)]
        per_entity_years = df.groupby("iso2").year.agg(lambda s: sorted(s) == YEARS)
        ind_meta = json.loads((RAW / "wdi" / f"indicator_{code}.json").read_bytes())[1][0]
        latest = eco[eco.value.notna()].groupby("iso2").year.max()
        res["wdi"][code] = {
            "official_name": ind_meta["name"], "source": ind_meta["source"]["value"],
            "api_total": header["total"], "pages": header["pages"], "records": len(recs),
            "V1_count_matches_total": len(recs) == header["total"] and header["pages"] == 1,
            "lastupdated": header["lastupdated"], "V1_lastupdated_matches_source": header["lastupdated"] == src["lastupdated"],
            "retrieved_utc": meta["retrieval_finished_utc"], "bytes": meta["bytes"], "sha256": meta["sha256"],
            "V2_indicator_code_all_match": bool((df.indicator == code).all()),
            "V2_duplicate_country_year": int(df.duplicated(["iso2", "year"]).sum()),
            "V2_all_entities_have_years_2000_2025": bool(per_entity_years.all()),
            "entities": int(df.iso2.nunique()),
            "V3_entities_resolved": int(df.iso2.isin(economies | aggregates).sum() / len(YEARS)),
            "V3_unresolved_entities": sorted(set(df.iso2) - economies - aggregates),
            "economies": int(eco.iso2.nunique()), "aggregates": int(df.iso2.isin(aggregates).sum() / len(YEARS)),
            "V4_null_pct_all_rows": round(100 * df.value.isna().mean(), 2),
            "V4_null_pct_economies": round(100 * eco.value.isna().mean(), 2),
            "V4_economies_with_no_value_2000_2025": sorted(set(eco.iso2) - set(eco[eco.value.notna()].iso2)),
            "V4_economies_with_value_by_year": eco[eco.value.notna()].groupby("year").iso2.nunique().to_dict(),
            "obs_status_values": df.obs_status.fillna("<null>").replace("", "<empty>").value_counts().to_dict(),
            "unit_values": df.unit.fillna("<null>").replace("", "<empty>").value_counts().to_dict(),
            "latest_year_per_economy": latest.to_dict(),
        }

    # ---------------- R2: OGHIST ----------------
    xl = RAW / "income_classification" / "OGHIST_2026_10_08.xlsx"
    wb = openpyxl.load_workbook(xl, read_only=True, data_only=True)
    sheets = {ws.title: (ws.max_row, ws.max_column) for ws in wb.worksheets}
    ws = wb["Country Analytical History"]
    rows = list(ws.iter_rows(values_only=True))
    fy_row = rows[4]
    fy_cols = [(i, v) for i, v in enumerate(fy_row) if isinstance(v, str) and v.startswith("FY")]
    data_year_row = rows[5]
    body = [r for r in rows[6:] if r[0] and isinstance(r[0], str) and len(r[0].strip()) == 3]
    footer = [r for r in rows[6:] if r not in body and any(c is not None for c in r)]
    codes = [r[0].strip() for r in body]
    values = collections.Counter(str(r[i]) for r in body for i, _ in fy_cols)
    fy_nums = [int(v[2:]) for _, v in fy_cols]
    fy_full = [(1900 if n >= 50 else 2000) + n for n in fy_nums]
    params = {r[0]: r[1] for r in wb["Parameters"].iter_rows(min_row=2, values_only=True) if r[0]}
    og = {code: {fy: str(r[i]) for (i, _), fy in zip(fy_cols, fy_full)} for code, r in zip(codes, body)}
    res["oghist"] = {
        "file": xl.name, "bytes": xl.stat().st_size, "sha256": sha256(xl), "sheets": sheets,
        "classification_sheet": "Country Analytical History", "header_row_fiscal_years": 5, "header_row_data_year": 6,
        "economy_rows": len(body), "V2_duplicate_codes": len(codes) - len(set(codes)),
        "fiscal_years": [fy_full[0], fy_full[-1]], "fiscal_year_columns": len(fy_cols),
        "V2_fiscal_years_contiguous": fy_full == list(range(fy_full[0], fy_full[-1] + 1)),
        "gni_data_year_first_last": [data_year_row[fy_cols[0][0]], data_year_row[fy_cols[-1][0]]],
        "value_counts": dict(values.most_common()),
        "footer_or_note_rows": len(footer),
        "codes_not_in_api_country_list": sorted(set(codes) - set(iso2_to_iso3.values())),
        "parameters_sheet": {str(k): str(v) for k, v in params.items()},
    }

    # ---------------- R3: local WGI / CPIA (unchanged) ----------------
    latest_manifest = {}
    for line in (HERE.parent / "samples" / "MANIFEST.jsonl").read_text().splitlines():
        r = json.loads(line)
        if "indicators/" in r["file"]:
            latest_manifest[r["file"]] = r["sha256"]
    res["r3_local_files_unchanged"] = {f: sha256(HERE.parent / f) == h for f, h in sorted(latest_manifest.items())}
    local = {}
    for key, code in LOCAL.items():
        recs = [json.loads(line) for line in gzip.open(HERE.parent / "samples" / "indicators" / f"{code}.jsonl.gz", "rt")]
        local[key] = {r["country"]["id"] for r in recs if r["value"] is not None and r["country"]["id"]}

    # ---------------- cohort coverage per source (D-29, D-31) ----------------
    wdi_cov = {code: set(f[f.value.notna() & f.iso2.isin(economies)].iso2) for code, f in wdi_frames.items()}
    rows_out = []
    for (wb_code, name), grp in single.groupby(["country_code", "country_name"]):
        iso2 = wb_to_iso2_source_specific(wb_code)
        iso3 = iso2_to_iso3.get(iso2 or "")
        row = {"wb_country_code": wb_code, "country_name": name, "projects": len(grp), "iso2": iso2 or "", "iso3": iso3 or "",
               "alias_applied": wb_code in WB_TO_ISO2}
        for code in SERIES:
            row[f"wdi_{code}"] = "matched" if iso2 in wdi_cov[code] else ("no_values" if iso2 in economies else "unmatched")
        row["wgi_GE.EST"] = "matched" if iso2 in local["wgi"] else "unmatched"
        row["cpia"] = "matched" if iso2 in local["cpia"] else "not_covered"
        row["oghist"] = "matched" if iso3 in og else "unmatched"
        rows_out.append(row)
    cm = pd.DataFrame(rows_out).sort_values("wb_country_code")
    cm.to_csv(OUT / "cohort_country_match_by_source.csv", index=False)
    summary = {}
    for col in [c for c in cm.columns if c.startswith(("wdi_", "wgi_", "cpia", "oghist"))]:
        summary[col] = {status: {"economies": int((cm[col] == status).sum()), "projects": int(cm.loc[cm[col] == status, "projects"].sum())}
                        for status in sorted(cm[col].unique())}
        summary[col]["unmatched_or_missing_list"] = cm.loc[cm[col] != "matched", ["wb_country_code", "country_name", "projects"]] \
            .to_dict(orient="records")
    res["cohort_coverage"] = {
        "projects": len(cohort), "regional_projects_excluded_D29": int(cohort.regional.sum()),
        "single_country_projects": len(single), "single_country_economies": int(single.country_code.nunique()),
        "by_source": summary,
    }
    # within-window gaps for cohort economies (years each cohort economy lacks a value), WDI
    gaps = {}
    for code, f in wdi_frames.items():
        sub = f[f.iso2.isin(set(cm.iso2))]
        miss = sub[sub.value.isna()]
        gaps[code] = {"cohort_country_years": len(sub), "missing": len(miss),
                      "missing_pct": round(100 * len(miss) / max(1, len(sub)), 2),
                      "missing_2005_2025_pct": round(100 * sub[(sub.year >= 2005)].value.isna().mean(), 2),
                      "economies_with_any_gap": miss.groupby("iso2").year.apply(list).to_dict()}
    res["cohort_coverage"]["wdi_missing_country_years"] = gaps
    # OGHIST classification availability for cohort economies, FY2005-FY2027
    og_gap = {}
    for iso3 in set(cm.iso3) - {""}:
        if iso3 in og:
            missing = [fy for fy in range(2005, 2028) if og[iso3].get(fy, "None") in ("..", "None", "")]
            if missing:
                og_gap[iso3] = missing
    res["cohort_coverage"]["oghist_missing_fy2005_2027"] = og_gap

    # publication-lag evidence in this vintage: economies with a value for the latest years
    res["publication_lag_evidence"] = {code: {y: res["wdi"][code]["V4_economies_with_value_by_year"].get(y, 0) for y in (2022, 2023, 2024, 2025)}
                                       for code in SERIES}
    # consistency with the 2026-10-22 coverage probe (latest non-empty year per economy)
    probe = json.loads((HERE / "metadata" / "coverage_probe.json").read_text())
    iso3_to_iso2 = {v: k for k, v in iso2_to_iso3.items()}
    cons = {}
    for code in SERIES:
        p = {iso3_to_iso2.get(k): int(v) for k, v in probe[code]["latest_nonempty_year_by_economy"].items()}
        d = res["wdi"][code]["latest_year_per_economy"]
        diff = {k: (p.get(k), d.get(k)) for k in set(d) | {k for k in p if k in economies} if p.get(k) != d.get(k) and (p.get(k) or 0) >= 2000}
        cons[code] = {"economies_compared": len(d), "differences": diff}
    res["probe_consistency"] = cons
    for code in SERIES:
        del res["wdi"][code]["latest_year_per_economy"]
    (OUT / "reference_validation.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: v for k, v in res.items() if k != "cohort_coverage"}, indent=1, default=str)[:7000])
    print(json.dumps({k: {kk: {s: x for s, x in vv.items() if s != "unmatched_or_missing_list"} for kk, vv in v.items()}
                      if k == "by_source" else v for k, v in res["cohort_coverage"].items()
                      if k not in ("wdi_missing_country_years", "oghist_missing_fy2005_2027")}, indent=1, default=str))


if __name__ == "__main__":
    main()
