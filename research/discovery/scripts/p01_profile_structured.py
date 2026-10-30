"""Task 3: schema discovery and data-quality profiling for all structured sources.

Outputs:
  profiling/<dataset>.profile.json   column-level statistics
  profiling/<dataset>.profile.md     markdown table (embedded in reports)
  schemas/<dataset>.schema.json      observed column names + observed value kinds
  profiling/dq_checks.json           dataset-specific data-quality checks
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from wbdisc import datasets as D  # noqa: E402
from wbdisc.config import SETTINGS  # noqa: E402
from wbdisc.normalize import (clean_label, normalize_project_id, normalize_supplier_name, parse_amount,  # noqa: E402
                              parse_date, wb_fiscal_year)
from wbdisc.profiling import markdown_table, profile_records  # noqa: E402

P, SCH = SETTINGS.profiling_dir, SETTINGS.schemas_dir
ISO2 = re.compile(r"^[A-Z]{2}$")


def save_profile(name: str, records: list[dict], *, source: str, columns: list[str] | None = None) -> dict:
    prof = profile_records(records, columns=columns)
    prof["dataset"], prof["source"] = name, source
    P.mkdir(parents=True, exist_ok=True)
    SCH.mkdir(parents=True, exist_ok=True)
    (P / f"{name}.profile.json").write_text(json.dumps(prof, indent=1, default=str))
    (P / f"{name}.profile.md").write_text(f"### {name}\n\nSource: {source}. Rows profiled: {prof['row_count']}\n\n"
                                         + markdown_table(prof) + "\n")
    schema = {"dataset": name, "source": source, "rows_profiled": prof["row_count"],
              "columns": [{"name": c, "observed_kinds": p["observed_kinds"], "populated_pct": p["populated_pct"]}
                          for c, p in prof["columns"].items()]}
    (SCH / f"{name}.schema.json").write_text(json.dumps(schema, indent=1))
    print(f"profiled {name}: {prof['row_count']} rows, {len(prof['columns'])} columns")
    return prof


def contract_checks(df: pd.DataFrame) -> dict:
    out: dict = {"rows": len(df)}
    pid = df["Project ID"]
    out["project_id_valid_pct"] = round(100 * pid.map(lambda v: normalize_project_id(v) is not None).mean(), 3)
    out["project_id_invalid_examples"] = pid[pid.map(lambda v: normalize_project_id(v) is None)].value_counts().head(10).to_dict()
    out["exact_duplicate_rows"] = int(df.duplicated().sum())
    out["duplicate_rows_ignoring_as_of"] = int(df.drop(columns=["As of Date"]).duplicated().sum())
    out["as_of_date_values"] = df["As of Date"].value_counts().head(5).to_dict()

    # Contract grain: WB Contract Number x Supplier ID
    g = df.groupby("WB Contract Number")
    sizes = g.size()
    out["distinct_wb_contract_number"] = int(sizes.shape[0])
    out["wb_contract_number_empty"] = int((df["WB Contract Number"].str.strip() == "").sum())
    out["contracts_with_multiple_rows"] = int((sizes > 1).sum())
    out["rows_per_contract_distribution"] = sizes.value_counts().sort_index().head(12).to_dict()
    key_dupes = df.duplicated(subset=["WB Contract Number", "Supplier ID"], keep=False)
    out["rows_sharing_contract_and_supplier_id"] = int(key_dupes.sum())
    multi = df[df["WB Contract Number"].isin(sizes[sizes > 1].index)]
    amt = multi["Supplier Contract Amount (USD)"].map(parse_amount)
    eq = multi.assign(_a=amt).groupby("WB Contract Number")["_a"].nunique()
    out["multi_row_contracts_with_equal_amounts_pct"] = round(100 * (eq == 1).mean(), 2) if len(eq) else None
    attr_cols = ["Project ID", "Contract Description", "Contract Signing Date", "Procurement Method", "Procurement Category"]
    inconsistent = {c: int((g[c].nunique() > 1).sum()) for c in attr_cols}
    out["multi_row_contracts_with_inconsistent_attributes"] = inconsistent

    # Supplier identity
    sup = df[["Supplier ID", "Supplier", "Supplier Country / Economy Code"]].drop_duplicates()
    names_per_id = sup.groupby("Supplier ID")["Supplier"].nunique()
    out["distinct_supplier_id"] = int(names_per_id.shape[0])
    out["supplier_id_empty_rows"] = int((df["Supplier ID"].str.strip() == "").sum())
    out["supplier_ids_with_multiple_names"] = int((names_per_id > 1).sum())
    ex = names_per_id[names_per_id > 1].sort_values(ascending=False).head(5).index
    out["supplier_id_multiple_name_examples"] = {i: sorted(sup[sup["Supplier ID"] == i]["Supplier"].unique())[:5] for i in ex}
    sup = sup.assign(_key=sup["Supplier"].map(normalize_supplier_name))
    ids_per_key = sup.groupby(["_key", "Supplier Country / Economy Code"])["Supplier ID"].nunique()
    out["normalized_name_country_keys_with_multiple_supplier_ids"] = int((ids_per_key > 1).sum())
    out["normalized_name_key_examples"] = [
        {"key": k[0], "country": k[1], "supplier_ids": sorted(sup[(sup["_key"] == k[0]) & (sup["Supplier Country / Economy Code"] == k[1])]["Supplier ID"].unique())[:6]}
        for k in ids_per_key[ids_per_key > 1].sort_values(ascending=False).head(5).index]

    # Countries
    for col in ["Borrower Country / Economy Code", "Supplier Country / Economy Code"]:
        codes = df[col]
        non_iso = codes[~codes.str.match(ISO2) | codes.str.match(r"^\d")]
        out[f"{col}_non_iso2_values"] = non_iso.value_counts().head(15).to_dict()
        out[f"{col}_non_iso2_rows"] = int(non_iso.shape[0])
    cc = df.groupby("Borrower Country / Economy Code")["Borrower Country / Economy"].nunique()
    out["borrower_codes_with_multiple_names"] = {k: sorted(df[df["Borrower Country / Economy Code"] == k]["Borrower Country / Economy"].unique()) for k in cc[cc > 1].index[:10]}

    # Amounts
    a = df["Supplier Contract Amount (USD)"].map(parse_amount)
    out["amount_unparseable"] = int(a.isna().sum() - (df["Supplier Contract Amount (USD)"].str.strip() == "").sum())
    out["amount_empty"] = int((df["Supplier Contract Amount (USD)"].str.strip() == "").sum())
    out["amount_zero"] = int((a == 0).sum())
    out["amount_negative"] = int((a < 0).sum())
    out["amount_quantiles_usd"] = {str(q): round(float(a.quantile(q)), 2) for q in (0.01, 0.1, 0.5, 0.9, 0.99, 0.999)}
    out["amount_max_usd"] = float(a.max())
    out["amount_lt_100_usd"] = int((a < 100).sum())

    # Dates
    sd = df["Contract Signing Date"].map(parse_date)
    out["signing_date_unparseable"] = int(sd.isna().sum())
    out["signing_date_min"], out["signing_date_max"] = str(sd.min()), str(sd.max())
    asof = parse_date(df["As of Date"].iloc[0])
    out["signing_date_after_as_of"] = int((sd > asof).sum()) if asof else None
    fy = sd.map(wb_fiscal_year)
    out["fiscal_year_mismatch_with_signing_date"] = int((fy.astype("Int64").astype(str) != df["Fiscal Year"]).sum())
    out["calendar_year_mismatch"] = int((sd.map(lambda d: str(d.year) if d else "") != df["Contract signed - Calendar year"]).sum())

    # Categoricals
    for col in ["Procurement Category", "Procurement Method", "Review type", "Region"]:
        vc = df[col].value_counts()
        out[f"{col}_distribution"] = vc.to_dict() if len(vc) <= 40 else vc.head(40).to_dict()
        cleaned = df[col].map(lambda v: (clean_label(v) or "").lower())
        out[f"{col}_variants_collapsing_after_whitespace_case_normalization"] = int(vc.shape[0] - cleaned.nunique())
    gp = df["Project Global Practice"]
    out["global_practice_empty_pct"] = round(100 * (gp.str.strip() == "").mean(), 2)
    out["global_practice_multivalued_pct"] = round(100 * gp.str.contains(";").mean(), 2)
    out["borrower_contract_reference_empty_pct"] = round(100 * (df["Borrower Contract Reference Number"].str.strip() == "").mean(), 2)
    out["contract_description_multiline_rows"] = int(df["Contract Description"].str.contains("\n").sum())
    return out


def main() -> None:
    checks: dict = {}
    ca = D.contracts()
    save_profile("ds00005_contract_awards_population", ca.to_dict("records"), source="Finances One DS00005 bulk CSV")
    save_profile("ds00005_contract_awards_api_sample", D.contracts_api_sample(), source="Finances One DS00005 API")
    checks["ds00005"] = contract_checks(ca)

    legacy = D.contracts_legacy()
    save_profile("ds01004_contract_awards_legacy_population", legacy.to_dict("records"), source="Finances One DS01004 bulk CSV")
    checks["ds01004"] = {
        "rows": len(legacy), "columns": list(legacy.columns),
        "fiscal_year_range": [legacy["Fiscal Year"].min(), legacy["Fiscal Year"].max()],
        "fiscal_year_distribution": legacy["Fiscal Year"].value_counts().sort_index().to_dict(),
        "as_of_date_values": legacy["As of Date"].value_counts().to_dict(),
        "region_values": legacy["Region"].value_counts().to_dict(),
        "exact_duplicate_rows": int(legacy.duplicated().sum()),
        "contract_number_overlap_with_ds00005": int(legacy["Contract Number"].isin(set(ca["WB Contract Number"])).sum()),
    }

    projs = D.projects()
    save_profile("projects_v3_population", projs, source="search API v3 projects (selected fields)")
    save_profile("projects_v3_cluster_allfields", D.projects_v3_cluster(), source="search API v3 projects fl=* (cluster)")
    save_profile("projects_v2_population", D.projects_v2(), source="search API v2 projects (selected fields; frozen index)")
    save_profile("projects_v2_allfields_sample", D.projects_allfields(), source="search API v2 projects fl=*")
    pdf = pd.DataFrame(projs)
    checks["projects"] = {
        "rows": len(pdf), "distinct_id": int(pdf["id"].nunique()),
        "id_invalid": pdf["id"][pdf["id"].map(lambda v: normalize_project_id(v) is None)].head(10).tolist(),
        "status_distribution": pdf["status"].value_counts(dropna=False).to_dict(),
        "v2_only_ids": len({p["id"] for p in D.projects_v2()} - set(pdf["id"])),
        "v3_only_ids": len(set(pdf["id"]) - {p["id"] for p in D.projects_v2()}),
        "milestones_populated_pct": round(100 * pdf["milestones"].map(lambda v: isinstance(v, list) and len(v) > 0).mean(), 2),
        "isr_ratings_populated_pct": round(100 * pdf["isr_ratings"].map(lambda v: isinstance(v, list) and len(v) > 0).mean(), 2),
        "isr_procurement_rating_present_pct": round(100 * pdf["isr_ratings"].map(
            lambda v: isinstance(v, list) and any(x.get("name") == "Procurement" for x in v)).mean(), 2),
        "isr_procurement_rating_distribution": pd.Series([x.get("value") for v in pdf["isr_ratings"] if isinstance(v, list)
                                                         for x in v if x.get("name") == "Procurement"]).value_counts().to_dict(),
        "isr_distinct_dates_per_project": pd.Series([len({x.get("isr_date") for x in v}) for v in pdf["isr_ratings"]
                                                    if isinstance(v, list) and v]).value_counts().to_dict(),
        "ieg_ratings_populated_pct": round(100 * pdf["ieg_ratings"].map(lambda v: isinstance(v, list) and len(v) > 0).mean(), 2),
        "icr_ratings_populated_pct": round(100 * pdf["icr_ratings"].map(lambda v: isinstance(v, list) and len(v) > 0).mean(), 2),
        "milestone_keys": dict(pd.Series([k for v in pdf["milestones"] if isinstance(v, list) for m in v for k, x in m.items() if x])
                               .value_counts()),
        "impagency_populated_pct": round(100 * pdf["impagency"].fillna("").astype(str).str.strip().ne("").mean(), 2),
        "impagency_distinct": int(pdf["impagency"].nunique()),
        "multi_country_projects": int(pdf["countrycode"].map(lambda v: isinstance(v, list) and len(v) > 1).sum()),
        "countrycode_kinds": dict(pdf["countrycode"].map(lambda v: type(v).__name__).value_counts()),
        "closingdate_unparseable_nonempty": int(pdf["closingdate"].dropna().map(lambda v: parse_date(v) is None).sum()),
        "boardapprovaldate_range": [str(pdf["boardapprovaldate"].dropna().map(parse_date).min()),
                                    str(pdf["boardapprovaldate"].dropna().map(parse_date).max())],
    }

    nc, ng = D.notices_cluster(), D.notices_global()
    save_profile("procnotices_cluster_sample", nc, source="search API procnotices (project cluster)")
    save_profile("procnotices_global_sample", ng, source="search API procnotices (stratified by notice_type)")
    nb = D.notices_bulk()
    save_profile("ds00979_procurement_notice_population", nb.to_dict("records"), source="Finances One DS00979 bulk CSV")
    ndf = pd.DataFrame(nc)
    checks["procnotices_cluster"] = {
        "rows": len(ndf), "distinct_id": int(ndf["id"].nunique()),
        "notice_type_distribution": ndf["notice_type"].value_counts().to_dict(),
        "bid_reference_no_empty_pct_by_type": ndf.assign(_e=ndf["bid_reference_no"].fillna("").str.strip().eq(""))
            .groupby("notice_type")["_e"].mean().mul(100).round(2).to_dict(),
        "deadline_populated_pct_by_type": ndf.assign(_d=ndf["submission_deadline_date"].notna())
            .groupby("notice_type")["_d"].mean().mul(100).round(2).to_dict(),
        "notice_status_distribution": ndf["notice_status"].value_counts(dropna=False).to_dict(),
        "language_distribution": ndf["notice_lang_name"].value_counts(dropna=False).head(10).to_dict(),
        "deadline_before_noticedate": int((ndf["submission_deadline_date"].map(parse_date) <
                                           ndf["noticedate"].map(parse_date)).sum()),
        "noticedate_vs_submission_date_mismatch": int((ndf["noticedate"].map(parse_date) !=
                                                       ndf["submission_date"].map(parse_date)).sum()),
    }
    checks["ds00979"] = {
        "rows": len(nb), "columns": list(nb.columns), "distinct_id": int(nb["ID"].nunique()),
        "id_format_examples": nb["ID"].head(3).tolist(),
        "url_embeds_api_id_pct": round(100 * nb.apply(lambda r: r["URL"].endswith("OP" + r["ID"].zfill(8)), axis=1).mean(), 2),
        "project_id_empty": int((nb["Project ID"].str.strip() == "").sum()),
        "project_id_invalid": int((~nb["Project ID"].str.match(r"^P\d{6}$")).sum()),
        "notice_type_distribution": nb["Notice Type"].value_counts().to_dict(),
        "publication_fy_distribution": nb["Publication - Fiscal year"].value_counts().sort_index().to_dict(),
        "deadline_equals_publication_pct_by_type": nb.assign(_eq=nb["Deadline Date"] == nb["Publication Date"])
            .groupby("Notice Type")["_eq"].mean().mul(100).round(2).to_dict(),
        "deadline_empty_pct_by_type": nb.assign(_e=nb["Deadline Date"].str.strip() == "")
            .groupby("Notice Type")["_e"].mean().mul(100).round(2).to_dict(),
        "procurement_method_empty_pct_by_type": nb.assign(_e=nb["Procurement Method"].str.strip() == "")
            .groupby("Notice Type")["_e"].mean().mul(100).round(2).to_dict(),
        "region_values": nb["Region"].value_counts().to_dict(),
        "procurement_method_distinct": int(nb["Procurement Method"].nunique()),
        "procurement_method_top": nb["Procurement Method"].value_counts().head(25).to_dict(),
    }

    save_profile("wds_v3_meta_sample_by_type", D.docs_by_type(), source="search API v3 wds (per docty sample)")
    save_profile("wds_v3_cluster_meta", D.docs_cluster(), source="search API v3 wds (cluster projects)")
    save_profile("wds_v2_meta_sample_by_type", D.docs_by_type("v2"), source="search API v2 wds (per docty sample)")
    save_profile("wds_v2_cluster_meta", D.docs_cluster("v2"), source="search API v2 wds (cluster projects)")

    ieg = D.ieg()
    save_profile("ds00053_ieg_ratings_population", ieg.to_dict("records"), source="Finances One DS00053 bulk CSV")
    checks["ds00053"] = {
        "rows": len(ieg), "distinct_project_id": int(ieg["Project ID"].nunique()) if "Project ID" in ieg else None,
        "columns": list(ieg.columns),
    }
    for col in ieg.columns:
        if ieg[col].nunique() <= 30:
            checks["ds00053"][f"{col}_distribution"] = ieg[col].value_counts().to_dict()

    for name, df in (("ds00047_ibrd_latest", D.ibrd_latest()), ("ds00001_ida_latest", D.ida_latest())):
        save_profile(name, df.to_dict("records"), source=f"Finances One {name[:7].upper()} bulk CSV")
    for ds in ("DS00975", "DS00976"):
        save_profile(f"{ds.lower()}_history_sample", D.finance_history(ds), source=f"Finances One {ds} API (3 loans)")

    for code in ("GOV_WGI_CC.EST", "IQ.CPA.TRAN.XQ"):
        rows = D.indicator(code)
        flat = [{"indicator": r["indicator"]["id"], "country_id": r["country"]["id"], "country_name": r["country"]["value"],
                 "countryiso3code": r["countryiso3code"], "date": r["date"], "value": r["value"],
                 "obs_status": r.get("obs_status")} for r in rows]
        save_profile(f"indicator_{code}", flat, source="Indicators API")
        nonnull = [r for r in flat if r["value"] is not None]
        checks[f"indicator_{code}"] = {
            "rows": len(flat), "non_null_rows": len(nonnull),
            "years_with_values": sorted({r["date"] for r in nonnull}),
            "countries_with_values": len({r["countryiso3code"] for r in nonnull}),
            "aggregate_rows_non_null": sum(1 for r in nonnull if not r["countryiso3code"] or len(r["country_id"]) != 2 or r["country_id"][0].isdigit()),
        }
    (P / "dq_checks.json").write_text(json.dumps(checks, indent=1, default=str))
    print("wrote dq_checks.json")


if __name__ == "__main__":
    main()
