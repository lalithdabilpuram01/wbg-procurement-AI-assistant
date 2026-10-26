"""Representative pilot_v2 samples (sanitised outputs only): one section chunked by A, B and C, a parent, a structured
table with its rows and description, a plan activity and its evidence link.

  python -m rag.ingestion.samples_v2
"""
from __future__ import annotations

import json

from . import config

SECTION = None                      # PAD (Sri Lanka) 'VI. APPRAISAL SUMMARY > D. Procurement' (found by heading)
TABLE_GROUP = "26256705:t03401"     # results framework table that continues across pages


def main() -> None:
    out = config.OUTPUT_V2

    def ld(p):
        return [json.loads(x) for x in (out / p).read_text().splitlines()]
    secs = {s["section_id"]: s for s in ld("shared/sections.jsonl")}
    sec = SECTION if SECTION in secs else next(s for s in secs if secs[s]["heading_text"] == "D. Procurement" and s.startswith("26256705"))
    sample = {"section": secs[sec]}
    for s in "ABC":
        sample[f"strategy_{s}_chunks"] = [c for c in ld(f"strategies/{s}/chunks.jsonl") if c["section_id"] == sec]
    parents = ld("strategies/C/parents.jsonl")
    sample["strategy_C_parent"] = next(p for p in parents if sec in p["section_ids"])
    descs = ld("shared/table_descriptions.jsonl")
    grp = next((d for d in descs if d["table_group_id"] == TABLE_GROUP), None) or next(d for d in descs if len(d["table_ids"]) > 1 and d["kind"] == "generic")
    tabs = {t["table_id"]: t for t in ld("shared/tables.jsonl")}
    sample["table_group"] = {"description": grp, "tables": [tabs[t] for t in grp["table_ids"]],
                             "first_rows": [r for r in ld("shared/table_rows.jsonl") if r["table_id"] in grp["table_ids"] and r["row_index"] <= 3]}
    act = next(a for a in ld("shared/plan_activities.jsonl") if a["package_reference_core"] == "ET-ERA-147679-CS-QCBS")
    sample["plan_activity"] = act
    sample["plan_table"] = tabs[act["table_id"]]
    sample["evidence_link"] = next(l for l in ld("shared/evidence_links.jsonl") if l["activity_id"] == act["activity_id"])
    (out / "samples").mkdir(exist_ok=True)
    (out / "samples" / "samples_v2.json").write_text(json.dumps(sample, indent=1, sort_keys=True))
    print(json.dumps({k: (len(v) if isinstance(v, list) else "ok") for k, v in sample.items()}))


if __name__ == "__main__":
    main()
