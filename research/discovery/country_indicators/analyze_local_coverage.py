"""Country-indicator discovery pass (2026-10-22): offline analysis of existing local data.

Reads (never writes): Phase 1 indicator samples, the API country metadata saved by
probe_indicator_metadata.py, the approved cohort manifest, and the cohort-bounded Bronze inputs
(DS00005 signing dates, DS00979 publication dates).
Writes: country_indicators/analysis/local_coverage.json
"""
from __future__ import annotations

import collections
import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pandas as pd  # noqa: E402

from wbdisc.countries import is_regional, to_iso2  # noqa: E402

REPO = HERE.parent.parent
IND = HERE.parent / "samples" / "indicators"
OUT = HERE / "analysis" / "local_coverage.json"
YEARS = range(2005, 2026)  # relevant window, justified in the report


def load_countries() -> tuple[dict, set]:
    meta = json.loads((HERE / "metadata" / "countries.json").read_text())[1]
    by_iso2 = {c["iso2Code"]: c for c in meta}
    economies = {c["iso2Code"] for c in meta if c["region"]["id"] != "NA"}
    return by_iso2, economies


def indicator_profile(path: Path, economies: set, cohort_iso2: set) -> dict:
    rows = [json.loads(line) for line in gzip.open(path, "rt")]
    df = pd.DataFrame({"iso2": [r["country"]["id"] for r in rows], "name": [r["country"]["value"] for r in rows],
                       "year": [int(r["date"]) for r in rows], "value": [r["value"] for r in rows]})
    eco = df[df.iso2.isin(economies)]
    nn = eco[eco.value.notna()]
    coh = eco[eco.iso2.isin(cohort_iso2) & eco.year.isin(YEARS)]
    latest_year_per_cohort_country = nn[nn.iso2.isin(cohort_iso2)].groupby("iso2").year.max()
    return {
        "indicator_name": rows[0]["indicator"]["value"],
        "records": len(df), "non_null_records": int(df.value.notna().sum()),
        "null_pct_all_rows": round(100 * df.value.isna().mean(), 1),
        "distinct_country_entries": int(df.name.nunique()),
        "rows_with_blank_country_id": int((df.iso2 == "").sum()),
        "blank_id_names": sorted(df[df.iso2 == ""].name.unique())[:10],
        "economies_in_file": int(eco.iso2.nunique()), "economies_with_any_value": int(nn.iso2.nunique()),
        "year_range_rows": [int(df.year.min()), int(df.year.max())],
        "year_range_values": [int(nn.year.min()), int(nn.year.max())] if len(nn) else None,
        "years_with_no_values": sorted(set(range(df.year.min(), df.year.max() + 1)) - set(nn.year)),
        "non_null_economies_by_year_recent": nn[nn.year >= 2015].groupby("year").iso2.nunique().to_dict(),
        "cohort_countries_with_any_value": int(nn[nn.iso2.isin(cohort_iso2)].iso2.nunique()),
        "cohort_countries_matched": len(cohort_iso2),
        f"cohort_country_years_{YEARS.start}_{YEARS.stop - 1}_null_pct": round(100 * coh.value.isna().mean(), 1)
        if len(coh) else None,
        "cohort_countries_latest_value_year_distribution": latest_year_per_cohort_country.value_counts().sort_index().to_dict(),
    }


