"""Score candidate countries for the 10-country / 70-project RAG scope (READ-ONLY).

Single-country cohort projects only (validated wdi_country_code; regional / multi-country excluded). Per country:
  eligible projects   PAD >= 1, ISR >= 2, Procurement Plan >= 1 (m05 API counts), contracts >= 10, notices >= 10
  lifecycle           eligible projects with ICR / ICR Review; eligible active vs closed
  diversity           distinct major sectors among eligible projects; distinct procurement methods in Gold contracts
  procurement         contracts and award value (Gold, exact decimal) for the eligible projects
  reference proxy     share of contracts whose borrower reference has the STEP plan-activity format
                      (CC-AGENCY-NNNNN-CW/GO/CS/NC-METHOD), i.e. the format that plan rows carry; plus the actual
                      plan->contract matches where a plan was parsed (probe)
  income group        latest WB classification (FY2026) from gold_country_context
Reads Databricks (Gold/Silver) read-only; writes only local JSON.
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from pyspark.sql import functions as F

from silver.common.session import get_spark

ROOT = Path(__file__).resolve().parents[2]
EV = Path(__file__).resolve().parent / "evidence"
OUT = Path(__file__).resolve().parent / "selection_v2_10_countries"
T = {"PAD": "Project Appraisal Document", "ISR": "Implementation Status and Results Report", "ICR": "Implementation Completion and Results Report",
     "ICRR": "Implementation Completion Report Review", "PP": "Procurement Plan"}
STEP = r"^[A-Z]{2}-[A-Z0-9/&.()\-]+-[0-9]{5,7}-(CW|GO|CS|NC|TA)-[A-Z]+$"


def main() -> None:
    with (ROOT / "cohort" / "poc_cohort_2000_manifest.csv").open(newline="", encoding="utf-8") as fh:
        cohort = {r["project_id"]: r for r in csv.DictReader(fh)}
    api = {r["project_id"]: r for r in (json.loads(x) for x in (EV / "api_document_counts_by_project.jsonl").read_text().splitlines())}
    cnt = {p: {k: api[p]["by_type"].get(v, 0) for k, v in T.items()} for p in api}
    probe = json.load(open(EV / "pp_reference_to_contract_match_probe.json"))["per_project"]
    s = get_spark()
    pf = {r["project_id"]: r.asDict() for r in s.table("workspace.wbg_gold.gold_project_portfolio").select(
        "project_id", "geography_type", "wdi_country_code", "wdi_country_iso3", "source_country_name", "region_name", "contract_award_count",
        "contract_award_amount_usd", "notice_count").collect()}
    single = {p for p, r in pf.items() if r["geography_type"] == "single_country" and r["wdi_country_code"]}
    ok = lambda p: (p in single and cnt[p]["PAD"] >= 1 and cnt[p]["ISR"] >= 2 and cnt[p]["PP"] >= 1 and pf[p]["contract_award_count"] >= 10  # noqa: E731
                    and pf[p]["notice_count"] >= 10)
    eligible = [p for p in single if ok(p)]
    ca = s.table("workspace.wbg_gold.gold_contract_analytics").where(F.col("project_id").isin(eligible)).join(
        s.table("workspace.wbg_silver.contract_awards").select("wb_contract_number", "borrower_contract_reference"), "wb_contract_number")
    per_p = {r["project_id"]: r.asDict() for r in ca.groupBy("project_id").agg(
        F.count("*").alias("contracts"), F.countDistinct("procurement_method").alias("methods"),
        F.sum(F.regexp_replace(F.upper("borrower_contract_reference"), r"\s+", "").rlike(STEP).cast("int")).alias("step_refs")).collect()}
    methods_by_country = defaultdict(set)
    for r in ca.select("wdi_country_code", "procurement_method").distinct().collect():
        methods_by_country[r[0]].add(r[1])
    inc = {r["country_code"]: (r["latest_income_group"]) for r in s.table("workspace.wbg_gold.gold_country_context").where("year = 2025").select(
        "country_code", F.col("income_group_fy_starting_in_year").alias("latest_income_group")).collect()}
    by_c = defaultdict(list)
    for p in eligible:
        by_c[pf[p]["wdi_country_code"]].append(p)
    countries = {}
    for c, ps in by_c.items():
        contracts = sum(per_p.get(p, {}).get("contracts", 0) for p in ps)
        step = sum(per_p.get(p, {}).get("step_refs", 0) for p in ps)
        countries[c] = {
            "country_code": c, "country_name": pf[ps[0]]["source_country_name"], "region": pf[ps[0]]["region_name"], "income_group_fy2026": inc.get(c),
            "eligible_projects": len(ps), "eligible_closed": sum(cohort[p]["status"] == "Closed" for p in ps), "eligible_active": sum(cohort[p]["status"] == "Active" for p in ps),
            "with_icr": sum(cnt[p]["ICR"] > 0 for p in ps), "with_icrr": sum(cnt[p]["ICRR"] > 0 for p in ps),
            "sectors": len({cohort[p]["major_sector"] for p in ps}), "methods": len(methods_by_country[c]), "contracts": contracts,
            "step_reference_share": round(step / contracts, 3) if contracts else 0.0,
            "probe_plan_matches": sum((probe.get(p) or [0, 0, 0])[1] for p in ps), "probe_projects": sum(1 for p in ps if p in probe),
            "fy_bands": len({cohort[p]["fy_band"] for p in ps}), "eligible_project_ids": sorted(ps)}
    viable = {c: v for c, v in countries.items() if v["eligible_projects"] >= 7 and v["eligible_active"] >= 2 and v["eligible_closed"] >= 2 and v["with_icr"] >= 2}
    for v in viable.values():
        v["score"] = round(min(v["eligible_projects"], 15) / 15 * 2 + min(v["with_icr"], 7) / 7 * 2 + v["step_reference_share"] * 2 + min(v["sectors"], 7) / 7
                           + min(v["methods"], 10) / 10 + min(v["fy_bands"], 5) / 5 + (0.5 if v["probe_plan_matches"] else 0), 3)
    res = {"single_country_eligible_projects": len(eligible), "countries_with_eligible": len(countries), "viable_countries (>=7 eligible, >=2 active, >=2 closed, >=2 ICR)": len(viable),
           "viable": sorted(viable.values(), key=lambda v: -v["score"]), "non_viable_top": sorted((v for c, v in countries.items() if c not in viable), key=lambda v: -v["eligible_projects"])[:10]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "country_scoring.json").write_text(json.dumps(res, indent=1, default=str))
    print(len(eligible), len(countries), len(viable))
    for v in res["viable"]:
        print(f'{v["country_code"]} {v["country_name"][:18]:18s} {v["region"][:22]:22s} inc={v["income_group_fy2026"]} elig={v["eligible_projects"]:2d} cl={v["eligible_closed"]:2d} ac={v["eligible_active"]:2d} '
              f'icr={v["with_icr"]:2d} sec={v["sectors"]} meth={v["methods"]:2d} contracts={v["contracts"]:5d} step={v["step_reference_share"]:.2f} probe={v["probe_plan_matches"]} score={v["score"]}')


if __name__ == "__main__":
    main()
