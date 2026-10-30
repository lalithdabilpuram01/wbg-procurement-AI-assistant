"""Module 1 discovery (READ-ONLY, local files only): document availability for all 2,000 cohort projects (from the m05 API
facet counts), estimated corpus size, and a reproducible design check for a ~500-project RAG subset and a 25-50-project
pilot. Nothing is created in Databricks; the 500 subset is NOT materialised (only stratum sizes are simulated); the pilot
is emitted as a PROPOSED candidate list for review.

Eligibility tiers (per project, from API counts):
  core_narrative  = has PAD AND >= 1 ISR                        (appraisal + implementation evidence)
  full_lifecycle  = core_narrative AND has ICR                  (adds completion evidence)
  procurement     = >= 1 Procurement Plan                       (structured procurement evidence)
Pages are estimated from per-type medians observed in the WDS v2 page counts (m02), not measured.
"""
from __future__ import annotations

import csv
import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EV = Path(__file__).resolve().parent / "evidence"
T = {"PAD": "Project Appraisal Document", "ISR": "Implementation Status and Results Report", "ICR": "Implementation Completion and Results Report",
     "ICRR": "Implementation Completion Report Review", "PP": "Procurement Plan", "PPR": "Project Paper", "PID": "Project Information Document",
     "ESRS": "Environmental and Social Review Summary", "AUD": "Auditing Document"}
SEED = 20261024