def main() -> None:
    by_iso2, economies = load_countries()
    cohort = pd.read_csv(REPO / "cohort" / "poc_cohort_2000_manifest.csv", dtype=str, keep_default_na=False)
    cohort["iso2"] = cohort.country_code.map(to_iso2)
    cohort["regional"] = cohort.country_code.map(is_regional)
    cohort["api_match"] = cohort.iso2.isin(economies)
    unmatched = cohort[~cohort.api_match & ~cohort.regional]
    cohort_iso2 = set(cohort[cohort.api_match].iso2)

    res: dict = {"window_years": [YEARS.start, YEARS.stop - 1]}
    res["cohort_countries"] = {
        "projects": len(cohort), "distinct_country_codes": int(cohort.country_code.nunique()),
        "single_country_projects": int((~cohort.regional).sum()),
        "regional_projects": int(cohort.regional.sum()),
        "regional_codes": cohort[cohort.regional].groupby(["country_code", "country_name"]).size()
        .rename("projects").reset_index().to_dict(orient="records"),
        "matched_to_api_economy_projects": int(cohort.api_match.sum()),
        "matched_distinct_economies": len(cohort_iso2),
        "matched_via_alias": cohort[cohort.api_match & (cohort.iso2 != cohort.country_code)]
        .groupby(["country_code", "iso2"]).size().rename("projects").reset_index().to_dict(orient="records"),
        "unmatched_non_regional": unmatched.groupby(["country_code", "country_name"]).size().rename("projects")
        .reset_index().to_dict(orient="records"),
        "current_income_level_of_matched_projects (API, today's value)":
            cohort[cohort.api_match].iso2.map(lambda c: by_iso2[c]["incomeLevel"]["value"]).value_counts().to_dict(),
        "lending_type_of_matched_projects (API, today's value)":
            cohort[cohort.api_match].iso2.map(lambda c: by_iso2[c]["lendingType"]["value"]).value_counts().to_dict(),
    }

    # relevant years
    bi = REPO / "databricks_upload" / "bronze_inputs"
    c5 = pd.read_csv(bi / "contract_awards_ds00005" / "ds00005_contract_awards__cohort2000.csv", dtype=str,
                     keep_default_na=False, encoding="utf-8-sig", usecols=["Project ID", "Contract Signing Date",
                                                                            "Borrower Country / Economy Code",
                                                                            "Borrower Country / Economy"])
    nb = pd.read_csv(bi / "procurement_notices_ds00979" / "ds00979_procurement_notice__cohort2000.csv", dtype=str,
                     keep_default_na=False, encoding="utf-8-sig", usecols=["Project ID", "Publication Date"])
    sign = pd.to_datetime(c5["Contract Signing Date"], format="%m/%d/%Y", errors="coerce")
    pub = pd.to_datetime(nb["Publication Date"], format="%m/%d/%Y", errors="coerce")
    fy = pd.to_numeric(cohort.approval_fy, errors="coerce")
    res["relevant_years"] = {
        "approval_fy_quantiles": fy.quantile([0, .05, .25, .5, .75, .95, 1]).to_dict(),
        "approval_fy_missing": int(fy.isna().sum()),
        "contract_signing_year_counts": sign.dt.year.value_counts().sort_index().astype(int).to_dict(),
        "contract_signing_unparsed": int(sign.isna().sum()),
        "notice_publication_year_counts": pub.dt.year.value_counts().sort_index().astype(int).to_dict(),
        "notice_publication_unparsed": int(pub.isna().sum()),
    }

    # regional projects: what borrower country do their contract rows carry? (read-only evidence for a mapping rule)
    reg_ids = set(cohort[cohort.regional].project_id)
    rc = c5[c5["Project ID"].isin(reg_ids)].copy()
    rc["borrower_is_regional_or_blank"] = rc["Borrower Country / Economy Code"].map(
        lambda c: (not c.strip()) or is_regional(c) or to_iso2(c) is None)
    per_proj = rc.groupby("Project ID").agg(
        rows=("Project ID", "size"),
        single_country_rows=("borrower_is_regional_or_blank", lambda s: int((~s).sum())),
        distinct_borrower_countries=("Borrower Country / Economy Code",
                                     lambda s: len({c for c in s if c.strip() and to_iso2(c)})))
    res["regional_projects_contract_borrower_country"] = {
        "regional_projects": len(reg_ids), "contract_rows": len(rc),
        "rows_with_single_country_borrower_code_pct": round(100 * (~rc.borrower_is_regional_or_blank).mean(), 1),
        "projects_all_rows_single_country": int((per_proj.single_country_rows == per_proj.rows).sum()),
        "projects_no_single_country_rows": int((per_proj.single_country_rows == 0).sum()),
        "projects_mixed": int(((per_proj.single_country_rows > 0) & (per_proj.single_country_rows < per_proj.rows)).sum()),
        "projects_with_gt1_borrower_country": int((per_proj.distinct_borrower_countries > 1).sum()),
    }
    # single-country projects whose contract rows carry a blank borrower code (known DS00005 issue)
    sc = c5[~c5["Project ID"].isin(reg_ids)]
    res["single_country_projects_contract_rows_blank_code"] = int((sc["Borrower Country / Economy Code"].str.strip() == "").sum())

    res["local_indicators"] = {p.name.replace(".jsonl.gz", ""): indicator_profile(p, economies, cohort_iso2)
                               for p in sorted(IND.glob("*.jsonl.gz"))}
    # CPIA structural coverage: which cohort economies ever have a CPIA value
    cp = res["local_indicators"]["IQ.CPA.PUBS.XQ"]
    res["cpia_cohort_projects_in_countries_with_any_cpia"] = None
    rows = [json.loads(line) for line in gzip.open(IND / "IQ.CPA.PUBS.XQ.jsonl.gz", "rt")]
    cpia_countries = {r["country"]["id"] for r in rows if r["value"] is not None}
    res["cpia_cohort_projects_in_countries_with_any_cpia"] = int(cohort.iso2.isin(cpia_countries).sum())
    res["cpia_cohort_projects_by_lending_type_without_cpia"] = cohort[cohort.api_match & ~cohort.iso2.isin(cpia_countries)] \
        .iso2.map(lambda c: by_iso2[c]["lendingType"]["value"]).value_counts().to_dict()
    del cp

    # new-indicator coverage from the mrnev probe (latest non-empty year only; no values stored)
    probe = json.loads((HERE / "metadata" / "coverage_probe.json").read_text())
    iso3_to_iso2 = {c["id"]: c["iso2Code"] for c in json.loads((HERE / "metadata" / "countries.json").read_text())[1]}
    newcov = {}
    for code, entry in probe.items():
        latest = entry.get("latest_nonempty_year_by_economy") or {}
        l2 = {iso3_to_iso2.get(k): int(v) for k, v in latest.items() if iso3_to_iso2.get(k) in economies}
        newcov[code] = {
            "economies_with_any_value": len(l2),
            "cohort_economies_with_any_value": len(set(l2) & cohort_iso2),
            "cohort_economies_without_any_value": sorted(cohort_iso2 - set(l2)),
            "cohort_latest_year_distribution": dict(sorted(collections.Counter(l2[c] for c in cohort_iso2 if c in l2).items())),
            "rows_all_years": (entry.get("page_header_all_years") or {}).get("total"),
            "rows_2000_2025": (entry.get("page_header_2000_2025") or {}).get("total"),
            "lastupdated": (entry.get("page_header_all_years") or {}).get("lastupdated"),
        }
    res["new_indicator_coverage_probe"] = newcov
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: v for k, v in res.items() if k not in ("local_indicators",)}, indent=1, default=str)[:9000])
    for k, v in res["local_indicators"].items():
        print(k, {kk: v[kk] for kk in ("records", "non_null_records", "economies_with_any_value", "year_range_values",
                                       "years_with_no_values", "cohort_countries_with_any_value",
                                       "cohort_country_years_2005_2025_null_pct", "rows_with_blank_country_id")})


if __name__ == "__main__":
    main()