def main() -> None:
    with (ROOT / "cohort" / "poc_cohort_2000_manifest.csv").open(newline="", encoding="utf-8") as fh:
        cohort = {r["project_id"]: r for r in csv.DictReader(fh)}
    api = {r["project_id"]: r for r in (json.loads(ln) for ln in (EV / "api_document_counts_by_project.jsonl").read_text().splitlines())}
    errors = [p for p, r in api.items() if r["status"] != "ok"]
    ok = {p: r for p, r in api.items() if r["status"] == "ok"}
    cnt = {p: {k: r["by_type"].get(v, 0) for k, v in T.items()} for p, r in ok.items()}
    med_pages = json.load(open(EV / "wds_metadata_coverage.json"))["summary"]["page_stats_by_type"]
    mp = {"PAD": med_pages["PAD"]["median"], "ISR": med_pages["ISR"]["median"], "ICR": med_pages["ICR"]["median"], "ICRR": med_pages["ICRR"]["median"],
          "PPR": med_pages["Project Paper"]["median"], "PP": med_pages["PP"]["median"]}
    tier = {p: {"core_narrative": c["PAD"] > 0 and c["ISR"] > 0, "full_lifecycle": c["PAD"] > 0 and c["ISR"] > 0 and c["ICR"] > 0, "procurement": c["PP"] > 0}
            for p, c in cnt.items()}
    totals = [r["total"] for r in ok.values()]
    has = {k: sum(1 for c in cnt.values() if c[k] > 0) for k in T}
    strata_cols = ["region", "status", "fy_band", "contract_volume_quartile", "major_sector", "country_is_regional"]
    by_stratum = {col: {} for col in strata_cols}
    for col in strata_cols:
        g = defaultdict(list)
        for p in ok:
            g[cohort[p][col]].append(p)
        by_stratum[col] = {k: {"projects": len(v), "core_narrative": sum(tier[p]["core_narrative"] for p in v), "full_lifecycle": sum(tier[p]["full_lifecycle"] for p in v),
                               "with_icr": sum(cnt[p]["ICR"] > 0 for p in v)} for k, v in sorted(g.items())}
    eligible = [p for p in ok if tier[p]["core_narrative"] and tier[p]["procurement"]]
    est = lambda ps, cap_isr=None: sum(mp["PAD"] * min(cnt[p]["PAD"], 1) + mp["ISR"] * (min(cnt[p]["ISR"], cap_isr) if cap_isr else cnt[p]["ISR"]) + mp["ICR"] * min(cnt[p]["ICR"], 1)  # noqa: E731
                                       + mp["ICRR"] * min(cnt[p]["ICRR"], 1) + mp["PPR"] * cnt[p]["PPR"] for p in ps)
    # simulated stratified 500: proportional allocation over region x status within the eligible pool, seeded; NOT materialised
    rng = random.Random(SEED)
    pool = defaultdict(list)
    for p in sorted(eligible):
        pool[(cohort[p]["region"], cohort[p]["status"])].append(p)
    target = 500
    alloc = {k: max(1, round(target * len(v) / len(eligible))) for k, v in pool.items()}
    sim = [p for k, v in sorted(pool.items()) for p in rng.sample(v, min(alloc[k], len(v)))]
    sim_mix = {col: dict(Counter(cohort[p][col] for p in sim)) for col in ("region", "status", "fy_band", "contract_volume_quartile")}
    # proposed pilot: 40 projects, region x status x ICR-availability, from eligible pool, seeded
    rng2 = random.Random(SEED + 1)
    pilot_pool = defaultdict(list)
    for p in sorted(eligible):
        pilot_pool[(cohort[p]["region"], cohort[p]["status"])].append(p)
    pilot = []
    for k in sorted(pilot_pool):
        v = pilot_pool[k]
        with_icr = [p for p in v if cnt[p]["ICR"] > 0]
        without = [p for p in v if cnt[p]["ICR"] == 0]
        take = rng2.sample(with_icr, min(2, len(with_icr))) if k[1] == "Closed" else []
        take += rng2.sample(without, min(3 - len(take), len(without)))
        pilot += take
    pilot = sorted(set(pilot))
    pilot_rows = [{"project_id": p, "region": cohort[p]["region"], "status": cohort[p]["status"], "fy_band": cohort[p]["fy_band"], "major_sector": cohort[p]["major_sector"],
                   "contract_volume_quartile": cohort[p]["contract_volume_quartile"], "counts": cnt[p], "api_total_documents": ok[p]["total"]} for p in pilot]
    res = {
        "api_projects_queried": len(api), "api_errors": errors, "documents_total_all_types": sum(totals),
        "documents_per_project": {"median": statistics.median(totals), "p10": sorted(totals)[len(totals) // 10], "p90": sorted(totals)[int(0.9 * (len(totals) - 1))],
                                  "max": max(totals), "zero": sum(t == 0 for t in totals)},
        "docs_by_type_total": {k: sum(c[k] for c in cnt.values()) for k in T},
        "projects_with_type": {k: {"projects": v, "pct": round(100 * v / len(ok), 1)} for k, v in has.items()},
        "per_project_type_count_distribution": {k: dict(sorted(Counter(min(c[k], 10) for c in cnt.values()).items())) for k in ("PAD", "ISR", "ICR", "ICRR", "PP")},
        "tiers": {"core_narrative": sum(t["core_narrative"] for t in tier.values()), "full_lifecycle": sum(t["full_lifecycle"] for t in tier.values()),
                  "procurement": sum(t["procurement"] for t in tier.values()), "eligible_core_plus_procurement": len(eligible)},
        "by_stratum": by_stratum, "median_pages_used": mp,
        "estimated_narrative_pages": {"all_eligible_all_isrs": est(eligible), "simulated_500_all_isrs": est(sim), "simulated_500_isr_cap_4": est(sim, 4),
                                      "pilot_isr_cap_4": est(pilot, 4)},
        "simulated_500_not_materialised": {"size": len(sim), "mix": sim_mix},
        "proposed_pilot": {"size": len(pilot), "mix": {col: dict(Counter(cohort[p][col] for p in pilot)) for col in ("region", "status", "fy_band", "contract_volume_quartile")},
                           "with_icr": sum(cnt[p]["ICR"] > 0 for p in pilot), "rows": pilot_rows},
        "languages_total": dict(Counter({lg: n for r in ok.values() for lg, n in r["by_lang"].items()}).most_common(10)),
    }
    (EV / "coverage_and_selection_design.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k not in ("by_stratum", "proposed_pilot")}, indent=1))
    print("pilot", res["proposed_pilot"]["size"], res["proposed_pilot"]["mix"], "with_icr", res["proposed_pilot"]["with_icr"])
    print(json.dumps(by_stratum["region"], indent=0)); print(json.dumps(by_stratum["status"])); print(json.dumps(by_stratum["fy_band"]))


if __name__ == "__main__":
    main()
